function "f" {
  params = [x]
  result = try(x.k, "none")
}
a = [f({k = 1}), f({})]
