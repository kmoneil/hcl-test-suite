dynamic "b" {
  for_each = "xy"
  content {
    v = 1
  }
}
