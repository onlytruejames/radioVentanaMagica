"""
Managing secrets and other setup code
"""

import discord, json, schema

confSchema = schema.Schema(
    {
        "name": schema.And(str, len),
        schema.Optional("log"): str,
        "key": str,
        "domains": {
            schema.Use(int): {
                "sources": {
                    schema.Use(int): {
                        schema.Optional("ttl", default=86400): int,
                        schema.Optional("prefSize", default=15): int,
                        schema.Optional("isolated", default=False): bool,
                        schema.Optional("private", default=False): bool,
                        schema.Optional("sampleSize", default=10): int
                    }
                },
                "broadcast": {
                    "channel": schema.Use(int)
                },
                schema.Optional("history", default=7): schema.And(
                    int,
                    schema.Use(lambda x: [0 for i in range(x)])
                )
            }
        }
    }
)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

client = discord.Client(intents=intents)

with open("config.json", "r") as file:
    config = confSchema.validate(json.load(file))

domains = config["domains"]
for d in domains:
    domains[d]["playing"] = False