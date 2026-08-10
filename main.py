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
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_TOKEN = os.getenv("GUILD_TOKEN")
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
    r'^(https?://)'                      # http:// or https://
    r'([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}'   # domain name
    r'(:\d+)?(/.*)?$',                   # optional port and path
    re.IGNORECASE
)

YOUTUBE_REGEX = re.compile(
    r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+$',
    re.IGNORECASE
)

ytdl_flat = yt_dlp.YoutubeDL(FLAT_YTDL_OPTIONS)
ytdl_stream = yt_dlp.YoutubeDL(STREAM_YTDL_OPTIONS)

def format_time(total_seconds: int) -> str:
    if not total_seconds:
        return "00:00"
    hours = int(total_seconds // 3600)
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"

def validate_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False

    if not URL_REGEX.match(url):
        return False

    return True

def extract_url_stream(url: str):
    info_dict = ytdl_stream.extract_info(url, download=False)
    if "entries" in info_dict:
        info_dict = info_dict["entries"][0]
    return info_dict['url']

def extract_metadata(url: str):
    if not validate_url(url):
        raise ValueError(f"Invalid URL: {url}")
    info_dict = ytdl_flat.extract_info(url, download=False)
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
        logger.exception(f"Error reproducing {next_song['url']}: {e}.\nCalling play_next recursively to keep the queue going")
        await ctx.send(f"Erro ao reproduzir a url {next_song['url']}, pulando para o próximo item da fila.")
        await play_next(voice_client, song_queue, ctx)
        return

    def after_callback(error):
        if error:
            logger.error(f"Error while playing: {error}")
        asyncio.run_coroutine_threadsafe(play_next(voice_client, song_queue, ctx), ctx.bot.loop)
        logger.debug(f"Called after_callback")

    voice_client.play(audio_source, after=after_callback)
    ctx.bot.song_start_time = time.time()
    asyncio.run_coroutine_threadsafe(ctx.send(f"Tocando agora {next_song['title'] or "!"}"), ctx.bot.loop)

class MyBot(commands.Bot):
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
                song_data = await asyncio.to_thread(extract_metadata, url)
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

