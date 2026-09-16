c {
  v = "first"
}
dynamic "b" {
  for_each = ["x", "y"]
  content {
    v = b.value
  }
}
c {
  v = "last"
}
