import discord
from discord.ext import commands, tasks
import random
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv
import os
import asyncio
import json
from pathlib import Path
from collections import defaultdict

# --------------------------
# Configuration
# --------------------------
env_path = os.path.join("config", "socks5-config.env")
load_dotenv(env_path)

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
API_KEY = os.getenv("LOCALTONET_API_KEY")
VIEW_PASSWORD = os.getenv("VIEW_PASSWORD", "980345")
HOST_IP = "130.51.20.126"

DATA_DIR = "data"
DATA_FILE = os.path.join(DATA_DIR, "accounts.json")
MAX_ACCOUNTS = 500

Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

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
        self.help_message = None
        self.system_channel = None
        self.accounts = self.load_accounts()
        self.used_usernames = {acc['username'] for acc in self.accounts}
        self.reaction_map = {
            "1️⃣": 1,
            "2️⃣": 2,
            "3️⃣": 3,
            "❌": 0
        }
        self.user_activity = defaultdict(lambda: {
            "total": 0,
            "demo": 0,
            "standard": 0,
            "premium": 0,
            "last_created": None
        })
        self.last_report_date = datetime.now(timezone.utc).date()
        self.status_message = None

    def load_accounts(self):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading accounts: {str(e)}")
            return []

    def save_accounts(self):
        try:
            with open(DATA_FILE, "w") as f:
                json.dump(self.accounts[-MAX_ACCOUNTS:], f)
        except Exception as e:
            print(f"Error saving accounts: {str(e)}")

    async def cog_load(self):
        self.menu_updater.start()
        self.daily_report.start()
        self.status_reminder.start()

    @tasks.loop(seconds=300)
    async def menu_updater(self):
        if self.system_channel:
            await self.show_main_menu()

    @tasks.loop(hours=24)
    async def daily_report(self):
        await self.post_daily_activity()
        self.last_report_date = datetime.now(timezone.utc).date()

    @tasks.loop(hours=6)
    async def status_reminder(self):
        if self.system_channel:
            embed = discord.Embed(
                title="🟢 Bot Status",
                description="Ready to create secure proxy accounts!",
                color=discord.Color.green()
            )
            if self.status_message:
                try:
                    await self.status_message.delete()
                except discord.NotFound:
                    pass
            self.status_message = await self.system_channel.send(embed=embed)

    async def post_daily_activity(self):
        embed = discord.Embed(
            title="📊 Daily Account Activity Report",
            description="Account creation statistics for the last 24 hours",
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc)
        )
        
        active_users = [uid for uid, data in self.user_activity.items() 
                      if data['total'] > 0 and 
                      (datetime.now(timezone.utc) - data['last_created']).days < 1]
        
        if not active_users:
            embed.add_field(
                name="No Activity",
                value="No accounts were created in the last 24 hours",
                inline=False
            )
        else:
            for user_id in active_users:
                user = await self.bot.fetch_user(user_id)
                data = self.user_activity[user_id]
                embed.add_field(
                    name=str(user),
                    value=(
                        f"Total: {data['total']}\n"
                        f"Demo: {data['demo']}\n"
                        f"Standard: {data['standard']}\n"
                        f"Premium: {data['premium']}"
                    ),
                    inline=True
                )
        
        embed.set_footer(text="Daily Activity Summary")
        await self.system_channel.send(embed=embed)
        self.user_activity.clear()

    async def show_main_menu(self):
        embed = discord.Embed(
            title="🔐 Secure Account Manager",
            description=(
                "**React to choose account type:**\n\n"
                "1️⃣ Demo Account (1 Thread • 1 Day)\n"
                "2️⃣ Standard Account (3 Threads • 30 Days)\n"
                "3️⃣ Premium Account (5 Threads • 30 Days)\n"
                "❌ Cancel Operation\n"
            ),
            color=discord.Color.blue()
        ).set_footer(text=f"Total accounts created: {len(self.accounts)}")
        
        try:
            if self.help_message:
                await self.help_message.clear_reactions()
                await self.help_message.edit(embed=embed)
            else:
                self.help_message = await self.system_channel.send(embed=embed)
            
            for emoji in self.reaction_map.keys():
                await self.help_message.add_reaction(emoji)
                
        except discord.NotFound:
            self.help_message = await self.system_channel.send(embed=embed)
            for emoji in self.reaction_map.keys():
                await self.help_message.add_reaction(emoji)

    @commands.Cog.listener()
    async def on_ready(self):
        self.system_channel = self.bot.get_channel(CHANNEL_ID)
        if self.system_channel:
            await self.system_channel.send("🟢 **System Online**")
            await self.show_main_menu()

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot or reaction.message.channel.id != CHANNEL_ID:
            return

        if reaction.message.id != self.help_message.id:
            return

        emoji = str(reaction.emoji)
        if emoji not in self.reaction_map:
            return

        choice = self.reaction_map[emoji]
        channel = reaction.message.channel
        
        try:
            if choice in [1, 2, 3]:
                await self.handle_create_account(user, choice)
            elif choice == 0:
                await channel.send("✅ Operation cancelled", delete_after=5)
                
            await self.help_message.remove_reaction(emoji, user)
            
        except Exception as e:
            await channel.send(f"❌ Error: {str(e)[:200]}", delete_after=5)

    async def handle_create_account(self, user, choice):
        if user.id in self.active_sessions:
            return
            
        self.active_sessions.add(user.id)
        try:
            region_msg = await self.system_channel.send(
                embed=discord.Embed(
                    title="🌍 Choose Region",
                    description=(
                        "1️⃣ US Southeast (Port 6097)\n"
                        "2️⃣ US Northeast (Port 1080)\n"
                        "3️⃣ US West (Port 2080)\n"
                        "❌ Cancel"
                    ),
                    color=discord.Color.gold()
                )
            )
            
            for emoji in self.reaction_map.keys():
                await region_msg.add_reaction(emoji)

            def check(reaction, r_user):
                return (
                    r_user == user and
                    str(reaction.emoji) in self.reaction_map and
                    reaction.message.id == region_msg.id
                )

            reaction, _ = await self.bot.wait_for(
                "reaction_add",
                check=check,
                timeout=60
            )
            
            region = self.reaction_map[str(reaction.emoji)]
            await region_msg.delete()
            
            if region == 0:
                return

            thread_limit = {1: 1, 2: 3, 3: 5}[choice]
            demo = choice == 1
            username, password = self.generate_credentials()
            validity_days = 1 if demo else 30
            expiry_date = datetime.now(timezone.utc) + timedelta(days=validity_days)
            
            response = requests.post(
                "https://localtonet.com/api/AddClientForSharedProxyTunnel",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={
                    "tunnelId": TUNNELS[region]["id"],
                    "username": username,
                    "password": password,
                    "enableExpirationDate": True,
                    "expirationDate": expiry_date.isoformat().replace("+00:00", "Z"),
                    "enableThreadLimit": True,
                    "threadLimit": thread_limit
                }
            )
            if not response.ok:
                raise Exception(f"API Error: {response.json().get('result', 'Unknown error')}")

            confirm_msg = await self.system_channel.send(
                embed=discord.Embed(
                    title="✅ Confirm Account Creation",
                    description=(
                        f"**Account Type:** {self.get_account_type(choice)}\n"
                        f"**Region:** {TUNNELS[region]['name']}\n"
                        f"**Username:** `{username}`\n"
                        f"**Password:** `{password}`\n"
                        f"**Expires:** {expiry_date.strftime('%d %b %Y %H:%M UTC')}"
                    ),
                    color=discord.Color.green()
                )
            )
            
            await confirm_msg.add_reaction("✅")
            await confirm_msg.add_reaction("❌")

            def confirm_check(reaction, r_user):
                return (
                    r_user == user and
                    str(reaction.emoji) in ["✅", "❌"] and
                    reaction.message.id == confirm_msg.id
                )

            reaction, _ = await self.bot.wait_for(
                "reaction_add",
                check=confirm_check,
                timeout=60
            )
            
            if str(reaction.emoji) == "✅":
                self.store_account(username, password, TUNNELS[region], expiry_date, thread_limit)
                
                # Updated success embed with username display
                success_embed = discord.Embed(
                    title="🎉 Account Successfully Created",
                    description=(
                        f"```yaml\n"
                        f"User: {user.display_name} ({user.name})\n"
                        f"Type: {self.get_account_type(choice)}\n"
                        f"Region: {TUNNELS[region]['name']}\n"
                        f"IP/Host: {HOST_IP}\n"
                        f"Port: {TUNNELS[region]['port']}\n"
                        f"Username: {username}\n"
                        f"Password: {password}\n"
                        f"Threads: {thread_limit}\n"
                        f"Expiration: {expiry_date.strftime('%d %b %Y %H:%M UTC')}\n"
                        f"Account ID: {len(self.accounts)}\n"
                        f"```"
                    ),
                    color=discord.Color.green()
                )
                await self.system_channel.send(embed=success_embed)
                
                # Update activity tracking
                self.user_activity[user.id]['total'] += 1
                if choice == 1:
                    self.user_activity[user.id]['demo'] += 1
                elif choice == 2:
                    self.user_activity[user.id]['standard'] += 1
                elif choice == 3:
                    self.user_activity[user.id]['premium'] += 1
                self.user_activity[user.id]['last_created'] = datetime.now(timezone.utc)
            
            await confirm_msg.delete()

        finally:
            self.active_sessions.discard(user.id)
            await self.show_main_menu()

    def generate_credentials(self):
        adjectives = ["silent", "quick", "hidden", "calm", "bright"]
        nouns = ["shadow", "forest", "river", "stone", "valley"]
        
        for _ in range(100):
            username = random.choice(adjectives) + random.choice(nouns)
            if username not in self.used_usernames:
                self.used_usernames.add(username)
                return (
                    username,
                    datetime.now().strftime("%d%m%Y")
                )
        
        return (
            "user" + "".join(random.sample("abcdefghjkmnpqrstuvwxyz", 3)),
            datetime.now().strftime("%d%m%Y")
        )

    def store_account(self, username, password, region, expiry_date, thread_limit):
        self.accounts.append({
            "username": username,
            "password": password,
            "region": region["name"],
            "port": region["port"],
            "threads": thread_limit,
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "expiry": expiry_date.strftime("%Y-%m-%d %H:%M:%S")
        })
        
        if len(self.accounts) > MAX_ACCOUNTS:
            self.accounts = self.accounts[-MAX_ACCOUNTS:]
            self.used_usernames = {acc['username'] for acc in self.accounts}
        
        self.save_accounts()

    def get_account_type(self, choice):
        return {
            1: "Demo (1 Thread)",
            2: "Standard (3 Threads)",
            3: "Premium (5 Threads)"
        }[choice]

# --------------------------
# Constants
# --------------------------
TUNNELS = {
    1: {"name": "US Southeast", "id": 1019575, "port": 6097},
    2: {"name": "US Northeast", "id": 1001992, "port": 1080},
    3: {"name": "US West", "id": 1019535, "port": 2080}
}

async def main():
    await bot.add_cog(AccountManager(bot))
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
