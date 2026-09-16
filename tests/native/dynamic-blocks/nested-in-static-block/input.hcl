s {
  dynamic "b" {
    for_each = ["x", "y"]
    content {
      v = b.value
    }
  }
}
