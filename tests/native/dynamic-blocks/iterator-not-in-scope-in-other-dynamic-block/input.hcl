dynamic "a" {
  for_each = ["x"]
  content {
    v = c.value
  }
}
dynamic "c" {
  for_each = ["y"]
  content {
    w = 1
  }
}
