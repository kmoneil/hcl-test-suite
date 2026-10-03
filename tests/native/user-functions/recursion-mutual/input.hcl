function "even" {
  params = [n]
  result = [for m in [n]: odd(m - 1) if m > 0]
}
function "odd" {
  params = [n]
  result = [for m in [n]: even(m - 1) if m > 0]
}
a = even(2)
