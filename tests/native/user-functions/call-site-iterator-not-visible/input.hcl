function "f" {
  params = []
  result = v
}
a = [for v in [1]: f()]
