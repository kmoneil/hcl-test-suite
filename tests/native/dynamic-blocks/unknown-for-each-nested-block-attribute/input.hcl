dynamic "b" {
  for_each = u
  content {
    c {
      w = 1
    }
  }
}
