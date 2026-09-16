dynamic "b" {
  for_each = ["x"]
  labels = ["é"]
  content {
    v = 1
  }
}
