# Test format (draft v0)

Each test is a directory under `tests/` containing an input file and a
`test.json` file:

```
tests/native/heredocs/flush-strips-common-indent/
  input.hcl
  test.json
```

The path is `tests/<syntax>/<area>/<name>`: the syntax (`native` or `json`),
the area of the spec, and a name saying what the test checks. Names are
lowercase words separated by dashes. Native syntax tests read `input.hcl`,
and JSON syntax tests read `input.hcl.json`, which is where the runner looks
unless `input` says otherwise.

## `test.json`

```json
{
  "description": "<<- removes the smallest common indentation from each line",
  "spec": [
    "hclsyntax/spec.md#template-expressions"
  ],
  "op": "eval",
  "expect": {
    "valid": true,
    "body": {
      "attributes": {
        "a": {
          "string": "hello\n  world\n"
        }
      },
      "blocks": []
    }
  }
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `description` | yes | One sentence stating the rule being tested, starting with a capital letter and without a final period. At most 100 characters, and unique in the suite. |
| `spec` | yes | The spec sections that define the behavior, as anchors from [tools/spec-anchors.txt](../tools/spec-anchors.txt). |
| `op` | yes | `"parse"`, `"eval"` or `"decode"`, the adapter command to run. JSON syntax tests are always decode tests. |
| `input` | no | The input file name in the test directory, if not `input.hcl` for native syntax tests or `input.hcl.json` for JSON syntax tests. Lint requires the default. |
| `features` | no | Optional features the test needs: `"typed-values"`, `"unknown-values"`, `"functions"`, `"json-syntax"`, `"static-analysis"`, `"type-expressions"` or `"try-functions"` (see [capabilities](protocol.md#capabilities)). Adapters without them skip the test. Lint requires `typed-values` when a variable, a function or the expected result has a list, set or map, or a null or unknown value of a type other than dynamic, or a function has a parameter of a collection or structural type, because an implementation without those types can't receive, declare or produce them. It requires `unknown-values` when any of them has an unknown value, `functions` when the test declares functions, `json-syntax` for JSON syntax tests and only for them, `static-analysis` exactly when the schema analyzes an attribute, `type-expressions` exactly when the schema analyzes a type expression or the test declares a type expression extension function, and `try-functions` exactly when the test declares the extension functions `try` or `can`, which must be declared under their own names, and no other function may be declared under those names. It also requires `typed-values` when an expected type result has a list, set or map type. |
| `status` | no | `"disputed"` if the spec doesn't clearly support the expected result (see below). |
| `notes` | no | Anything a reader needs to know. Required for disputed tests. |
| `variables` | no | Variables for `eval` and `decode`, as a JSON object of [values](protocol.md#values). |
| `functions` | no | Functions for `eval` and `decode`, as a JSON object mapping function names to [declarations](protocol.md#functions). Lint checks that function and parameter names are identifiers (function names may join several with `::`), and that the `functions` feature is listed exactly when the test declares functions. |
| `evaluation_mode` | no | `"literal-only"` to evaluate in literal-only mode (see [eval](protocol.md#eval)), for `eval` and `decode` tests without variables or functions. |
| `schema` | for `decode` | The [schema](protocol.md#schemas) to apply. Lint requires leaving out fields that only repeat a default (empty `attributes`, `blocks` or `labels`, `"required": false`, and an `analysis` or part that is `{"kind": "value"}`), and an expected result that the schema could give: only the attributes it requests, with every required one, blocks of the types it requests with as many labels as it names, `remain` exactly where it has a remain schema, and for each attribute a result of the kind its analysis asks for, down to each part, or a value where there is no analysis. A `"type"` analysis must give an exact type, without `"dynamic"` or optional attributes. An `"analysis"` phase needs a schema with an analysis. |
| `reference_error` | for expected errors | Text that the error reported by hashicorp/hcl contains (see below). |
| `expect` | yes | The expected result (see below). |

The runner rejects unknown fields, so a misspelled field is an error rather
than being silently ignored. `python3 tools/lint.py --fix` writes `test.json`
files in their canonical form: two-space indentation and the field order of
the table above.

## Expected results

A successful result gives the body, in the format of the
[`parse`](protocol.md#parse), [`eval`](protocol.md#eval) or
[`decode`](protocol.md#decode) output:

```json
{"valid": true, "body": {...}}
```

An expected error:

```json
{"valid": false}
```

`parse` tests don't give a phase. `eval` and `decode` tests that expect an
error must give it:
`"phase": "parse"` if the file itself is invalid, or `"phase": "eval"` if it
parses and then fails during evaluation. `decode` tests can also expect
`"phase": "schema"`, for a file that parses but doesn't fit the schema, and
`"phase": "analysis"`, for a static analysis that fails. Error messages and
positions are never compared between implementations.

### Reference errors

A test that expects an error could pass for the wrong reason, for example
because of a typo in its input. To guard against that, every such test
records part of the error message hashicorp/hcl reports, usually the summary:

```json
"reference_error": "Attribute redefined",
```

The runner looks for this text anywhere in the message, which is the summary
and the detail joined by `": "`. When hashicorp/hcl uses one summary for
several different mistakes (such as "Invalid 'for' expression"), use a
distinctive part of the detail instead, so the check tells the mistakes apart.

Some hashicorp/hcl messages cover several mistakes, such as "Invalid JSON
string" for every malformed JSON string. Use them when nothing more distinctive
exists, and make sure the input has no other mistake that could produce the
same message.

This is checked only when the runner is given `--reference-errors`, which is
meant for the hashicorp/hcl adapter. Other implementations may word their
errors however they like.

## Disputed tests

Sometimes the spec and the reference implementation (hashicorp/hcl) disagree,
or the spec doesn't decide at all. Such a test follows the reference
implementation, because that is what real configuration files depend on, and
is marked:

```json
"status": "disputed",
"notes": "The spec says byte order marks are not permitted. The reference implementation deliberately skips a leading UTF-8 BOM."
```

The notes must say what the spec says (or that it's silent), what the
reference implementation does, and where in its source that happens. A
failing disputed test is reported but doesn't make the run fail unless
`--strict` is given. Each disputed test is a spec issue worth raising
upstream. To list them: `grep -rl '"disputed"' tests`.

## Coverage files

`coverage/` lists the rules that the spec states, area by area, and the tests
that check each one. This is what makes it possible to say an area is fully
covered: every rule has tests, and every test checks a rule.

`coverage/native-heredocs.json` covers `tests/native/heredocs`:

```json
{
  "area": "native/heredocs",
  "rules": [
    {
      "id": "heredocs-flush-removes-common-indent",
      "spec": "hclsyntax/spec.md#template-expressions",
      "rule": "With <<-, the smallest number of leading spaces on any line is removed from every line",
      "tests": [
        "native/heredocs/flush-strips-common-indent"
      ]
    },
    {
      "id": "heredocs-marker-is-plain-text",
      "spec": "hclsyntax/spec.md#template-expressions",
      "rule": "The closing marker is matched as plain text",
      "untested": "Explain why this rule can't be tested through the protocol yet"
    }
  ]
}
```

- A rule is one precise statement of behavior, in your own words. Include the
  edge cases a rule implies, not just its main case: each boundary,
  each kind of operand, each way the input can be malformed.
- `id` starts with the area's last path component and a dash (here
  `heredocs-`), and is unique across all coverage files.
- A rule describes the behavior its tests expect. If all of its tests are
  disputed, its text ends with `(disputed)`; lint checks this.
- A rule has either `tests` (which may be in any area) or an `untested`
  reason. Use `untested` only for rules that can't be tested through the
  protocol yet, such as rules about error positions.
- Every test must be listed by at least one rule.

## Writing tests

- **Test one rule per test.** A failure should point at one rule. Keep inputs
  as small as possible, with one attribute unless the rule needs more.
- **Work from the spec.** Write the expected result from the spec text
  first, then check it against the reference implementation:
  `bin/hcl-go-adapter eval tests/.../input.hcl`, or for a test with a context
  (variables, functions, a schema or an evaluation mode),
  `python3 runner/hcltest.py --adapter bin/hcl-go-adapter -v tests/.../name`,
  which shows the difference when the result doesn't match.
  If they disagree, find the reason in the hashicorp/hcl source before
  deciding, and mark the test disputed if the spec is wrong or silent.
  For an extension, such as the type expression extension, the spec is the
  README of its hashicorp/hcl package together with the documentation of the
  package's exported names, and tests link to the README's sections.
- **Test both sides of every rule.** Include inputs that must be rejected,
  not just ones that must be accepted. New parsers tend to accept too much.
- **Make expected errors unambiguous.** An input that should fail must contain
  exactly one mistake, and its `reference_error` must match that mistake.
- **Declare only the functions a test needs.** Give each argument exactly its
  parameter's type unless the test is about conversion: the spec's function
  call rules make an argument that doesn't match its parameter an error, while
  hashicorp/hcl converts it, so a test that depends on that conversion is
  disputed. Name parameters when the reference error has to say which one
  failed.
- **Keep results exact and portable.**
  - Non-integer values must be exactly representable in binary, like `0.5`
    or `2.25`, and their exact decimal expansion must have at most 70
    significant digits. A result like `1/3` depends on the implementation's
    precision, and so does the printed form of a longer binary fraction: an
    implementation with the minimum 256-bit precision prints about 77 digits.
    To test precision beyond that, compare values instead of printing them.
  - Don't depend on the order of set elements.
  - Avoid characters added in recent Unicode versions unless the test is
    about them. Identifier tests must use characters from Unicode 9.0 or
    earlier, the version of hashicorp/hcl's identifier tables.
- **Don't lean on gaps in the spec by accident.** The grammar has no place
  for blank lines or lines holding only a comment in a body, or for a file
  that ends without a newline after its last item, although every
  implementation accepts them. (Newlines inside brackets and parentheses are
  a different matter, covered by the rules for those constructs.)
  Tests about those cases are disputed. Every other test should end its last
  item with a newline and avoid blank and comment-only lines, so that an
  implementation following the grammar strictly fails only the disputed tests.
- **The newline after a heredoc belongs to what contains it.** The grammar
  ends the heredoc production with a Newline, but the prose says the heredoc
  ends when its marker appears on a line of its own, and prose outranks the
  grammar. So the newline after the closing marker ends the attribute (or
  separates what follows), as in hashicorp/hcl; this is not disputed.
- **Inputs are exact bytes.** Some tests depend on a missing final newline,
  CR LF line endings, a byte order mark or invalid UTF-8. `.gitattributes`
  and `.editorconfig` stop git and editors from changing input files. Check
  unusual inputs with a hex dump (`xxd input.hcl`).
- **Write tests yourself.** Don't copy test cases from other projects' test
  suites. That keeps the licensing of this suite simple.

## Checks before committing

```sh
python3 tools/lint.py
python3 runner/hcltest.py --adapter bin/hcl-go-adapter --reference-errors
python3 runner/hcltest.py --adapter bin/hcl-rs-adapter
python3 tools/coverage.py rules
```

The lint must report no problems, and every test must pass with the
hashicorp/hcl adapter.
