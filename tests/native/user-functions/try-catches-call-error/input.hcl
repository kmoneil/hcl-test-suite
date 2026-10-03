function "f" {
  params = [x]
  result = x.k
}
a = try(f({}), "fallback")
