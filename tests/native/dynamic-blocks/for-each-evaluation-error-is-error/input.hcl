dynamic "b" {
  for_each = nope
  content {
    v = 1
  }
}
