dynamic "b" {
  for_each = ["x", "y"]
  content {
    v = 1
    dynamic "c" {
      for_each = [b.value]
      labels = [b.value]
      content {
        w = "${b.value}${c.value}"
      }
    }
  }
}
