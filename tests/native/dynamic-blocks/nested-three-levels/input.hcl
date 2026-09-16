dynamic "a" {
  for_each = ["1"]
  content {
    dynamic "b" {
      for_each = ["2"]
      content {
        dynamic "c" {
          for_each = ["3"]
          content {
            v = "${a.value}${b.value}${c.value}"
          }
        }
      }
    }
  }
}
