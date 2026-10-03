function "f" {
  params = [x]
  variadic_param = r
  result = [x, r[1]]
}
a = f(xs...)
