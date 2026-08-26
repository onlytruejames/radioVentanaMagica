"""
Module for code related to the Attachment model
"""

from typing import Union
import discord, json, traceback
from components import helpers, config
from ffmpeg.asyncio import FFmpeg

class Attachment:
    """
    Model for Attachments, either from the database or discord

    Attachments are uniquely identified by messageID and url
    """
    def __init__(
            self,
            messageID: int,
            url: str,
            length: int,
            name: str,
            author: int,
            dob: int,
            channel: int,
            message: int,
            playcount: int | None = None
        ):
        # composite key: REQUIRED
        self.messageID = messageID
        self.url = url
        self.uid = hash((messageID, url))

        # present when created from attachment or database: we will know these
        self.length = length
        self.name = name
        self.author = author
        self.dob = dob
        self.channel = channel
        self.message = message

        # only database
        self.playcount = playcount

    async def fromAttachment(message: discord.Message, attachment: discord.Attachment) -> Union[bool, 'Attachment']:
        """
        Creates an Attachment object from a discord message and an attachment
        
        If the attachment is too big or long, return False
        """
        if length := await Attachment.validateAttachment(message, attachment):
            return Attachment(
                helpers.getMessageHash(message),
                attachment.url,
                length,
                attachment.filename,
                message.author,
                message.created_at.timestamp(),
                message.channel.id,
                message.id
            )
        return False

    async def getAttachmentsWhere(condition: str = "") -> list['Attachment']:
        """
        condition (optional): a SQL condition

        Select all attachments meeting a SQL condition, return as Attachment objects. Variables are:
        
        messageID
        
        url
        
        length
        
        name
        
        author
        
        dob
        
        channel
        
        message
        
        playcount
        """
        if len(condition) == 0:
            results = await helpers.transaction("SELECT * FROM attachments JOIN messages ON attachments.messageID = messages.messageID;")
        else:
            if ";" in condition:
                raise SyntaxError("Semicolons not allowed in conditions")
            condition = condition.replace("messageID", "attachments.messageID")
            results = await helpers.transaction(f"SELECT * FROM attachments JOIN messages ON attachments.messageID = messages.messageID WHERE {condition};")

        return [Attachment(
            result["messageID"],
            result["url"],
            result["length"],
            result["name"],
            result["author"],
            result["dob"],
            result["channel"],
            result["message"],
            result["playcount"]
        ) for result in results]

    async def delete(self) -> None:
        await helpers.transaction(f"DELETE FROM attachments WHERE messageID={self.messageID} and url='{self.url}';")
        del self

    async def addAttachment(self) -> None:
        """
        Add this attachment to the database
        """
        await helpers.transaction(f"INSERT INTO attachments VALUES ({self.messageID}, 0, '{self.url}', {self.length}, '{self.name}');")

    async def increment(self) -> None:
        """
        Increment the playcount of this attachment
        """
        await self.refreshPlaycount()
        pc = self.playcount + 1
        self.playcount = pc
        await helpers.transaction(f"UPDATE attachments SET playcount={pc} WHERE messageID='{self.messageID}' and url='{self.url}';")

    async def validateAttachment(message: discord.Message, attachment: discord.Attachment) -> float | int | bool:
        """
        message: discord.Message
        attachment: discord.Attachment

        Get the length of the audio file, and in doing so ensure the file is not too large or too long

        Returns False if invalid, returns a number if not
        """
        try:
            assert attachment.content_type.startswith("audio/")
            assert attachment.size < config.config["fileSizeLimit"]

            # adapted from https://python-ffmpeg.readthedocs.io/en/latest/examples/querying-metadata/
            ffprobe = FFmpeg(executable="ffprobe").input(
                attachment.url,
                print_format="json", # ffprobe will output the results in JSON format
                show_streams=None,
            )

            media = json.loads(await ffprobe.execute())
            length = float(media['streams'][0]['duration'])

            assert length < config.domains[message.guild.id]["sources"][message.channel.id]["maxLength"]
            return length
        except AssertionError:
            return False
        except Exception as e:
            await helpers.log(traceback.format_exc())
            raise e

    async def refreshPlaycount(self):
        """
        Ensure the playcount is up to date
        """
        if self.playcount:
            return self.playcount
        pc = await helpers.transaction(f"SELECT playcount FROM attachments WHERE messageID={self.messageID} and url='{self.url}';")
        try:
            return pc[0]["playcount"]
        except:
            raise ValueError("This attachment does not exist")

    def __eq__(self, value: 'Attachment') -> bool:
        if not (t := type(value)) == Attachment:
            return False
        return value.uid == self.uid
    async def getAttachments(message: discord.Message | tuple[int, int, int]) -> list['Attachment']:
        """
        Audits all attachment from either a message object or a reference to one (guild, channel, message)
        """
        if type(message) == tuple:
            message = await (config.client.get_channel(message[1])).fetch_message(message[2])
        attachments = []
        for attachment in message.attachments:
            if attachment := await Attachment.fromAttachment(message, attachment):
                attachments.append(attachment)
        return attachments