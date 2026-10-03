function "f" {
  params = [x]
  result = [[for x in [9]: x], x]
}
a = f(1)
