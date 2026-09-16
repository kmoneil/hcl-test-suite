dynamic "b" {
  for_each = []
  content {
    v = 1
  }
  content {
    v = 2
  }
}
