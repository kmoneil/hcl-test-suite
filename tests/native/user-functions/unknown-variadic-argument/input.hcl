function "f" {
  params = []
  variadic_param = r
  result = [for v in r: v]
}
a = f(1, u)
