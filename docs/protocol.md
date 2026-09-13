# Adapter protocol (draft v0)

An adapter is a small program that lets the test runner talk to one HCL
implementation. The runner starts the adapter once per test, passes it the
path of the test's input file (and for `decode` and some `eval` tests, a
context file), and reads one JSON document from its standard output.

This keeps the requirements for an adapter low. It needs to read a file, call
the implementation, and print JSON. Only tests with a context file require
reading JSON.

## Commands

```
<adapter> capabilities
<adapter> parse <file>
<adapter> eval <file> [<context.json>]
<adapter> decode <file> <context.json>
```

The adapter must exit with status 0 whenever it produced a result, **including
when the input is invalid HCL**. A non-zero exit status means the adapter
itself failed. The runner reports that as an adapter error and shows whatever
the adapter wrote to standard error.

Input files ending in `.hcl` use the native syntax, and files ending in
`.hcl.json` use the JSON syntax. The JSON syntax can only be read with a schema,
so only `decode` gets JSON syntax files.

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

- `operations` lists the commands the adapter supports, any of `parse`, `eval`
  and `decode`. Tests for other operations are skipped.
- `features` lists optional parts of HCL the implementation supports. Tests
  that need a missing feature are skipped:
  - `typed-values`: list, set and map values distinct from tuples and
    objects, null and unknown values that keep a type, and function
    parameters whose type is a collection or structural type.
  - `unknown-values`: unknown values, including the dynamic value (the
    unknown value of the dynamic pseudo-type).
  - `functions`: the adapter can add the [test functions](#functions) a test
    declares to the function table.
  - `json-syntax`: the implementation reads the JSON syntax.

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

The optional context file describes the evaluation context:

```json
{
  "variables": {"u": {"unknown": "bool"}, "name": {"string": "web"}},
  "functions": {"f": {"params": [{"type": "string"}], "result": "arguments"}}
}
```

- `variables` maps variable names to [values](#values).
- `functions` maps function names to [declarations](#functions).
- `evaluation_mode`, if present, is `"literal-only"`, and the context then has
  no variables or functions. Expressions are evaluated in the spec's
  literal-only mode, in which variables and functions aren't available.
  Without it, they are evaluated in full expression mode. In the native syntax
  the two modes differ only in that; in the JSON syntax, literal-only mode also
  reads strings as literal text instead of as templates.
- `schema` is only for [`decode`](#decode), which always has one.

The other fields are optional. Without a context file there are no variables,
and the function table is empty, so every function call is an error.

An implementation without a literal-only mode can evaluate native syntax
expressions without variables or functions instead, which the spec allows for
syntaxes with their own expression syntax.

- Names are used exactly as given, without Unicode normalization or other
  changes, because tests depend on names that differ only in normalization.
- The runner only sends the fields described here, and never sends `null` for
  them. Adapters should reject anything else, so that a test written for a
  later version of the protocol fails loudly instead of being evaluated
  wrongly.
- What a context file contains depends on the test's
  [features](#capabilities): unknown values only appear in tests that need
  `unknown-values`, and lists, sets and maps, nulls and unknowns whose type
  isn't `"dynamic"`, and function parameters whose type isn't `"string"`,
  `"number"`, `"bool"` or `"dynamic"` only appear in tests that need
  `typed-values`. Parameters of type `"dynamic"` and the `allow_unknown` and
  `allow_dynamic_type` flags can appear in any test.

## `decode`

Parses the file, applies a [schema](#schemas) to its body, and evaluates the
attributes the schema selects, including those in the blocks it selects.

```json
{"valid": true, "body": <body content>}
```

or

```json
{"valid": false, "phase": "schema", "errors": [<error>, ...]}
```

The context file has the same fields as for `eval`, and a `schema`, which it
always has. The adapter applies the schema with the implementation's own body
processing; an implementation without schema-driven processing doesn't list
`decode` in its operations.

`phase` says where the first error was found:

- `"parse"`: the file isn't valid. For the JSON syntax that means it breaks
  the RFC 7159 grammar or its root value is neither an object nor an array.
- `"schema"`: applying the schema failed. For the JSON syntax this includes
  body structure the schema can't use, such as an array element that isn't an
  object or a block property that is a string, which is only found when a
  schema is applied to that body. An implementation that rejects the schema
  itself also reports that as a schema error.
- `"eval"`: evaluating an attribute the schema selected failed, including
  a JSON string that isn't a valid template.

Apply the whole schema, including to nested blocks and remaining bodies, before
evaluating anything, so that each error is reported in its phase. A block
type's body schema is only applied to blocks of that type that exist. Only the
attributes the schema selects are evaluated, and an error in any of them makes
the whole result invalid.

### Schemas

A schema says how to process a body, in one of the three ways the spec
defines:

```json
{
  "mode": "exhaustive",
  "attributes": [{"name": "region", "required": true}, {"name": "tags"}],
  "blocks": [
    {"type": "resource", "labels": ["type", "name"], "body": {"mode": "dynamic-attributes"}}
  ]
}
```

| Field | Meaning |
| --- | --- |
| `mode` | How to process the body (below). Required. |
| `attributes` | Attribute schemata, each a `name` and whether the attribute is `required` (`false` if left out). None if left out. |
| `blocks` | Block header schemata, each a block `type`, the names of its `labels` (none if left out), and the schema for the `body` of each block of that type, which is required. None if left out. |
| `remain` | For partial processing, the schema for the body it leaves. Optional. |

- `"exhaustive"`: schema-driven processing, in which an attribute or block
  the schema doesn't mention is an error.
- `"partial"`: partial processing, which puts the attributes and blocks the
  schema doesn't mention into a new body. That body is processed with
  `remain`, if it is given.
- `"dynamic-attributes"`: dynamic attributes processing, which gives every
  attribute and no blocks. In the native syntax a block in the body is an
  error. This mode has no other fields.

A schema never has the same block type twice, which would give one block type
two body schemas. It can have the same attribute name twice, or an attribute
and a block type with the same name, which the spec says is an error, because
tests check what implementations do with such schemas. If the implementation
can't express such a schema at all, the adapter should exit with a non-zero
status and say why on standard error.

Schemas only concern body structure. Attribute names, block types and labels
are never templates, and `evaluation_mode` doesn't change how a body is
processed, only how the selected attributes are evaluated (including the
property names of JSON objects that are expressions).

The body content is a [body](#bodies) with evaluated attribute values:
`attributes` has the attributes the schema selected, and `blocks` the blocks
it selected, in the order the syntax defines, each with its body processed with
the schema for its type. With a `remain` schema, the body content also has
`remain`: the body content of the body that partial processing left.

## Functions

HCL has no built-in functions: the application defines them. Tests that call
functions declare test-only functions, which the adapter adds to the function
table. A declaration describes a function the way the spec does:

```json
{
  "params": [
    {"name": "s", "type": "string"},
    {"name": "n", "type": "number", "allow_null": true}
  ],
  "variadic_param": {"name": "rest", "type": "dynamic", "allow_unknown": true},
  "result": "arguments"
}
```

| Field | Meaning |
| --- | --- |
| `params` | The positional parameters, in order. Optional, and empty if left out. |
| `variadic_param` | The variadic parameter, if the function has one. |
| `result` | What the function returns. Required. |

Each parameter has these fields:

| Field | Meaning |
| --- | --- |
| `name` | The parameter's name, for error messages. Optional. |
| `type` | The parameter's type specification, in the [type notation](#values) of values, such as `"string"` or `["list", "dynamic"]`. Required. |
| `allow_null` | Whether the parameter accepts null values. Defaults to `false`. |
| `allow_unknown` | Whether the parameter accepts unknown values. Defaults to `false`. |
| `allow_dynamic_type` | Whether the parameter accepts values of the dynamic pseudo-type. Defaults to `false`. |

A parameter of type `"dynamic"` accepts values of every type, but still only
accepts null if `allow_null` is set. An implementation without unknown values
can ignore `allow_unknown` and `allow_dynamic_type`.

`result` is one of:

- `"arguments"`: the function returns a tuple of the argument values it
  receives, the positional arguments followed by the variadic ones. These are
  the values after whatever the implementation does to arguments before
  calling a function, such as converting them to the parameter types. The
  result type is the tuple type of those values' types, which is also defined
  for unknown values; the dynamic value contributes `"dynamic"`.
- `{"value": <value>}`: the function returns this [value](#values), whatever
  the arguments. Its result type is the value's type. The value can be unknown
  or hold unknown values.
- `{"error": "<message>"}`: the function fails with this message when it is
  called. Its result type is the dynamic pseudo-type. A call that doesn't run
  the function, for example because an argument is unknown, doesn't fail.

The adapter defines each function through the implementation's usual interface
for application-defined functions. Everything else the spec says about calls
is the implementation's job, and it is what the tests check: mapping arguments
to parameters, checking them against the parameter types and flags, and giving
an unknown or dynamic result for unknown or dynamic arguments instead of
running the function. If the implementation's interface needs a single result
type for every call, declare the dynamic pseudo-type for `"arguments"`; tests of
unknown results will then show the difference.

If the implementation can't express a declaration, for example a parameter type
it has no equivalent for, the adapter should exit with a non-zero status and
say why on standard error.

A function name can contain `::`, as in `provider::aws::arn`, for the
namespaced function calls that hashicorp/hcl supports (see the disputed tests).
An adapter can register such a name as one string or split it into a namespace
and a name, as long as `provider::aws::arn()` calls the function and `arn()`
doesn't. An adapter for an implementation without namespaced functions can
leave these declarations out.

## Errors

```json
{"message": "Attribute redefined", "range": {"start": {"line": 2, "column": 1, "byte": 10}, "end": {"line": 2, "column": 5, "byte": 14}}}
```

The runner shows errors when a test fails. It only compares messages when it
is given `--reference-errors`, which is meant for the hashicorp/hcl adapter.
`errors` may be empty and `range` may be left out.

## Bodies

```json
{
  "attributes": {"name": <attribute value>},
  "blocks": [
    {"type": "resource", "labels": ["aws_instance", "web"], "body": <body>}
  ]
}
```

Attributes are keyed by name. Blocks are listed in source order, which for the
JSON syntax is the order of the properties that define them. Bodies from
[`decode`](#decode) can also have `remain`.

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
`"Infinity"` and `"-Infinity"`. Context files only contain numbers in this
plain form, while adapters may print any decimal form, such as `"1.5e3"`
(see [what the runner normalizes](#what-the-runner-normalizes)).

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
- **Unknown value refinements** and marks.
- **Bare traversal keys in parse trees.** How an object key written as a bare
  multi-step traversal such as `{a.b = 1}` appears in a parse tree. Its
  evaluation is tested.
