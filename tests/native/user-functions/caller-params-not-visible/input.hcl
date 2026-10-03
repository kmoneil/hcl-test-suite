function "f" {
  params = [x]
  result = g()
}
function "g" {
  params = []
  result = x
}
a = f("param")
