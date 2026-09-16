a = 1
dynamic "b" {
  for_each = ["x"]
  content {
    v = b.value
  }
}
c = 2
