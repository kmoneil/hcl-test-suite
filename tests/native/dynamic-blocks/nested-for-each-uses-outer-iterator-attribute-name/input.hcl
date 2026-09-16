dynamic "a" {
  for_each = [["p", "q"]]
  iterator = outer
  content {
    dynamic "b" {
      for_each = outer.value
      content {
        v = b.value
      }
    }
  }
}
