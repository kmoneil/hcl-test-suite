function "add" {
  params = [a, b]
  result = a + b
}
function "list" {
  params         = []
  variadic_param = items
  result         = items
}
a = add(1, 2)
