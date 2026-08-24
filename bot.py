"""
RADIO VENTANA MAGICA
by James Young (resampler.xyz)
made for The Magic Window
"""

# TODO:
# - Standardise certain datatypes. So far:
#    · "Audios" -> Attachments
#    · Removed AudioHandler
# - Song announcements in channels
# - History reading
#    · On start, check the last n messages
# - Make errors fail loud enough that we know about it
# - Track voting
# - Skipping functions (permissions: who can do this...)
# - Stricter config files and paramaterise things like
#    · Whether to use PCM or Opus
#    · Length and filesize over which attachments are rejected
# - More types of attachments, like soundcloud links
# - Write README


import asyncio, random, discord, traceback
from time import time

from components.attachment import Attachment
import components.helpers as helpers
import components.config as config

FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5','options': '-vn'}

domains = config.config["domains"]

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

async def getMessages() -> list[discord.Message]:
    """
    Retrieve all messages stored in the database. Delete any that no longer exist.
    """
    messages = await helpers.transaction(f"SELECT domain, channel, message FROM messages;")
    messageObjs = []
    for m in messages:
        try:
            message = await helpers.getMessage(m["channel"], m["message"])
            messageObjs.append(message)
        except discord.NotFound:
            uid = helpers.getMessageHash((m["domain"], m["channel"], m["message"]))
            await helpers.transaction(f"DELETE FROM messages WHERE messageID={uid};")
            await helpers.transaction(f"DELETE FROM attachments WHERE messageID={uid};")
        except:
            await helpers.log(traceback.format_exc())

    return messageObjs

async def addMessage(domain, message: discord.Message) -> None:
    """
    message: The message we want to store
    
    Add message to database
    """
    id = helpers.getMessageHash(message)
    await helpers.transaction(f"INSERT INTO messages VALUES ({domain}, {message.channel.id}, {message.id}, {id}, {message.created_at.timestamp()}, {message.author.id});")
    
async def deleteMessage(message: discord.Message | tuple[int, int, int]):
    """
    Delete this message and all its attachment from the database
    """
    id = helpers.getMessageHash(message)
    await helpers.transaction(f"DELETE FROM messages WHERE messageID={id};")
    await helpers.transaction(f"DELETE FROM attachments WHERE messageID={id};")

async def getAudio(domain: int, exclude=None) -> Attachment:
    """
    domain: int - ID of domain we're talking about

    exclude (optional): int - ID of channel we want to avoid
    
    Choose the next song to play
    
    Returns an attachment
    """
    results = await Attachment.getAttachmentsWhere(f"domain = {domain}")
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

    # combine the tracks from the channels and return one of the least penalised
    results = [audio for c in channels for audio in channels[c]]

    # attachment format: [attachment, penalty]
    # results is a list of these

    results.sort(key=lambda x: x[1])
    minPenalty = results[0][1]
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

    # lazy lock on this domain
    if domains[domain]["playing"]:
        pass
    domains[domain]["playing"] = True

    # BEGIN BORING CONFIG
    guild = config.client.get_guild(domain)

    exclude = None
    nextEvent = False

    channelID = domains[domain]["broadcast"]["channel"]
    channel = config.client.get_channel(channelID)
    voice_client = await channel.connect()
    # END BORING CONFIG

    await helpers.log(f"We are connected on {domain}")

    while await hasAudience(channelID):
        try:
            track = await getAudio(domain, exclude=exclude)
            if not track:
                # nothing to do... disappear
                await helpers.log(f"No tracks in database in guild {config.config['name']}")
                break

            await track.increment()
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

            # Tl;dr: PCM is uncompressed, Opus is compressed
            # preload the track, then wait until the previous track finishes
            source = discord.FFmpegOpusAudio(track.url, **FFMPEG_OPTIONS)  # load attachment as audio discord can broadcast
            if nextEvent:
                await nextEvent.wait()

            # if people are still listening, play the next track and create a new event
            if not await hasAudience(channelID):
                break
            nextEvent = asyncio.Event()
            try:
                voice_client.play(source, after = lambda x: nextEvent.set())
            except Exception:
                await helpers.log(f"Track didn't play, {traceback.format_exc()}")
                nextEvent = False
            
            if policy["private"]:
                await channel.edit(status=f"You are listening to {config.config['name']}")
            else:
                await channel.edit(status=f"Now Playing: {author.display_name} - {track.name}")

        except Exception:
            await helpers.log(f"Error in guild {guild.name}")
            await helpers.log(traceback.format_exc())
            break

    # cleanup
    await channel.edit(status="")
    await helpers.log(f"Disconnecting from {guild.name}")
    await voice_client.disconnect()
    domains[domain]["playing"] = False

@config.client.event
async def on_ready():
    await helpers.log(f'RVM has logged in as {config.client.user}')
    await helpers.log(f'Auditing messages in the database...')
    messages = await getMessages()
    for m in messages:
        await rollcall(m)
    for guild in config.client.guilds:
        if guild.id in domains:
            await helpers.log(f"{guild.name} connected and registered")
            if await hasAudience(domains[guild.id]["broadcast"]["channel"]):
                asyncio.run_coroutine_threadsafe(play(guild.id), asyncio.get_event_loop())
        else:
            await helpers.log(f"{guild.name} is not registered")

async def rollcall(message: discord.Message) -> None:
    """
    Compare all known attachments on a message with its actual attachments, and update the database to reflect reality

    Assumes message exists
    """

    # Find the attachments the message actually has
    attachments = await Attachment.getAttachments(message)
    if attachments == []:
        await deleteMessage(message)
        return

    # Find the attachments we think the message has
    prevattachment = await Attachment.getAttachmentsWhere(f"messageID = {helpers.getMessageHash(message)}")

    added = []
    for a in attachments:
        found = False
        for e in prevattachment:
            if a == e:
                # the database already knows about this attachment, don't do anything with it
                prevattachment.remove(e)
                found = True
                break
        if not found:
            added.append(a)

    # All attachments left in prevattachment have been deleted
    for a in added:
        await a.addAttachment()
    for d in prevattachment:
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
        await addMessage(domain, message)
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
    await deleteMessage(message)

@config.client.event
async def on_voice_state_update(member, before, after):
    channel = after.channel
    if member.id == config.client.user.id or not channel:
        return
    if not domains[channel.guild.id]["broadcast"]["channel"] == channel.id:
        return
    if not channel.guild.id in domains:
        return
    if not domains[channel.guild.id]["playing"]:
        await play(channel.guild.id)

config.client.run(config.config["key"])
