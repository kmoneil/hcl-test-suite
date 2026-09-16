s {
  dynamic "b" {
    for_each = xs
    labels = [f()]
    content {
      v = b.value
    }
  }
}
