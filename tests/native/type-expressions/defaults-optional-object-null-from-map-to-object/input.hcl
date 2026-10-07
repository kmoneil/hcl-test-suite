a = convert(convert({x = convert(m, object({x = optional(object({b = optional(string)}))})).x, y = null}, map(any)), object({x = object({b = optional(string)}), y = object({b = optional(string)})}))
