function "f" {
  params = [g]
  result = g() + g
}
function "g" {
  params = []
  result = 10
}
a = f(1)
