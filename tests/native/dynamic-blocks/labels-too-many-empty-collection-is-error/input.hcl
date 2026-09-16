dynamic "b" {
  for_each = []
  labels = ["x", "y"]
  content {
    v = 1
  }
}
