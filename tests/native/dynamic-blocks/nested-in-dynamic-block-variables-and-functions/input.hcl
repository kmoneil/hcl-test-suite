dynamic "a" {
  for_each = ["p", "q"]
  content {
    dynamic "b" {
      for_each = xs
      labels = ["${a.value}${f()}"]
      content {
        v = b.value
      }
    }
  }
}
