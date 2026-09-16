dynamic "b" {
  for_each = ["x"]
  content "l" {
    v = 1
  }
}
