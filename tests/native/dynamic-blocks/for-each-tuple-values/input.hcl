dynamic "b" {
  for_each = ["x", 2, true]
  content {
    v = b.value
  }
}
