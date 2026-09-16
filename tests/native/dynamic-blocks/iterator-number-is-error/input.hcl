dynamic "b" {
  for_each = ["x"]
  iterator = 1
  content {
    v = 1
  }
}
