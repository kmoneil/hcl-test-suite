dynamic "b" {
  for_each = ["x"]
  labels = [for l in ["x"]: l]
  content {
    v = 1
  }
}
