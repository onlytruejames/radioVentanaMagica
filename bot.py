import asyncio, random, discord, aiosqlite, aiohttp, json
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
    return [
        audio["channel"],
        audio["message"],
        audio["index"]
    ]

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
        except Exception as e:
            await self.delete(audio)
            return False
    
    #TODO: Update database schema
    async def add(self, audio):
        async with aiosqlite.connect("tracks.db") as db:
            await db.execute(f"INSERT INTO tracks (domain, channel, message, length, playing, fileIndex, dob, playcount) VALUES ({self.domain}, {audio['channel']}, {audio['message']}, {audio['length']}, 1, {audio['index']}, {audio['dob']}, {audio['playcount']});")
            await db.commit()
    
    async def delete(self, audio):
        async with aiosqlite.connect("tracks.db") as db:
            await db.execute(f"UPDATE tracks SET playing=0 WHERE domain={self.domain} AND channel={audio['channel']} AND message={audio['message']} AND fileIndex={audio['index']};")
            await db.commit()
    
    async def increment(self, audio):
        async with aiosqlite.connect("tracks.db") as db:
            await db.execute(f"UPDATE tracks SET playcount={audio['playcount'] + 1} where domain={self.domain} AND channel={audio['channel']} AND message={audio['message']} AND fileIndex={audio['index']};")
            await db.commit()

    def list(self):
        return "\n".join([str(a) for a in self.audios])

    async def getAudio(self, exclude=None):
        "list of recent audio messages"
        async with aiosqlite.connect("tracks.db") as db:
            cur = await db.execute(f"SELECT channel, message, length, fileIndex, dob, playcount FROM tracks WHERE domain={self.domain} AND playing=1;")
            results = await cur.fetchall()
        channels = {}
        history = domains[self.domain]["history"]
        bin = {}
        for result in results:
            audio = {
                "channel": result[0],
                "message": result[1],
                "length": result[2],
                "index": result[3],
                "dob": result[4],
                "playcount": result[5],
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
        #assert length < 600
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
            if await equalTracks(track, prevTrack) or not await audios.checkAudio(track):
                continue
            message = await audios.getMessage(
                track["channel"],
                track["message"]
            )
            await audios.increment(track)
            uid = audioUID(track)
            domains[domain]["history"] = [uid] + domains[domain]["history"][:-1]
            policy = domains[domain]["sources"][track["channel"]]
            exclude = None
            if policy["isolated"]:
                exclude = track["channel"]
            attachment = message.attachments[track["index"]]
            source = discord.FFmpegPCMAudio(attachment.url, **FFMPEG_OPTIONS)  # load attachment as audio discord can broadcast
            if nextEvent:
                await nextEvent.wait()
            if not await hasAudience(channelID):
                break
            nextEvent = asyncio.Event()
            if await audios.checkAudio(track):
                voice_client.play(source, after = lambda x: nextEvent.set())
            else:
                nextEvent = False
            if policy["private"]:
                await channel.edit(status=f"You are listening to {config['name']}")
            else:
                await channel.edit(status=f"Now Playing: {message.author.display_name} - {attachment.filename}")
            #tsince = time()
            #tsleep = track["length"]
        except Exception as e:
            await log(f"Error in guild {guild.name}: {e.with_traceback(None)}")
            break
    await log(f"Disconnecting from {guild.name}")
    await voice_client.disconnect()
    domains[domain]["playing"] = False

@client.event
async def on_ready():
    await log(f'RVM has logged in as {client.user}')
    for guild in client.guilds:
        await log(domains)
        if guild.id in domains:
            await log(f"{guild.name} connected and registered")
            if await hasAudience(domains[guild.id]["broadcast"]["channel"]):
                asyncio.run_coroutine_threadsafe(play(guild.id), asyncio.get_event_loop())
        else:
            await log(f"{guild.name} is not registered")

@client.event
async def on_message(message):
    if not message.guild.id in domains:
        return
    if not message.channel.id in domains[domain := message.guild.id]["sources"]:
        return
    audios = AudioHandler(domain)
    for i, attachment in enumerate(message.attachments):
        if length := await validateAttachment(attachment):
            audioData = {
                "message": message.id,
                "channel": message.channel.id,
                "length": length,
                "index": i,
                "dob": message.created_at.timestamp(),
                "playcount": 0
            }
            #await message.channel.send(audioData)
            await audios.add(audioData)
    if not domains[domain]["playing"]:
        await play(domain)

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
