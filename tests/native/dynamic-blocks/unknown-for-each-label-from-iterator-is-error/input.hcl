dynamic "b" {
  for_each = u
  labels = [b.value]
  content {
    v = 1
  }
}
