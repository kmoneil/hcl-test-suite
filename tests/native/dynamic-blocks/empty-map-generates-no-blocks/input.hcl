dynamic "b" {
  for_each = m
  content {
    v = 1
  }
}
