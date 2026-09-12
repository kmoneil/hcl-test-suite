a = <<EOT
%{ for v in ["a", "b"] ~}
${v}
%{ endfor ~}
EOT
