dynamic "b" {
  for_each = ["x"]
  content {
    w = 1
  }
}
