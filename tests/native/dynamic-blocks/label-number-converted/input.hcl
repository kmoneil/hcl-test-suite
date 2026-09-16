dynamic "b" {
  for_each = [1, 2.5]
  labels = [b.value]
  content {
    v = b.value
  }
}
