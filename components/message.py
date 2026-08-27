"""
Models to describe messages
"""

from components import logging, database, config
import traceback, discord

class MessageReference:
    """
    Standardisation for references to messages
    """
    def __init__(self, guild: int, channel: int, message: int):
        self.guild = guild
        self.channel = channel
        self.message = message

        self.hash = hash((guild, channel, message))

    async def toDiscordMessage(self) -> discord.Message | bool:
        """
        Returns a message based on channel and message, or False if it doesn't exist

        If this message doesn't exist, deletes all references to it in the database.
        """
        try:
            c = config.client.get_channel(self.channel)
            m = await c.fetch_message(self.message)
            return m
        except discord.NotFound:
            await self.deleteMessage()
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
        
    async def deleteMessage(self):
        """
        Delete this message and all its attachment from the database
        """
        await database.transaction(f"DELETE FROM messages WHERE messageID={self.hash}; DELETE FROM attachments WHERE messageID={self.hash};")

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