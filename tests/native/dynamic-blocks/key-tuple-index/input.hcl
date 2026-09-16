dynamic "b" {
  for_each = ["x", "y"]
  content {
    k = b.key
  }
}
