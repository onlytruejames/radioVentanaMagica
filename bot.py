"""
RADIO VENTANA MAGICA
by James Young (resampler.xyz)
made for The Magic Window
"""

# TODO:
# - Memory/Time optimisations; maybe use multiprocessing for some things...
# - Enforce standards on some data structures, like references to messages
# - Song announcements in channels
# - History reading
#    · On start, check the last n messages
#    · Make sure not to include messages that have been deleted cause they were played to death
# - Make errors fail loud enough that we know about it
# - Track voting
# - Skipping functions (permissions: who can do this...)
# - More types of attachments, like soundcloud links
# - Write README
# - Bug found: Attachment URLs are signed wth a token that changes every 24hr. This resets playcounts.
# - Cleanup config file


import asyncio, random, discord, traceback
from time import time

from components.attachment import Attachment
from components import database
from components import config
from components import logging
from components.message import MessageReference, getMessageHash

# Preselect the audio source generator
# Tl;dr: PCM is uncompressed, Opus is compressed
FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5','options': '-vn'}
match config.config["broadcastEncoding"]:
    case "opus":
        loadTrack = lambda trackURL: discord.FFmpegOpusAudio(trackURL, **FFMPEG_OPTIONS)
    case "pcm":
        loadTrack = lambda trackURL: discord.FFmpegPCMAudio(trackURL, **FFMPEG_OPTIONS)
    case _:
        raise ValueError(f"Unsupported broadcast encoding: {config.config['broadcastEncoding']}")

domains = config.domains

async def getMessages() -> list[discord.Message]:
    """
    Retrieve all messages stored in the database. Delete any that no longer exist.
    """
    messages = await database.transaction(f"SELECT domain, channel, message FROM messages;")
    messageObjs = []
    for m in messages:
        ref = MessageReference(m["domain"], m["channel"], m["message"])
        # Deleting is handled by toDiscordMessage
        if msg := await ref.toDiscordMessage():
            messageObjs.append(msg)
    return messageObjs

async def addMessage(message: discord.Message) -> None:
    """
    message: The message we want to store
    
    Add message to database
    """
    id = getMessageHash(message)
    await database.transaction(f"INSERT INTO messages VALUES ({message.guild.id}, {message.channel.id}, {message.id}, {id}, {message.created_at.timestamp()}, {message.author.id});")

async def getAudio(domain: int, exclude=None) -> Attachment:
    """
    domain: int - ID of domain we're talking about

    exclude (optional): int - ID of channel we want to avoid
    
    Choose the next song to play
    
    Returns an attachment
    """
    results: list[Attachment] = await Attachment.getAttachmentsWhere(f"domain = {domain}")
    if len(results) == 0:
        return None
    channels = {}
    history = domains[domain]["history"]
    bin = {}
    for attachment in results:
        policy = domains[domain]["sources"][attachment.channel]
        penaltySum = 0

        # The last thing we want is the same song playing twice in a row
        # We don't want an excluded channel twice in a row
        # We want to give more plays to underplayed tracks
        # Moribund tracks may be played with no penalty if there's not enough new tracks
        
        # penalise repeats
        uid = attachment.uid
        if uid in history:
            penalty = 1
            for t in reversed(history):
                penalty *= 1.5
                if t == uid:
                    penaltySum += penalty

        # add new channel to check
        if not attachment.channel in channels:
            channels[attachment.channel] = []
            bin[attachment.channel] = []

        # check if the channel is being excluded, penalise if so
        if attachment.channel == exclude:
            penaltySum += 10

        # check if the audio is moribund, try and kill if so
        if ((age := (time() - attachment.dob) > policy["ttl"]) and (policy["ttl"] >= 0)):
            bin[attachment.channel].append([attachment, age])

        # add to playlist
        channels[attachment.channel].append([attachment.uid, attachment, penaltySum])

    # attachment format: [uid, attachment, penalty]
    # channels is a list of these

    # delete what you can
    for c in channels:
        deleted = []
        policy = domains[domain]["sources"][c]
        if excess := (len(channels[c]) - policy["prefSize"]) > 0:
            # delete oldest tunes
            bin.sort(key = lambda x: x[1], reversed=True)
            async for track in bin[:min(len(bin), excess)]:
                deleted.append(track.uid)
                await track.delete()
        channels[c] = [track[1:] for track in channels[c] if not track[0] in deleted]

    # select least played tracks from each channel
    for c in channels:
        policy = domains[domain]["sources"][c]
        channels[c].sort(key=lambda x: x[0].playcount)
        if len(channels[c]) > policy["sampleSize"]:
            channels[c] = channels[c][:policy["sampleSize"]]

    # we now have the sampleSize least played tracks and their penalties
    
    # combine the tracks from the channels and return one of the least penalised
    results = [audio for c in channels for audio in channels[c]]

    # attachment format: [attachment, penalty]
    # results is a list of these

    minPenalty = min(results, key=lambda x: x[1])[1]
    results = [r[0] for r in results if r[1] == minPenalty]
    return random.choice(results)

async def hasAudience(channel: int) -> bool:
    """
    channel: int - The channel id of the voice channel

    Check if anyone's in the vc
    """
    channel = config.client.get_channel(channel)
    if len(channel.voice_states) == 0:
        return False
    return not (len(channel.voice_states) == 1 and config.client.user.id in channel.voice_states)

async def play(domain: int):
    """
    domain: guild id as int

    This is more or less the mainloop
    """
    channelID = domains[domain]["broadcast"]["channel"]

    # lazy lock on this domain
    if domains[domain]["playing"] or not await hasAudience(channelID):
        pass
    domains[domain]["playing"] = True

    # BEGIN BORING CONFIG
    guild = config.client.get_guild(domain)

    exclude = None
    nextEvent = False

    channel = config.client.get_channel(channelID)
    voice_client = await channel.connect()
    # END BORING CONFIG

    await logging.log(f"We are connected on {domain}")

    while await hasAudience(channelID):
        try:
            track = await getAudio(domain, exclude=exclude)
            if not track:
                # nothing to do... disappear
                await logging.log(f"No tracks in database in guild {config.config['name']}")
                break

            ref = MessageReference(domain, track.channel, track.message)
            if not await track.validCDNURL():
                await rollcall(ref)
                continue

            # see if we have the user in memory
            author = config.client.get_user(track.author)
            if not author:
                # user is not in memory, API call :cry:
                author = await config.client.fetch_user(track.author)

            # add this track to the front of the history
            domains[domain]["history"] = [track.uid] + domains[domain]["history"][:-1]

            # how are we treating the channel this came from
            policy = domains[domain]["sources"][track.channel]
            exclude = None
            if policy["isolated"]:
                exclude = track.channel

            # preload the track, then wait until the previous track finishes. if it fails, rollcall its message and choose a different track
            try:
                source = loadTrack(track.url)
            except:
                await rollcall(ref)
            if nextEvent:
                await nextEvent.wait()

            # if people are still listening, play the next track and create a new event
            if not await hasAudience(channelID):
                break
            nextEvent = asyncio.Event()
            try:
                voice_client.play(source, after = lambda x: nextEvent.set())
                await track.increment()
            except Exception:
                await logging.log(f"Track didn't play, {traceback.format_exc()}")
                nextEvent = False
            
            if policy["private"]:
                await channel.edit(status=f"You are listening to {config.config['name']}")
            else:
                await channel.edit(status=f"Now Playing: {author.display_name} - {track.name}")

        except Exception:
            await logging.log(f"Error in guild {guild.name}")
            await logging.log(traceback.format_exc())
            break

    # cleanup
    await channel.edit(status="")
    await logging.log(f"Disconnecting from {guild.name}")
    await voice_client.disconnect()
    domains[domain]["playing"] = False

    # clear history to not mess with the next person that listens
    domains[domain]["history"] = [0 for a in domains[domain]["history"]]

@config.client.event
async def on_ready():
    await logging.log(f'RVM has logged in as {config.client.user}')
    await logging.log(f'Auditing messages in the database...')
    messages = await getMessages()
    for m in messages:
        await rollcall(m)
    for guild in config.client.guilds:
        if guild.id in domains:
            await logging.log(f"{guild.name} connected and registered")
            if await hasAudience(domains[guild.id]["broadcast"]["channel"]):
                asyncio.run_coroutine_threadsafe(play(guild.id), asyncio.get_event_loop())
        else:
            await logging.log(f"{guild.name} is not registered")

async def rollcall(message: discord.Message | MessageReference) -> None:
    """
    message: discord.Message object MessageReference object

    Compare all known attachments on a message with its actual attachments, and update the database to reflect reality

    Deletes message from database if it does not exist
    """

    # Ensure we have both a message and a reference to it
    if type(message) == MessageReference:
        ref = message
        message = await ref.toDiscordMessage()
        if not message:
            return
    else:
        ref = MessageReference.fromDiscordMessage(message)

    # Find the attachments the message actually has
    attachments = await Attachment.getAttachments(message)
    if attachments == []:
        # delete message AND its attachments
        await ref.deleteMessage()
        return

    # Find the attachments the database thinks the message has
    prevattachments = await Attachment.getAttachmentsWhere(f"messageID = {ref.hash}")

    added = []
    for current in attachments:
        found = False
        for prev in prevattachments:
            # check if we know about this attachment, updating the attachment hash if needed
            if prev == current:
                # database knows about this attachment
                if prev.url != current.url:
                    # update to new url
                    await database.transaction(f"UPDATE attachments SET url='{current.url}' WHERE url='{prev.url}';")
                prevattachments.remove(prev)
                found = True
                break
        if not found:
            added.append(current)

    # All attachments left in prevattachment have been deleted
    for current in added:
        await current.addAttachment()
    for d in prevattachments:
        await d.delete()

@config.client.event
async def on_message(message):
    if not (domain := message.guild.id) in domains:
        return
    if not message.channel.id in domains[domain]["sources"]:
        return
    # get all audios associated with the message
    attachment = await Attachment.getAttachments(message)
    if attachment == []:
        return # none - do nothing
    else:
        # add all audios
        await addMessage(message)
        for a in attachment:
            await a.addAttachment()
    if not domains[domain]["playing"]:
        await play(domain)

@config.client.event
async def on_message_edit(before, after):
    if not (domain := after.guild.id) in domains:
        return
    if not after.channel.id in domains[domain]["sources"]:
        return
    # now just need to check if there's any changes
    # pass over to the rollcall function
    await rollcall(after)

@config.client.event
async def on_message_delete(message):
    if not (domain := message.guild.id) in domains:
        return
    if not message.channel.id in domains[domain]["sources"]:
        return
    msg = MessageReference.fromDiscordMessage(message)
    await msg.deleteMessage()

@config.client.event
async def on_voice_state_update(member, before, after):
    channel = after.channel
    if member.id == config.client.user.id or not channel:
        return
    if not channel.guild.id in domains:
        return
    if not domains[channel.guild.id]["broadcast"]["channel"] == channel.id:
        return
    if not domains[channel.guild.id]["playing"]:
        await play(channel.guild.id)

config.client.run(config.config["key"])
