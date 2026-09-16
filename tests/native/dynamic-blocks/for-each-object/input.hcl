dynamic "b" {
  for_each = {z = "last", a = "first"}
  content {
    k = b.key
    v = b.value
  }
}
