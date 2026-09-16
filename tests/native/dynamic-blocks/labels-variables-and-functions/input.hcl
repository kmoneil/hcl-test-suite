dynamic "b" {
  for_each = ["x"]
  labels = [prefix, f()]
  content {
    v = 1
  }
}
