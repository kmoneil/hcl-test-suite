dynamic "b" {
  for_each = b
  content {
    v = b.value
  }
}
