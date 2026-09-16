dynamic "b" {
  for_each = ["x"]
  iterator = it[0]
  content {
    v = 1
  }
}
