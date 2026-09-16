dynamic "b" {
  for_each = ["x"]
  content {
    v = 1
  }
  content {
    w = 2
  }
}
