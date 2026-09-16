dynamic "b" {
  for_each = ["x", "y"]
  content {
    v = 1
  }
}
