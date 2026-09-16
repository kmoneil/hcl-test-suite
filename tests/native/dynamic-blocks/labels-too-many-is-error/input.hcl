dynamic "b" {
  for_each = ["x"]
  labels = ["x", "y"]
  content {
    v = 1
  }
}
