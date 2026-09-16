dynamic "b" {
  for_each = u
  content {
    v = 1
    w = 2
  }
}
