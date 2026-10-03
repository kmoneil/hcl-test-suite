function "fact" {
  params = [n]
  result = try([for m in [n - 1]: n * fact(m) if m > 0][0], 1)
}
a = fact(5)
