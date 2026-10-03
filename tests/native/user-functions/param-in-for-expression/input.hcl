function "f" {
  params = [x]
  result = [for v in x: v * 2]
}
a = f([1, 2])
