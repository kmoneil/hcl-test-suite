#!/usr/bin/env python3
"""Run the HCL test suite against an implementation.

    python3 runner/hcltest.py --adapter bin/hcl-go-adapter
    python3 runner/hcltest.py --adapter "python3 my_adapter.py" tests/native/heredocs

An adapter is a small program that exposes an HCL implementation to this
runner. docs/protocol.md describes what it must do, and docs/test-format.md
describes the tests. Only the Python 3.9+ standard library is needed.
"""

import argparse
import decimal
import difflib
import json
import shlex
import subprocess
import sys
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
SPEC_URL = "https://github.com/hashicorp/hcl/blob/v2.24.0/"
OPERATIONS = ("parse", "eval")
TEST_FIELDS = {"description", "spec", "op", "input", "features", "status", "notes", "variables", "reference_error", "expect"}

# Enough precision that normalizing a number never rounds it.
decimal.getcontext().prec = 10_000


class TestError(Exception):
    """A test.json file is malformed."""


class AdapterError(Exception):
    """The adapter crashed, hung, or printed output that breaks the protocol."""


class KeyConflict(AdapterError):
    """An object or map has two keys that are the same string under NFC, which no
    correct implementation can produce. compare() reports it as a failure."""


class Test:
    def __init__(self, path):
        self.dir = path.parent
        try:
            self.name = self.dir.resolve().relative_to(TESTS_DIR).as_posix()
        except ValueError:
            self.name = self.dir.as_posix()
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise TestError(f"{path}: invalid JSON: {e}")
        unknown = set(meta) - TEST_FIELDS
        if unknown:
            raise TestError(f"{path}: unknown fields: {', '.join(sorted(unknown))}")
        for field in ("description", "op", "expect"):
            if field not in meta:
                raise TestError(f'{path}: missing "{field}"')
        if meta["op"] not in OPERATIONS:
            raise TestError(f'{path}: "op" must be one of {", ".join(OPERATIONS)}')
        if meta.get("status", "normal") not in ("normal", "disputed"):
            raise TestError(f'{path}: "status" must be "normal" or "disputed"')

        self.description = meta["description"]
        self.op = meta["op"]
        self.spec = meta.get("spec", [])
        self.features = meta.get("features", [])
        self.disputed = meta.get("status") == "disputed"
        self.notes = meta.get("notes")
        self.variables = meta.get("variables")
        self.reference_error = meta.get("reference_error")
        self.input = self.dir / meta.get("input", "input.hcl")
        if not self.input.is_file():
            raise TestError(f"{path}: input file {self.input.name} not found")

        self.expect = meta["expect"]
        if not isinstance(self.expect.get("valid"), bool):
            raise TestError(f'{path}: "expect" needs a boolean "valid"')
        self.expected_body = None
        if self.expect["valid"]:
            try:
                self.expected_body = normalize_body(self.expect.get("body"), self.op)
            except AdapterError as e:  # the normalizers blame the adapter by default
                raise TestError(f"{path}: malformed expected body: {e}")


class Result:
    def __init__(self, test, outcome, message=None, detail=None):
        self.test = test
        self.outcome = outcome  # "pass", "fail", "error" or "skip"
        self.message = message
        self.detail = detail


def main():
    parser = argparse.ArgumentParser(description="Run the HCL test suite against an adapter.")
    parser.add_argument("--adapter", required=True, help='adapter command, for example "bin/hcl-go-adapter"')
    parser.add_argument("paths", nargs="*", type=Path, help="test directories to run (default: all tests)")
    parser.add_argument("-v", "--verbose", action="store_true", help="also list tests that passed or were skipped")
    parser.add_argument("--strict", action="store_true", help="fail the run when a disputed test fails")
    parser.add_argument("--timeout", type=float, default=10, help="seconds to allow each adapter call (default: 10)")
    parser.add_argument("--reference-errors", action="store_true",
                        help='check that errors match each test\'s "reference_error" (for the hashicorp/hcl adapter)')
    args = parser.parse_args()

    adapter = shlex.split(args.adapter)
    try:
        caps = capabilities(adapter, args.timeout)
        tests = discover(args.paths or [TESTS_DIR])
    except (AdapterError, TestError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"{caps['implementation']} {caps.get('version', '')}".rstrip())
    with tempfile.TemporaryDirectory(prefix="hcltest-") as tmp:
        results = [run_test(test, adapter, caps, args, Path(tmp)) for test in tests]
    for result in results:
        if args.verbose or result.outcome in ("fail", "error"):
            print_result(result)
    print_summary(results)

    blocking = [r for r in results if r.outcome in ("fail", "error") and (args.strict or not r.test.disputed)]
    return 1 if blocking else 0


def capabilities(adapter, timeout):
    caps = call_adapter(adapter, ["capabilities"], timeout)
    if not isinstance(caps, dict) or not isinstance(caps.get("implementation"), str):
        raise AdapterError('"capabilities" output needs an "implementation" name')
    if not isinstance(caps.get("operations"), list):
        raise AdapterError('"capabilities" output needs an "operations" list')
    caps.setdefault("features", [])
    return caps


def discover(paths):
    tests = []
    for path in paths:
        if not path.exists():
            raise TestError(f"{path}: no such file or directory")
        files = [path] if path.name == "test.json" else sorted(path.rglob("test.json"))
        tests.extend(Test(f) for f in files)
    return sorted(tests, key=lambda t: t.name)


def call_adapter(adapter, args, timeout):
    try:
        proc = subprocess.run(adapter + args, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise AdapterError(f"adapter not found: {adapter[0]}")
    except PermissionError:
        raise AdapterError(f"adapter is not executable: {adapter[0]}")
    except subprocess.TimeoutExpired:
        raise AdapterError(f"adapter timed out after {timeout:g}s")
    stderr = proc.stderr.decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        raise AdapterError(f"adapter exited with status {proc.returncode}" + (f":\n{stderr}" if stderr else ""))
    try:
        return json.loads(proc.stdout, object_pairs_hook=unique_keys)
    except ValueError as e:
        raise AdapterError(f"adapter printed invalid JSON ({e})")


def unique_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("an object has the same key twice")
    return result


def run_test(test, adapter, caps, args, tmp):
    if test.op not in caps["operations"]:
        return Result(test, "skip", f'adapter does not support "{test.op}"')
    missing = sorted(set(test.features) - set(caps["features"]))
    if missing:
        return Result(test, "skip", f"adapter does not support {', '.join(missing)}")

    command = [test.op, str(test.input)]
    if test.variables is not None:
        variables = tmp / (test.name.replace("/", "__") + ".json")
        variables.write_text(json.dumps(test.variables, ensure_ascii=False), encoding="utf-8")
        command.append(str(variables))
    try:
        actual = call_adapter(adapter, command, args.timeout)
        failure = compare(test, actual, args.reference_errors)
    except AdapterError as e:
        return Result(test, "error", str(e))
    if failure:
        return Result(test, "fail", *failure)
    return Result(test, "pass")


def compare(test, actual, reference_errors=False):
    """Returns None if the adapter's output matches the test, or (message, detail)."""
    if not isinstance(actual, dict) or not isinstance(actual.get("valid"), bool):
        raise AdapterError('output must be a JSON object with a boolean "valid"')
    if not test.expect["valid"]:
        if actual["valid"]:
            return "expected an error, but the input was accepted", None
        phase = test.expect.get("phase")
        if test.op == "eval" and phase and actual.get("phase") != phase:
            return f"expected an error during {phase}, but it was reported during {actual.get('phase')}", error_messages(actual)
        if reference_errors and test.reference_error:
            messages = [str(e.get("message", "")) for e in actual.get("errors") or []]
            if not any(test.reference_error in m for m in messages):
                return f'expected the reference error "{test.reference_error}"', error_messages(actual)
        return None
    if not actual["valid"]:
        during = f" during {actual['phase']}" if actual.get("phase") else ""
        return f"expected success, but got errors{during}", error_messages(actual)
    try:
        body = normalize_body(actual.get("body"), test.op)
    except KeyConflict as e:
        return str(e), None
    if body != test.expected_body:
        return "output differs from expected", json_diff(test.expected_body, body)
    return None


def error_messages(actual):
    lines = []
    for error in actual.get("errors") or []:
        where = ""
        start = (error.get("range") or {}).get("start")
        if start:
            where = f"{start.get('line')}:{start.get('column')}: "
        lines.append(where + str(error.get("message", "")))
    return "\n".join(lines) or None


def json_diff(expected, actual):
    def dump(x):
        return json.dumps(x, indent=2, sort_keys=True, ensure_ascii=False).splitlines()

    return "\n".join(difflib.unified_diff(dump(expected), dump(actual), "expected", "actual", lineterm=""))


# Normalization lets adapters differ in ways that don't change meaning. Both the
# expected and actual results pass through it before they are compared.


def normalize_body(body, op):
    normalize_attr = normalize_expr if op == "parse" else normalize_value
    if not isinstance(body, dict):
        raise AdapterError(f"body must be an object, got {body!r}")
    try:
        return {
            "attributes": {name: normalize_attr(v) for name, v in body.get("attributes", {}).items()},
            "blocks": [
                {
                    "type": block["type"],
                    "labels": list(block.get("labels", [])),
                    "body": normalize_body(block.get("body", {}), op),
                }
                for block in body.get("blocks", [])
            ],
        }
    except (KeyError, TypeError, AttributeError) as e:
        raise AdapterError(f"malformed body ({type(e).__name__}: {e})")


VALUE_KINDS = ("string", "number", "bool", "null", "unknown", "tuple", "object", "list", "set", "map")


def normalize_value(value):
    kinds = [k for k in value if k in VALUE_KINDS] if isinstance(value, dict) else []
    if len(kinds) != 1:
        raise AdapterError(f"not a value: {value!r}")
    kind = kinds[0]
    x = value[kind]
    if kind == "number":
        return {"number": normalize_number(x)}
    if kind in ("tuple", "list", "set"):
        items = [normalize_value(item) for item in x]
        if kind == "set":  # sets are unordered
            items.sort(key=lambda item: json.dumps(item, sort_keys=True))
        out = {kind: items}
    elif kind in ("object", "map"):
        items = {}
        for key, item in x.items():
            if nfc(key) in items:
                raise KeyConflict(f"the {kind} has two keys that are equal under NFC: {nfc(key)!r}")
            items[nfc(key)] = normalize_value(item)
        out = {kind: items}
    elif kind == "string":
        return {"string": nfc(x)}
    else:
        return {kind: x}
    if kind in ("list", "set", "map"):
        out["element_type"] = value.get("element_type")
    return out


def nfc(text):
    """Strings are equal in HCL if their NFC normalizations are equal."""
    if not isinstance(text, str):
        raise AdapterError(f"strings must be JSON strings, got {text!r}")
    return unicodedata.normalize("NFC", text)


def normalize_number(text):
    if not isinstance(text, str):
        raise AdapterError(f"numbers must be strings, got {text!r}")
    try:
        number = decimal.Decimal(text)
    except decimal.InvalidOperation:
        raise AdapterError(f"invalid number {text!r}")
    if number.is_nan():
        raise AdapterError("NaN is not an HCL number")
    if number.is_infinite():
        return "-Infinity" if number < 0 else "Infinity"
    if number.is_zero():
        return "0"
    return format(number.normalize(), "f")


# Fields holding lists of template parts, by node kind.
PART_LISTS = {"template": ("parts",), "template_if": ("then", "else"), "template_for": ("body",)}


def normalize_expr(node):
    if not isinstance(node, dict) or not isinstance(node.get("kind"), str):
        raise AdapterError(f"not an expression: {node!r}")
    kind = node["kind"]
    out = {}
    had_text = False
    for field, x in node.items():
        if x is None:
            continue  # a null optional field is the same as a missing one
        if kind == "literal" and field == "value":
            out[field] = normalize_value(x)
        elif field in PART_LISTS.get(kind, ()):
            parts = [normalize_expr(part) for part in x]
            had_text = had_text or any(is_text(part) for part in parts)
            out[field] = merge_text(parts)
        elif kind == "object" and field == "items":
            out[field] = [{"key": normalize_expr(i["key"]), "value": normalize_expr(i["value"])} for i in x]
        elif isinstance(x, dict):
            out[field] = normalize_expr(x)
        elif isinstance(x, list):
            out[field] = [normalize_expr(item) for item in x]
        else:
            out[field] = x
    if kind == "unary" and out.get("operator") == "-" and is_number(out.get("operand", {})):
        # Negating a number literal is the same as a negative number literal.
        number = normalize_number(str(-decimal.Decimal(out["operand"]["value"]["number"])))
        return {"kind": "literal", "value": {"number": number}}
    if kind == "template":
        # A template with no interpolations or directives is just a string.
        parts = out.get("parts", [])
        if not parts:
            return {"kind": "literal", "value": {"string": ""}}
        if len(parts) == 1 and is_text(parts[0]):
            return parts[0]
        if len(parts) == 1 and parts[0]["kind"] == "interpolation" and had_text:
            # Text emptied by strip markers still stops the template from being
            # unwrapped, so keep one empty part to tell it apart from "${x}".
            out["parts"] = parts + [{"kind": "literal", "value": {"string": ""}}]
    return out


def is_number(node):
    return node.get("kind") == "literal" and "number" in node.get("value", {})


def is_text(node):
    return node.get("kind") == "literal" and "string" in node.get("value", {})


def merge_text(parts):
    """Drops empty text parts and joins adjacent ones."""
    merged = []
    for part in parts:
        if is_text(part):
            text = part["value"]["string"]
            if not text:
                continue
            if merged and is_text(merged[-1]):
                text = merged.pop()["value"]["string"] + text
            part = {"kind": "literal", "value": {"string": nfc(text)}}
        merged.append(part)
    return merged


def print_result(result):
    test = result.test
    label = result.outcome.upper() + (" (disputed)" if test.disputed and result.outcome != "pass" else "")
    print(f"\n{label}  {test.name}")
    print(f"  {test.description}")
    if result.message:
        print(f"  {result.message}")
    if result.outcome in ("fail", "error"):
        for ref in test.spec:
            print(f"  spec: {SPEC_URL}{ref}")
        if test.notes:
            print(f"  note: {test.notes}")
        if result.detail:
            print("\n" + "\n".join("    " + line for line in result.detail.splitlines()))


def print_summary(results):
    groups = defaultdict(lambda: defaultdict(int))
    for result in results:
        group = "/".join(result.test.name.split("/")[:2])
        groups[group][result.outcome] += 1
        groups["total"][result.outcome] += 1
    width = max(len(g) for g in groups) if groups else 5
    print("\nSummary")
    for group in sorted(g for g in groups if g != "total") + ["total"]:
        counts = groups[group]
        parts = [f"{counts[o]} {word}" for o, word in
                 (("pass", "passed"), ("fail", "failed"), ("error", "adapter errors" if counts["error"] > 1 else "adapter error"),
                  ("skip", "skipped")) if counts[o]]
        print(f"  {group:<{width}}  {', '.join(parts)}")
    disputed = [r for r in results if r.test.disputed and r.outcome in ("fail", "error")]
    if disputed:
        which = "1 failing test is disputed and doesn't" if len(disputed) == 1 else f"{len(disputed)} failing tests are disputed and don't"
        print(f"\n{which} fail the run (use --strict to change that).")


if __name__ == "__main__":
    sys.exit(main())
