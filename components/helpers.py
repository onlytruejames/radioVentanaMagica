"""
Commonly used functions across all components
"""

import discord, aiosqlite, aiohttp, traceback
from components import config

def getMessageHash(message: discord.Message | list | tuple) -> int:
    """
    message: discord.Message object or list/tuple (guild, channel, message)
    
    Returns an integer hash uniquely identifying the message
    
    Also known as MessageID
    """
    if type(message) == discord.Message:
        return hash((message.guild.id, message.channel.id, message.id))
    elif type(message) in [list, tuple] and len(message) == 3:
        return hash(message)
    raise ValueError(f"Invalid message format in getMessageHash")

async def transaction(statement: str) -> list:
    """
    statement: string

    Execute a line of sql, commit, tidy up, and return anything found by the statement
    """
    async with aiosqlite.connect("audio.db") as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(statement)
        results = await cur.fetchall()
        await db.commit()
    return results

async def getMessage(channel: int, message: int) -> discord.Message:
    """
    channel: The channel id of the channel the message is in
    
    message: The message id of the message

    Returns a message based on channel and message
    """
    try:
        c = config.client.get_channel(channel)
        m = await c.fetch_message(message)
        return m
    except discord.NotFound as e:
        raise e
    except Exception:
        await log(traceback.format_exc())

async def log(msg):
    # send logs to discord webhook or print
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(config.config["log"], data={"content": msg})
    except:
        print(msg)