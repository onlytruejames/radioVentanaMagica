"""
RADIO VENTANA MAGICA
by James Young (resampler.xyz)
made for The Magic Window

# TODO:
 - Song announcements in channels
 - History reading
 - Make errors fail loud enough that we know about it
 - Track voting
 - Skipping functions (who can do this...)
"""

import asyncio, random, discord, aiosqlite, aiohttp, json, traceback
from time import time
from pydub import AudioSegment
from io import BytesIO

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

client = discord.Client(intents=intents)

FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5','options': '-vn'}

with open("config.json", "r") as file:
    config = json.load(file)

async def log(msg):
    # send logs to discord webhook or print
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(config["log"], data={"content": msg})
    except:
        print(msg)

domains = config["domains"]

# casting to the right datatypes
tmpDomains = {}
for domain in domains:
    tmp = domains[domain]
    tmpSources = {}
    for source in tmp["sources"]:
        tmpSources[int(source)] = tmp["sources"][source]
    tmp["sources"] = tmpSources
    tmpDomains[int(domain)] = tmp

domains = tmpDomains

def audioUID(audio):
    return hash((audio["messageID"], audio["url"]))

def getHash(message: discord.Message | tuple):
    """Takes message object or a tuple (guild, channel, message)"""
    if type(message) == discord.Message:
        return hash((message.guild.id, message.channel.id, message.id))
    elif type(message) == tuple:
        return hash(message)
    raise ValueError("Invalid message format in getHash")

async def transaction(statement):
    async with aiosqlite.connect("audio.db") as db:
        cur = await db.execute(statement)
        results = await cur.fetchall()
        await db.commit()
    return results

async def getMessages():
    messages = await transaction(f"SELECT domain, channel, message FROM messages;")
    messageObjs = []
    for m in messages:
        try:
            message = await client.get_channel(m[1]).fetch_message(m[2])
            messageObjs.append(message)
        except discord.NotFound:
            uid = getHash(m)
            await transaction(f"DELETE FROM messages WHERE messageID={uid};")
            await transaction(f"DELETE FROM attachments WHERE messageID={uid};")
        except:
            await log(traceback.format_exc())

    return messageObjs

class AudioHandler:
    def __init__(self, domain: str):
        self.domain = domain

    async def getMessage(self, channel: int, message: int):
        try:
            c = client.get_channel(channel)
            m = await c.fetch_message(message)
            return m
        except Exception as e:
            return False

    async def checkAudio(self, audio) -> bool:
        try:
            message = await self.getMessage(audio["channel"], audio["message"])
            assert message
            return True
        except Exception:
            await log(traceback.format_exc())
            await self.deleteAudio(audio)
            return False

    async def addMessage(self, message: discord.Message):
        id = getHash(message)
        await transaction(f"INSERT INTO messages VALUES ({self.domain}, {message.channel.id}, {message.id}, {id}, {message.created_at.timestamp()}, {message.author.id});")

    async def addAudio(self, audio: dict):
        await transaction(f"INSERT INTO attachments VALUES ({audio['messageID']}, 0, '{audio['url']}', {audio['length']}, '{audio['name']}');")

    async def deleteAudio(self, audio: dict):
        await transaction(f"DELETE FROM attachments WHERE messageID={audio['messageID']} and url='{audio['url']}';")

    async def increment(self, audio):
        await transaction(f"UPDATE attachments SET playcount={audio['playcount'] + 1} WHERE messageID='{audio['messageID']}' and url='{audio['url']}';")

    async def getFromMessage(self, messageID: int):
        results = await transaction(f"SELECT messageID, playcount, url, length FROM attachments WHERE messageID={messageID};")
        return [{
            "messageID": r[0],
            "playcount": r[1],
            "url": r[2],
            "length": r[3]
        } for r in results]
    
    async def deleteMessage(self, message: discord.Message | tuple[int, int, int]):
        id = getHash(message)
        await transaction(f"DELETE FROM messages WHERE messageID={id};")
        await transaction(f"DELETE FROM attachments WHERE messageID={id};")

    def list(self):
        return "\n".join([str(a) for a in self.audios])

    async def getAudio(self, exclude=None):
        "Get one of the recent audio messages"
        results = await transaction(f"SELECT messages.channel, messages.messageID, attachments.length, messages.dob, attachments.playcount, attachments.url, messages.author, attachments.name FROM attachments JOIN messages ON attachments.messageID = messages.messageID WHERE messages.domain = {self.domain};")
        if len(results) == 0:
            return None
        channels = {}
        history = domains[self.domain]["history"]
        bin = {}
        for result in results:
            audio = {
                "channel": result[0],
                "messageID": result[1],
                "length": result[2],
                "dob": result[3],
                "playcount": result[4],
                "url": result[5],
                "author": result[6],
                "name": result[7],
                "penalty": 0
            }
            policy = domains[self.domain]["sources"][audio["channel"]]

            # The last thing we want is the same song playing twice in a row
            # We don't want an excluded channel twice in a row
            # We want to give more plays to underplayed tracks

            # Moribund tracks may be played with no penalty if there's not enough new tracks

            # penalise repeats
            uid = audioUID(audio)
            if uid in history:
                penalty = 1
                for t in reversed(history):
                    penalty *= 1.5
                    if t == uid:
                        audio["penalty"] += penalty

            # add new channel to check
            if not audio["channel"] in channels:
                channels[audio["channel"]] = []
                bin[audio["channel"]] = []

            # check if the channel is being excluded, penalise if so
            if audio["channel"] == exclude:
                audio["penalty"] += 10

            # check if the audio is moribund, try and kill if so
            if ((age := (time() - audio["dob"]) > policy["ttl"]) and (policy["ttl"] >= 0)):
                bin[audio["channel"]].append([audio, age])

            # add to playlist
            channels[audio["channel"]].append(audio)

        # delete what you can
        for c in channels:
            deleted = []
            policy = domains[self.domain]["sources"][c]
            if excess := (len(channels[c]) - policy["prefSize"]) > 0:
                # delete oldest tunes
                bin.sort(key = lambda x: x[1], reversed=True)
                async for track in bin[:min(len(bin), excess)]:
                    await self.delete(track)
                    deleted.append(audioUID(track))
            channels[c] = [track for track in channels[c] if not audioUID(track) in deleted]

        # select least played tracks from each channel
        for c in channels:
            policy = domains[self.domain]["sources"][c]
            channels[c].sort(key=lambda x: x["playcount"])
            if len(channels[c]) > policy["sampleSize"]:
                channels[c] = channels[c][:policy["sampleSize"]]

        # combine the tracks from the channels and return one of the least penalised
        results = [audio for c in channels for audio in channels[c]]
        results.sort(key=lambda x: x["penalty"])
        minPenalty = results[0]["penalty"]
        results = [r for r in results if r["penalty"] == minPenalty]
        return random.choice(results)

async def validateAttachment(attachment):
    try:
        assert attachment.content_type.startswith("audio/")
        assert attachment.size < 100000000
        
        stream = BytesIO()
        await attachment.save(stream)
        seg = AudioSegment.from_file(stream)
        length = seg.duration_seconds
        assert length < 600
        return length
    except Exception as e:
        return False

async def equalTracks(t1, t2):
    if not t1 or not t2:
        return False
    return (
        t1["channel"] == t2["channel"] and
        t1["message"] == t2["message"] and
        t1["index"] == t2["index"]
    )

async def hasAudience(channel) -> bool:
    channel = client.get_channel(channel)
    if len(channel.voice_states) == 0:
        return False
    return not (len(channel.voice_states) == 1 and client.user.id in channel.voice_states)

async def play(domain):
    if domains[domain]["playing"]:
        pass
    domains[domain]["playing"] = True
    guild = client.get_guild(domain)
    exclude = None
    prevTrack = None
    nextEvent = False
    channelID = domains[domain]["broadcast"]["channel"]
    channel = client.get_channel(channelID)
    audios = AudioHandler(domain)
    voice_client = await channel.connect()
    await log(f"We are connected on {domain}")
    while await hasAudience(channelID):
        try:
            track = await audios.getAudio(exclude=exclude)
            if not track:
                await log(f"No tracks in database in guild {config['name']}")
                break
            if await equalTracks(track, prevTrack):
                continue
            await audios.increment(track)
            uid = audioUID(track)
            author = client.get_user(track["author"])
            if not author:
                author = await client.fetch_user(track["author"])
            domains[domain]["history"] = [uid] + domains[domain]["history"][:-1]
            policy = domains[domain]["sources"][track["channel"]]
            exclude = None
            if policy["isolated"]:
                exclude = track["channel"]
            source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTIONS)  # load attachment as audio discord can broadcast
            if nextEvent:
                await nextEvent.wait()
            if not await hasAudience(channelID):
                break
            nextEvent = asyncio.Event()
            try:
                voice_client.play(source, after = lambda x: nextEvent.set())
            except Exception as e:
                await log(f"Track didn't play, next...")
                nextEvent = False
            if policy["private"]:
                await channel.edit(status=f"You are listening to {config['name']}")
            else:
                await channel.edit(status=f"Now Playing: {author.display_name} - {track['name']}")

        except Exception:
            await log(f"Error in guild {guild.name}")
            await log (traceback.format_exc())
            break
    await log(f"Disconnecting from {guild.name}")
    await voice_client.disconnect()
    domains[domain]["playing"] = False

@client.event
async def on_ready():
    await log(f'RVM has logged in as {client.user}')
    await log(f'Auditing messages in the database...')
    messages = await getMessages()
    for m in messages:
        await rollcall(m)
    for guild in client.guilds:
        if guild.id in domains:
            await log(f"{guild.name} connected and registered")
            if await hasAudience(domains[guild.id]["broadcast"]["channel"]):
                asyncio.run_coroutine_threadsafe(play(guild.id), asyncio.get_event_loop())
        else:
            await log(f"{guild.name} is not registered")

async def scan(message: discord.Message | tuple[int, int, int]):
    """
    Audits all attachments from either a message object or a reference to one (guild, channel, message)
    """
    if type(message) == tuple:
        message = await (await client.get_channel(message[1])).fetch_message(message[2])
    attachments = []
    id = getHash(message)
    for i, attachment in enumerate(message.attachments):
        if length := await validateAttachment(attachment):
            audioData = {
                "messageID": id,
                "length": length,
                "url": attachment.url,
                "playcount": 0,
                "name": attachment.filename,
                "author": message.author.id
            }
            attachments.append(audioData)
    return attachments

async def rollcall(message):
    """
    Compare all known attachments on a message with its actual attachments, and update the database to reflect reality
    Assumes message exists
    """

    # Find the attachments the message actually has
    attachments = await scan(message)
    if attachments == []:
        
        return
    audios = AudioHandler(message.guild.id)

    # Find the attachments we think the message has
    prevAttachments = await audios.getFromMessage(getHash(message))

    added = []
    for a in attachments:
        found = False
        for e in prevAttachments:
            if a["url"] == e["url"]:
                # the database already knows about this attachment, don't do anything with it
                prevAttachments.remove(e)
                found = True
                break
        if not found:
            added.append(a)

    # All attachments left in prevAttachments have been deleted
    for a in added:
        await audios.addAudio(a)
    for d in prevAttachments:
        await audios.deleteAudio(d)

@client.event
async def on_message(message):
    if not (domain := message.guild.id) in domains:
        return
    if not message.channel.id in domains[domain]["sources"]:
        return
    # get all audios associated with the message
    attachments = await scan(message)
    if attachments == []:
        return # none - do nothing
    else:
        # add all audios
        audios = AudioHandler(domain)
        await audios.addMessage(message)
        for a in attachments:
            await audios.addAudio(a)
    if not domains[domain]["playing"]:
        await play(domain)

@client.event
async def on_message_edit(before, after):
    if not (domain := after.guild.id) in domains:
        return
    if not after.channel.id in domains[domain]["sources"]:
        return
    # now just need to check if there's any changes
    # pass over to the rollcall function
    await rollcall(after)

@client.event
async def on_message_delete(message):
    if not (domain := message.guild.id) in domains:
        return
    if not message.channel.id in domains[domain]["sources"]:
        return
    audios = AudioHandler(domain)
    await audios.deleteMessage(message)

@client.event
async def on_voice_state_update(member, before, after):
    channel = after.channel
    if member.id == client.user.id or not channel:
        return
    if not domains[channel.guild.id]["broadcast"]["channel"] == channel.id:
        return
    if not channel.guild.id in domains:
        return
    if not domains[channel.guild.id]["playing"]:
        await play(channel.guild.id)

client.run(config["key"])
