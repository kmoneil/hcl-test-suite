function "f" {
  params = [x]
  result = g(x) + 1
}
function "g" {
  params = [y]
  result = y * 2
}
a = f(3)
