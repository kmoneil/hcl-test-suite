dynamic "b" {
  for_each = ["x"]
  labels = [f()]
  content {
    v = 1
  }
}
