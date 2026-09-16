dynamic "a" {
  for_each = [["p", "q"]]
  content {
    dynamic "b" {
      for_each = a.value
      iterator = a
      content {
        v = 1
      }
    }
  }
}
