dynamic "b" {
  for_each = u
  content {
    v = list(string)
  }
}
