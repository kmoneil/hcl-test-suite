dynamic "b" {
  for_each = ["x"]
  content {
    c {
      w = b.value
    }
  }
}
