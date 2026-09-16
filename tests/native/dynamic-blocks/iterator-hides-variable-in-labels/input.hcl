dynamic "b" {
  for_each = ["x"]
  labels = [b.value]
  content {
    v = 1
  }
}
