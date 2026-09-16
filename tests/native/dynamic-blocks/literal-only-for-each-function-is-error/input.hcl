dynamic "b" {
  for_each = f()
  content {
    v = 1
  }
}
