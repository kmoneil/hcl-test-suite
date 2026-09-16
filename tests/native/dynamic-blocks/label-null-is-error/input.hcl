dynamic "b" {
  for_each = ["x"]
  labels = [null]
  content {
    v = 1
  }
}
