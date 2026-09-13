#!/usr/bin/env python3
"""Connects the test runner to python-hcl2, a Python parser for HCL.

    adapter.py capabilities
    adapter.py validate <file.hcl>

python-hcl2 keeps heredocs as raw text and leaves escape sequences in strings
undecoded, so it can't give the protocol's expression trees, and it doesn't
evaluate anything. The adapter only supports validate
(docs/protocol.md#validate), which reports whether hcl2.parses accepts a file.
That is the parser and the syntax tree python-hcl2 builds, without hcl2.loads's
conversion to dictionaries, which can fail for reasons that have nothing to do
with syntax, such as an attribute followed by a block with the same name.
"""

import importlib.metadata
import json
import sys

import hcl2
from lark.exceptions import UnexpectedInput, VisitError

USAGE = """usage:
  adapter.py capabilities
  adapter.py validate <file.hcl>"""


def main(args):
    if args == ["capabilities"]:
        output = capabilities()
    elif len(args) == 2 and args[0] == "validate":
        output = validate(args[1])
    else:
        sys.exit(USAGE)
    print(json.dumps(output))


def capabilities():
    version = importlib.metadata.version
    return {
        "implementation": "python-hcl2",
        "version": f"{version('python-hcl2')} (lark {version('lark')})",
        "operations": ["validate"],
        "features": [],
    }


def validate(path):
    if path.endswith(".hcl.json"):
        sys.exit("python-hcl2 doesn't read the JSON syntax")
    with open(path, "rb") as f:
        source = f.read()
    try:
        # Decoding the bytes, instead of opening the file as text, keeps
        # carriage returns as they are.
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        # python-hcl2 only accepts text, so invalid UTF-8 never reaches its parser.
        return invalid("input is not valid UTF-8")
    try:
        hcl2.parses(text)
    except UnexpectedInput as e:
        return invalid(message(e))
    except VisitError as e:
        # Building the syntax tree raises RuntimeError for an if or for directive
        # without its end (hcl2/transformer.py). Anything else is a crash.
        if type(e.orig_exc) is not RuntimeError:
            raise
        return invalid(message(e.orig_exc))
    return {"valid": True}


def invalid(message):
    return {"valid": False, "phase": "parse", "errors": [{"message": message}]}


def message(error):
    """The first line of an error message, without Lark's list of expected tokens,
    shortened because it can quote the rest of the file."""
    return str(error).strip().split("\n", 1)[0][:200]


if __name__ == "__main__":
    main(sys.argv[1:])
