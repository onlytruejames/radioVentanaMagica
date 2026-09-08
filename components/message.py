"""
Models to describe messages
"""

from components import logging, database, config
import traceback, discord
from aiosqlite import Connection

class MessageReference:
    """
    Standardisation for references to messages
    """
    def __init__(self, guild: int, channel: int, message: int):
        self.guild = guild
        self.channel = channel
        self.message = message

        self.hash = hash((guild, channel, message))

    async def toDiscordMessage(self, conn: Connection = None) -> discord.Message | bool:
        """
        Returns a message based on channel and message, or False if it doesn't exist

        If this message doesn't exist, deletes all references to it in the database.
        """
        owner = False
        if not conn:
            conn = await database.connection()
            owner = True
        try:
            c = config.client.get_channel(self.channel)
            m = await c.fetch_message(self.message)
            return m
        except discord.NotFound:
            await self.deleteMessage(conn)
            if owner:
                await conn.commit()
            return False
        except Exception as e:
            await logging.log(traceback.format_exc())
            raise e

    def fromDiscordMessage(message: discord.Message) -> 'MessageReference':
        """
        Create reference from message object
        """
        return MessageReference(
            message.guild.id,
            message.channel.id,
            message.id
        )
        
    async def deleteMessage(self, conn: Connection = None):
        """
        Delete this message and all its attachment from the database
        """
        owner = False
        if not conn:
            owner = True
            conn = await database.connection()
        await database.executeMultiple([f"DELETE FROM messages WHERE messageID={self.hash};", f"DELETE FROM attachments WHERE messageID={self.hash};"], conn)
        if owner:
            await database.finish()
        else:
            await conn.commit()

def getMessageHash(message: discord.Message | MessageReference) -> int:
    """
    message: discord.Message object or reference to one
    
    Returns an integer hash uniquely identifying the message
    
    Also known as MessageID
    """
    if type(message) == discord.Message:
        return hash((message.guild.id, message.channel.id, message.id))
    elif type(message) == MessageReference:
        return message.hash
    raise ValueError(f"Invalid message format in getMessageHash")