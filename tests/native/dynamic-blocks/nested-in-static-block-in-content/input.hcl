dynamic "a" {
  for_each = [{name = "x", items = ["p"]}, {name = "y", items = ["q"]}]
  content {
    s {
      dynamic "b" {
        for_each = a.value.items
        labels = [a.value.name]
        content {
          v = b.value
        }
      }
    }
  }
}
