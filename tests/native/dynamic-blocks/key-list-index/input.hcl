dynamic "b" {
  for_each = xs
  content {
    k = b.key
  }
}
