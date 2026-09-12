a = "%{ for a in ["x", "y"] }%{ for b in [1, 2] }${a}${b};%{ endfor }%{ endfor }"
