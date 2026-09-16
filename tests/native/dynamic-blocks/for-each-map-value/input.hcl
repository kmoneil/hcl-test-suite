dynamic "b" {
  for_each = m
  content {
    v = b.value
  }
}
