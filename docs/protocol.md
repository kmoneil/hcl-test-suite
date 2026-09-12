# Adapter protocol (draft v0)

An adapter is a small program that lets the test runner talk to one HCL
implementation. The runner starts the adapter once per test, passes it a file
path, and reads one JSON document from its standard output.

This keeps the requirements for an adapter low. It needs to read a file, call
the implementation, and print JSON. Only `eval` tests with variables require
reading JSON.

## Commands

```
<adapter> capabilities
<adapter> parse <file>
<adapter> eval <file> [<variables.json>]
```

The adapter must exit with status 0 whenever it produced a result, **including
when the input is invalid HCL**. A non-zero exit status means the adapter
itself failed. The runner reports that as an adapter error and shows whatever
the adapter wrote to standard error.

Input files ending in `.hcl` use the native syntax. (JSON syntax files will
end in `.hcl.json`; no tests use them yet.)

## `capabilities`

Describes the implementation and what the adapter supports.

```json
{
  "implementation": "my-hcl",
  "version": "0.1.0",
  "operations": ["parse"],
  "features": []
}
```

- `operations` lists the commands the adapter supports: `parse`, `eval` or
  both. Tests for other operations are skipped.
- `features` lists optional parts of HCL the implementation supports. Tests
  that need a missing feature are skipped:
  - `typed-values`: list, set and map values distinct from tuples and
    objects, and null and unknown values that keep a type.
  - `unknown-values`: unknown values and the dynamic pseudo-type.

## `parse`

Parses the file and describes its structure, without evaluating anything.

```json
{"valid": true, "body": <body with expression trees as attribute values>}
```

or, if the file is not valid HCL:

```json
{"valid": false, "phase": "parse", "errors": [<error>, ...]}
```

## `eval`

Parses the file and evaluates every attribute, including attributes inside
blocks.

```json
{"valid": true, "body": <body with values as attribute values>}
```

or

```json
{"valid": false, "phase": "eval", "errors": [<error>, ...]}
```

`phase` is `"parse"` if the file couldn't be parsed and `"eval"` if parsing
succeeded but evaluation failed. Tests that expect an evaluation error check
it, so a parser bug can't pass as the expected evaluation error.

The optional variables file is a JSON object mapping variable names to
[values](#values):

```json
{"u": {"unknown": "bool"}, "name": {"string": "web"}}
```

No functions are available during evaluation yet.

## Errors

```json
{"message": "Attribute redefined", "range": {"start": {"line": 2, "column": 1, "byte": 10}, "end": {"line": 2, "column": 5, "byte": 14}}}
```

The runner shows errors when a test fails but never compares them. `errors`
may be empty and `range` may be left out.

## Bodies

```json
{
  "attributes": {"name": <attribute value>},
  "blocks": [
    {"type": "resource", "labels": ["aws_instance", "web"], "body": <body>}
  ]
}
```

Attributes are keyed by name. Blocks are listed in source order.

## Values

Each value is a JSON object with one key naming its kind.

| Value | Encoding |
| --- | --- |
| string | `{"string": "text"}` |
| number | `{"number": "12.5"}` (a string, so no precision is lost) |
| bool | `{"bool": true}` |
| null | `{"null": <type>}` |
| unknown | `{"unknown": <type>}` |
| tuple | `{"tuple": [<value>, ...]}` |
| object | `{"object": {"name": <value>}}` |
| list | `{"list": [<value>, ...], "element_type": <type>}` |
| set | `{"set": [<value>, ...], "element_type": <type>}` |
| map | `{"map": {"key": <value>}, "element_type": <type>}` |

Numbers are decimal strings such as `"-3"`, `"0.25"` or `"1500"`, or
`"Infinity"` and `"-Infinity"`.

Types use the same JSON notation as go-cty: `"string"`, `"number"`, `"bool"`,
`"dynamic"`, `["list", "string"]`, `["set", <type>]`, `["map", <type>]`,
`["tuple", [<type>, ...]]` and `["object", {"name": <type>}]`. The type of the
`null` keyword is `"dynamic"`.

An implementation without types (such as one whose arrays aren't typed)
should report arrays as tuples, objects as objects and nulls as
`{"null": "dynamic"}`, and leave `typed-values` out of its capabilities.

## Expression trees

`parse` describes each attribute value as a tree of nodes. Every node has a
`kind`. The kinds follow the grammar in
[hclsyntax/spec.md](https://github.com/hashicorp/hcl/blob/v2.24.0/hclsyntax/spec.md).

| Kind | Fields | Source example |
| --- | --- | --- |
| `literal` | `value` | `1`, `true`, `null` |
| `template` | `parts` | `"hello ${name}"`, heredocs |
| `interpolation` | `expr` | the `${name}` part of a template |
| `template_if` | `condition`, `then`, `else` | `%{ if c }yes%{ else }no%{ endif }` |
| `template_for` | `key_var`, `value_var`, `collection`, `body` | `%{ for v in list }${v}%{ endfor }` |
| `variable` | `name` | `name` |
| `get_attr` | `object`, `name` | `a.b` |
| `index` | `collection`, `key` | `a[0]`, `a.0` |
| `splat` | `source`, `each` | `a[*].b` |
| `splat_item` | | the element a splat's `each` applies to |
| `call` | `name`, `args`, `expand_final` | `f(a, b...)`, `provider::aws::arn()` |
| `tuple` | `elements` | `[1, 2]` |
| `object` | `items` (each `{"key": <node>, "value": <node>}`) | `{a = 1}` |
| `for` | `result` (`"tuple"` or `"object"`), `key_var`, `value_var`, `collection`, `key`, `value`, `condition`, `grouping` | `{for k, v in m: k => v...}` |
| `conditional` | `condition`, `true_result`, `false_result` | `a ? b : c` |
| `binary` | `operator`, `left`, `right` | `a + b` |
| `unary` | `operator`, `operand` | `-a`, `!a` |

- `then`, `else` and `body` are lists of template parts, like `parts`. An
  omitted `else` is an empty list.
- Template text is a `literal` string part, with escape sequences, strip
  markers and heredoc indentation already applied. The spec defines stripping
  at the syntax level, so it belongs in the tree even if an implementation
  applies it later. Text that stripping empties is still reported, as an empty
  `literal` part: `" ${~ x ~} "` is not unwrapped the way `"${x}"` is.
- Report `literal` parts only for text that is in the source. An
  implementation that creates empty text parts of its own, such as between
  two interpolations, must leave them out.
- A bare identifier used as an object key is a `literal` string (`{a = 1}`
  has the key `"a"`). So are the keywords `true`, `false` and `null` used as
  bare keys, as in hashicorp/hcl (`{null = 1}` has the key `"null"`; see the
  disputed tests). A parenthesized key is an expression (`{(a) = 1}` has the
  key `{"kind": "variable", "name": "a"}`).
- Parentheses have no node; the tree's shape already records grouping.
- A splat's `each` describes what is applied to each element, with
  `splat_item` standing for the element. `a[*].b[0]` is a splat whose `each`
  is `b[0]` applied to `splat_item`, while `a.*.b[0]` indexes the result of a
  splat whose `each` is only `.b`.
- `key_var`, `key` and `condition` are `null` (or missing) when absent.

## What the runner normalizes

Before comparing, the runner rewrites both the expected and the actual output
into one canonical form. Adapters don't need to produce it:

- Numbers are compared by value, so `"1500"`, `"1.5e3"` and `"1500.0"` match.
- A `unary` minus applied to a number `literal` is the same as a negative
  number `literal`, so `-1` may be reported either way.
- Set elements are compared in any order.
- Adjacent text parts in a template are joined, and empty ones are dropped.
  The exception is a template left with one interpolation and some emptied
  text, which keeps one empty text part because it isn't unwrapped.
- A template with no interpolations or directives is the same as a `literal`
  string. `"abc"` may be reported either way.
- A field set to `null` is the same as a missing field.
- Strings are compared after NFC normalization, because the spec defines
  string equality that way. This applies to string values, object and map
  keys, and template text, but not to attribute names, block types or labels.

## Open questions

These are undecided in v0:

- **Non-integer precision.** The spec allows implementations with different
  precision, so a result like `1 / 3` has no single correct decimal string.
- **Error positions.** Columns count grapheme clusters, which depends on the
  Unicode version. Checking positions could be an optional stricter level.
- **JSON syntax**, which can't be interpreted without a schema, so it will
  need a schema input.
- **Functions.** HCL has no built-in functions, so tests will need a few
  test-only functions provided by the adapter.
- **Unknown value refinements** and marks.
- **Bare traversal keys in parse trees.** How an object key written as a bare
  multi-step traversal such as `{a.b = 1}` appears in a parse tree. Its
  evaluation is tested.
