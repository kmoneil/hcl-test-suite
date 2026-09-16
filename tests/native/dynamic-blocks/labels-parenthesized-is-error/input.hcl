dynamic "b" {
  for_each = ["x"]
  labels = (["x"])
  content {
    v = 1
  }
}
