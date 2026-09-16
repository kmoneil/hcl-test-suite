dynamic "b" {
  for_each = ["x"]
  iterator = true
  content {
    v = 1
  }
}
