dynamic "b" {
  for_each = ["x", "y"]
  labels = ["same"]
  content {
    v = b.value
  }
}
