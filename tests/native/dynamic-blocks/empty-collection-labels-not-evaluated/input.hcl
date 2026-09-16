dynamic "b" {
  for_each = []
  labels = [nope]
  content {
    v = 1
  }
}
