dynamic "b" {
  for_each = ["1"]
  content {
    v = b.value
  }
}
dynamic "b" {
  for_each = ["2", "3"]
  content {
    v = b.value
  }
}
