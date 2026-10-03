function "f" {
  params = []
  variadic_param = r
  result = g(r...)
}
function "g" {
  params = [x, y]
  result = x - y
}
a = f(5, 3)
