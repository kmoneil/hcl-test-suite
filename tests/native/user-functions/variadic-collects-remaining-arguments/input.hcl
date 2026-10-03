function "f" {
  params = [x]
  variadic_param = r
  result = [for v in r: v * 10]
}
a = f(1, 2, 3)
