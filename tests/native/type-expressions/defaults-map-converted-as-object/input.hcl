a = convert(m, object({x = optional(object({a = string}), {a = "1"}), y = optional(object({a = string, c = list(string)}), {a = "2", c = []}), z = map(string)}))
