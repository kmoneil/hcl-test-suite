#!/usr/bin/env python3
"""Connects the test runner to tree-sitter-hcl, the tree-sitter grammar for HCL.

    adapter.py capabilities
    adapter.py validate <file.hcl>

Editors use tree-sitter grammars to highlight and navigate code. A tree-sitter
parser always builds a syntax tree, marking what it couldn't parse with ERROR
and MISSING nodes, and it leaves text as it is in the source, so it can't give
the protocol's expression trees. The adapter only supports validate
(docs/protocol.md#validate): a file is valid if its tree has neither kind of
node.
"""

import importlib.metadata
import json
import locale
import sys

import tree_sitter_hcl
from tree_sitter import Language, Parser

USAGE = """usage:
  adapter.py capabilities
  adapter.py validate <file.hcl>"""


def main(args):
    use_utf8_locale()
    if args == ["capabilities"]:
        output = capabilities()
    elif len(args) == 2 and args[0] == "validate":
        output = validate(args[1])
    else:
        sys.exit(USAGE)
    print(json.dumps(output))


def use_utf8_locale():
    """The grammar's scanner classifies characters with the C library's locale
    functions, so without this, results would depend on the caller's locale."""
    for name in ("C.UTF-8", "C.utf8", "UTF-8", "en_US.UTF-8"):
        try:
            locale.setlocale(locale.LC_CTYPE, name)
            return
        except locale.Error:
            pass
    sys.exit("tree-sitter-hcl's results depend on the locale, and no UTF-8 locale is available")


def capabilities():
    grammar, runtime = importlib.metadata.version("tree-sitter-hcl"), importlib.metadata.version("tree-sitter")
    return {
        "implementation": "tree-sitter-hcl",
        "version": f"{grammar} (py-tree-sitter {runtime})",
        "operations": ["validate"],
        "features": [],
    }


def validate(path):
    if path.endswith(".hcl.json"):
        sys.exit("tree-sitter-hcl doesn't read the JSON syntax")
    with open(path, "rb") as f:
        source = f.read()
    root = Parser(Language(tree_sitter_hcl.language())).parse(source).root_node
    if not root.has_error:
        return {"valid": True}
    return {"valid": False, "phase": "parse", "errors": syntax_errors(root, source)}


def syntax_errors(root, source):
    """Describes the outermost ERROR and MISSING nodes of a tree, in source order."""
    errors = []
    stack = [root]  # not recursive, because deeply nested input makes deep trees
    while stack:
        node = stack.pop()
        # Counted from the byte offset: Point.row in py-tree-sitter 0.26.0 is
        # wrong, and can crash, beyond row 256.
        line = source.count(b"\n", 0, node.start_byte) + 1
        if node.is_missing:
            errors.append({"message": f"line {line}: missing {node.type}"})
        elif node.is_error:
            text = source[node.start_byte:node.end_byte].decode("utf-8", "replace")
            errors.append({"message": f"line {line}: can't parse {json.dumps(text[:40])}"})
        else:
            stack.extend(reversed([child for child in node.children if child.has_error]))
    return errors


if __name__ == "__main__":
    main(sys.argv[1:])
