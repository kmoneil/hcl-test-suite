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
KEY_ORDER = ["description", "spec", "op", "input", "features", "status", "notes", "variables", "functions",
             "evaluation_mode", "schema", "reference_error", "expect"]
FEATURES = {"unknown-values", "typed-values", "functions", "json-syntax", "static-analysis", "type-expressions"}
TYPE_KINDS = ("type", "type-constraint", "type-constraint-with-defaults")
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_INPUT_BYTES = 2048
MAX_DESCRIPTION = 100
EM_DASH = "\u2014"


def is_function_name(name):
    """Whether a name is an identifier, or identifiers joined by :: for a namespaced function. Python's identifier
    characters are XID_Start and XID_Continue, close to the ID_Start and ID_Continue that HCL uses."""
    return all(part and part[0].isidentifier() and part.replace("-", "_").isidentifier() for part in name.split("::"))


def needs_unknown_values(value):
    """Whether a value contains an unknown value."""
    kind = next(k for k in value if k != "element_type")
    x = value[kind]
    if kind == "unknown":
        return True
    if kind in ("tuple", "list", "set"):
        return any(needs_unknown_values(item) for item in x)
    if kind in ("object", "map"):
        return any(needs_unknown_values(item) for item in x.values())
    return False


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


def decode_body_problems(body, schema, where="expected body"):
    """Lists the ways an expected decode result couldn't come from applying its schema."""
    problems = []
    analyses = {attr["name"]: attr["analysis"] for attr in schema.get("attributes", []) if "analysis" in attr}
    for name, result in body.get("attributes", {}).items():
        problems.extend(analysis_result_problems(result, analyses.get(name), f"{where}: attribute {name!r}"))
    if schema["mode"] == "dynamic-attributes":
        if body.get("blocks"):
            problems.append(f"{where} has blocks, but dynamic attributes processing gives none")
    else:
        requested = {attr["name"] for attr in schema.get("attributes", [])}
        for name in body.get("attributes", {}):
            if name not in requested:
                problems.append(f"{where} has the attribute {name!r}, which its schema doesn't request")
        for attr in schema.get("attributes", []):
            if attr.get("required") and attr["name"] not in body.get("attributes", {}):
                problems.append(f"{where} lacks the attribute {attr['name']!r}, which its schema requires")
        block_schemas = {block["type"]: block for block in schema.get("blocks", [])}
        for i, block in enumerate(body.get("blocks", [])):
            block_schema = block_schemas.get(block.get("type"))
            if block_schema is None:
                problems.append(f"{where} has a block of type {block.get('type')!r}, which its schema doesn't request")
                continue
            if len(block.get("labels", [])) != len(block_schema.get("labels", [])):
                problems.append(f"{where}: block {i} has {len(block.get('labels', []))} labels, but its schema names "
                                f"{len(block_schema.get('labels', []))}")
            problems.extend(decode_body_problems(block.get("body", {}), block_schema["body"], f"{where}: body of block {i}"))
    if (body.get("remain") is not None) != ("remain" in schema):
        problems.append(f"{where} must have a remain body exactly when its schema has a remain schema")
    elif "remain" in schema:
        problems.extend(decode_body_problems(body["remain"], schema["remain"], f"{where}: remain"))
    return problems


RESULT_KEYS = {"static-list": "static_list", "static-map": "static_map", "static-call": "static_call",
               "static-traversal": "static_traversal", "type": "type", "type-constraint": "type",
               "type-constraint-with-defaults": "type"}


def analysis_result_problems(result, analysis, where):
    """Lists the ways an expected result couldn't come from an analysis."""
    kind = analysis["kind"] if analysis else "value"
    got = next(iter(result)) if isinstance(result, dict) and len(result) == 1 else None
    if kind == "value":
        return [f"{where} is a {got} result, but it is evaluated, not analyzed"] if got in hcltest.ANALYSIS_RESULTS else []
    if got != RESULT_KEYS[kind]:
        return [f"{where} must be a {RESULT_KEYS[kind]} result for its {kind} analysis"]
    x = result[got]
    problems = []
    if kind == "type" and not is_exact_type(x):
        problems.append(f"{where} must be an exact type, without dynamic parts or optional attributes, for its type analysis")
    if kind == "static-list":
        for i, item in enumerate(x):
            problems.extend(analysis_result_problems(item, analysis.get("elements"), f"{where}: element {i}"))
    elif kind == "static-map":
        for i, pair in enumerate(x):
            problems.extend(analysis_result_problems(pair["key"], analysis.get("keys"), f"{where}: key {i}"))
            problems.extend(analysis_result_problems(pair["value"], analysis.get("values"), f"{where}: value {i}"))
    elif kind == "static-call":
        for i, item in enumerate(x["arguments"]):
            problems.extend(analysis_result_problems(item, analysis.get("arguments"), f"{where}: argument {i}"))
    return problems


def is_exact_type(ty):
    """Whether a type in JSON type notation has no dynamic parts and no optional attributes."""
    if isinstance(ty, str):
        return ty != "dynamic"
    if ty[0] == "tuple":
        return all(is_exact_type(item) for item in ty[1])
    if ty[0] == "object":
        return len(ty) == 2 and all(is_exact_type(item) for item in ty[1].values())
    return is_exact_type(ty[1])


def type_needs_typed_values(ty):
    """Whether a type can only exist in an implementation with typed values: it has a list, set or map type."""
    if isinstance(ty, str):
        return False
    if ty[0] in ("list", "set", "map"):
        return True
    items = ty[1] if ty[0] == "tuple" else ty[1].values()
    return any(type_needs_typed_values(item) for item in items)


def analysis_kinds(analysis):
    """The kinds of an analysis and of all its parts."""
    kinds = {analysis["kind"]}
    for part in hcltest.ANALYSIS_PARTS[analysis["kind"]]:
        if part in analysis:
            kinds |= analysis_kinds(analysis[part])
    return kinds


def schema_analysis_kinds(schema):
    kinds = set()
    for attr in schema.get("attributes", []):
        if "analysis" in attr:
            kinds |= analysis_kinds(attr["analysis"])
    for block in schema.get("blocks", []):
        kinds |= schema_analysis_kinds(block["body"])
    if "remain" in schema:
        kinds |= schema_analysis_kinds(schema["remain"])
    return kinds


def result_types(result):
    """The types in the type results of a decoded attribute."""
    kind = next(iter(result)) if isinstance(result, dict) and len(result) == 1 else None
    if kind == "type":
        yield result[kind]
    elif kind in ("static_list", "static_map", "static_call"):
        items = result[kind]["arguments"] if kind == "static_call" else result[kind]
        for item in items:
            if kind == "static_map":
                yield from result_types(item["key"])
                yield from result_types(item["value"])
            else:
                yield from result_types(item)


def body_types(body):
    for result in body.get("attributes", {}).values():
        yield from result_types(result)
    for block in body.get("blocks", []):
        yield from body_types(block.get("body", {}))
    if body.get("remain") is not None:
        yield from body_types(body["remain"])


def analysis_default_problems(analysis, where):
    problems = []
    for part in hcltest.ANALYSIS_PARTS[analysis["kind"]]:
        if analysis.get(part) == {"kind": "value"}:
            problems.append(f'{where}: leave out "{part}", which evaluates the expressions anyway')
        elif part in analysis:
            problems.extend(analysis_default_problems(analysis[part], f"{where}: {part}"))
    return problems


def schema_default_problems(schema, where="schema"):
    """Lists fields of a schema that only repeat their default, which would hide duplicate tests."""
    problems = []
    for field in ("attributes", "blocks"):
        if field in schema and not schema[field]:
            problems.append(f'{where}: leave out the empty "{field}"')
    for attr in schema.get("attributes", []):
        if attr.get("required") is False:
            problems.append(f'{where}: leave out "required": false for {attr["name"]!r}')
        if attr.get("analysis") == {"kind": "value"}:
            problems.append(f'{where}: leave out the analysis of {attr["name"]!r}, which evaluates it anyway')
        elif "analysis" in attr:
            problems.extend(analysis_default_problems(attr["analysis"], f"{where}: analysis of {attr['name']!r}"))
    for block in schema.get("blocks", []):
        if "labels" in block and not block["labels"]:
            problems.append(f'{where}: leave out the empty "labels" of block type {block["type"]!r}')
        problems.extend(schema_default_problems(block["body"], f"{where}: body of block type {block['type']!r}"))
    if "remain" in schema:
        problems.extend(schema_default_problems(schema["remain"], f"{where}: remain"))
    return problems


def result_values(result):
    """The values in a decoded attribute: the attribute value, or the values inside an analysis result."""
    kind = next(iter(result)) if isinstance(result, dict) and len(result) == 1 else None
    if kind == "static_list":
        for item in result[kind]:
            yield from result_values(item)
    elif kind == "static_map":
        for pair in result[kind]:
            yield from result_values(pair["key"])
            yield from result_values(pair["value"])
    elif kind == "static_call":
        for item in result[kind]["arguments"]:
            yield from result_values(item)
    elif kind == "static_traversal":
        for step in result[kind]:
            if "index" in step:
                yield step["index"]
    elif kind != "type":
        yield result


def schema_has_analysis(schema):
    return (any("analysis" in attr for attr in schema.get("attributes", []))
            or any(schema_has_analysis(block["body"]) for block in schema.get("blocks", []))
            or ("remain" in schema and schema_has_analysis(schema["remain"])))


def body_values(body):
    for result in body.get("attributes", {}).values():
        yield from result_values(result)
    for block in body.get("blocks", []):
        yield from body_values(block.get("body", {}))
    if body.get("remain") is not None:
        yield from body_values(body["remain"])


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
            meta = json.loads(test_json.read_text(encoding="utf-8"))
            self.disputed[where] = isinstance(meta, dict) and meta.get("status") == "disputed"
        except (OSError, ValueError, RecursionError):
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

        syntax = parts[0]
        if syntax not in hcltest.INPUT_NAMES:
            self.problem(where, f"unknown syntax {syntax!r}; tests are under tests/native or tests/json")
        elif test.input.name != hcltest.INPUT_NAMES[syntax]:
            self.problem(where, f"{syntax} syntax tests read the input file {hcltest.INPUT_NAMES[syntax]}")
        elif "input" in meta:
            self.problem(where, f'leave out "input", which is {hcltest.INPUT_NAMES[syntax]} for {syntax} syntax tests anyway')
        if "schema" in test.context:
            for issue in schema_default_problems(test.context["schema"]):
                self.problem(where, issue)
            if meta["expect"].get("valid") and isinstance(meta["expect"].get("body"), dict):
                for issue in decode_body_problems(meta["expect"]["body"], test.context["schema"]):
                    self.problem(where, issue)

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

        features = meta.get("features", [])
        needs = {}  # features that the variables, functions and expected result call for, with the reason
        # hcltest.Test has checked the format of variables and functions, and that their values could exist.
        for name, value in test.context.get("variables", {}).items():
            if needs_typed_values(value) and "typed-values" not in features:
                self.problem(where, f'variable {name!r} is a list, set, map, or typed null or unknown, so the '
                                    'test needs "features": ["typed-values"]')
            if needs_unknown_values(value):
                needs.setdefault("unknown-values", f"variable {name!r} is unknown or holds an unknown value")

        if test.input.name.endswith(".hcl.json"):
            needs["json-syntax"] = "the input is in the JSON syntax"
        elif "json-syntax" in features:
            self.problem(where, 'lists the "json-syntax" feature, but the input is in the native syntax')
        kinds = schema_analysis_kinds(test.context["schema"]) if "schema" in test.context else set()
        if kinds:
            needs["static-analysis"] = "the schema analyzes an attribute"
        elif "static-analysis" in features:
            self.problem(where, 'lists the "static-analysis" feature, but its schema analyzes no attribute')
        extensions = {hcltest.EXTENSION_FUNCTIONS[decl["extension"]]
                      for decl in test.context.get("functions", {}).values() if "extension" in decl}
        if kinds & set(TYPE_KINDS):
            needs["type-expressions"] = "the schema analyzes a type expression"
        elif "type-expressions" in extensions:
            needs["type-expressions"] = "the test declares a function of the type expression extension"
        elif "type-expressions" in features:
            self.problem(where, 'lists the "type-expressions" feature, but uses no type expressions')
        if "functions" in test.context:
            needs["functions"] = "the test declares functions"
            for name, decl in test.context["functions"].items():
                if not is_function_name(name):
                    self.problem(where, f"function name {name!r} is not an identifier, or identifiers joined by ::")
                if "extension" in decl:
                    continue
                params = decl.get("params", []) + ([decl["variadic_param"]] if "variadic_param" in decl else [])
                for param in params:
                    if "name" in param and ("::" in param["name"] or not is_function_name(param["name"])):
                        self.problem(where, f"parameter name {param['name']!r} of function {name!r} is not an "
                                            "identifier")
                if any(isinstance(param["type"], list) for param in params):
                    # Implementations without typed values have no collection or structural types to declare.
                    needs.setdefault("typed-values", f"function {name!r} has a parameter of a collection or "
                                                     "structural type")
                result = decl["result"]
                if isinstance(result, dict) and "value" in result:
                    if needs_typed_values(result["value"]):
                        needs.setdefault("typed-values", f"function {name!r} returns a list, set, map, or typed null "
                                                         "or unknown")
                    if needs_unknown_values(result["value"]):
                        needs.setdefault("unknown-values", f"function {name!r} returns an unknown value")
                if isinstance(result, dict) and "error" in result:
                    self.text_field(where, f"function {name!r} error", result["error"])

        expect = meta["expect"]
        if test.op in hcltest.PHASES and expect.get("valid"):
            values = list(body_values(expect.get("body", {})))
            for value in values:
                try:
                    hcltest.check_value(value, "expected value")
                except ValueError as e:
                    self.problem(where, str(e))
            if any(needs_typed_values(v) for v in values) and "typed-values" not in features:
                self.problem(where, 'the expected result has a list, set, map, or typed null or unknown, so the test needs '
                                    '"features": ["typed-values"]')
            if any(needs_unknown_values(v) for v in values):
                needs.setdefault("unknown-values", "the expected result has an unknown value")
            if any(type_needs_typed_values(ty) for ty in body_types(expect.get("body", {}))):
                needs.setdefault("typed-values", "the expected result has a list, set or map type")
        for feature in sorted(set(needs) - set(features)):
            self.problem(where, f'{needs[feature]}, so the test needs "features": ["{feature}"]')
        if "functions" in features and "functions" not in meta:
            self.problem(where, 'lists the "functions" feature but declares no functions')
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
            phases = hcltest.PHASES.get(test.op)
            if phases and expect.get("phase") not in phases:
                self.problem(where, f'{test.op} tests that expect an error need a "phase": '
                                    f'{" or ".join(json.dumps(p) for p in phases)}')
            if test.op == "parse" and "phase" in expect:
                self.problem(where, 'parse tests don\'t need a "phase"')
            if expect.get("phase") == "analysis" and not schema_has_analysis(test.context.get("schema", {})):
                self.problem(where, 'expects an error in the "analysis" phase, but its schema analyzes no attribute')

        data = test.input.read_bytes()
        if len(data) > MAX_INPUT_BYTES:
            self.problem(where, f"input is larger than {MAX_INPUT_BYTES} bytes; keep tests minimal")
        key = (test.op, test.input.name, data, json.dumps(test.context, sort_keys=True))
        if key in inputs:
            self.problem(where, f"same op, input and context as {inputs[key]}")
        inputs.setdefault(key, where)
        return where

    def register_only(self, test_json, where, descriptions, inputs):
        """Records an out-of-scope test so in-scope tests can be checked against it."""
        try:
            test = hcltest.Test(test_json)
            data = test.input.read_bytes()
        except (OSError, hcltest.TestError):
            return where  # reported when the test is in scope
        descriptions.setdefault(test.description, where)
        inputs.setdefault((test.op, test.input.name, data, json.dumps(test.context, sort_keys=True)), where)
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
