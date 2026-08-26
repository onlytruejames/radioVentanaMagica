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

async def transaction(statement: str) -> list[aiosqlite.Row]:
    """
    statement: string

    Execute a line of sql, commit, tidy up, and return anything found by the statement
    """
    statements = statement.split(";")
    async with aiosqlite.connect("audio.db") as db:
        db.row_factory = aiosqlite.Row
        for s in statements:
            if len(s) != 0:
                cur = await db.execute(s + ";")
        results = await cur.fetchall()
        await db.commit()
    return results

async def getMessage(guild: int, channel: int, message: int) -> discord.Message | bool:
    """
    guild: id of guild the message belongs to

    channel: The channel id of the channel the message is in
    
    message: The message id of the message

    Returns a message based on channel and message. False if it doesn't exist
    """
    try:
        c = config.client.get_channel(channel)
        m = await c.fetch_message(message)
        return m
    except discord.NotFound:
        await deleteMessage((guild, channel, message))
        return False
    except Exception as e:
        await log(traceback.format_exc())
        raise e

async def log(msg):
    # send logs to discord webhook or print
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(config.config["log"], data={"content": msg})
    except:
        print(msg)

async def deleteMessage(message: discord.Message | tuple[int, int, int]):
    """
    Delete this message and all its attachment from the database
    """
    id = getMessageHash(message)
    await transaction(f"DELETE FROM messages WHERE messageID={id}; DELETE FROM attachments WHERE messageID={id};")

async def validCDNURL(url: str) -> bool:
    """
    url: string

    Checks if a given URL is still valid. Discord CDN links are only valid for around 24hr.
    """
    async with aiohttp.ClientSession() as session:
        async with session.head(url) as got:
            return got.status == 200