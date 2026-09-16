dynamic "b" {
  for_each = u
  content {
    k = b.key
    v = b.value
  }
}
