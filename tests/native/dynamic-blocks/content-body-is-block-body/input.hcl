dynamic "b" {
  for_each = ["x"]
  content {
    v = 1
    c {
      w = 2
    }
  }
}
