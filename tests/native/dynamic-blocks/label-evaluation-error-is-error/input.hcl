dynamic "b" {
  for_each = ["x"]
  labels = [nope]
  content {
    v = 1
  }
}
