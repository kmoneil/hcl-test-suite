dynamic "b" {
  for_each = u
  content {
    dynamic "c" {
      for_each = [1, 2]
      content {
        w = 1
      }
    }
  }
}
