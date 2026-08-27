"""
Holder for logging functions, may be expanded or replaced in future
"""
import aiohttp
from components import config

async def log(msg):
    # send logs to discord webhook or print
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(config.config["log"], data={"content": msg})
    except:
        print(msg)