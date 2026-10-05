# HCL test suite

A conformance test suite for [HCL](https://github.com/hashicorp/hcl), the
HashiCorp configuration language, in the spirit of
[test262](https://github.com/tc39/test262) for JavaScript and
[web-platform-tests](https://github.com/web-platform-tests/wpt) for the web.
Any HCL implementation, in any language, can run it by providing a small
adapter program.

**Status:** draft. 3,644 tests of the native and JSON syntaxes, checking 1,223
rules from the spec and the type expression, try function, dynamic block and
user function extensions at hashicorp/hcl v2.24.0. The test and adapter
formats may still change.

## How it works

- **`tests/`** has one directory per test, containing an input file
  (`input.hcl` for the native syntax, `input.hcl.json` for the JSON syntax) and
  a `test.json` file with the expected result
  ([test format](docs/test-format.md)).
- **`coverage/`** lists every rule the spec states, area by area, and the
  tests that check each one. `coverage/mutation/` explains, for each small
  change to hashicorp/hcl that no test catches, why no test can.
- **Adapters** connect one implementation to the runner. An adapter reads a
  file, parses, evaluates or decodes it with a schema, and prints JSON
  ([protocol](docs/protocol.md)). `adapters/` has
  adapters for [hashicorp/hcl](https://github.com/hashicorp/hcl) (Go, the
  reference implementation) and [hcl-rs](https://github.com/martinohmann/hcl-rs)
  (Rust). The Go adapter can also be built with the fork of hashicorp/hcl that
  [OpenTofu](https://github.com/opentofu/opentofu) uses. Three more only report
  whether a file parses:
  [python-hcl2](https://github.com/amplify-education/python-hcl2) (Python),
  [bc-python-hcl2](https://github.com/bridgecrewio/python-hcl2) (its fork that
  [Checkov](https://github.com/bridgecrewio/checkov) uses) and
  [tree-sitter-hcl](https://github.com/tree-sitter-grammars/tree-sitter-hcl)
  (the HCL grammar for tree-sitter, which editors use).
- **`runner/hcltest.py`** runs each test through an adapter, compares the
  output with the expected result and prints a report. It needs only
  Python 3.9 or later.
- **`tools/`** has the lint (`lint.py`), coverage measurements
  (`coverage.py`) and mutation testing (`mutation.py`, with the mutant
  generator in `mutate/`).

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

# bc-python-hcl2, the fork Checkov uses, only whether inputs parse (needs Python 3.10 or later)
python3 -m venv bin/bc-python-hcl2
bin/bc-python-hcl2/bin/pip install --require-hashes -r adapters/bc-python-hcl2/requirements.txt
python3 runner/hcltest.py --validate --adapter "bin/bc-python-hcl2/bin/python adapters/bc-python-hcl2/adapter.py"

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
   values, functions, static analysis, type expressions, `try` and `can`,
   dynamic blocks, or user functions) are skipped, not failed. A parser that
   can't describe what it parsed can support `validate` instead.
2. Run `python3 runner/hcltest.py --adapter "<command that runs your adapter>"`,
   adding `--validate` for an adapter that supports only `validate`.
3. If you're unsure how some input should be read, ask the reference
   implementation: `bin/hcl-go-adapter parse file.hcl`.

## How complete it is

| Area | Tests | Rules | Rules without tests |
| --- | ---: | ---: | ---: |
| lexical elements | 168 | 65 | 1 |
| numbers | 60 | 29 | 1 |
| structure (bodies, attributes, blocks, schemas) | 185 | 68 | 0 |
| collections (tuples, objects) | 152 | 66 | 0 |
| strings | 79 | 27 | 0 |
| heredocs | 124 | 36 | 0 |
| templates | 322 | 87 | 0 |
| variables, attribute access, index | 160 | 60 | 1 |
| splat | 104 | 30 | 0 |
| function calls | 157 | 38 | 0 |
| for expressions | 195 | 68 | 0 |
| operators | 359 | 105 | 0 |
| types, conversions, unification | 137 | 80 | 4 |
| unknown values | 213 | 86 | 0 |
| static analysis | 121 | 38 | 0 |
| JSON grammar | 98 | 29 | 0 |
| JSON bodies (attributes, blocks, schemas) | 106 | 33 | 0 |
| JSON expressions | 87 | 22 | 0 |
| JSON static analysis | 102 | 27 | 0 |
| type expressions (`ext/typeexpr`) | 189 | 53 | 0 |
| JSON type expressions | 34 | 8 | 0 |
| `try` and `can` (`ext/tryfunc`) | 98 | 21 | 1 |
| JSON `try` and `can` | 8 | 2 | 0 |
| dynamic blocks (`ext/dynblock`) | 171 | 66 | 3 |
| JSON dynamic blocks | 42 | 23 | 0 |
| user functions (`ext/userfunc`) | 116 | 46 | 1 |
| JSON user functions | 57 | 22 | 0 |
| **total** | **3,644** | **1,235** | **12** |

- Most rules without tests need something the protocol can't express yet:
  error positions or messages, capsule values, literal-only evaluation with
  variables (which hashicorp/hcl can't express either), user functions
  without a context, and for dynamic blocks, lists of the variables that
  expressions use, hcldec specifications, and separate variables and
  functions for `for_each` and generated blocks. Two depend on choices the
  spec leaves to implementations (rounding and the order of set elements),
  and one is guidance for applications.
  `python3 tools/coverage.py rules` lists them.
- The tests exercise 87.8% of the statements in hashicorp/hcl's `hclsyntax`
  package, 86.0% of its `json` package, 79.3% of `ext/typeexpr`, 97.1% of
  `ext/tryfunc`, 71.2% of `ext/dynblock`, all of `ext/userfunc` and 32.6% of
  the root package, much of which is the text output of errors and source
  positions (`python3 tools/coverage.py go`).
  Most of the rest is syntax tree walking, listing the variables an
  expression uses, lookups by source position and source ranges, which the
  protocol doesn't reach, and error handling for states that valid use can't
  produce. In `ext/typeexpr` it is mostly `TypeString` and Go helpers for
  type constraint values; reading type expressions is fully covered. In
  `ext/dynblock` it is listing the variables that dynamic blocks use, value
  marks, hcldec interfaces and the option for checking `for_each` values.
- Running a line isn't checking it, so `python3 tools/mutation.py run <package>`
  measures more: it makes small changes to the hand-written code of a
  hashicorp/hcl package, one at a time (an operator swapped, a condition
  forced, a statement removed), builds the Go adapter with each, and runs the
  tests that reach the changed line. The tests catch every change the
  protocol can see, in every package:

  | Package | Changes | Caught | Not caught, with a reason | In code no test reaches |
  | --- | ---: | ---: | ---: | ---: |
  | `hclsyntax` | 2,780 | 1,892 | 672 | 216 |
  | `json` | 444 | 250 | 139 | 55 |
  | `ext/typeexpr` | 259 | 196 | 24 | 39 |
  | `ext/tryfunc` | 27 | 17 | 9 | 1 |
  | `ext/dynblock` | 265 | 164 | 25 | 76 |
  | `ext/userfunc` | 53 | 45 | 8 | 0 |
  | `ext/customdecode` | 15 | 7 | 3 | 5 |
  | the root package | 597 | 116 | 62 | 419 |
  | **total** | **4,440** | **2,687** | **942** | **811** |

  For each change the tests don't catch, `coverage/mutation/` says why no test
  can: 506 only change error messages, how many errors there are or their
  ranges, 135 only change source ranges, 192 behave the same for every input,
  85 only differ in states the protocol can't produce (Go API calls or
  hand-built syntax trees), 10 need an input with two mistakes, 11 only change
  value marks and 3 only change output the protocol treats as equal. Most of
  the code no test reaches is what statement coverage leaves out; in the root
  package it is mostly the text output of errors, source positions and
  lookups by them, and merging the bodies of several files, which the
  protocol never does. One change in `ext/typeexpr`, to the sorted order of a map's keys
  when defaults are unified, makes hashicorp/hcl iterate a Go map, so the
  tests catch it in about three runs out of four.

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
- Mutation testing of hashicorp/hcl's packages found behaviors the tests
  didn't check, which statement coverage can't show: error tests whose input
  could fail for another reason, what hashicorp/hcl knows about unknown
  values, states of the heredoc, template and JSON scanners, and how defaults
  are applied to collections. Each surviving change was either given a test or
  a reason in `coverage/mutation/`, and separate reviewers checked the new
  tests and the reasons.

## Results

| Implementation | Passed | Failed | Adapter errors | Skipped |
| --- | ---: | ---: | ---: | ---: |
| hashicorp/hcl v2.24.0 | 3,644 | 0 | 0 | 0 |
| opentofu/hcl, as used by OpenTofu v1.12.6 | 3,642 | 2 | 0 | 0 |
| hcl-rs 0.19.8 | 1,716 | 225 (91 disputed) | 21 | 1,682 |

OpenTofu has no HCL implementation of its own: its `go.mod` replaces
hashicorp/hcl with the fork [opentofu/hcl](https://github.com/opentofu/hcl), so
its row shows how that fork differs from hashicorp/hcl v2.24.0.

Every test also says whether its input parses, and `--validate` checks only
that. This measures parsers that can't run the tests, like python-hcl2,
bc-python-hcl2 and tree-sitter-hcl, and checks the other implementations'
parsers on the inputs of all 3,110 native syntax tests, including tests they
skip. Only hashicorp/hcl and its fork read the JSON syntax, so the others skip
its 534 tests.

| Implementation | Passed | Accepted invalid input | Rejected valid input | Adapter errors | Skipped |
| --- | ---: | ---: | ---: | ---: | ---: |
| hashicorp/hcl v2.24.0 | 3,644 | 0 | 0 | 0 | 0 |
| opentofu/hcl, as used by OpenTofu v1.12.6 | 3,644 | 0 | 0 | 0 | 0 |
| hcl-rs 0.19.8 | 3,032 | 30 (6 disputed) | 48 (31 disputed) | 0 | 534 |
| python-hcl2 8.1.4 | 2,831 | 98 (12 disputed) | 181 (77 disputed) | 0 | 534 |
| bc-python-hcl2 0.4.3 | 2,798 | 129 (12 disputed) | 183 (61 disputed) | 0 | 534 |
| tree-sitter-hcl 1.2.0 | 2,939 | 117 (22 disputed) | 54 (24 disputed) | 0 | 534 |

hcl-rs fails all 78 of these tests in the first table too. As in the first
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

The 134 failures on tests that aren't disputed fall into these groups:

- **Parsing:** it accepts `[for, x]`, `{for: 1}` and `[for v inxs: v]`; literal
  newlines and the escapes `\b`, `\f` and `\/` in strings; and newlines after
  `.` and inside `[*]`. Comparison operators have no associativity, so
  `1 < 2 < 3` is true and the rest of a chain is dropped. `-1[0]` indexes
  `-1`. It also rejects `t.01` and a newline after a unary operator even inside
  parentheses or brackets, accepts `foo.0.0.bar` and `t[*].0.1`, uses
  XID_Start instead of ID_Start, and treats a lone CR as whitespace but
  rejects one in a line comment. A `$` or `%` written as `\u0024` or `\u0025` right before `${` or
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
  types, schema-driven processing (`decode`) with the static analysis,
  dynamic blocks and user functions tests that use it, type expressions, `try`
  and `can`, and the JSON syntax.

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
reports invalid UTF-8 as a parse error, which decides the 29 tests whose input
isn't UTF-8.

The 190 failures on tests that aren't disputed fall into these groups:

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
  item before it, and a one-line block with two items, a nested block or its
  `}` on a later line. Inside parentheses, brackets and argument lists, where
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

Some of its 89 failures on disputed tests come from the problems above, such
as the 17 tests disputed about which characters strip markers remove, whose
strip markers it rejects. Most concern the syntax the tests are disputed
about. For example, of the namespaced calls that aren't in the spec, it accepts
`provider::aws::arn()`, with exactly two namespaces, but rejects `ns::f()` and
`a::b::c::f()`.

### bc-python-hcl2 0.4.3

bc-python-hcl2 is Bridgecrew's fork of python-hcl2, which Checkov uses to parse
Terraform files. Checkov 3.3.21 pins bc-python-hcl2 0.4.3, which accepts any
lark from 1.0.0 on, and the adapter runs it with lark 1.3.1. Like python-hcl2,
it can only take part in `--validate`. The fork has no function that only
parses, so the adapter reports whether `hcl2.loads`, the function Checkov
calls, accepts a file. `hcl2.loads` runs a line-based check for unclosed
quotes, which rejects a line with an odd number of `"` outside heredocs and
comments, then the Lark parser, then the conversion to dictionaries, and an
error in any of them rejects the file. bc-python-hcl2 only accepts text, so as
for python-hcl2, the adapter reports invalid UTF-8 as a parse error, which
decides the 29 tests whose input isn't UTF-8.

The 239 failures on tests that aren't disputed fall into these groups.
python-hcl2 fails 131 of the same tests. Of the 190 tests that aren't disputed
and that python-hcl2 fails, bc-python-hcl2 passes 59: 24 with strip markers in
quoted strings, which it doesn't parse, 15 with comments, and 20 others, most
with newlines around blocks or in quoted strings, or a lone CR.

- **Names:** like python-hcl2, it only allows ASCII letters, digits, `_` and
  `-` in names, so `größe = 1`, `café()` and `o.café` are errors, and so are
  non-ASCII bare block labels. Its lexer also reads `for`, `if` and `in` as
  keywords wherever its parse tables allow one, so it rejects an attribute or
  block named `for` or `if` anywhere but at the start of the file (`a = 1` then
  `if = 3`), and a bare block label `in` or `if` after the type or another bare
  label (`b in {}`).
- **Numbers:** it rejects every number with an exponent, such as `1e3`, `15E2`
  and `1.5e3`, because its lexer reads the `e` as the start of a name. It also
  rejects `1.e`, which is `1` followed by the attribute access `.e`, because a
  `.` after a number always starts a fraction. A number is a sequence of
  one-digit tokens, and spaces and inline comments between them are ignored,
  so it accepts `1 000` and `1/**/2` as 1000 and 12, and `[1 2]`, `f(1 2)` and
  `(1 2)`, each with the single number 12. python-hcl2 passes all of these
  tests.
- **Newlines:** like python-hcl2, it doesn't check the newlines that end
  attributes. It accepts an expression that continues on the next line
  (`a = 1 +` then `2`, `a = 1` then `+ 2`, or a conditional split before or
  after its `?` or `:`, including a `:` on the line after a heredoc's closing
  marker), two attributes on one line (`a = 1 b = 2`, also in a one-line
  block), a `}` on the line of the attribute before it, and a one-line block
  whose `}` is on a later line, as after a line comment, or whose value is a
  heredoc. Unlike python-hcl2, it requires a newline after a block and none
  before its `{`, so `a {} b {}`, `outer { inner {} }` and a block's `{` on the
  next line are errors. Inside parentheses, brackets and argument lists, where
  newlines are ignored, it rejects a newline around `.`, before `(` or `[`, or
  after a unary operator, as in `[o` then `.b]`, though unlike python-hcl2 it
  allows one before `-` or `...`.
- **Objects:** the `=` or `:` between a key and its value is optional, and so
  is the comma between elements, so it accepts `{b 1}` and, like python-hcl2,
  `{b = 1 c = 2}`. A name followed by brackets or parentheses is read as a key
  and a value, so `{k[i] = 1}` and `{f(1) = 2}` are errors: `k` is the key,
  `[i]` its value, and the `=` unexpected. The conversion to dictionaries fails
  for keys that are tuples or objects, as in `{[] = 1}` and `{{} = 1}`, because
  Python lists and dictionaries can't be dictionary keys.
- **Strings:** like python-hcl2, it accepts any backslash sequence, including
  ones that aren't escapes in HCL, such as `\q`, `\b` and `\x41`, incomplete or
  malformed `\u` and `\U` escapes, and escapes of surrogates or of code points
  above U+10FFFF, in strings and block labels. It also accepts interpolations
  and directives in block labels, and rejects `$${` when no `}` follows it, as
  at the end of a string, because it reads the `${` as the start of an
  interpolation. Unlike python-hcl2, it accepts `%%{` there, and the line-based
  check for unclosed quotes rejects newlines in quoted strings and block
  labels.
- **Templates:** it doesn't parse templates in quoted strings. Its lexer reads
  a quoted string as one token, with a regular expression that only finds
  where each interpolation ends, and treats directives as text. So it accepts
  any directive, such as `%{ endif }` without `%{ if }`, `%{ for v [1] }` or
  `%{ elif true }`, and interpolations that aren't one expression, such as
  `${ }`, `${a b}` and `${~ ~}`. A `"` in a directive ends the string, so it
  rejects directives that contain strings, as in `"%{ if "x" }a%{ endif }"`,
  and a string inside an interpolation can't contain an escaped quote, so
  `"${"\""}"` is an error. The line-based check for unclosed quotes rejects
  `"<${ /* " */ "x" }>"`, whose line has an odd number of quotes. python-hcl2
  parses quoted templates and fails only 8 of the 71 tests in this group.
- **Heredocs:** its lexer also reads a heredoc as one token, with a regular
  expression that needs a marker of at least two ASCII characters that starts
  with a letter, an LF right after the opening marker, and at least two
  characters, the last a line break, between that LF and the closing marker.
  So it rejects one-letter markers (`<<A`), CR LF line endings, and heredocs
  with no lines or only an empty line, and like python-hcl2, non-ASCII markers
  (`<<ÉTÉ`, `<<Ω`). Also like python-hcl2, it doesn't parse the template in a
  heredoc, so it accepts an interpolation without its closing brace there.
- **Other syntax:** like python-hcl2, it accepts an attribute defined twice and
  legacy index chains like `foo.0.0.bar` and `t[*].0.1`, and rejects repeated
  unary operators (`--5`, `!!x`, `-!x`) and spaces, newlines or inline comments
  inside `[*]` or between the `.` and `*` of `.*` (`t[ * ]`, `t. *`), which it
  reads as single tokens. It rejects a lone CR except at the end of the file,
  where the newline that `hcl2.loads` appends turns it into CR LF, while
  python-hcl2 accepts a lone CR as whitespace.

Some of its 73 failures on disputed tests come from the problems above, such as
4 tests disputed about Unicode normalization, whose names aren't ASCII, 2 about
very large numbers, which are written with exponents (`1e9000`), and 5 with
objects whose keys are calls, indexes, tuples or objects. Most concern the
syntax the tests are disputed about. Like python-hcl2, of the namespaced calls
that aren't in the spec, it accepts `provider::aws::arn()`, with exactly two
namespaces, but rejects `ns::f()` and `a::b::c::f()`, which accounts for 15
failures, and the line-based check for unclosed quotes rejects 7 tests whose
directives in quoted strings span lines.

### tree-sitter-hcl 1.2.0

tree-sitter-hcl is the HCL grammar for tree-sitter, which editors use to
highlight and navigate code. The adapter parses with py-tree-sitter 0.26.0 and
reports a file as invalid when its tree has an ERROR or MISSING node. The
grammar classifies characters with the C library's locale functions, so the
adapter sets a UTF-8 locale. These results are from macOS 27.0 on arm64, and
they depend on the platform:

- Its scanner keeps the characters of a heredoc's marker in C `char`s, so
  only a marker whose characters are all up to U+00FF can be matched, and
  one with characters from U+0080 to U+00FF only where `char` is unsigned,
  as on Linux arm64 but not on macOS or x86-64. On Linux arm64 it passes the
  two tests whose markers have an `É`, and a line whose character only
  shares the marker's low byte, like `©` for `Ω`, ends the heredoc early.
- macOS counts U+00A0 NO-BREAK SPACE as whitespace and glibc 2.41 doesn't, so
  here it passes the disputed test with a no-break space before the closing
  marker of a `<<-` heredoc, which it fails with glibc.
- In the C locale it treats no non-ASCII character as a letter or
  whitespace, so U+00A0 wouldn't be whitespace on macOS either, and markers
  with an `É` would be rejected on Linux arm64 too.

The 125 failures on tests that aren't disputed fall into these groups:

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
  marker, and a closing marker inside a directive doesn't end the heredoc. It
  never finds the closing marker when the marker has a character above
  U+00FF, such as `Ω`, or here one from U+0080 to U+00FF, such as `É`.
- **Other syntax:** it accepts an attribute defined twice, braces around the
  top-level body, legacy index chains like `foo.0.0.bar`, `ns::f` without
  parentheses, `ns::()`, `ns::::f()` and `ns::1(2)`, and `inxs` or `ifc` in
  for expressions. It rejects spaces, newlines or inline comments inside
  `[*]` or between the `.` and `*` of `.*` (`t[ * ]`, `t. *`).

### Where the spec and hashicorp/hcl disagree

642 tests are disputed. `python3 tools/coverage.py disputes` lists them all by
spec section, with notes. The main themes:

- **Source text:** a byte order mark, identifiers starting with `_`, and some
  invalid UTF-8 are accepted, while a lone CR in a string is rejected.
- **Structure:** blank lines, comment-only lines and a missing final newline
  have no place in the grammar.
- **Newlines:** object constructors use newlines as separators although the
  prose says they are ignored, so a newline in an element's value ends the
  element unless it is inside parentheses, brackets, a nested object, a for
  expression or a template, and the next line can even be another element.
  Newlines inside for expressions, interpolations and directives are ignored
  although the spec doesn't say so.
- **Templates:** which characters strip markers and `<<-` remove isn't
  decided, and stripping in heredocs stops at line ends.
- **Nulls:** null operands and conditions are errors. The spec's rules for the
  dynamic pseudo-type read as unknown results, which its own guarantees rule
  out.
- **Logic operators:** `&&` and `||` return known results for some unknown
  operands and drop errors from the operand that doesn't decide, and an
  unknown left operand hides errors in the right one.
- **Unknown values:** hashicorp/hcl records what it knows about an unknown
  result, so a conditional, template, splat or arithmetic result that can't
  be null is not equal to null, and number ranges, collection lengths and a
  template's known start decide some comparisons. After an unknown condition
  or key, a for expression doesn't report a later null or invalid one. The
  spec doesn't say what type a result that failed contributes to a
  conditional.
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
  is ignored. After applying defaults, convert unifies the elements of a list,
  set or map value to one type, a map's in the sorted order of its keys, which
  can turn a string default like `"01"` into `"1"`.
- **`try` and `can`:** when the first argument that succeeds has a value that
  isn't wholly known, `try` gives the dynamic value, and `can` gives an
  unknown bool for such an argument, although the README says they give that
  value and true. An error in the expression expanded with `...`, or a null
  or string there, makes the call fail, even when an earlier argument of
  `try` succeeds, and expanding an unknown tuple gives the dynamic value, into
  `can` and even after an argument of `try` that succeeds. `try` moving on
  from a function that fails, and `can` giving false for it, depend on a
  failing function making its call an error, which the spec doesn't say.
- **Dynamic blocks:** the README doesn't say what the iterator's key is or in
  which order maps and objects generate blocks, which hashicorp/hcl takes from
  for expressions, nor what a missing or null `for_each`, an unknown tuple or
  object, or a set with unknown elements does. A dynamic block needs exactly
  one content block, and a malformed dynamic block is an error even when it
  generates no blocks. Labels must be a tuple constructor, and the iterator is
  read as a static traversal. An unknown `for_each` makes every attribute of
  the generated block unknown, not just those using the iterator, which hides
  evaluation errors and makes static analyses fail. The iterator isn't in
  scope in dynamic attributes processing or in the expressions static
  analyses give, and in literal-only mode iterators are still in scope and
  JSON strings in generated blocks are still templates.
- **User functions:** the README and package documentation don't say what
  the variadic parameter holds (a tuple), whether parameters accept null or
  unknown values (they don't, so an unknown argument makes even a constant
  result unknown, and the dynamic value skips evaluating the result), or what
  repeated function or parameter names do (the later one wins). `params` is
  required and read statically, so a variable holding names is an error, and
  nothing else may be in a function block. Parentheses make a parameter list
  or name invalid, while `true`, `false` and `null` are accepted as names that
  can't be referenced. In the native syntax a quoted parameter name, as in
  the package documentation, is an error, and in the JSON syntax spaces
  around a name are ignored, while a name string isn't a template. `params`
  set to null, or in the JSON syntax `variadic_param` set to null, is an
  error rather than missing, a function name that isn't an identifier is
  declared without an error, labels aren't normalized, and an attribute named
  `function` is left for the schema in the native syntax and absent from it
  in the JSON syntax. In the native syntax, dynamic attributes processing of
  the body left after the function blocks are taken fails on them.
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
- With dynamic blocks expanded, dynamic attributes processing passes the
  original body's attributes through (`expandBody.JustAttributes` in
  `ext/dynblock`): in a generated block they can't use the iterator, and in a
  body left by partial processing they include the attributes, and in the
  JSON syntax the blocks, that partial processing took. A later step that
  requires an attribute partial processing took doesn't report it missing
  either.
- In literal-only mode, the dynamic blocks extension gives nested bodies an
  evaluation context of its own, so in the JSON syntax the `for_each` of a
  dynamic block inside another block is read as a template.
- The `ext/dynblock` README names the helpers `WalkForEachVariables` and
  `ForEachVariablesHCLDec`, which are `WalkExpandVariables` and
  `ExpandVariablesHCLDec` in v2.24.0.
- A user function that calls itself in a conditional's result, as in
  `n <= 1 ? 1 : n * fact(n - 1)`, overflows Go's stack and ends the program
  instead of giving a value: `ConditionalExpr.Value` in `hclsyntax`
  evaluates both results before looking at the condition. Recursion that a
  for expression stops, by calling the function for no elements, works.
- The `ext/userfunc` package documentation declares `params = ["name"]`,
  which hashicorp/hcl rejects in the native syntax: parameter names must be
  bare identifiers there, as in the README.

## Next steps

- Mutation testing of go-cty's conversion and unification code, which
  decides many of the results the tests check.
- An optional protocol feature for error positions and source ranges, which
  several rules without tests need, and which most of the changes in
  `coverage/mutation/` only affect.

## License

MIT. See [LICENSE](LICENSE).
