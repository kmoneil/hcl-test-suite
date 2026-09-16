dynamic "b" {
  for_each = ["x", "y"]
  content {
    c {
      v = b.value
    }
  }
}
