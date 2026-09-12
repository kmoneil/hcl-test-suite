# Test format (draft v0)

Each test is a directory under `tests/` containing an input file and a
`test.json` file:

```
tests/native/heredocs/flush-strips-common-indent/
  input.hcl
  test.json
```

The path groups tests by syntax (`native`, later `json`) and then by area of
the spec. The directory name says what the test checks.

## `test.json`

```json
{
  "description": "<<- removes the smallest common indentation from each line",
  "spec": ["hclsyntax/spec.md#template-expressions"],
  "op": "eval",
  "expect": {
    "valid": true,
    "body": {"attributes": {"a": {"string": "hello\n  world\n"}}, "blocks": []}
  }
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `description` | yes | One sentence stating the behavior being tested. |
| `op` | yes | `"parse"` or `"eval"`, the adapter command to run. |
| `expect` | yes | The expected result (see below). |
| `spec` | no | Links to the spec sections that define the behavior, relative to the hashicorp/hcl repository at v2.24.0. |
| `input` | no | The input file name, if not `input.hcl`. |
| `features` | no | Optional features the test needs, such as `"unknown-values"`. Adapters without them skip the test. |
| `variables` | no | Variables for `eval`, as a JSON object of [values](protocol.md#values). |
| `status` | no | `"disputed"` if the spec doesn't clearly support the expected result (see below). |
| `notes` | no | Anything a reader needs to know, such as why the test is disputed. |

The runner rejects unknown fields, so a misspelled field name is an error,
not a silently ignored field.

## Expected results

A successful result gives the body, in the format of the
[`parse`](protocol.md#parse) or [`eval`](protocol.md#eval) output:

```json
{"valid": true, "body": {...}}
```

An expected error:

```json
{"valid": false}
```

For `eval` tests, add `"phase": "eval"` when the file must parse successfully
and fail only during evaluation. Error messages and positions are never
checked.

## Disputed tests

Sometimes the spec and the reference implementation (hashicorp/hcl) disagree,
or the spec doesn't decide at all. Such a test follows the reference
implementation, because that is what real configuration files depend on, and
is marked:

```json
"status": "disputed",
"notes": "The spec says byte order marks are not permitted. The reference implementation deliberately skips a leading UTF-8 BOM."
```

A failing disputed test is reported but doesn't make the run fail unless
`--strict` is given. Each disputed test is a spec issue worth raising
upstream. To list them:

```sh
grep -rl '"disputed"' tests
```

## Writing tests

- **Test one behavior per test.** A failure should point at one rule. Keep
  inputs as small as possible.
- **Work from the spec.** Write the expected result from the spec text first,
  then check it against the reference implementation:
  `bin/hcl-go-adapter eval tests/.../input.hcl`. If they disagree, find out
  why before choosing, and mark the test disputed if the spec is wrong or
  silent.
- **Write tests yourself.** Don't copy test cases from other projects' test
  suites. That keeps the licensing of this suite simple.
- **Inputs are exact bytes.** Some tests depend on a missing final newline,
  CR LF line endings, a byte order mark or invalid UTF-8. `.gitattributes`
  and `.editorconfig` stop git and editors from changing input files. Check
  unusual inputs with a hex dump (`xxd input.hcl`).
- **Include invalid inputs.** New parsers tend to accept too much, so tests
  that expect errors are as important as ones that expect success.
