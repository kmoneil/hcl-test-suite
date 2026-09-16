dynamic "b" "c" {
  for_each = ["x"]
  content {
    v = 1
  }
}
