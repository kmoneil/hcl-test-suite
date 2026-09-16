dynamic "b" {
  for_each = ["x"]
  labels = ["fixed", b.value]
  content {
    v = 1
  }
}
