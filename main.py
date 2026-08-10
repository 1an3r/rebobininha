import asyncio

import discord
from discord import voice_client
from discord.ext import commands
import dotenv
import os
import logging
import yt_dlp
from urllib.parse import urlparse

dotenv.load_dotenv(".env")
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_TOKEN = os.getenv("GUILD_TOKEN")
GUILD = discord.Object(id=GUILD_TOKEN)

discord.utils.setup_logging(root=True)
logger = logging.getLogger("MyBot")
logger.setLevel(logging.DEBUG)

YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


def validate_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False

    for extractor in yt_dlp.extractor.gen_extractors():
        if extractor.suitable(url) and extractor.IE_NAME != "generic":
            return True
    return False

def extract_url_stream(url: str):
    if not validate_url(url):
        raise ValueError("Invalid URL provided in the play command")

    info_dict = ytdl.extract_info(url, download=False)
    if "entries" in info_dict:
        info_dict = info_dict["entries"][0]
    return info_dict

async def play_next(voice_client: discord.VoiceClient, song_queue: list, ctx: commands.Context):
    if len(song_queue) == 0:
        logger.info("Queue ended. Nothing to do.")
        asyncio.run_coroutine_threadsafe(ctx.send("Acabei de tocar."), ctx.bot.loop)
        asyncio.run_coroutine_threadsafe(ctx.voice_client.disconnect(), ctx.bot.loop)
        return

    next_song = song_queue.pop(0)
    audio_source = discord.FFmpegPCMAudio(next_song['url'], **FFMPEG_OPTIONS)

    def after_callback(error):
        if error:
            logger.error(f"Error while playing: {error}")
        asyncio.run_coroutine_threadsafe(play_next(voice_client, song_queue, ctx), ctx.bot.loop)
        logger.debug(f"Called after_callback")

    voice_client.play(audio_source, after=after_callback)
    asyncio.run_coroutine_threadsafe(ctx.send(f"Tocando agora {next_song['title'] or "!"}"), ctx.bot.loop)

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.queue = []
        self.current_song = None

    async def setup_hook(self) -> None:
        self.tree.copy_global_to(guild=GUILD)
        await self.tree.sync(guild=GUILD)
        logger.debug(f"Setup hook called to sync commands locally using {GUILD_TOKEN} as the local guild")

    async def on_ready(self) -> None:
        logger.info(f"Logged in as {self.user}!")

if __name__ == "__main__":
    try:
        bot = MyBot()

        # THIS ONLY EXISTS HERE FOR A REFERENCE TO THE OLD COMMAND FORMAT
        @bot.tree.command(name="old", description="Antigo formato de comandos")
        async def old(interaction: discord.Interaction):
            await interaction.response.send_message("Este é um formato antigo")

        # PREFER THIS AS A BETTER COMMAND ALTERNATIVE, IT WORKS FOR BOTH PREFIX AND SLASH COMMANDS
        @bot.hybrid_command(name="ping", description="Responde com Pong!")
        async def ping(ctx: commands.Context):
            await ctx.send("Pong!")

        @bot.hybrid_command(name="play", description="Toca uma URL especificada ou coloca ela na fila")
        async def play(ctx: commands.Context, url: str):
            if ctx.author.voice is None:
                await ctx.send("Você deve estar em um canal de voz para usar esse comando!")
                return

            voice_client = ctx.voice_client
            if voice_client is None:
                voice_channel = ctx.author.voice.channel
                voice_client = await voice_channel.connect()
                logger.debug(f"Retrieved voiceClient object after connection {voice_client}")

            try:
                song_data = await asyncio.to_thread(extract_url_stream, url)
                ctx.bot.queue.append(song_data)
                if not voice_client.is_playing():
                    await play_next(voice_client, ctx.bot.queue, ctx)
                elif voice_client.is_playing():
                    await ctx.send(f"Coloquei {song_data['title'] or song_data['url']} na fila!")

            except (Exception, ValueError) as e:
                logger.exception(f"Error processing url: {e}")
                await ctx.send("Não consegui processar essa URL!")

        @bot.hybrid_command(name="fila", description="Mostra a fila de músicas")
        async def fila(ctx: commands.Context):
            tmp_queue = []
            if ctx.voice_client is None or len(ctx.bot.queue) == 0:
                await ctx.send("Não há uma fila para ser mostrada.")
            await ctx.send("FILA:")
            for idx, item in enumerate(ctx.bot.queue):
                await ctx.send(f"{idx+1}: {item['title'] or "Não achei o título :("}")

        @bot.hybrid_command(name="skip", description="Pula a música atual")
        async def skip(ctx: commands.Context):
            if ctx.voice_client.is_playing():
                await ctx.send("Pulando a música atual")
                await ctx.voice_client.stop()
            else:
                await ctx.send("Não há nada tocando para ser pulado")

        @bot.hybrid_command(name="clear", description="Para de tocar e limpa a fila")
        async def clear(ctx: commands.Context):
            if ctx.voice_client.is_playing():
                await ctx.send("Parando de tocar e limpando a fila.")
                ctx.bot.queue.clear()
                await ctx.voice_client.stop()
            else:
                await ctx.send("Não há nada tocando, a fila provavelmente já está limpa")


        bot.run(TOKEN, log_handler=None)

    except ValueError as err:
        logger.exception(f"ValueError exception: {err}")

    except Exception as err:
        logger.exception(f"An unexpected error has occurred: {err}")

