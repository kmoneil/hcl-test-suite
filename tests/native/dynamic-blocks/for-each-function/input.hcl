dynamic "b" {
  for_each = f()
  content {
    v = b.value
  }
}
