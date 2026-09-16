b {
  v = "static"
}
dynamic "b" {
  for_each = ["x"]
  content {
    v = b.value
  }
}
c = 1
