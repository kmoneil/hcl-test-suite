dynamic "b" {
  for_each = [null, "x"]
  content {
    v = b.value
  }
}
