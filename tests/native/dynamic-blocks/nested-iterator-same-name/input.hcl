dynamic "a" {
  for_each = ["outer"]
  content {
    dynamic "b" {
      for_each = ["inner"]
      iterator = a
      content {
        v = a.value
      }
    }
    w = a.value
  }
}
