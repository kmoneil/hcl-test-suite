dynamic "b" {
  for_each = xs
  content {
    v = 1
  }
}
