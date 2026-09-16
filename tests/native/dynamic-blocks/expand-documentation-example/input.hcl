parent {
  dynamic "child" {
    for_each = child_objs
    content {
      dynamic "grandchild" {
        for_each = child.value.children
        labels   = [grandchild.key]
        content {
          parent_key = child.key
          value      = grandchild.value
        }
      }
    }
  }
}
