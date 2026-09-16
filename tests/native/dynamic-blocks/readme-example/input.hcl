toplevel {
  nested {
    foo = "static block 1"
  }
  dynamic "nested" {
    for_each = ["a", "b", "c"]
    iterator = nested
    content {
      foo = "dynamic block ${nested.value}"
    }
  }
  nested {
    foo = "static block 2"
  }
}
