dynamic "b" {
  for_each = ["x"]
  iterator = it
  labels = [b.value]
  content {
    v = 1
  }
}
