dynamic "b" {
  for_each = u
  content {
    dynamic "c" {
      for_each = "x"
      content {
        w = 1
      }
    }
  }
}
