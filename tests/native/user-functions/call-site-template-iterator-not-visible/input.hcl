function "f" {
  params = []
  result = v
}
a = "%{ for v in ["it"] }${f()}%{ endfor }"
