a = <<EOT
%{ for v in ns
::
f() }${v}%{ endfor }
EOT
