dynamic "b" {
  for_each = ["x"]
  content {
    v = 1
    w = b.value
  }
}
