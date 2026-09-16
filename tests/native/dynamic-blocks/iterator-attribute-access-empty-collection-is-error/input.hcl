dynamic "b" {
  for_each = []
  iterator = it.x
  content {
    v = 1
  }
}
