dynamic "a" {
  for_each = u
  content {
    dynamic "b" {
      for_each = ["x"]
      labels = [a.value]
      content {
        v = 1
      }
    }
  }
}
