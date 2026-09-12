#!/usr/bin/env python3
"""Checks tests and coverage files for mistakes, gaps and style problems.

    python3 tools/lint.py                        # report problems
    python3 tools/lint.py --fix                  # also rewrite test.json files in canonical form
    python3 tools/lint.py tests/native/heredocs  # only check some areas or tests

It exits with status 1 if it finds any problem. See docs/test-format.md for the
rules it enforces.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runner"))
import hcltest  # noqa: E402  reuses the runner's test loading and normalization

TESTS = ROOT / "tests"
COVERAGE = ROOT / "coverage"
KEY_ORDER = ["description", "spec", "op", "input", "features", "status", "notes", "variables", "reference_error", "expect"]
FEATURES = {"unknown-values", "typed-values"}
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_INPUT_BYTES = 2048
MAX_DESCRIPTION = 100
EM_DASH = "\u2014"


def value_type(value):
    """The type of a protocol value, in go-cty's JSON type notation."""
    kind = next(k for k in value if k != "element_type")
    x = value[kind]
    if kind in ("string", "number", "bool"):
        return kind
    if kind in ("null", "unknown"):
        return x
    if kind == "tuple":
        return ["tuple", [value_type(item) for item in x]]
    if kind == "object":
        return ["object", {key: value_type(item) for key, item in x.items()}]
    return [kind, value["element_type"]]


def type_problems(value, path="value"):
    """Lists places where a list, set or map holds elements of another type than it declares."""
    problems = []
    kind = next(k for k in value if k != "element_type")
    items = value[kind]
    if kind in ("tuple", "list", "set"):
        items = dict(enumerate(items))
    if kind in ("tuple", "list", "set", "object", "map"):
        for key, item in items.items():
            if kind in ("list", "set", "map") and value_type(item) != value.get("element_type"):
                problems.append(f"{path}[{key!r}] has type {json.dumps(value_type(item))}, "
                                f"but element_type is {json.dumps(value.get('element_type'))}")
            problems.extend(type_problems(item, f"{path}[{key!r}]"))
    return problems


def needs_typed_values(value):
    """Whether a value can only be produced by an implementation with typed values."""
    kind = next(k for k in value if k != "element_type")
    x = value[kind]
    if kind in ("list", "set", "map"):
        return True
    if kind in ("null", "unknown"):
        return x != "dynamic"
    if kind == "tuple":
        return any(needs_typed_values(item) for item in x)
    if kind == "object":
        return any(needs_typed_values(item) for item in x.values())
    return False


def body_values(body):
    for value in body.get("attributes", {}).values():
        yield value
    for block in body.get("blocks", []):
        yield from body_values(block.get("body", {}))


def load_anchors():
    lines = (ROOT / "tools" / "spec-anchors.txt").read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


class Linter:
    def __init__(self, fix, scopes):
        self.fix = fix
        self.scopes = scopes
        self.anchors = load_anchors()
        self.problems = []
        self.disputed = {}

    def in_scope(self, name):
        return not self.scopes or any(name == s or name.startswith(s + "/") for s in self.scopes)

    def problem(self, where, message):
        self.problems.append(f"{where}: {message}")

    def text_field(self, where, field, value, required=True):
        if value is None:
            if required:
                self.problem(where, f'"{field}" is required')
            return
        if not isinstance(value, str) or not value.strip():
            self.problem(where, f'"{field}" must be a non-empty string')
        elif EM_DASH in value:
            self.problem(where, f'"{field}" contains an em dash')

    def check_test(self, test_json, descriptions, inputs):
        where = test_json.parent.relative_to(TESTS).as_posix()
        try:
            self.disputed[where] = json.loads(test_json.read_text(encoding="utf-8")).get("status") == "disputed"
        except (OSError, ValueError):
            pass
        if not self.in_scope(where):
            return self.register_only(test_json, where, descriptions, inputs)
        parts = where.split("/")
        if len(parts) != 3:
            self.problem(where, "tests must be at tests/<syntax>/<area>/<name>")
        for part in parts:
            if not NAME.match(part):
                self.problem(where, f'"{part}" is not a lowercase, dash-separated name')

        try:
            raw = test_json.read_text(encoding="utf-8")
            meta = json.loads(raw)
            test = hcltest.Test(test_json)
        except (ValueError, hcltest.TestError) as e:
            self.problem(where, str(e))
            return None

        extra = sorted(p.name for p in test_json.parent.iterdir() if p.name not in ("test.json", test.input.name))
        if extra:
            self.problem(where, f"unexpected files: {', '.join(extra)}")

        canonical = json.dumps({k: meta[k] for k in KEY_ORDER if k in meta}, indent=2, ensure_ascii=False) + "\n"
        if raw != canonical:
            if self.fix:
                test_json.write_text(canonical, encoding="utf-8")
            else:
                self.problem(where, "test.json is not in canonical form (run with --fix)")

        description = meta.get("description")
        self.text_field(where, "description", description)
        if isinstance(description, str):
            if description.endswith("."):
                self.problem(where, "description should not end with a period")
            if description[:1].islower():
                self.problem(where, "description should start with a capital letter")
            if len(description) > MAX_DESCRIPTION:
                self.problem(where, f"description is longer than {MAX_DESCRIPTION} characters")
            if description in descriptions:
                self.problem(where, f"same description as {descriptions[description]}")
            descriptions.setdefault(description, where)

        spec = meta.get("spec")
        if not isinstance(spec, list) or not spec:
            self.problem(where, '"spec" must list at least one spec section')
        else:
            for anchor in spec:
                if anchor not in self.anchors:
                    self.problem(where, f"unknown spec section {anchor!r} (see tools/spec-anchors.txt)")

        for feature in meta.get("features", []):
            if feature not in FEATURES:
                self.problem(where, f"unknown feature {feature!r}")
        if meta.get("status") == "normal":
            self.problem(where, 'leave out "status" instead of setting it to "normal"')
        if meta.get("status") == "disputed" and not meta.get("notes"):
            self.problem(where, "disputed tests must explain the dispute in notes")
        self.text_field(where, "notes", meta.get("notes"), required=False)

        if "variables" in meta:
            if test.op != "eval":
                self.problem(where, "only eval tests can have variables")
            elif not isinstance(meta["variables"], dict):
                self.problem(where, '"variables" must be an object')
            else:
                for name, value in meta["variables"].items():
                    try:
                        hcltest.normalize_value(value)
                    except hcltest.AdapterError as e:
                        self.problem(where, f"variable {name!r}: {e}")
                        continue
                    for issue in type_problems(value, f"variable {name!r}"):
                        self.problem(where, issue)
                    if needs_typed_values(value) and "typed-values" not in meta.get("features", []):
                        self.problem(where, f'variable {name!r} is a list, set, map, or typed null or unknown, so the '
                                            'test needs "features": ["typed-values"]')

        expect = meta["expect"]
        if test.op == "eval" and expect.get("valid"):
            values = list(body_values(expect.get("body", {})))
            for value in values:
                for issue in type_problems(value, "expected value"):
                    self.problem(where, issue)
            if any(needs_typed_values(v) for v in values) and "typed-values" not in meta.get("features", []):
                self.problem(where, 'the expected result has a list, set, map, or typed null or unknown, so the test needs '
                                    '"features": ["typed-values"]')
        unknown = set(expect) - {"valid", "body", "phase"}
        if unknown:
            self.problem(where, f"unknown fields in expect: {', '.join(sorted(unknown))}")
        if expect["valid"]:
            if "body" not in expect:
                self.problem(where, "a successful expectation needs a body")
            if "phase" in expect:
                self.problem(where, "only expected errors have a phase")
            if "reference_error" in meta:
                self.problem(where, "only tests that expect an error have a reference_error")
        else:
            if "body" in expect:
                self.problem(where, "an expected error cannot have a body")
            self.text_field(where, "reference_error", meta.get("reference_error"))
            if test.op == "eval" and expect.get("phase") not in ("parse", "eval"):
                self.problem(where, 'eval tests that expect an error need "phase": "parse" or "eval"')
            if test.op == "parse" and "phase" in expect:
                self.problem(where, 'parse tests don\'t need a "phase"')

        data = test.input.read_bytes()
        if len(data) > MAX_INPUT_BYTES:
            self.problem(where, f"input is larger than {MAX_INPUT_BYTES} bytes; keep tests minimal")
        key = (test.op, data, json.dumps(meta.get("variables"), sort_keys=True))
        if key in inputs:
            self.problem(where, f"same op, input and variables as {inputs[key]}")
        inputs.setdefault(key, where)
        return where

    def register_only(self, test_json, where, descriptions, inputs):
        """Records an out-of-scope test so in-scope tests can be checked against it."""
        try:
            meta = json.loads(test_json.read_text(encoding="utf-8"))
            data = (test_json.parent / meta.get("input", "input.hcl")).read_bytes()
        except (OSError, ValueError):
            return where
        descriptions.setdefault(meta.get("description"), where)
        inputs.setdefault((meta.get("op"), data, json.dumps(meta.get("variables"), sort_keys=True)), where)
        return where

    def check_coverage(self, test_names):
        covered = defaultdict(list)
        ids = {}
        for path in sorted(COVERAGE.glob("*.json")):
            where = path.relative_to(ROOT).as_posix()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as e:
                self.problem(where, f"invalid JSON: {e}")
                continue
            area = data.get("area")
            report = self.in_scope(str(area))
            saved = len(self.problems)
            if not isinstance(area, str) or not (TESTS / area).is_dir():
                self.problem(where, f'"area" must name a directory under tests/, got {area!r}')
            if path.stem != str(area).replace("/", "-"):
                self.problem(where, f"file should be named {str(area).replace('/', '-')}.json")
            rules = data.get("rules")
            if not isinstance(rules, list) or not rules:
                self.problem(where, '"rules" must be a non-empty list')
                continue
            for i, rule in enumerate(rules):
                rule_where = f"{where} rule {rule.get('id', i)!r}"
                unknown = set(rule) - {"id", "spec", "rule", "tests", "untested"}
                if unknown:
                    self.problem(rule_where, f"unknown fields: {', '.join(sorted(unknown))}")
                rule_id = rule.get("id")
                prefix = str(area).split("/")[-1] + "-"
                if not isinstance(rule_id, str) or not NAME.match(rule_id):
                    self.problem(rule_where, '"id" must be a lowercase, dash-separated name')
                elif not rule_id.startswith(prefix):
                    self.problem(rule_where, f'"id" must start with "{prefix}"')
                elif rule_id in ids:
                    self.problem(rule_where, f"id already used in {ids[rule_id]}")
                else:
                    ids[rule_id] = where
                if rule.get("spec") not in self.anchors:
                    self.problem(rule_where, f"unknown spec section {rule.get('spec')!r}")
                self.text_field(rule_where, "rule", rule.get("rule"))
                tests, untested = rule.get("tests"), rule.get("untested")
                if (tests is None) == (untested is None):
                    self.problem(rule_where, 'needs either "tests" or an "untested" reason')
                if untested is not None:
                    self.text_field(rule_where, "untested", untested)
                if tests is not None:
                    if not isinstance(tests, list) or not tests:
                        self.problem(rule_where, '"tests" must be a non-empty list')
                        continue
                    for name in tests:
                        if name not in test_names:
                            self.problem(rule_where, f"no such test {name!r}")
                        covered[name].append(rule_id)
                    marked = str(rule.get("rule", "")).endswith("(disputed)")
                    all_disputed = all(self.disputed.get(name, False) for name in tests)
                    if all_disputed and not marked:
                        self.problem(rule_where, 'all of its tests are disputed, so the rule must describe what they '
                                                 'expect and end with "(disputed)"')
                    if marked and not any(self.disputed.get(name, False) for name in tests):
                        self.problem(rule_where, 'ends with "(disputed)" but none of its tests is disputed')
            if not report:
                del self.problems[saved:]
        for name in sorted(test_names - set(covered)):
            if self.in_scope(name):
                self.problem(name, "no rule in coverage/ lists this test")


def main():
    parser = argparse.ArgumentParser(description="Check tests and coverage files.")
    parser.add_argument("--fix", action="store_true", help="rewrite test.json files in canonical form")
    parser.add_argument("paths", nargs="*", help="areas or tests to check, such as tests/native/heredocs")
    args = parser.parse_args()

    scopes = [Path(p).resolve().relative_to(TESTS).as_posix() for p in args.paths]
    linter = Linter(args.fix, scopes)
    descriptions, inputs, names = {}, {}, set()
    for test_json in sorted(TESTS.rglob("test.json")):
        name = linter.check_test(test_json, descriptions, inputs)
        if name:
            names.add(name)
    for directory in sorted(p for p in TESTS.rglob("*") if p.is_dir()):
        depth = len(directory.relative_to(TESTS).parts)
        if depth == 3 and not (directory / "test.json").exists() and linter.in_scope(directory.relative_to(TESTS).as_posix()):
            linter.problem(directory.relative_to(TESTS).as_posix(), "test directory without test.json")
    linter.check_coverage(names)

    for problem in linter.problems:
        print(problem)
    checked = sum(1 for name in names if linter.in_scope(name))
    print(f"{checked} tests checked, {len(linter.problems)} problems")
    return 1 if linter.problems else 0


if __name__ == "__main__":
    sys.exit(main())
