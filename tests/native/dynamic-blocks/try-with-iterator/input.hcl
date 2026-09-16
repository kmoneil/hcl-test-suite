dynamic "b" {
  for_each = [{x = "1"}, {}]
  content {
    v = try(b.value.x, "none")
  }
}
