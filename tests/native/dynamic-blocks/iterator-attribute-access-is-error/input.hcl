dynamic "b" {
  for_each = ["x"]
  iterator = it.x
  content {
    v = 1
  }
}
