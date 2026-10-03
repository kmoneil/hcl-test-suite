function "f" {
  params = [x]
  result = "%{ for x in [9] }${x}%{ endfor }${x}"
}
a = f(1)
