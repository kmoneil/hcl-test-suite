# HCL test suite

A conformance test suite for [HCL](https://github.com/hashicorp/hcl), the
HashiCorp configuration language, in the spirit of
[test262](https://github.com/tc39/test262) for JavaScript and
[web-platform-tests](https://github.com/web-platform-tests/wpt) for the web.
Any HCL implementation, in any language, can run it by providing a small
adapter program.

**Status:** draft. 1,994 tests of the native syntax, checking 758 rules from
the spec at hashicorp/hcl v2.24.0. The test and adapter formats may still
change.

## How it works

- **`tests/`** has one directory per test, containing an `input.hcl` file and
  a `test.json` file with the expected result
  ([test format](docs/test-format.md)).
- **`coverage/`** lists every rule the spec states, area by area, and the
  tests that check each one.
- **Adapters** connect one implementation to the runner. An adapter reads a
  file and prints JSON ([protocol](docs/protocol.md)). `adapters/` has
  adapters for [hashicorp/hcl](https://github.com/hashicorp/hcl) (Go, the
  reference implementation) and [hcl-rs](https://github.com/martinohmann/hcl-rs)
  (Rust).
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
```

- To run part of the suite, pass test directories:
  `python3 runner/hcltest.py --adapter bin/hcl-go-adapter tests/native/heredocs`
- `-v` lists every test, not only the failures.
- `--strict` makes failing [disputed tests](docs/test-format.md#disputed-tests)
  fail the run.

The runner exits with 0 if no test failed, 1 if one did, and 2 if it couldn't
run the tests at all.

## Testing your own implementation

1. Write an adapter ([protocol](docs/protocol.md)). You can start with just
   `capabilities` and `parse`. Tests for operations or features you don't
   support (such as `eval`, typed values or unknown values) are skipped, not
   failed.
2. Run `python3 runner/hcltest.py --adapter "<command that runs your adapter>"`.
3. If you're unsure how some input should be read, ask the reference
   implementation: `bin/hcl-go-adapter parse file.hcl`.

## How complete it is

| Area | Tests | Rules | Rules without tests |
| --- | ---: | ---: | ---: |
| lexical elements | 146 | 64 | 1 |
| numbers | 59 | 28 | 1 |
| structure (bodies, attributes, blocks) | 137 | 61 | 9 |
| collections (tuples, objects) | 151 | 69 | 3 |
| strings | 74 | 27 | 0 |
| heredocs | 104 | 36 | 0 |
| templates | 300 | 85 | 2 |
| variables, attribute access, index | 137 | 59 | 3 |
| splat | 87 | 30 | 0 |
| function calls | 55 | 29 | 11 |
| for expressions | 172 | 63 | 0 |
| operators | 328 | 103 | 0 |
| types, conversions, unification | 101 | 75 | 7 |
| unknown values | 143 | 69 | 3 |
| **total** | **1,994** | **798** | **40** |

- Every rule without tests needs something the protocol can't express yet:
  working functions, schema-driven body processing, static analysis or
  literal-only evaluation. `python3 tools/coverage.py rules` lists them.
- The tests exercise 72.9% of the statements in hashicorp/hcl's `hclsyntax`
  package (`python3 tools/coverage.py go`). Most of the rest is static
  analysis, syntax tree walking and function calls, which the protocol doesn't
  reach yet.
- Not covered yet: the JSON syntax, static analysis, function call semantics,
  and the `ext/` packages.

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
| hashicorp/hcl v2.24.0 | 1,994 | 0 | 0 | 0 |
| hcl-rs 0.19.8 | 1,495 | 197 (70 disputed) | 21 | 281 |

### hcl-rs 0.19.8

The 130 failures on tests that aren't disputed fall into these groups:

- **Parsing:** it accepts `[for, x]`, `{for: 1}` and `[for v inxs: v]`; literal
  newlines and the escapes `\b`, `\f` and `\/` in strings; and newlines after
  `.` and inside `[*]`. Comparison operators have no associativity, so
  `1 < 2 < 3` is true and the rest of a chain is dropped. `-1[0]` indexes
  `-1`. It also rejects `t.01`, accepts `foo.0.0.bar`, uses XID_Start instead
  of ID_Start, and treats a lone CR as whitespace.
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
  unknown values, and infinity.

### Where the spec and hashicorp/hcl disagree

274 tests are disputed. `python3 tools/coverage.py disputes` lists them all by
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
- **Types:** unification prefers lists over tuples and maps over objects, and
  string to number conversion accepts `"1e3"`, `"1p4"`, `"+5"` and `"Inf"`.
- **Numbers:** division by zero gives infinity, and integers above 512 bits
  are silently rounded.
- **Syntax that isn't in the spec:** namespaced function calls like
  `provider::aws::arn()`.

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

## Next steps

- Test-only functions in the protocol, to cover function call semantics.
- The JSON syntax, which needs schemas in the protocol.
- Static analysis operations (static list, map, call and traversal).
- The `ext/` packages (`typeexpr`, `dynblock`, `tryfunc`, `userfunc`) as
  optional features.

## License

MIT. See [LICENSE](LICENSE).
