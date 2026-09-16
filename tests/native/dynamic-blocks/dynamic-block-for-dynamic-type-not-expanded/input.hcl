dynamic "dynamic" {
  for_each = ["x"]
  content {
    for_each = ["y"]
    content {
      v = 1
    }
  }
}
