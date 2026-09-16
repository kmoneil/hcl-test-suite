dynamic "b" {
  for_each = ["x"]
  iterator = (it)
  content {
    v = 1
  }
}
