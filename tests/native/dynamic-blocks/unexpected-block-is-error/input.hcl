dynamic "b" {
  for_each = ["x"]
  other {
  }
  content {
    v = 1
  }
}
