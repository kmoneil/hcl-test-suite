dynamic "b" {
  for_each = ["x"]
  content {
    v = list(string)
  }
}
