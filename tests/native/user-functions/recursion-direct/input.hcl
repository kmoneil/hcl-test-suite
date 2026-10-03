function "f" {
  params = [x]
  result = [for v in x: f(v)]
}
a = f([[[]], []])
