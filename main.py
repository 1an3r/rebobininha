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
logger = logging.getLogger("Rebobininha")
logger.setLevel(logging.DEBUG)

FFMPEG_OPTIONS = {
    'before_options': ( '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 '
                        '-user_agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"'
),
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
            'player_client': ['tv_embedded', 'web_embedded', 'web'],
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

def extract_metadata(query: str):
    """
    takes an url (str) and returns a dictionary containing title, duration, and the url from ytdlp
    """
    if not validate_url(query):
        # raise ValueError(f"Invalid URL: {url}")
        search_target = "ytsearch1:" + query
    else:
        search_target = query.split("&")[0]

    logger.info("Extracting metadata for %s", search_target)
    info_dict = ytdl_flat.extract_info(search_target, download=False) or {}
    if "entries" in info_dict and info_dict["entries"]:
        info_dict = info_dict["entries"][0]

    if not info_dict:
        raise ValueError("Não foi possível encontrar resultados para %s", query)

    resolved_url = (
        info_dict.get("webpage_url")
        or info_dict.get("url")
        or info_dict.get("original_url")
        or search_target
    )

    return {
        'title': info_dict.get("title", "Título Desconhecido"),
        'url': resolved_url,
        'duration': info_dict.get("duration", 0)
    }

def search_multiple(query: str, limit: int = 5) -> list[dict]:
    """Searches for a query in ytdlp with a limit defaulted to 5"""
    search_target = f"ytsearch{limit}:{query}"
    info_dict = ytdl_flat.extract_info(search_target, download=False) or {}

    entries = info_dict.get("entries", [])
    results = []

    for item in entries:
        if not item:
            continue
        resolved_url = (
                item.get("webpage_url")
                or item.get("url")
                or item.get("original_url")
        )
        results.append({
            'title': item.get("title", "Título Desconhecido"),
            'url': resolved_url,
            'duration': item.get("duration", 0)
        })

    return results

async def connect_to_vc(ctx):
    """Receives the ctx and connects to the voice channel tied to the ctx.author"""
    voice_channel = ctx.author.voice.channel
    voice_client = await voice_channel.connect()
    logger.debug("Retrieved voiceClient object after connection %s", voice_client)
    return voice_client

async def play_next(voice_client: discord.VoiceClient, song_queue: deque, ctx: commands.Context):
    """Receives a VoiceClient object, a Deque, and a Context object and sets the next song to play
    using the queue calling a callback function after current song stopped playing"""
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
        """Calls back after voice_client.play(), in a threadsafe coroutine to keep the bot playing without relying on timing loops"""
        if error:
            logger.error("Error while playing: %s", error)
        asyncio.run_coroutine_threadsafe(play_next(voice_client, song_queue, ctx), ctx.bot.loop)
        logger.debug("Called after_callback")

    asyncio.run_coroutine_threadsafe(ctx.send(f"Tocando agora: **{next_song['title'] or "!"}** ({format_time(next_song['duration'])})"), ctx.bot.loop)
    voice_client.play(audio_source, after=after_callback)
    ctx.bot.song_start_time = time.time()

class Rebobininha(commands.Bot):
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
        bot = Rebobininha()

        # THIS ONLY EXISTS HERE FOR A REFERENCE TO THE OLD COMMAND FORMAT
        @bot.tree.command(name="old", description="Antigo formato de comandos")
        async def old(interaction: discord.Interaction):
            await interaction.response.send_message("Este é um formato antigo")

        # PREFER THIS AS A BETTER COMMAND ALTERNATIVE, IT WORKS FOR BOTH PREFIX AND SLASH COMMANDS
        @bot.hybrid_command(name="ping", description="Responde com Pong!")
        async def ping(ctx: commands.Context):
            """Answers with pong"""
            await ctx.send("Pong!")

        @bot.hybrid_command(name="play", description="Toca uma URL especificada ou coloca ela na fila")
        async def play(ctx: commands.Context, *, query: str):
            """Receives a query in discord chat and plays the song.
            Query can be either url or 1 or more keyword, in the case of a keyword (or group) it searches and plays the first result."""
            if ctx.author.voice is None:
                await ctx.send("Você deve estar em um canal de voz para usar esse comando!")
                return

            voice_client = ctx.voice_client
            if voice_client is None:
                voice_client = await connect_to_vc(ctx)

            try:
                song_data = await asyncio.to_thread(extract_metadata, query)
                ctx.bot.queue.append(song_data)
                if not voice_client.is_playing():
                    await play_next(voice_client, ctx.bot.queue, ctx)
                elif voice_client.is_playing():
                    await ctx.send(f"Coloquei **{song_data['title'] or song_data['url']}** ({format_time(song_data['duration'])}) na fila!")

            except (Exception, ValueError) as e:
                logger.exception("Error processing url: %s", e)
                await ctx.send(f"Não consegui processar essa URL! Erro: {e}")

        @bot.hybrid_command(name="search", description="Procura por uma keyword e mostra os resultados para colocar na fila")
        async def search(ctx: commands.Context, *, query: str):
            """Searches yt for a query (can be one or more keywords) and displays N results, waiting for user to pick one and reproduces picked result."""
            if ctx.author.voice is None:
                await ctx.send("Você deve estar em um canal de voz para usar esse comando!")
                return

            voice_client = ctx.voice_client
            if voice_client is None:
                voice_client = await connect_to_vc(ctx)

            results = await asyncio.to_thread(search_multiple, query, 5)
            if not results:
                await ctx.send(f"Nenhum resultado encontrado para {query}")
                return

            options_text = "\n".join(
                f"**{idx + 1}.** {result['title']} ({format_time(result['duration'])})"
                for idx, result in enumerate(results)
            )

            prompt_message = await ctx.send(
                f"🔎 **Resultados para:** `{query}`\n"
                f"{options_text}\n\n"
                f"Digite o **número (1-{len(results)})** correspondente ou `cancelar`:"
            )

            def check(msg: discord.Message) -> bool:
                return (
                    msg.author == ctx.author
                    and msg.channel == ctx.channel
                    and (msg.content.isdigit() or msg.content.lower() == "cancelar")
                )

            try:
                user_response = await ctx.bot.wait_for("message", check=check, timeout=60.0)
            except asyncio.TimeoutError:
                await ctx.send("⏰ Tempo esgotado! Operação cancelada.")
                return

            if user_response.content.lower() == "cancelar":
                await ctx.send("Busca cancelada.")
                return

            selection_index = int(user_response.content) - 1
            if not (0 <= selection_index < len(results)):
                await ctx.send("Número inválido. Operação cancelada.")
                return

            selected_song = results[selection_index]
            ctx.bot.queue.append(selected_song)
            if not voice_client.is_playing():
                await play_next(voice_client, ctx.bot.queue, ctx)
            else:
                await ctx.send(f"Coloquei **{selected_song['title']}** ({format_time(selected_song['duration'])}) na fila!")

        @bot.hybrid_command(name="queue", description="Mostra a fila de músicas")
        async def queue(ctx: commands.Context):
            """Shows the current queue if there is one."""
            current_title = ctx.bot.current_song['title'] if ctx.bot.current_song else "Nenhuma"
            if not ctx.voice_client or len(ctx.bot.queue) == 0:
                await ctx.send("Não há uma fila para ser mostrada.")
                return
            if not ctx.voice_client.is_playing() or ctx.bot.current_song is None:
                await ctx.send("Não há nada tocando, então não pode haver uma fila.")
                return

            description = "\n".join(f"{idx + 1}. *{item['title'] or "Undefined"}* - ({item['duration']})" for idx, item in enumerate(ctx.bot.queue))
            duration_seconds = ctx.bot.current_song['duration']
            elapsed = int(time.time() - ctx.bot.song_start_time)
            formatted_time = f"{format_time(elapsed)} | {format_time(duration_seconds)}"
            await ctx.send(f"Tocando agora: **{current_title}** ({formatted_time})\n**FILA ATUAL:**\n{description}")

        @bot.hybrid_command(name="skip", description="Pula a música atual")
        async def skip(ctx: commands.Context):
            """Skips current reproduction if there is one."""
            if not ctx.voice_client or not ctx.voice_client.is_playing():
                await ctx.send("Não há nada tocando no momento.")
                return
            ctx.voice_client.stop()
            await ctx.send(f"Pulando: **{ctx.bot.current_song['title']}** ({ctx.bot.current_song['duration']})")

        @bot.hybrid_command(name="pop", description="Tira uma música da fila (aceita um índice da fila como argumento)")
        async def pop(ctx: commands.Context, idx: int):
            """Pop index out of the queue if there is one."""
            if not ctx.bot.queue:
                await ctx.send("Não há uma fila para retirar algo...")
                return

            if idx > len(ctx.bot.queue):
                await ctx.send(f"Índice inválido. Tamanho atual da fila é {len(ctx.bot.queue)}.\n!queue para ver a fila.")
                return

            await ctx.send(f"Popando {ctx.bot.queue[idx-1]['title']} da fila.")
            del ctx.bot.queue[idx-1]

        @bot.hybrid_command(name="clear", description="Para de tocar e limpa a fila")
        async def clear(ctx: commands.Context):
            """Stops current reproduction if there is one and clears the queue."""
            if not ctx.voice_client or not ctx.voice_client.is_playing():
                await ctx.send("Não tem nada tocando no momento!")
                return
            if len(ctx.bot.queue) == 0:
                await ctx.send("A fila já está vazia, vou interromper a reprodução atual!")
                ctx.voice_client.stop()
                return
            ctx.bot.queue.clear()
            ctx.voice_client.stop()
            await ctx.send("Limpei a fila e interrompi a reprodução atual.")

        @bot.hybrid_command(name="playing", description="Mostra dados da reprodução atual")
        async def playing(ctx: commands.Context):
            """Displays current reproduction if there is one."""
            if not ctx.voice_client or not ctx.voice_client.is_playing() or not ctx.bot.current_song:
                await ctx.send("Não tem nada tocando no momento!")
                return
            duration_seconds = ctx.bot.current_song['duration']
            elapsed = int(time.time() - ctx.bot.song_start_time)
            formatted_time = f"{format_time(elapsed)} | {format_time(duration_seconds)}"
            await ctx.send(f"Tocando agora: **{ctx.bot.current_song['title']}** ({formatted_time})")

        bot.run(TOKEN, log_handler=None)

    except ValueError as err:
        logger.exception(f"ValueError exception: {err}")

    except Exception as err:
        logger.exception(f"An unexpected error has occurred: {err}")

