dynamic "b" {
  for_each = []
  labels = ["x"]
  content {
    v = 1
  }
}
