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
import re
import shlex
import subprocess
import sys
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = (ROOT / "tests").resolve()
SPEC_URL = "https://github.com/hashicorp/hcl/blob/v2.24.0/"
OPERATIONS = ("parse", "eval", "decode")
PHASES = {"eval": ("parse", "eval"), "decode": ("parse", "schema", "analysis", "eval")}  # where errors can be reported
INPUT_NAMES = {"native": "input.hcl", "json": "input.hcl.json"}  # the input file of a test, by its syntax
TEST_FIELDS = {"description", "spec", "op", "input", "features", "status", "notes", "variables", "functions",
               "evaluation_mode", "schema", "reference_error", "expect"}
# The test fields passed to eval and decode in their context file.
CONTEXT_FIELDS = ("variables", "functions", "evaluation_mode", "schema")


class TestError(Exception):
    """A test.json file is malformed."""


class AdapterError(Exception):
    """The adapter crashed, hung, or printed output that breaks the protocol."""


class KeyConflict(AdapterError):
    """An object or map has two keys that are the same string under NFC, which no
    correct implementation can produce. compare() reports it as a failure."""


class Test:
    def __init__(self, path):
        try:
            self._load(path)
        except RecursionError:
            raise TestError(f"{path}: nested too deeply")

    def _load(self, path):
        self.dir = path.parent
        try:
            self.name = self.dir.resolve().relative_to(TESTS_DIR).as_posix()
        except ValueError:
            self.name = self.dir.as_posix()
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise TestError(f"{path}: invalid JSON: {e}")
        if not isinstance(meta, dict):
            raise TestError(f"{path}: must be a JSON object")
        try:
            json.dumps(meta, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            raise TestError(f"{path}: contains an escaped lone surrogate, which isn't Unicode text")
        unknown = set(meta) - TEST_FIELDS
        if unknown:
            raise TestError(f"{path}: unknown fields: {', '.join(sorted(unknown))}")
        for field in ("description", "op", "expect"):
            if field not in meta:
                raise TestError(f'{path}: missing "{field}"')
        for field in ("description", "op", "input", "status", "notes", "reference_error"):
            if field in meta and not isinstance(meta[field], str):
                raise TestError(f'{path}: "{field}" must be a string')
        for field in ("spec", "features"):
            if field in meta and not (isinstance(meta[field], list) and all(isinstance(x, str) for x in meta[field])):
                raise TestError(f'{path}: "{field}" must be a list of strings')
        if not isinstance(meta["expect"], dict):
            raise TestError(f'{path}: "expect" must be an object')
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
        self.context = {}  # the context file (docs/protocol.md#eval)
        checks = {"variables": check_variables, "functions": check_functions,
                  "evaluation_mode": check_evaluation_mode, "schema": check_schema}
        for field in CONTEXT_FIELDS:
            if field not in meta:
                continue
            if field == "schema" and self.op != "decode":
                raise TestError(f'{path}: only decode tests can have a "schema"')
            if self.op == "parse":
                raise TestError(f'{path}: only eval and decode tests can have "{field}"')
            try:
                checks[field](meta[field])
            except ValueError as e:
                raise TestError(f'{path}: malformed "{field}": {e}')
            self.context[field] = meta[field]
        if self.op == "decode" and "schema" not in self.context:
            raise TestError(f'{path}: decode tests need a "schema"')
        if "evaluation_mode" in self.context and ("variables" in self.context or "functions" in self.context):
            raise TestError(f"{path}: literal-only evaluation has no variables or functions")
        self.reference_error = meta.get("reference_error")
        self.syntax = self.dir.resolve().parent.parent.name  # tests/<syntax>/<area>/<name>
        input_name = meta.get("input", INPUT_NAMES.get(self.syntax, "input.hcl"))
        if "/" in input_name or "\\" in input_name or input_name in ("", ".", ".."):
            raise TestError(f'{path}: "input" must be the name of a file in the test directory')
        self.input = self.dir / input_name
        if not self.input.is_file():
            raise TestError(f"{path}: input file {self.input.name} not found")
        if self.input.name.endswith(".hcl.json") and self.op != "decode":
            raise TestError(f"{path}: the JSON syntax can only be read with a schema, so JSON syntax tests are decode tests")

        self.expect = meta["expect"]
        if not isinstance(self.expect.get("valid"), bool):
            raise TestError(f'{path}: "expect" needs a boolean "valid"')
        phase = self.expect.get("phase")
        if phase is not None and self.expect["valid"]:
            raise TestError(f'{path}: only expected errors have a "phase"')
        if phase is not None and phase not in PHASES.get(self.op, ()):
            raise TestError(f'{path}: "phase" {phase!r} is not a phase where a {self.op} test can expect an error')
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
    for field in ("operations", "features"):
        if field not in caps and field == "features":
            caps[field] = []
        if not (isinstance(caps.get(field), list) and all(isinstance(x, str) for x in caps[field])):
            raise AdapterError(f'"capabilities" output needs a list of strings in "{field}"')
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
        output = json.loads(proc.stdout, object_pairs_hook=unique_keys)
    except ValueError as e:
        raise AdapterError(f"adapter printed invalid JSON ({e})")
    except RecursionError:
        raise AdapterError("adapter output is nested too deeply")
    try:
        json.dumps(output, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        raise AdapterError("adapter printed an escaped lone surrogate, which isn't Unicode text")
    return output


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
    if test.context:
        context_file = tmp / (test.name.replace("/", "__") + ".json")
        context_file.write_text(json.dumps(test.context, ensure_ascii=False), encoding="utf-8")
        command.append(str(context_file))
    try:
        actual = call_adapter(adapter, command, args.timeout)
        failure = compare(test, actual, args.reference_errors)
    except AdapterError as e:
        return Result(test, "error", str(e))
    except RecursionError:
        return Result(test, "error", "adapter output is nested too deeply")
    if failure:
        return Result(test, "fail", *failure)
    return Result(test, "pass")


def compare(test, actual, reference_errors=False):
    """Returns None if the adapter's output matches the test, or (message, detail)."""
    if not isinstance(actual, dict) or not isinstance(actual.get("valid"), bool):
        raise AdapterError('output must be a JSON object with a boolean "valid"')
    errors = actual.get("errors")
    if errors is not None and not (isinstance(errors, list) and all(isinstance(e, dict) for e in errors)):
        raise AdapterError('"errors" must be a list of error objects')
    if not test.expect["valid"]:
        if actual["valid"]:
            return "expected an error, but the input was accepted", None
        phase = test.expect.get("phase")
        if test.op in PHASES and phase and actual.get("phase") != phase:
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
    if canonical(body) != canonical(test.expected_body):
        return "output differs from expected", json_diff(test.expected_body, body)
    return None


def error_messages(actual):
    lines = []
    for error in actual.get("errors") or []:
        where = ""
        start = error["range"].get("start") if isinstance(error.get("range"), dict) else None
        if isinstance(start, dict):
            where = f"{start.get('line')}:{start.get('column')}: "
        lines.append(where + str(error.get("message", "")))
    return "\n".join(lines) or None


def canonical(x):
    """JSON text that tells apart what Python's == doesn't, such as true and 1."""
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


def json_diff(expected, actual):
    def dump(x):
        return json.dumps(x, indent=2, sort_keys=True, ensure_ascii=False).splitlines()

    return "\n".join(difflib.unified_diff(dump(expected), dump(actual), "expected", "actual", lineterm=""))


# Checks of the values and function declarations that tests pass to adapters, so
# that a mistake in a test is reported as one instead of as an adapter error.

PRIMITIVE_TYPES = ("string", "number", "bool", "dynamic")
# Numbers in tests are plain decimals, so that adapters don't need to parse other forms.
PLAIN_NUMBER = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")
FUNCTION_FIELDS = {"params", "variadic_param", "result"}
PARAM_FIELDS = {"name", "type", "allow_null", "allow_unknown", "allow_dynamic_type"}


def check_variables(variables):
    """Raises ValueError if a "variables" object doesn't map names to values."""
    if not isinstance(variables, dict):
        raise ValueError("must be an object mapping variable names to values")
    for name, value in variables.items():
        check_value(value, f"variable {name!r}")


def check_value(value, where="value"):
    """Raises ValueError if a value in a test doesn't follow the encoding in docs/protocol.md#values, or couldn't
    exist: a list, set or map with elements of another type than it declares, an object or map with two keys that
    are equal under NFC, or a set with two equal elements."""
    check_value_encoding(value, where)
    problems = type_problems(value, where)
    if problems:
        raise ValueError(problems[0])


def check_value_encoding(value, where):
    kinds = [k for k in value if k in VALUE_KINDS] if isinstance(value, dict) else []
    if len(kinds) != 1 or set(value) - set(VALUE_KINDS) - {"element_type"}:
        raise ValueError(f"{where}: not a value: {json.dumps(value)}")
    kind = kinds[0]
    x = value[kind]
    if ("element_type" in value) != (kind in ("list", "set", "map")):
        raise ValueError(f'{where}: lists, sets and maps need an "element_type", and other values have none')
    expected = {"string": str, "number": str, "bool": bool, "tuple": list, "list": list, "set": list, "object": dict,
                "map": dict}.get(kind)
    if expected and not isinstance(x, expected):
        raise ValueError(f"{where}: malformed {kind} value: {json.dumps(value)}")
    if kind == "number" and not (x in ("Infinity", "-Infinity") or PLAIN_NUMBER.fullmatch(x)):
        raise ValueError(f"{where}: {x!r} is not a plain decimal number such as -12.5, or Infinity or -Infinity")
    elif kind in ("null", "unknown"):
        check_type(x, where)
    elif kind in ("list", "set", "map"):
        check_type(value["element_type"], where)
    items = x.items() if kind in ("object", "map") else enumerate(x) if kind in ("tuple", "list", "set") else ()
    for key, item in items:
        check_value_encoding(item, f"{where}[{key!r}]")
    if kind in ("object", "map"):
        keys = {}
        for key in x:
            if nfc(key) in keys:
                raise ValueError(f"{where}: the keys {keys[nfc(key)]!r} and {key!r} are equal under NFC")
            keys[nfc(key)] = key
    if kind == "set":
        elements = [json.dumps(normalize_value(item), sort_keys=True) for item in x]
        if len(set(elements)) != len(elements):
            raise ValueError(f"{where}: the set has two equal elements")


def value_type(value):
    """The type of a value, in go-cty's JSON type notation."""
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


SCHEMA_MODES = ("exhaustive", "partial", "dynamic-attributes")


def check_evaluation_mode(mode):
    if mode != "literal-only":
        raise ValueError('must be "literal-only"; leave it out for full expression mode')


def check_schema(schema, where="schema"):
    """Raises ValueError if a body schema doesn't follow the format in docs/protocol.md#decode."""
    if not isinstance(schema, dict) or schema.get("mode") not in SCHEMA_MODES:
        raise ValueError(f'{where} must be an object whose "mode" is one of {", ".join(SCHEMA_MODES)}')
    mode = schema["mode"]
    allowed = {"mode"} if mode == "dynamic-attributes" else {"mode", "attributes", "blocks"}
    if mode == "partial":
        allowed.add("remain")
    unknown = set(schema) - allowed
    if unknown:
        raise ValueError(f"{where}: {mode} mode doesn't take {', '.join(sorted(unknown))}")
    attributes = schema.get("attributes", [])
    if not isinstance(attributes, list):
        raise ValueError(f'{where}: "attributes" must be a list')
    for attr in attributes:
        if not (isinstance(attr, dict) and not set(attr) - {"name", "required", "analysis"} and isinstance(attr.get("name"), str)
                and isinstance(attr.get("required", False), bool)):
            raise ValueError(f'{where}: attributes are {{"name": <string>, "required": <bool>, "analysis": <analysis>}}, '
                             f"not {json.dumps(attr)}")
    names = [attr["name"] for attr in attributes]
    for attr in attributes:
        if "analysis" in attr:
            if names.count(attr["name"]) > 1:
                raise ValueError(f"{where}: attribute {attr['name']!r} has an analysis, so it can only be listed once")
            check_analysis(attr["analysis"], f"{where}: analysis of attribute {attr['name']!r}")
    blocks = schema.get("blocks", [])
    if not isinstance(blocks, list):
        raise ValueError(f'{where}: "blocks" must be a list')
    types = set()
    for block in blocks:
        labels = block.get("labels", []) if isinstance(block, dict) else None
        if not (isinstance(block, dict) and not set(block) - {"type", "labels", "body"}
                and isinstance(block.get("type"), str) and "body" in block
                and isinstance(labels, list) and all(isinstance(label, str) for label in labels)):
            raise ValueError(f'{where}: blocks are {{"type": <string>, "labels": [<string>, ...], "body": <schema>}}, '
                             f"not {json.dumps(block)}")
        if block["type"] in types:
            raise ValueError(f"{where}: block type {block['type']!r} is in the schema twice, with two body schemas")
        types.add(block["type"])
        check_schema(block["body"], f"{where}: body of block {block['type']!r}")
    if "remain" in schema:
        check_schema(schema["remain"], f"{where}: remain")


# The parts of each kind of static analysis, which are analyses of their own.
ANALYSIS_PARTS = {"value": (), "static-list": ("elements",), "static-map": ("keys", "values"), "static-call": ("arguments",),
                  "static-traversal": ()}


def check_analysis(analysis, where="analysis"):
    """Raises ValueError if an analysis doesn't follow the format in docs/protocol.md#static-analysis."""
    if not isinstance(analysis, dict) or not isinstance(analysis.get("kind"), str) or analysis["kind"] not in ANALYSIS_PARTS:
        raise ValueError(f'{where} must be an object whose "kind" is one of {", ".join(ANALYSIS_PARTS)}')
    parts = ANALYSIS_PARTS[analysis["kind"]]
    unknown = set(analysis) - {"kind"} - set(parts)
    if unknown:
        raise ValueError(f"{where}: a {analysis['kind']} analysis doesn't take {', '.join(sorted(unknown))}")
    for part in parts:
        if part in analysis:
            check_analysis(analysis[part], f"{where}: {part}")


def check_functions(functions):
    """Raises ValueError if a "functions" object doesn't follow the declaration format."""
    if not isinstance(functions, dict):
        raise ValueError("must be an object mapping function names to declarations")
    for name, decl in functions.items():
        try:
            check_function(decl)
        except ValueError as e:
            raise ValueError(f"function {name!r}: {e}")


def check_function(decl):
    if not isinstance(decl, dict):
        raise ValueError("a declaration must be an object")
    unknown = set(decl) - FUNCTION_FIELDS
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
    params = decl.get("params", [])
    if not isinstance(params, list):
        raise ValueError('"params" must be a list')
    for i, param in enumerate(params):
        check_param(param, f"parameter {i}")
    if "variadic_param" in decl:
        check_param(decl["variadic_param"], "variadic parameter")
    result = decl.get("result")
    if result == "arguments":
        return
    if not isinstance(result, dict) or len(result) != 1 or not set(result) <= {"value", "error"}:
        raise ValueError('"result" must be "arguments", {"value": <value>} or {"error": <message>}')
    if "error" in result and not isinstance(result["error"], str):
        raise ValueError("an error result needs a message string")
    if "value" in result:
        check_value(result["value"], "result value")


def check_param(param, where):
    if not isinstance(param, dict):
        raise ValueError(f"{where} must be an object")
    unknown = set(param) - PARAM_FIELDS
    if unknown:
        raise ValueError(f"{where} has unknown fields: {', '.join(sorted(unknown))}")
    if "name" in param and not isinstance(param["name"], str):
        raise ValueError(f"{where}: \"name\" must be a string")
    if "type" not in param:
        raise ValueError(f'{where} needs a "type"')
    check_type(param["type"], where)
    for flag in ("allow_null", "allow_unknown", "allow_dynamic_type"):
        if not isinstance(param.get(flag, False), bool):
            raise ValueError(f'{where}: "{flag}" must be true or false')


def check_type(ty, where):
    """Checks a type in go-cty's JSON type notation, as used in values."""
    if isinstance(ty, str) and ty in PRIMITIVE_TYPES:
        return
    if isinstance(ty, list) and len(ty) == 2:
        kind, arg = ty
        if kind in ("list", "set", "map"):
            return check_type(arg, where)
        if kind == "tuple" and isinstance(arg, list):
            for item in arg:
                check_type(item, where)
            return
        if kind == "object" and isinstance(arg, dict):
            for item in arg.values():
                check_type(item, where)
            return
    raise ValueError(f"{where}: not a type: {json.dumps(ty)}")


# Normalization lets adapters differ in ways that don't change meaning. Both the
# expected and actual results pass through it before they are compared.


def normalize_body(body, op):
    normalize_attr = {"parse": normalize_expr, "decode": normalize_result}.get(op, normalize_value)
    if not isinstance(body, dict):
        raise AdapterError(f"body must be an object, got {body!r}")
    unknown = set(body) - {"attributes", "blocks", "remain"}
    if unknown:
        raise AdapterError(f"unknown fields in a body: {', '.join(sorted(unknown))}")
    try:
        out = {
            "attributes": {name: normalize_attr(v) for name, v in body.get("attributes", {}).items()},
            "blocks": [normalize_block(block, op) for block in body.get("blocks", [])],
        }
        if "remain" in body:
            if op != "decode":
                raise AdapterError("only decode results have a remain body")
            out["remain"] = normalize_body(body["remain"], op)  # the body that partial processing left
        return out
    except (KeyError, TypeError, AttributeError) as e:
        raise AdapterError(f"malformed body ({type(e).__name__}: {e})")


def normalize_block(block, op):
    unknown = set(block) - {"type", "labels", "body"}
    if unknown:
        raise AdapterError(f"unknown fields in a block: {', '.join(sorted(unknown))}")
    labels = block.get("labels", [])
    if not isinstance(block["type"], str) or not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise AdapterError(f"a block needs a string type and a list of string labels, got {block!r}")
    return {"type": block["type"], "labels": labels, "body": normalize_body(block.get("body", {}), op)}


VALUE_KINDS = ("string", "number", "bool", "null", "unknown", "tuple", "object", "list", "set", "map")


ANALYSIS_RESULTS = ("static_list", "static_map", "static_call", "static_traversal")


def normalize_result(result):
    """Normalizes a decoded attribute: a value, or the result of a static analysis."""
    if not (isinstance(result, dict) and len(result) == 1 and next(iter(result)) in ANALYSIS_RESULTS):
        return normalize_value(result)
    kind, x = next(iter(result.items()))
    items = x.get("arguments") if kind == "static_call" and isinstance(x, dict) else x
    if not isinstance(items, list):
        raise AdapterError(f"malformed {kind} result: {result!r}")
    try:
        if kind == "static_list":
            return {kind: [normalize_result(item) for item in x]}
        if kind == "static_map":
            if not all(isinstance(pair, dict) and set(pair) == {"key", "value"} for pair in x):
                raise AdapterError(f"static map pairs are {{\"key\": ..., \"value\": ...}}, got {x!r}")
            return {kind: [{"key": normalize_result(pair["key"]), "value": normalize_result(pair["value"])} for pair in x]}
        if kind == "static_call":
            if not (isinstance(x, dict) and set(x) == {"name", "arguments"} and isinstance(x["name"], str)):
                raise AdapterError(f'a static call is {{"name": <string>, "arguments": [...]}}, got {x!r}')
            return {kind: {"name": x["name"], "arguments": [normalize_result(item) for item in x["arguments"]]}}
        steps = []
        for i, step in enumerate(x):
            name = next(iter(step)) if isinstance(step, dict) and len(step) == 1 else None
            if (i == 0) != (name == "root") or name not in ("root", "attr", "index"):
                raise AdapterError(f"a static traversal is a root step and attr or index steps, got {x!r}")
            if name == "index":
                steps.append({name: normalize_value(step[name])})
            elif isinstance(step[name], str):
                steps.append({name: step[name]})
            else:
                raise AdapterError(f"a traversal step's name must be a string, got {step!r}")
        if not steps:
            raise AdapterError("a static traversal needs a root step")
        return {kind: steps}
    except TypeError as e:
        raise AdapterError(f"malformed {kind} result ({e})")


def normalize_value(value):
    kinds = [k for k in value if k in VALUE_KINDS] if isinstance(value, dict) else []
    if len(kinds) != 1 or set(value) - {kinds[0], "element_type"}:
        raise AdapterError(f"not a value: {value!r}")
    kind = kinds[0]
    x = value[kind]
    if ("element_type" in value) != (kind in ("list", "set", "map")):
        raise AdapterError(f"lists, sets and maps need an element_type, and other values have none: {value!r}")
    container = {"tuple": list, "list": list, "set": list, "object": dict, "map": dict, "bool": bool}.get(kind)
    if container and not isinstance(x, container):
        raise AdapterError(f"malformed {kind} value: {value!r}")
    if kind == "number":
        return {"number": normalize_number(x)}
    if kind in ("null", "unknown"):
        return {kind: normalize_type(x)}
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
        out["element_type"] = normalize_type(value["element_type"])
    return out


def normalize_type(ty):
    """A type in go-cty's JSON type notation, with object attribute names in NFC like object keys."""
    try:
        check_type(ty, "type")
    except ValueError as e:
        raise AdapterError(str(e))
    if isinstance(ty, str):
        return ty
    kind, arg = ty
    if kind == "tuple":
        return [kind, [normalize_type(item) for item in arg]]
    if kind == "object":
        return [kind, {nfc(name): normalize_type(item) for name, item in arg.items()}]
    return [kind, normalize_type(arg)]


def nfc(text):
    """Strings are equal in HCL if their NFC normalizations are equal."""
    if not isinstance(text, str):
        raise AdapterError(f"strings must be JSON strings, got {text!r}")
    return unicodedata.normalize("NFC", text)


# The number forms adapters may print.
DECIMAL_NUMBER = re.compile(r"-?Infinity|[-+]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][-+]?[0-9]+)?")


def normalize_number(text):
    if not isinstance(text, str):
        raise AdapterError(f"numbers must be strings, got {text!r}")
    if not DECIMAL_NUMBER.fullmatch(text):
        raise AdapterError(f"{text!r} is not a decimal number or Infinity")
    number = decimal.Decimal(text)
    if number.is_infinite():
        return "-Infinity" if number < 0 else "Infinity"
    if number.is_zero():
        return "0"
    # Keep every digit, and report exponents beyond the context's range instead of rounding.
    context = decimal.Context(prec=max(28, len(number.as_tuple().digits)), traps=[decimal.Overflow, decimal.Inexact])
    try:
        return format(number.normalize(context), "f")
    except decimal.DecimalException:
        raise AdapterError(f"out of range number {text!r}")


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
        number = normalize_number(str(decimal.Decimal(out["operand"]["value"]["number"]).copy_negate()))
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
