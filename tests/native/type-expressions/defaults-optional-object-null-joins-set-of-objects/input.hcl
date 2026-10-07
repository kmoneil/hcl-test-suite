a = convert([convert(m, object({a = optional(object({b = optional(string)}))})).a, {b = "s"}], set(object({b = optional(string)})))
