dynamic "b" {
  for_each = ["x"]
  labels = ["${b.value}-suffix"]
  content {
    v = 1
  }
}
