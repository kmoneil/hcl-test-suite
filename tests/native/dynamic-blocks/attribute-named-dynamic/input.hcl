dynamic = 1
dynamic "b" {
  for_each = ["x"]
  content {
    v = b.value
  }
}
