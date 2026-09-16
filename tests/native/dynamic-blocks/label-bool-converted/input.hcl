dynamic "b" {
  for_each = ["x"]
  labels = [true]
  content {
    v = 1
  }
}
