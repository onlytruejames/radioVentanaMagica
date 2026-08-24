"""
Managing secrets and other setup code
"""

import discord, json

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

client = discord.Client(intents=intents)

with open("config.json", "r") as file:
    config = json.load(file)