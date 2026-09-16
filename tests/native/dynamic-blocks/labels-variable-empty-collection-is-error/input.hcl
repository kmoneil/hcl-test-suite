dynamic "b" {
  for_each = []
  labels = names
  content {
    v = 1
  }
}
