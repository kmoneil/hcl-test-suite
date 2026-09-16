dynamic "a" {
  for_each = [{name = "x", items = ["p", "q"]}, {name = "y", items = ["r"]}]
  content {
    dynamic "b" {
      for_each = a.value.items
      labels = [a.value.name]
      content {
        outer = a.value.name
        inner = b.value
      }
    }
  }
}
