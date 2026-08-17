import asyncio
import time
import re
import discord
from discord.ext import commands
import dotenv
import os
import logging
import yt_dlp
from collections import deque

dotenv.load_dotenv(".env")
TOKEN = os.getenv("DISCORD_TOKEN") or "DISCORD_TOKEN"   # In case there isn't a .env file (and close-sourced)
GUILD_TOKEN = os.getenv("GUILD_TOKEN") or "GUILD_TOKEN" # Same as above
GUILD = discord.Object(id=GUILD_TOKEN)
discord.utils.setup_logging(root=True)
logger = logging.getLogger("MyBot")
logger.setLevel(logging.DEBUG)

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

FLAT_YTDL_OPTIONS = {
    'extract_flat': True,
    'skip_download': True,
    'quiet': True,
}

STREAM_YTDL_OPTIONS = {
    'extract_flat': False,
    'skip_download': True,
    'quiet': True,
    'noplaylist': True,
    'format': 'bestaudio/best',
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'web'],
            'skip': ['configs', 'webpage']
        }
    }
}

URL_REGEX = re.compile(
    r'^(https?://)'
    r'([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}'
    r'(:\d+)?(/.*)?$',
    re.IGNORECASE
)

YOUTUBE_REGEX = re.compile(
    r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+$',
    re.IGNORECASE
)

ytdl_flat = yt_dlp.YoutubeDL(FLAT_YTDL_OPTIONS)
ytdl_stream = yt_dlp.YoutubeDL(STREAM_YTDL_OPTIONS)

def format_time(total_seconds: int) -> str:
    """
    takes an amount of seconds (integer)
    returns a formatted time string (either minutes:seconds or hours:minutes:seconds)
    """
    if not total_seconds:
        return "00:00"
    hours = int(total_seconds // 3600)
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"

def validate_url(url: str) -> bool:
    """
    takes an url (str) and validates it through regex returning a bool
    """
    if not url or not isinstance(url, str):
        return False

    if not URL_REGEX.match(url):
        return False

    return True

def extract_url_stream(url: str):
    """
    takes an url (str) and returns the direct stream url from ytdlp
    """
    info_dict = ytdl_stream.extract_info(url, download=False) or {}
    if "entries" in info_dict:
        info_dict = info_dict["entries"][0]
    return info_dict['url']

def extract_metadata(url: str):
    """
    takes an url (str) and returns a dictionary containing title, duration, and the url from ytdlp
    """
    if not validate_url(url):
        raise ValueError(f"Invalid URL: {url}")
    info_dict = ytdl_flat.extract_info(url, download=False) or {}
    if "entries" in info_dict:
        info_dict = info_dict["entries"][0]

    resolved_url = (
        info_dict.get("webpage_url")
        or info_dict.get("url")
        or info_dict.get("original_url")
        or url
    )

    return {
        'title': info_dict.get("title", "Título Desconhecido"),
        'url': resolved_url,
        'duration': info_dict.get("duration", 0)
    }

async def play_next(voice_client: discord.VoiceClient, song_queue: deque, ctx: commands.Context):
    """Receives a VoiceClient object, a Deque, and a Context object and sets the next song to play
    using the queue calling an callback function after current song stopped playing"""
    if len(song_queue) == 0:
        logger.info("Queue ended. Nothing to do.")
        asyncio.run_coroutine_threadsafe(ctx.send("Fila terminada, estou indo embora."), ctx.bot.loop)
        asyncio.run_coroutine_threadsafe(ctx.voice_client.disconnect(), ctx.bot.loop)
        return
    try:
        next_song = song_queue.popleft()
        ctx.bot.current_song = next_song
        next_song_stream_url = await asyncio.to_thread(extract_url_stream, next_song['url'])
        audio_source = discord.FFmpegPCMAudio(next_song_stream_url, **FFMPEG_OPTIONS)
    except (ValueError, Exception) as e:
        logger.exception(f"Error reproducing %s: %s.\nCalling play_next recursively to keep the queue going", next_song['url'], e)
        await ctx.send(f"Erro ao reproduzir a url {next_song['url']}, pulando para o próximo item da fila.")
        await play_next(voice_client, song_queue, ctx)
        return

    def after_callback(error):
        if error:
            logger.error("Error while playing: %s", error)
        asyncio.run_coroutine_threadsafe(play_next(voice_client, song_queue, ctx), ctx.bot.loop)
        logger.debug("Called after_callback")

    voice_client.play(audio_source, after=after_callback)
    ctx.bot.song_start_time = time.time()
    asyncio.run_coroutine_threadsafe(ctx.send(f"Tocando agora {next_song['title'] or "!"}"), ctx.bot.loop)

class MyBot(commands.Bot):
    """bot class, constructor doesn't take arguments."""
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.queue = deque()
        self.current_song = None
        self.song_start_time = None

    async def setup_hook(self) -> None:
        self.tree.copy_global_to(guild=GUILD)
        await self.tree.sync(guild=GUILD)
        logger.debug("Setup hook called to sync commands locally using %s as the local guild", GUILD_TOKEN)

    async def on_ready(self) -> None:
        """
        overwrides on_ready function
        executes when bot runs
        """
        logger.info("Logged in as %s!", self.user)

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
                logger.debug("Retrieved voiceClient object after connection %s", voice_client)

            try:
                song_data = await asyncio.to_thread(extract_metadata, url)
                ctx.bot.queue.append(song_data)
                if not voice_client.is_playing():
                    await play_next(voice_client, ctx.bot.queue, ctx)
                elif voice_client.is_playing():
                    await ctx.send("Coloquei {song_data['title'] or song_data['url']} na fila!")

            except (Exception, ValueError) as e:
                logger.exception("Error processing url: %s", e)
                await ctx.send(f"Não consegui processar essa URL! Erro: {e}")

        @bot.hybrid_command(name="fila", description="Mostra a fila de músicas")
        async def fila(ctx: commands.Context):
            current_title = ctx.bot.current_song['title'] if ctx.bot.current_song else "Nenhuma"
            if not ctx.voice_client or len(ctx.bot.queue) == 0:
                await ctx.send("Não há uma fila para ser mostrada.")
                return
            if not ctx.voice_client.is_playing() or ctx.bot.current_song is None:
                await ctx.send("Não há nada tocando, então não pode haver uma fila.")
                return

            description = "\n".join(f"{idx + 1}. {item['title'] or "Undefined"}" for idx, item in enumerate(ctx.bot.queue))
            duration_seconds = ctx.bot.current_song['duration']
            elapsed = int(time.time() - ctx.bot.song_start_time)
            formatted_time = f"{format_time(elapsed)} | {format_time(duration_seconds)}"
            await ctx.send(f"Tocando agora: {current_title} {formatted_time}\n**FILA ATUAL:**\n{description}")

        @bot.hybrid_command(name="skip", description="Pula a música atual")
        async def skip(ctx: commands.Context):
            if not ctx.voice_client or not ctx.voice_client.is_playing():
                await ctx.send("Não há nada tocando no momento.")
                return
            ctx.voice_client.stop()
            await ctx.send(f"Pulando: {ctx.bot.current_song['title']}")

        @bot.hybrid_command(name="clear", description="Para de tocar e limpa a fila")
        async def clear(ctx: commands.Context):
            if not ctx.voice_client or not ctx.voice_client.is_playing():
                await ctx.send("Não tem nada tocando no memento!")
                return
            if len(ctx.bot.queue) == 0:
                await ctx.send("A fila já está vazia, vou interromper a reprodução atual!")
                ctx.voice_client.stop()
                return
            ctx.bot.queue.clear()
            ctx.voice_client.stop()
            await ctx.send("Limpei a fila e interrompi a reprodução atual.")

        @bot.hybrid_command(name="tocando", description="Mostra dados da reprodução atual")
        async def tocando(ctx: commands.Context):
            if not ctx.voice_client or not ctx.voice_client.is_playing() or not ctx.bot.current_song:
                await ctx.send("Não tem nada tocando no momento!")
                return
            duration_seconds = ctx.bot.current_song['duration']
            elapsed = int(time.time() - ctx.bot.song_start_time)
            formatted_time = f"{format_time(elapsed)} | {format_time(duration_seconds)}"
            await ctx.send(f"Tocando agora: {ctx.bot.current_song['title']} {formatted_time}")

        bot.run(TOKEN, log_handler=None)

    except ValueError as err:
        logger.exception(f"ValueError exception: {err}")

    except Exception as err:
        logger.exception(f"An unexpected error has occurred: {err}")

