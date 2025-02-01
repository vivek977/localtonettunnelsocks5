import discord
from discord.ext import commands, tasks
import random
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv
import os
import sys
import asyncio
import json

# --------------------------
# Configuration
# --------------------------
load_dotenv("1.env")
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
API_KEY = os.getenv("LOCALTONET_API_KEY")
VIEW_PASSWORD = os.getenv("VIEW_PASSWORD", "980345")  # Set in .env
HOST_IP = "130.51.20.126"
DATA_FILE = "accounts.json"

# Initialize accounts data
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump([], f)

# --------------------------
# Bot Setup
# --------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# --------------------------
# Account Manager Cog
# --------------------------
class AccountManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_sessions = set()
        self.used_usernames = set()
        self.help_message = None
        self.system_channel = None
        self.accounts = self.load_accounts()
        self.auth_users = set()

    def load_accounts(self):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except:
            return []

    def save_accounts(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.accounts[-100:], f)

    async def cog_load(self):
        self.menu_updater.start()

    @tasks.loop(seconds=300)
    async def menu_updater(self):
        if self.system_channel:
            await self.show_main_menu()

    async def show_main_menu(self):
        embed = discord.Embed(
            title="🔑 Secure Account Manager v3.0",
            description=(
                "**Commands:**\n"
                "`1` Create account\n"
                "`2` View accounts (auth required)\n"
                "`3` Refresh menu\n"
                "`0` Cancel operation"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Total accounts: {len(self.accounts)} | Auth users: {len(self.auth_users)}")
        try:
            if self.help_message:
                await self.help_message.edit(embed=embed)
            else:
                self.help_message = await self.system_channel.send(embed=embed)
        except discord.NotFound:
            self.help_message = await self.system_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        self.system_channel = self.bot.get_channel(CHANNEL_ID)
        if self.system_channel:
            await self.system_channel.send("🔒 **Secure System Activated**")
            await self.show_main_menu()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.channel.id != CHANNEL_ID:
            return

        try:
            if message.content == "1":
                await self.handle_create_account(message)
            elif message.content == "2":
                await self.handle_view_accounts(message)
            elif message.content == "3":
                await self.show_main_menu()
            elif message.content == "0":
                await self.cancel_operation(message)

        except Exception as e:
            await message.channel.send(f"❌ Error: {str(e)[:200]}")
            self.active_sessions.discard(message.author.id)

    async def handle_create_account(self, message):
        if message.author.id in self.active_sessions:
            await message.channel.send("❗ Complete current session first")
            return

        self.active_sessions.add(message.author.id)
        try:
            # Region selection
            region = await self.get_number_input(
                user=message.author,
                channel=message.channel,
                valid_options={1, 2, 3, 0},
                prompt="🌍 Choose region:\n1. US Southeast\n2. US Northeast\n3. US West\n0. Cancel"
            )
            
            if region == 0:
                return await message.channel.send("❌ Operation cancelled")

            # Confirmation
            confirm = await self.get_number_input(
                user=message.author,
                channel=message.channel,
                valid_options={1, 0},
                prompt=f"Confirm {TUNNELS[region]['name']}? (1=Yes/0=Cancel)"
            )
            
            if confirm != 1:
                return await message.channel.send("❌ Creation cancelled")

            # Generate credentials
            username, password = self.generate_credentials()
            
            # API call
            expiration_date = datetime.now(timezone.utc) + timedelta(days=30)
            payload = {
                "tunnelId": TUNNELS[region]["id"],
                "username": username,
                "password": password,
                "enableExpirationDate": True,
                "expirationDate": expiration_date.isoformat().replace("+00:00", "Z"),
                "enableThreadLimit": True,
                "threadLimit": 3,
                "description": "Created via Secure Bot"
            }

            response = requests.post(
                "https://localtonet.com/api/AddClientForSharedProxyTunnel",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                },
                json=payload
            )

            if response.status_code != 200:
                error_data = response.json()
                error_msg = error_data.get("result", "Unknown API error")
                raise Exception(f"API Error: {error_msg}")

            # Store account
            self.accounts.append({
                "username": username,
                "password": password,
                "expiry": expiration_date.strftime("%Y-%m-%d"),
                "region": TUNNELS[region]["name"],
                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            self.save_accounts()

            # Success response
            await message.channel.send(embed=discord.Embed(
                title="🔐 Account Created",
                description=(
                    f"**Region:** {TUNNELS[region]['name']}\n"
                    f"**Host:** `{HOST_IP}:{TUNNELS[region]['port']}`\n"
                    f"**Username:** ||{username}||\n"
                    f"**Password:** ||{password}||\n"
                    f"**Expires:** {expiration_date.strftime('%Y-%m-%d')}"
                ),
                color=discord.Color.green()
            ))

        finally:
            self.active_sessions.discard(message.author.id)
            await self.show_main_menu()

    async def handle_view_accounts(self, message):
        """Secure account viewing flow"""
        if message.author.id in self.active_sessions:
            await message.channel.send("❗ Finish current session first")
            return

        self.active_sessions.add(message.author.id)
        try:
            # Password authentication
            def check(m):
                return m.author == message.author and m.channel == message.channel

            await message.channel.send("🔒 Enter view password:")
            password_msg = await self.bot.wait_for("message", check=check, timeout=60)
            
            if password_msg.content != VIEW_PASSWORD:
                await message.channel.send("❌ Invalid password")
                return

            # Successful authentication
            self.auth_users.add(message.author.id)
            await self.show_recent_accounts(message.channel)

        except TimeoutError:
            await message.channel.send("⌛ Authentication timed out")
        finally:
            self.active_sessions.discard(message.author.id)

    async def show_recent_accounts(self, channel):
        """Display last 10 accounts (authenticated users only)"""
        recent_accounts = self.accounts[-10:][::-1]
        
        embed = discord.Embed(
            title="🔍 Recent Accounts (Last 10)",
            color=discord.Color.gold()
        )
        
        for acc in recent_accounts:
            embed.add_field(
                name=f"{acc['username']} ({acc['region']})",
                value=(
                    f"Created: {acc['created']}\n"
                    f"Expires: {acc['expiry']}\n"
                    f"Password: ||{acc['password']}||"
                ),
                inline=False
            )
            
        embed.set_footer(text="Authentication valid for 5 minutes")
        await channel.send(embed=embed)

    async def get_number_input(self, user, channel, valid_options, prompt):
        def check(m):
            return (
                m.author == user and
                m.channel == channel and
                m.content.strip().isdigit() and
                int(m.content) in valid_options
            )

        try:
            await channel.send(prompt)
            msg = await self.bot.wait_for("message", check=check, timeout=120)
            return int(msg.content)
        except TimeoutError:
            await channel.send("⏰ Session timed out")
            return 0
        except:
            return 0

    def generate_credentials(self):
        """Generate DDMMYYYY password format"""
        adjectives = ["secure", "private", "locked", "hidden", "vault"]
        cities = ["zone", "base", "hub", "node", "core"]
        
        while True:
            username = f"{random.choice(adjectives)}{random.choice(cities)}"
            if username not in self.used_usernames:
                self.used_usernames.add(username)
                return (
                    username,
                    datetime.now().strftime("%d%m%Y")  # DDMMYYYY format
                )

    async def cancel_operation(self, message):
        self.active_sessions.discard(message.author.id)
        await message.channel.send("✅ Operation cancelled")
        await self.show_main_menu()

# --------------------------
# Constants
# --------------------------
TUNNELS = {
    1: {"name": "US Southeast", "id": 1019575, "port": 6097},
    2: {"name": "US Northeast", "id": 1001992, "port": 1080},
    3: {"name": "US West", "id": 1019535, "port": 1080}
}

async def main():
    await bot.add_cog(AccountManager(bot))
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
