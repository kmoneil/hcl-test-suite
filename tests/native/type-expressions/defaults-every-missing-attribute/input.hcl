a = convert({}, object({a = optional(object({x = optional(string, "y")}), {x = "p"}), b = optional(object({x = optional(string, "y")}), {x = "q"})}))
