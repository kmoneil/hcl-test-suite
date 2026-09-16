dynamic "b" {
  for_each = ["x"]
  labels = []
  content {
    v = 1
  }
}
