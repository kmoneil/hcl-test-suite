# HCL test suite

A conformance test suite for [HCL](https://github.com/hashicorp/hcl), the
HashiCorp configuration language, in the spirit of
[test262](https://github.com/tc39/test262) for JavaScript and
[web-platform-tests](https://github.com/web-platform-tests/wpt) for the web.
Any HCL implementation, in any language, can run it by providing a small
adapter program.

**Status:** early draft. 40 tests of the native syntax so far. The test and
adapter formats may still change.

## How it works

- **`tests/`** has one directory per test, containing an `input.hcl` file and
  a `test.json` file with the expected result
  ([test format](docs/test-format.md)).
- **Adapters** connect one implementation to the runner. An adapter reads a
  file and prints JSON ([protocol](docs/protocol.md)). `adapters/` has
  adapters for [hashicorp/hcl](https://github.com/hashicorp/hcl) (Go, the
  reference implementation) and [hcl-rs](https://github.com/martinohmann/hcl-rs)
  (Rust).
- **`runner/hcltest.py`** runs each test through an adapter, compares the
  output with the expected result and prints a report. It needs only
  Python 3.9 or later.

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
   support are skipped, not failed.
2. Run `python3 runner/hcltest.py --adapter "<command that runs your adapter>"`.
3. If you're unsure how some input should be read, ask the reference
   implementation: `bin/hcl-go-adapter parse file.hcl`.

## Results

| Implementation | Passed | Failed | Skipped |
| --- | --- | --- | --- |
| hashicorp/hcl v2.24.0 | 40 | 0 | 0 |
| hcl-rs 0.19.8 | 34 | 5 (2 disputed) | 1 |

### hcl-rs 0.19.8

- Accepts `[for, foo, baz]`, which the spec says is a syntax error.
- Rejects integers that don't fit in 64 bits as a syntax error. The spec
  requires at least 256 bits.
- Compares strings byte by byte, so `"é" == "é"` is `false`. The
  spec compares strings after NFC normalization.
- Disputed: rejects a UTF-8 byte order mark, and reports the undefined
  variable in `true || nope`.
- Skipped: the unknown values test, because hcl-rs has no unknown values.

### Where the spec and the reference implementation disagree

Each disputed test marks a place where the spec should probably change or
say more:

- A UTF-8 byte order mark is accepted. The spec says it isn't permitted.
- Identifiers can start with `_`. The spec's identifier grammar doesn't allow
  that.
- `u || true` is `true` when `u` is unknown. The spec says the result is
  unknown.
- `true || nope` is `true`, with no error for the undefined variable. The
  spec doesn't say whether logic operators short-circuit.
- A file doesn't need a final newline. The grammar requires one after every
  attribute.
- `(5)[*]` produces a tuple. The spec's prose says a list, while its example
  suggests a tuple.

## License

MIT. See [LICENSE](LICENSE).
