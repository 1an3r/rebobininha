import discord
from discord.ext import commands
import dotenv
import os
import logging

dotenv.load_dotenv(".env")
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_TOKEN = os.getenv("GUILD_TOKEN")
GUILD = discord.Object(id=GUILD_TOKEN)

discord.utils.setup_logging(root=True)
logger = logging.getLogger("MyBot")
logger.setLevel(logging.DEBUG)

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        self.tree.copy_global_to(guild=GUILD)
        await self.tree.sync(guild=GUILD)
        logger.debug(f"Setup hook called to sync commands locally using {GUILD_TOKEN} as the local guild")

bot = MyBot()

@bot.tree.command(name="old", description="Old slash command invocation")
async def old(interaction: discord.Interaction):
    await interaction.response.send_message("This is an old command example")

@bot.hybrid_command(name="ping", description="Responds with Pong!")
async def hello(ctx: commands.Context):
    await ctx.send("Pong!")

bot.run(TOKEN)