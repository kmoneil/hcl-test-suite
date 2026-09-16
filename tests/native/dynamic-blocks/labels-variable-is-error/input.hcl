dynamic "b" {
  for_each = ["x"]
  labels = names
  content {
    v = 1
  }
}
