function "f" {
  params = [x]
  result = x * 2
}
a = [for v in [1, 2]: f(v)]
