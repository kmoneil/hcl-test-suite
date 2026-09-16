dynamic "b" {
  for_each = ["x", "y"]
  labels = [b.value]
  content {
    v = 1
  }
}
