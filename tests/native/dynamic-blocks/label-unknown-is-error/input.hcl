dynamic "b" {
  for_each = ["x"]
  labels = [u]
  content {
    v = 1
  }
}
