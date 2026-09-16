dynamic "b" {
  for_each = ["x", "y"]
  iterator = it
  labels = [it.value]
  content {
    v = 1
  }
}
