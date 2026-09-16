dynamic "a" {
  for_each = [["x"]]
  content {
    dynamic "b" {
      for_each = a.value
      content {
        v = b.value
      }
    }
  }
}
