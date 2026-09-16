dynamic "b" {
  for_each = ["x", "y"]
  content {
    v = b.value
    w = "${b.value}!"
  }
}
