# HCL test suite

A conformance test suite for [HCL](https://github.com/hashicorp/hcl), the
HashiCorp configuration language, in the spirit of
[test262](https://github.com/tc39/test262) for JavaScript and
[web-platform-tests](https://github.com/web-platform-tests/wpt) for the web.
Any HCL implementation, in any language, can run it by providing a small
adapter program.

**Status:** draft. 3,030 tests of the native and JSON syntaxes, checking 1,047
rules from the spec and the type expression and try function extensions at
hashicorp/hcl v2.24.0. The test and adapter formats may still change.

## How it works

- **`tests/`** has one directory per test, containing an input file
  (`input.hcl` for the native syntax, `input.hcl.json` for the JSON syntax) and
  a `test.json` file with the expected result
  ([test format](docs/test-format.md)).
- **`coverage/`** lists every rule the spec states, area by area, and the
  tests that check each one.
- **Adapters** connect one implementation to the runner. An adapter reads a
  file, parses, evaluates or decodes it with a schema, and prints JSON
  ([protocol](docs/protocol.md)). `adapters/` has
  adapters for [hashicorp/hcl](https://github.com/hashicorp/hcl) (Go, the
  reference implementation) and [hcl-rs](https://github.com/martinohmann/hcl-rs)
  (Rust). The Go adapter can also be built with the fork of hashicorp/hcl that
  [OpenTofu](https://github.com/opentofu/opentofu) uses. Two more only report
  whether a file parses:
  [python-hcl2](https://github.com/amplify-education/python-hcl2) (Python) and
  [tree-sitter-hcl](https://github.com/tree-sitter-grammars/tree-sitter-hcl)
  (the HCL grammar for tree-sitter, which editors use).
- **`runner/hcltest.py`** runs each test through an adapter, compares the
  output with the expected result and prints a report. It needs only
  Python 3.9 or later.
- **`tools/`** has the lint (`lint.py`) and coverage measurements
  (`coverage.py`).

## Running the suite

```sh
# hashicorp/hcl (needs Go)
(cd adapters/go && go build -o ../../bin/hcl-go-adapter .)
python3 runner/hcltest.py --adapter bin/hcl-go-adapter

# hcl-rs (needs Rust)
(cd adapters/hcl-rs && cargo build --release && cp target/release/hcl-rs-adapter ../../bin/)
python3 runner/hcltest.py --adapter bin/hcl-rs-adapter

# the HCL of OpenTofu v1.12.6 (needs Go)
(cd adapters/go && go build -modfile=opentofu.mod -o ../../bin/hcl-opentofu-adapter .)
python3 runner/hcltest.py --adapter bin/hcl-opentofu-adapter

# python-hcl2, only whether inputs parse (needs Python 3.10 or later)
python3 -m venv bin/python-hcl2
bin/python-hcl2/bin/pip install --require-hashes -r adapters/python-hcl2/requirements.txt
python3 runner/hcltest.py --validate --adapter "bin/python-hcl2/bin/python adapters/python-hcl2/adapter.py"

# tree-sitter-hcl, only whether inputs parse (needs Python 3.10 or later, and a
# C compiler on platforms without wheels for py-tree-sitter or tree-sitter-hcl)
python3 -m venv bin/tree-sitter-hcl
bin/tree-sitter-hcl/bin/pip install --require-hashes -r adapters/tree-sitter-hcl/requirements.txt
python3 runner/hcltest.py --validate --adapter "bin/tree-sitter-hcl/bin/python adapters/tree-sitter-hcl/adapter.py"
```

- To run part of the suite, pass test directories:
  `python3 runner/hcltest.py --adapter bin/hcl-go-adapter tests/native/heredocs`
- `-v` lists every test, not only the failures.
- `--strict` makes failing [disputed tests](docs/test-format.md#disputed-tests)
  fail the run.
- `--validate` checks only whether each test's input parses, with the
  adapter's [`validate`](docs/protocol.md#validate) command.

The runner exits with 0 if no test failed, 1 if one did, and 2 if it couldn't
run the tests at all.

## Testing your own implementation

1. Write an adapter ([protocol](docs/protocol.md)). You can start with just
   `capabilities` and `parse`. Tests for operations or features you don't
   support (such as `eval`, `decode`, the JSON syntax, typed values, unknown
   values, functions, static analysis, type expressions or `try` and `can`)
   are skipped, not failed. A parser that can't describe what it parsed can
   support `validate` instead.
2. Run `python3 runner/hcltest.py --adapter "<command that runs your adapter>"`,
   adding `--validate` for an adapter that supports only `validate`.
3. If you're unsure how some input should be read, ask the reference
   implementation: `bin/hcl-go-adapter parse file.hcl`.

## How complete it is

| Area | Tests | Rules | Rules without tests |
| --- | ---: | ---: | ---: |
| lexical elements | 146 | 64 | 1 |
| numbers | 59 | 28 | 1 |
| structure (bodies, attributes, blocks, schemas) | 177 | 67 | 0 |
| collections (tuples, objects) | 152 | 66 | 0 |
| strings | 76 | 27 | 0 |
| heredocs | 104 | 36 | 0 |
| templates | 303 | 86 | 0 |
| variables, attribute access, index | 146 | 58 | 1 |
| splat | 93 | 30 | 0 |
| function calls | 142 | 38 | 0 |
| for expressions | 175 | 64 | 0 |
| operators | 334 | 104 | 0 |
| types, conversions, unification | 136 | 80 | 4 |
| unknown values | 181 | 78 | 0 |
| static analysis | 121 | 38 | 0 |
| JSON grammar | 94 | 29 | 0 |
| JSON bodies (attributes, blocks, schemas) | 103 | 33 | 0 |
| JSON expressions | 75 | 20 | 0 |
| JSON static analysis | 98 | 27 | 0 |
| type expressions (`ext/typeexpr`) | 175 | 51 | 0 |
| JSON type expressions | 34 | 8 | 0 |
| `try` and `can` (`ext/tryfunc`) | 98 | 21 | 1 |
| JSON `try` and `can` | 8 | 2 | 0 |
| **total** | **3,030** | **1,055** | **8** |

- Most rules without tests need something the protocol can't express yet:
  error positions or messages, capsule values, or literal-only evaluation
  with variables, which hashicorp/hcl can't express either. Two depend on
  choices the spec leaves to implementations (rounding and the order of set
  elements), and one is guidance for applications.
  `python3 tools/coverage.py rules` lists them.
- The tests exercise 84.9% of the statements in hashicorp/hcl's `hclsyntax`
  package, 85.8% of its `json` package, 78.6% of `ext/typeexpr` and 97.1% of
  `ext/tryfunc` (`python3 tools/coverage.py go`).
  Most of the rest is syntax tree walking, listing the variables an
  expression uses, lookups by source position and source ranges, which the
  protocol doesn't reach, and error handling for states that valid use can't
  produce. In `ext/typeexpr` it is mostly `TypeString` and Go helpers for
  type constraint values; reading type expressions is fully covered.
- Not covered yet: the `dynblock` and `userfunc` extensions.

## How the tests are checked

- Each expected result is worked out from the spec text first, then compared
  with hashicorp/hcl. Every difference was traced in the hashicorp/hcl or
  go-cty source before deciding.
- Where the spec and hashicorp/hcl disagree, or the spec doesn't decide, the
  test follows hashicorp/hcl and is marked disputed, with notes saying what
  the spec says, what hashicorp/hcl does and where.
- Every test that expects an error records the error hashicorp/hcl reports,
  so a test can't pass because of an unintended mistake in its input
  (`--reference-errors`).
- Every area was written against a rule inventory, then reviewed by a
  separate reviewer who re-derived the results from the spec and looked for
  missing cases, and then fixed.
- `tools/lint.py` checks formats, spec links, rule coverage, feature flags,
  duplicates and portability.

## Results

| Implementation | Passed | Failed | Adapter errors | Skipped |
| --- | ---: | ---: | ---: | ---: |
| hashicorp/hcl v2.24.0 | 3,030 | 0 | 0 | 0 |
| opentofu/hcl, as used by OpenTofu v1.12.6 | 3,028 | 2 | 0 | 0 |
| hcl-rs 0.19.8 | 1,593 | 207 (74 disputed) | 21 | 1,209 |

OpenTofu has no HCL implementation of its own: its `go.mod` replaces
hashicorp/hcl with the fork [opentofu/hcl](https://github.com/opentofu/hcl), so
its row shows how that fork differs from hashicorp/hcl v2.24.0.

Every test also says whether its input parses, and `--validate` checks only
that. This measures parsers that can't run the tests, like python-hcl2 and
tree-sitter-hcl, and checks the other implementations' parsers on the inputs
of all 2,618 native syntax tests, including tests they skip. Only
hashicorp/hcl and its fork read the JSON syntax, so the others skip its 412
tests.

| Implementation | Passed | Accepted invalid input | Rejected valid input | Adapter errors | Skipped |
| --- | ---: | ---: | ---: | ---: | ---: |
| hashicorp/hcl v2.24.0 | 3,030 | 0 | 0 | 0 | 0 |
| opentofu/hcl, as used by OpenTofu v1.12.6 | 3,030 | 0 | 0 | 0 | 0 |
| hcl-rs 0.19.8 | 2,557 | 29 (6 disputed) | 32 (15 disputed) | 0 | 412 |
| python-hcl2 8.1.4 | 2,375 | 88 (9 disputed) | 155 (57 disputed) | 0 | 412 |
| tree-sitter-hcl 1.2.0 | 2,486 | 93 (13 disputed) | 39 (14 disputed) | 0 | 412 |

hcl-rs fails all 61 of these tests in the first table too. As in the first
table, a failure counts as disputed when its test is disputed, even if the
dispute is about evaluation rather than parsing.

### opentofu/hcl, as used by OpenTofu v1.12.6

`adapters/go/opentofu.mod` builds the Go adapter with the versions in OpenTofu
v1.12.6's `go.mod`: opentofu/hcl at commit 587d123c2828 and go-cty v1.18.0. That
commit is hashicorp/hcl v2.23.0 with part of the later upstream changes, plus
APIs that list the functions and variables expressions and bodies use, which
don't change results.

- Both failures come from a fix the fork doesn't have yet,
  [hashicorp/hcl#763](https://github.com/hashicorp/hcl/pull/763): indexing an
  unknown object with a string, as in `u["a"]`, gives the dynamic value instead
  of an unknown value of the attribute's type
  (`native/unknowns/index-unknown-object`), and a name the object type lacks
  isn't an error
  (`native/unknowns/index-unknown-object-missing-attribute-fails`).
- OpenTofu's main branch uses a later version of the fork that includes the
  fix and passes every test.
- To follow a new OpenTofu release, copy the `replace` directive for
  `github.com/hashicorp/hcl/v2` and the go-cty version from its `go.mod` into
  `adapters/go/opentofu.mod`, then run
  `go mod tidy -modfile=opentofu.mod` in `adapters/go`.

### hcl-rs 0.19.8

The 133 failures on tests that aren't disputed fall into these groups:

- **Parsing:** it accepts `[for, x]`, `{for: 1}` and `[for v inxs: v]`; literal
  newlines and the escapes `\b`, `\f` and `\/` in strings; and newlines after
  `.` and inside `[*]`. Comparison operators have no associativity, so
  `1 < 2 < 3` is true and the rest of a chain is dropped. `-1[0]` indexes
  `-1`. It also rejects `t.01` and a newline after a unary operator even inside
  parentheses or brackets, accepts `foo.0.0.bar`, uses XID_Start instead of
  ID_Start, and treats a lone CR as whitespace but rejects one in a line
  comment. A `$` or `%` written as `\u0024` or `\u0025` right before `${` or
  `%{` is read as the escaped `$${` or `%%{`.
- **Numbers:** integers are limited to 64 bits (larger literals are rejected
  and arithmetic wraps), non-integers are 64-bit floats, and `0 / 0` gives
  NaN.
- **Evaluation:** there are no automatic conversions (string operands, string
  conditions, index keys) and no type unification in conditionals.
  - Object attributes are iterated in source order instead of sorted, and
    strings are compared without NFC normalization.
  - Interpolating a collection produces HCL text instead of an error.
  - Strip markers remove at most one line break.
  - `<<-` turns CR LF into LF and doesn't remove indentation inside
    directives, and a closing marker inside a directive doesn't end a heredoc.
- **Not supported (skipped or adapter errors):** list, set and map types,
  unknown values, infinity, function parameters of collection or structural
  types, schema-driven processing (`decode`) with the static analysis tests
  that use it, type expressions, `try` and `can`, and the JSON syntax.

### python-hcl2 8.1.4

python-hcl2 can only take part in `--validate`: it doesn't evaluate anything,
and its syntax tree keeps heredocs as raw text and escape sequences undecoded.
The adapter runs python-hcl2 8.1.4 with lark 1.3.1 and reports whether
`hcl2.parses` accepts a file, which runs the parser and builds python-hcl2's
syntax tree. It leaves out `hcl2.loads`'s conversion to dictionaries, which
fails for some valid files: a for expression whose result is a number literal,
as in `[for v in xs: 1]`, raises a TypeError, and an attribute followed by a
block with the same name is an error (in the other order, the block is
silently dropped). python-hcl2 only accepts text, so as for hcl-rs, the adapter
reports invalid UTF-8 as a parse error, which decides the 15 tests whose input
isn't UTF-8.

The 177 failures on tests that aren't disputed fall into these groups:

- **Strip markers:** it rejects them on interpolations in quoted strings, as in
  `"${~ x}"` and `"${x ~}"`, although it accepts them in heredocs and on
  directives.
- **Names:** it rejects non-ASCII characters in identifiers, so `größe = 1`,
  `café()` and `o.café` are errors, and so are non-ASCII bare block labels and
  heredoc markers.
- **Comments:** it reads an inline comment as a newline, so it rejects one
  wherever it rejects a newline: next to the `=` of an attribute or object
  element, around `.`, before a call's `(` or an index's `[`, after a unary
  operator, inside a splat's `[*]` or between its `.` and `*`, after a block's
  type or between its labels, around the comma between a for expression's
  variables and before its `=>`, and inside interpolations and directives in
  quoted strings, as in `a = /* c */ 1`, `f /* c */ (1)` and
  `"${ /* c */ x }"`.
- **Newlines:** it doesn't check the newlines that end attributes and block
  lines. It accepts an expression that continues on the next line (`a = 1 +`
  then `2`, `a = 1` then `+ 2`, a conditional split across lines, or more of an
  expression after a heredoc), several items on one line (`a = 1 b = 2`,
  `a {} b {}`, an item after a block's `}`, object elements without commas), a
  block's `{` on the line after its type or labels, a `}` on the line of the
  item before it, and a one-line block with two items, a nested block or its `}` on
  a later line. Inside parentheses, brackets and argument lists, where
  newlines are ignored, it rejects a newline around `.`, before `(`, `[`, `-`
  or `...`, or after a unary operator, as in `[o` then `.b]`.
- **Strings:** it accepts backslash sequences that aren't escapes in HCL, such
  as `\q`, `\b` and `\x41`, incomplete or malformed `\u` and `\U` escapes, and
  escapes of surrogates or of code points above U+10FFFF, in strings and block
  labels. It also accepts newlines in quoted strings and block labels, and
  interpolations and directives in block labels. It rejects `$${` and `%%{`
  when no `}` follows them, as at the end of a string.
- **Templates:** it accepts `%{ else }`, `%{ endif }` and `%{ endfor }` without
  the directive they belong to, `%{ else }` inside `%{ for }` or twice in one
  `%{ if }`, and strip markers separated from a directive's `%{` or `}` by a
  space. It doesn't check the syntax of templates in heredocs, so it accepts
  an interpolation without its closing brace there.
- **Other syntax:** it accepts an attribute defined twice, a lone CR as
  whitespace, `for` as the first key of an object (`{for = 1}`), and legacy
  index chains like `foo.0.0.bar`. It rejects repeated unary operators (`--5`,
  `!!x`), and spaces or newlines inside `[*]` or between the `.` and `*` of
  `.*` (`t[ * ]`, `t. *`).

Some of its 66 failures on disputed tests come from the problems above, such
as the 17 tests disputed about which characters strip markers remove, whose
strip markers it rejects. Most concern the syntax the tests are disputed about. For
example, of the namespaced calls that aren't in the spec, it accepts
`provider::aws::arn()`, with exactly two namespaces, but rejects `ns::f()` and
`a::b::c::f()`.

### tree-sitter-hcl 1.2.0

tree-sitter-hcl is the HCL grammar for tree-sitter, which editors use to
highlight and navigate code. The adapter parses with py-tree-sitter 0.26.0 and
reports a file as invalid when its tree has an ERROR or MISSING node. The
grammar classifies characters with the C library's locale functions, so the
adapter sets a UTF-8 locale. These results are from glibc 2.41; in the C
locale, non-ASCII heredoc markers would be rejected.

The 105 failures on tests that aren't disputed fall into these groups:

- **Newlines:** it doesn't check the newlines that end attributes and block
  lines. It accepts an attribute's `=` or value, a block label or `{`, or the
  rest of an expression on the next line (`a` then `= 1`, `a =` then `1`,
  `a = 1 +` then `2`, `a = o` then `.b`), several items on one line
  (`a = 1 b = 2`, `block { a = 1 b = 2 }`, `a {} b {}`, an item after a block's
  `}`, object elements without commas), a `}` on the line of the item before
  it, and a one-line block with a nested block or with its `}` on a later
  line.
- **Numbers:** it rejects an exponent after an integer, as in `1e3` and
  `15E2`, while `1.5e3` works, and accepts `0x1F`.
- **Strings and templates:** it accepts escapes of surrogates and of code
  points above U+10FFFF, newlines in quoted strings and block labels,
  interpolations and directives in block labels, empty interpolations (`${}`,
  `${ }`, `${~ ~}`), and strip markers separated by a space (`${ ~x}`). It
  rejects `"%%%{x}"` and a NUL character in a string.
- **Source text:** it accepts a lone CR, a form feed and a vertical tab as
  whitespace, and invalid UTF-8 in strings and heredocs.
- **Heredocs:** it accepts text after the opening marker, markers that start
  with a digit, `<<--EOT`, and an expression that continues after the closing
  marker, and a closing marker inside a directive doesn't end the heredoc.
- **Other syntax:** it accepts an attribute defined twice, braces around the
  top-level body, legacy index chains like `foo.0.0.bar`, `ns::f` without
  parentheses, `ns::()`, and `inxs` or `ifc` in for expressions. It rejects
  spaces, newlines or inline comments inside `[*]` or between the `.` and `*`
  of `.*` (`t[ * ]`, `t. *`).

### Where the spec and hashicorp/hcl disagree

427 tests are disputed. `python3 tools/coverage.py disputes` lists them all by
spec section, with notes. The main themes:

- **Source text:** a byte order mark, identifiers starting with `_`, and some
  invalid UTF-8 are accepted, while a lone CR in a string is rejected.
- **Structure:** blank lines, comment-only lines and a missing final newline
  have no place in the grammar.
- **Newlines:** object constructors use newlines as separators although the
  prose says they are ignored, while newlines inside for expressions and
  interpolations are ignored although the spec doesn't say so.
- **Templates:** which characters strip markers and `<<-` remove isn't
  decided, and stripping in heredocs stops at line ends.
- **Nulls:** null operands and conditions are errors. The spec's rules for the
  dynamic pseudo-type read as unknown results, which its own guarantees rule
  out.
- **Logic operators:** `&&` and `||` return known results for some unknown
  operands and drop errors from the operand that doesn't decide.
- **Types:** unification prefers lists over tuples and maps over objects, bool
  and number don't unify to string, and string to number conversion accepts
  `"1e3"`, `"1p4"`, `"+5"` and `"Inf"`.
- **Numbers:** division by zero gives infinity, and integers above 512 bits
  are silently rounded.
- **Function calls:** arguments are converted to their parameter's type,
  which the spec's call rules don't provide for, so `"12"` is accepted for a
  number. Sets can be expanded with `...` although the spec only allows lists
  and tuples. The spec doesn't decide what expanding null or an unknown value
  does, or whether a function that fails makes the call fail.
- **Parameters of the dynamic pseudo-type:** go-cty ignores `allow_unknown`
  for the dynamic value, stops checking the arguments after it, and treats the
  `null` keyword like the dynamic value.
- **Syntax that isn't in the spec:** namespaced function calls like
  `provider::aws::arn()`.
- **JSON grammar:** a byte order mark and UTF-16 files are errors, which
  RFC 7159 leaves open, and lone surrogate escapes and invalid UTF-8 in strings
  become U+FFFD.
- **JSON bodies:** a null block value defines no blocks, and a null or an array
  inside a block array defines one block, although the spec only allows
  objects there. An empty label level is an error, and a schema that asks for
  `//` gets it.
- **Static analysis:** parentheses make a tuple, object, call or traversal
  unusable for static analysis, and only literal index keys are traversal
  steps, not constant expressions like `-1`. A bare identifier key analyzes
  as a traversal although it evaluates to a string, a bare `true`, `false` or
  `null` key is a string, and an expanded final argument loses its `...`. The
  JSON syntax reads traversal strings with a traversal parser, which rejects
  `.0`, `[true]`, `[null]` and heredoc keys, and string content read for
  static analysis ignores newlines, a trailing line comment and a leading
  byte order mark.
- **Type expressions:** parentheses make a type expression or an object
  attribute name invalid, bare `true` and `null` keys name object type
  attributes, and names equal under NFC name one attribute, while naming an
  attribute twice is an error. `optional` is an error in an exact type, `any`
  is allowed with defaults, `ns::list(string)` is read as a call instead of
  being a syntax error, `list(string...)` is read as `list(string)`, and a
  bare identifier key of a static map is read as a type keyword. Default
  values can use operators, an optional attribute set to null gets its
  default, and so do the objects inside a default value, while a default
  added to a map value is unified with the map's elements. A value that lacks
  a required attribute doesn't convert, with or without defaults, and a
  default value that lacks one is an error, although the spec's object
  conversion fills missing attributes with null. `convert` also fails for a
  value that lacks an optional attribute, which the README says becomes null.
  In the JSON syntax, a newline or line comment after a type keyword or call
  is ignored.
- **`try` and `can`:** when the first argument that succeeds has a value that
  isn't wholly known, `try` gives the dynamic value, and `can` gives an
  unknown bool for such an argument, although the README says they give that
  value and true. An error in the expression expanded with `...`, or a null
  or string there, makes the call fail, even when an earlier argument of
  `try` succeeds, and expanding an unknown tuple gives the dynamic value, into
  `can` and even after an argument of `try` that succeeds. `try` moving on
  from a function that fails, and `can` giving false for it, depend on a
  failing function making its call an error, which the spec doesn't say.
- **Schemas:** requesting an optional attribute twice (or in the JSON syntax a
  required one), or an attribute and a block type with the same name, isn't
  reported as an error, and dynamic attributes of the body left from a JSON
  array fail.

### Suspected bugs in hashicorp/hcl and go-cty

- The identifier scanner accepts invalid UTF-8: `a\xC4 = 1` defines an
  attribute whose name includes the following space.
- `<<-` indentation removal deletes a combining mark attached to the last
  indentation space.
- Comparing objects that contain unknown values is nondeterministic (go-cty
  iterates a Go map). No test depends on it.
- `(1/0) % 2` dereferences a nil pointer in go-cty's Modulo; it is recovered
  and reported as "Operation failed".
- `true && null` is false, while `null || false` is an error.
- Passing `null` to a function parameter of the dynamic pseudo-type that
  accepts null but not that type gives an unknown result, which the spec's
  guarantee that nothing is unknown without an unknown input rules out
  (go-cty's `Function.returnTypeForValues`).
- For parameters of the dynamic pseudo-type, an argument that is the dynamic
  value stops go-cty from checking the arguments after it, so `f(d, null)`
  gives the dynamic value while `f(null, d)` is an error.
- Expanding an unknown list with `...` skips evaluating the other arguments,
  so `f(nope, u...)` reports no error for the undefined `nope`.
- In the native syntax, dynamic attributes processing of the body left by
  partial processing fails on blocks that partial processing already took.
- In a JSON string evaluated as a template, a carriage return not followed by
  a line feed stops template processing, so `"x\ry${1}"` gives
  `x\ry${1}`, and a leading U+FEFF is silently removed.
- A native syntax object key can only be analyzed as a static traversal, so
  `{f(1) = 2}` has no static call key and `{[1, 2] = 3}` no static list key:
  `ObjectConsKeyExpr.UnwrapExpression` returns `hclsyntax.Expression` instead
  of `hcl.Expression`, so hcl's unwrapping never reaches the key's expression.
- `typeexpr.ConvertFunc` fails for a value that lacks an optional attribute:
  its implementation converts to the result type its type function computed,
  which no longer has the optional attributes.
- An object type whose two attribute names differ only in Unicode
  normalization (`é` written as U+00E9 and as `e` followed by U+0301), one
  `string` and one `number`, gives an attribute of either type from one run
  to the next: typeexpr checks for duplicates by the names as written, and
  go-cty merges them in Go map order. No test depends on it.
- The documentation of `hcl.ExprAsKeyword` says the native syntax's `true`,
  `false` and `null` can't be keywords, but it gives their names.

## Next steps

- The other `ext/` packages (`dynblock`, `userfunc`) as optional features.

## License

MIT. See [LICENSE](LICENSE).
