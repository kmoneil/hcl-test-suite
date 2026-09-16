dynamic "b" {
  for_each = m
  content {
    k = b.key
  }
}
