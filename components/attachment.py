"""
Module for code related to the Attachment model
"""

from typing import Union
import discord, json, traceback
from components import config, logging, database
from components.message import getMessageHash, MessageReference
from ffmpeg.asyncio import FFmpeg
import aiohttp
from aiosqlite import Connection

class Attachment:
    """
    Model for Attachments, either from the database or discord

    Attachments are uniquely identified by messageID and url
    """
    def __init__(
            self,
            messageID: int,
            url: str,
            length: int | float,
            name: str,
            author: int,
            dob: int | float,
            channel: int,
            message: int,
            domain: int,
            playcount: int | None = None
        ):
        # composite key: REQUIRED
        self.messageID = messageID
        assert type(messageID) == int
        self.url = url
        assert type(url) == str
        self.uid = hash((messageID, url.split("?")[0]))

        # present when created from attachment or database: we will know these
        self.length = length
        assert type(length) in [int, float]
        self.name = name
        assert type(name) == str
        self.author = author
        assert type(author) == int
        self.dob = dob
        assert type(dob) in [int, float]
        self.channel = channel
        assert type(channel) == int
        self.message = message
        assert type(message) == int
        self.domain = domain
        assert type(domain) == int

        self.messageRef = MessageReference(domain, channel, message)

        # only database
        self.playcount = playcount
        assert type(playcount) in [int, type(None)]

    async def fromAttachment(message: discord.Message, attachment: discord.Attachment) -> Union[bool, 'Attachment']:
        """
        Creates an Attachment object from a discord message and an attachment
        
        If the attachment is too big or long, return False
        """
        if length := await Attachment.validateAttachment(message, attachment):
            return Attachment(
                getMessageHash(message),
                attachment.url,
                length,
                attachment.filename,
                message.author.id,
                message.created_at.timestamp(),
                message.channel.id,
                message.id,
                message.guild.id
            )
        return False

    async def getAttachmentsWhere(condition: str = "", conn: Connection = None) -> list['Attachment']:
        """
        condition (optional): a SQL condition

        conn (optional): an aiosqlite connection

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
        owner = False
        if not conn:
            owner = True
            conn = await database.connection()
        
        if len(condition) == 0:
            results = await database.execute("SELECT * FROM attachments JOIN messages ON attachments.messageID = messages.messageID;", conn)
        else:
            if ";" in condition:
                raise SyntaxError("Semicolons not allowed in conditions")
            condition = condition.replace("messageID", "attachments.messageID")
            results = await database.execute(f"SELECT * FROM attachments JOIN messages ON attachments.messageID = messages.messageID WHERE {condition};", conn)

        if owner:
            await database.finish(conn)

        return [Attachment(
            result["messageID"],
            result["url"],
            result["length"],
            result["name"],
            result["author"],
            result["dob"],
            result["channel"],
            result["message"],
            result["domain"],
            result["playcount"]
        ) for result in results]

    async def delete(self, conn: Connection = None) -> None:
        """
        conn: aiosqlite.Connection to the database (optional)
        Delete this attachment from the database.
        """
        owner = False
        if not conn:
            owner = True
            conn = await database.connection()
        await database.execute(f"DELETE FROM attachments WHERE messageID={self.messageID} and url='{self.url}';", conn)
        await conn.commit()
        if len(await Attachment.getAttachmentsWhere(f"messageID = {self.messageID}", conn)) == 0:
            await self.messageRef.deleteMessage(conn)
        if owner:
            await conn.commit()
        del self

    async def addAttachment(self, conn: Connection = None) -> None:
        """
        conn: aiosqlite.Connection to the database (optional)

        Add this attachment to the database
        """
        owner = False
        if not conn:
            owner = True
            conn = await database.connection()
        await database.execute(f"INSERT INTO attachments VALUES ({self.messageID}, 0, '{self.url}', {self.length}, '{self.name}');", conn)
        if owner:
            await conn.commit()

    async def increment(self, conn: Connection = None) -> None:
        """
        Increment the playcount of this attachment
        """
        owner = False
        if not conn:
            owner = True
            conn = await database.connection()
        await self.refreshPlaycount(conn)
        pc = self.playcount + 1
        self.playcount = pc
        await database.execute(f"UPDATE attachments SET playcount={pc} WHERE messageID='{self.messageID}' and url='{self.url}';", conn)
        if owner:
            await conn.commit()

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
            await logging.log(traceback.format_exc())
            raise e

    async def refreshPlaycount(self, conn: Connection = None) -> int:
        """
        Ensure the playcount in this object is up to date, and return the playcount
        """
        owner = False
        if not conn:
            owner = True
            conn = await database.connection()
        try:
            self.playcount = (await database.execute(f"SELECT playcount FROM attachments WHERE messageID={self.messageID} and url='{self.url}';", conn))[0]["playcount"]
            if owner:
                database.finish(conn)
            return self.playcount
        except:
            raise ValueError("This attachment does not exist")

    def __eq__(self, value: 'Attachment') -> bool:
        if not (t := type(value)) == Attachment:
            return False
        return value.uid == self.uid
    
    async def getAttachments(message: discord.Message) -> list['Attachment']:
        """
        Audits all attachments from a discord.Message object
        """
        attachments = []
        for attachment in message.attachments:
            if attachment := await Attachment.fromAttachment(message, attachment):
                attachments.append(attachment)
        return attachments
    
    async def validCDNURL(self) -> bool:
        """
        Checks if this attachment's URL is still valid. Discord CDN links are only valid for around 24hr.
        """
        async with aiohttp.ClientSession() as session:
            async with session.head(self.url) as got:
                return got.status == 200