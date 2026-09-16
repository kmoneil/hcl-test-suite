dynamic "b" {
  for_each = [{x = "1"}, {}]
  content {
    v = can(b.value.x)
  }
}
