dynamic "a" {
  for_each = u
  content {
    dynamic "b" {
      for_each = a.value
      content {
        v = b.value
      }
    }
  }
}
