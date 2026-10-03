function "add" {
  params = [a, b]
  result = a + b
}
function "list" {
  params         = []
  variadic_param = items
  result         = items
}
a = list(1, 2)
