#!/usr/bin/env python3
"""Connects the test runner to bc-python-hcl2, the fork of python-hcl2 that
Checkov pins.

    adapter.py capabilities
    adapter.py validate <file.hcl>

Like python-hcl2, it can't give the protocol's expression trees and doesn't
evaluate anything, so the adapter only supports validate
(docs/protocol.md#validate). It reports whether hcl2.loads, the function
Checkov calls, accepts a file. The fork has no parse-only API: hcl2.loads
runs a line-based check for unclosed quotes, then the Lark parser, then the
conversion to dictionaries, and an error in any of them rejects the file.
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
        "implementation": "bc-python-hcl2",
        "version": f"{version('bc-python-hcl2')} (lark {version('lark')})",
        "operations": ["validate"],
        "features": [],
    }


def validate(path):
    if path.endswith(".hcl.json"):
        sys.exit("bc-python-hcl2 doesn't read the JSON syntax")
    with open(path, "rb") as f:
        source = f.read()
    try:
        # Decoding the bytes, instead of opening the file as text, keeps
        # carriage returns as they are.
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        # bc-python-hcl2 only accepts text, so invalid UTF-8 never reaches its parser.
        return invalid("input is not valid UTF-8")
    try:
        hcl2.loads(text)
    except UnexpectedInput as e:
        return invalid(message(e))
    except ValueError as e:
        # The line-based check for unclosed quotes in hcl2.loads (hcl2/api.py).
        return invalid(message(e))
    except VisitError as e:
        # The conversion to dictionaries rejects some trees (hcl2/transformer.py).
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
