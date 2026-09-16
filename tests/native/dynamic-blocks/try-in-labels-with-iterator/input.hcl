dynamic "b" {
  for_each = [{name = "x"}, {}]
  labels = [try(b.value.name, "none")]
  content {
    v = 1
  }
}
