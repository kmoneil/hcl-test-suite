dynamic "b" {
  for_each = ["x"]
  content {
    v = b.index
  }
}
