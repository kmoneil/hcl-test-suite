dynamic "b" {
  for_each = u
  labels = ["fixed"]
  content {
    v = b.value
  }
}
