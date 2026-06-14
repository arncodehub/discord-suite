import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta
import asyncio
import traceback

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix='/', intents=intents)

# Bot version
BOT_VERSION = "1.3.1"
BOT_OWNER_ID = 807087691522375681  # Set this to your Discord ID for owner commands

# Data storage files
DATA_FILE = "shame_data.json"
VOTE_DATA_FILE = "vote_data.json"
USER_ACTIVITY_FILE = "user_activity.json"

# Cooldown tracking: {guild_id: {user_id: timestamp}}
cooldowns = {}

# Vote data: {guild_id: {target_user_id: {voter_id: vote_timestamp}}}
vote_data = {}

# User activity data: {guild_id: {user_id: last_message_timestamp}}
user_activity = {}

# Last critical amount refresh time per guild: {guild_id: datetime}
last_critical_refresh = {}

# Remote Logging Configuration
ERROR_GUILD_ID = 1392955205527670936
ERROR_CHANNEL_ID = 1511879285802012833

async def broadcast_error_log(message_content: str):
    """Broadcasts traceback details safely to your admin text channel."""
    try:
        if not bot.is_ready():
            return
        guild = bot.get_guild(ERROR_GUILD_ID)
        if guild:
            channel = guild.get_channel(ERROR_CHANNEL_ID) or await guild.fetch_channel(ERROR_CHANNEL_ID)
            if channel:
                for i in range(0, len(message_content), 1900):
                    chunk = message_content[i:i+1900]
                    await channel.send(chunk)
    except Exception as dev_err:
        print(f"Failed to transmit error logs to Discord: {dev_err}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Intercepts app command errors globally and reports tracebacks to your channel."""
    tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
    tb_text = "".join(tb_lines)
    
    log_payload = (
        f"⚠️ **Application Command Error Intercepted!**\n"
        f"**Command:** `/{interaction.command.name if interaction.command else 'Unknown'}`\n"
        f"**User:** {interaction.user} (`{interaction.user.id}`)\n"
        f"**Guild:** {interaction.guild.name if interaction.guild else 'DMs'} (`{interaction.guild_id}`)\n"
        f"```python\n{tb_text}\n```"
    )
    
    await broadcast_error_log(log_payload)
    
    try:
        error_msg = "❌ An unexpected operational error occurred while processing this request. The developer has been automatically notified."
        if interaction.response.is_done():
            await interaction.followup.send(error_msg, ephemeral=True)
        else:
            await interaction.response.send_message(error_msg, ephemeral=True)
    except Exception:
        pass

async def run_discord_channel_backup():
    """Backup data files straight to the developer channel if 24 hours have passed."""
    try:
        data = load_shame_data()
        g_id_str = str(ERROR_GUILD_ID)
        
        if g_id_str not in data:
            data[g_id_str] = {}
        
        last_backup_str = data[g_id_str].get("last_dev_channel_backup")
        now = datetime.now()
        
        if last_backup_str:
            last_backup_time = datetime.fromisoformat(last_backup_str)
            if now < last_backup_time + timedelta(hours=24):
                print("⏱️ Discord Backup Skipped: Last archive was sent less than 24 hours ago.")
                return

        guild = bot.get_guild(ERROR_GUILD_ID)
        if not guild:
            print("❌ Backup Error: Dev guild not found.")
            return
            
        channel = guild.get_channel(ERROR_CHANNEL_ID) or await guild.fetch_channel(ERROR_CHANNEL_ID)
        if not channel:
            print("❌ Backup Error: Dev channel not found.")
            return

        files_to_send = []
        for file_name in [DATA_FILE, VOTE_DATA_FILE, USER_ACTIVITY_FILE]:
            if os.path.exists(file_name):
                files_to_send.append(discord.File(file_name))

        if not files_to_send:
            print("⚠️ Backup Warning: No local files exist to transmit.")
            return

        date_stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        await channel.send(
            content=f"📦 **Automated 24-Hour Database Backup**\n📅 Timestamp: `{date_stamp}`\n⚠️ *Keep these files safe for disaster recovery.*",
            files=files_to_send
        )
        print("💾 Success: Live JSON files dispatched to dev channel.")

        data[g_id_str]["last_dev_channel_backup"] = now.isoformat()
        save_shame_data(data)

    except Exception as e:
        print(f"Error handling live channel backup: {e}")
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        await broadcast_error_log(f"⚠️ **Discord Backup Engine Failed:**\n```python\n{tb}\n```")

def load_shame_data():
    """Load shame data from JSON file safely with corruption handling."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, PermissionError) as e:
            asyncio.create_task(broadcast_error_log(f"🚨 **Corrupted `{DATA_FILE}` found!** Rebuilt as empty.\nError: `{e}`"))
            return {}
    return {}

def save_shame_data(data):
    """Save shame data to JSON file atomically to prevent corruption/loss."""
    tmp_file = DATA_FILE + ".tmp"
    try:
        with open(tmp_file, 'w') as f:
            json.dump(data, f, indent=4)
        os.replace(tmp_file, DATA_FILE)
    except Exception as e:
        print(f"Error saving shame data safely: {e}")
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        asyncio.create_task(broadcast_error_log(f"💾 **Disk Save Blocked (`save_shame_data`)** — Disk likely full!\n```python\n{tb}\n```"))

def get_guild_data(guild_id):
    """Get or create guild data."""
    data = load_shame_data()
    guild_id_str = str(guild_id)
    if guild_id_str not in data:
        data[guild_id_str] = {
            "manager_role": None,
            "shame_channel": None,
            "cooldown": 0,
            "expiry_days": None,
            "votekick_ban_duration": 7,
            "entries": {},
            "disabled_commands": [],
        }
        save_shame_data(data)
    return data[guild_id_str]

def get_all_data():
    """Get all data."""
    return load_shame_data()

def update_guild_data(guild_id, guild_data):
    """Update guild data."""
    data = load_shame_data()
    data[str(guild_id)] = guild_data
    save_shame_data(data)

def load_vote_data():
    """Load vote data from JSON file with corruption handling."""
    global vote_data
    if os.path.exists(VOTE_DATA_FILE):
        try:
            with open(VOTE_DATA_FILE, 'r') as f:
                vote_data = json.load(f)
                return
        except (json.JSONDecodeError, PermissionError) as e:
            asyncio.create_task(broadcast_error_log(f"🚨 **Corrupted `{VOTE_DATA_FILE}` found!** Rebuilt as empty.\nError: `{e}`"))
    vote_data = {}

def save_vote_data():
    """Save vote data to JSON file atomically."""
    tmp_file = VOTE_DATA_FILE + ".tmp"
    try:
        with open(tmp_file, 'w') as f:
            json.dump(vote_data, f, indent=4)
        os.replace(tmp_file, VOTE_DATA_FILE)
    except Exception as e:
        print(f"Error saving vote data safely: {e}")
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        asyncio.create_task(broadcast_error_log(f"💾 **Disk Save Blocked (`save_vote_data`)** — Disk likely full!\n```python\n{tb}\n```"))

def get_vote_data(guild_id):
    """Get or create vote data for a guild."""
    guild_id_str = str(guild_id)
    if guild_id_str not in vote_data:
        vote_data[guild_id_str] = {}
        save_vote_data()
    return vote_data[guild_id_str]

def load_user_activity():
    """Load user activity data from JSON file with corruption handling."""
    global user_activity
    if os.path.exists(USER_ACTIVITY_FILE):
        try:
            with open(USER_ACTIVITY_FILE, 'r') as f:
                user_activity = json.load(f)
                return
        except (json.JSONDecodeError, PermissionError) as e:
            asyncio.create_task(broadcast_error_log(f"🚨 **Corrupted `{USER_ACTIVITY_FILE}` found!** Rebuilt as empty.\nError: `{e}`"))
    user_activity = {}

def save_user_activity():
    """Save user activity data to JSON file atomically."""
    tmp_file = USER_ACTIVITY_FILE + ".tmp"
    try:
        with open(tmp_file, 'w') as f:
            json.dump(user_activity, f, indent=4)
        os.replace(tmp_file, USER_ACTIVITY_FILE)
    except Exception as e:
        print(f"Error saving user activity safely: {e}")
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        asyncio.create_task(broadcast_error_log(f"💾 **Disk Save Blocked (`save_user_activity`)** — Disk likely full!\n```python\n{tb}\n```"))

def get_user_activity(guild_id, user_id):
    """Get last message timestamp for a user in a guild."""
    guild_id_str = str(guild_id)
    user_id_str = str(user_id)
    if guild_id_str not in user_activity:
        return None
    if user_id_str not in user_activity[guild_id_str]:
        return None
    return datetime.fromisoformat(user_activity[guild_id_str][user_id_str])

def update_user_activity(guild_id, user_id):
    """Update user's last message timestamp."""
    guild_id_str = str(guild_id)
    user_id_str = str(user_id)
    if guild_id_str not in user_activity:
        user_activity[guild_id_str] = {}
    user_activity[guild_id_str][user_id_str] = datetime.now().isoformat()
    save_user_activity()

async def scan_guild_history_async():
    """Scans text channel history for all joined guilds in the background."""
    await bot.wait_until_ready()
    print("🔍 [History Scanner] Starting background message history sync across all guilds...")
    
    try:
        now = datetime.now()
        cutoff = now - timedelta(days=7)
        updated_any = False

        for guild in bot.guilds:
            print(f"📊 [History Scanner] Analyzing guild: {guild.name} ({guild.id})")
            guild_id_str = str(guild.id)
            
            if guild_id_str not in user_activity:
                user_activity[guild_id_str] = {}

            for channel in guild.text_channels:
                perms = channel.permissions_for(guild.me)
                if not perms.read_messages or not perms.read_message_history:
                    continue
                
                try:
                    async for message in channel.history(after=cutoff, limit=1000):
                        if message.author.bot:
                            continue
                        
                        user_id_str = str(message.author.id)
                        msg_time = message.created_at.replace(tzinfo=None)
                        
                        existing_ts_str = user_activity[guild_id_str].get(user_id_str)
                        if existing_ts_str:
                            existing_ts = datetime.fromisoformat(existing_ts_str)
                            if msg_time > existing_ts:
                                user_activity[guild_id_str][user_id_str] = msg_time.isoformat()
                                updated_any = True
                        else:
                            user_activity[guild_id_str][user_id_str] = msg_time.isoformat()
                            updated_any = True
                            
                except discord.Forbidden:
                    continue
                except Exception as channel_err:
                    print(f"⚠️ [History Scanner] Could not read channel {channel.name}: {channel_err}")
            
            refresh_critical_amount(guild.id)

        if updated_any:
            save_user_activity()
            print("💾 [History Scanner] Historical synchronization complete. user_activity.json updated.")
        else:
            print("ℹ️ [History Scanner] Historical scan finished. No newer logs found to overwrite cache.")
            
        await broadcast_error_log("🔄 **Background Message History Sync Complete!** Server metrics successfully restored.")

    except Exception as e:
        print(f"Error in scan_guild_history_async: {e}")
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        await broadcast_error_log(f"🚨 **History Scanner Runtime Failure:**\n```python\n{tb}\n```")

def get_active_users_count(guild: discord.Guild) -> int:
    """Count active users using stored activity data."""
    guild_config = get_guild_data(guild.id)
    window_days = guild_config.get("activity_window_days", 7)
    cutoff = datetime.now() - timedelta(days=window_days)
    
    guild_activity = user_activity.get(str(guild.id), {})
    active_count = 0
    
    for member in guild.members:
        if member.bot:
            continue
        ts_str = guild_activity.get(str(member.id))
        if ts_str:
            try:
                if datetime.fromisoformat(ts_str) >= cutoff:
                    active_count += 1
            except Exception:
                pass
                
    return max(1, active_count)

def remove_expired_votes():
    """Remove expired votes from all guilds and return affected users."""
    affected_users = {}
    current_time = datetime.now()
    one_day_ago = current_time - timedelta(hours=24)
    
    for guild_id_str, users in vote_data.items():
        guild_id = int(guild_id_str)
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
        
        users_to_remove = []
        
        for target_id_str, voters in users.items():
            target_id = int(target_id_str)
            expired_voters = []
            
            for voter_id_str, vote_time_str in voters.items():
                vote_time = datetime.fromisoformat(vote_time_str)
                if vote_time < one_day_ago:
                    expired_voters.append(voter_id_str)
            
            for voter_id_str in expired_voters:
                del voters[voter_id_str]
            
            if target_id not in affected_users:
                affected_users[target_id] = 0
            affected_users[target_id] = len(voters)
        
        users_to_remove = [uid for uid, voters in users.items() if not voters]
        for uid in users_to_remove:
            del users[uid]
        
        if users:
            vote_data[guild_id_str] = users
            save_vote_data()
    
    return affected_users

def clear_all_votes_in_guild(guild_id: int):
    """Clear all votes in a guild."""
    guild_id_str = str(guild_id)
    if guild_id_str in vote_data:
        del vote_data[guild_id_str]
        save_vote_data()

def remove_expired_entries(guild_data):
    """Remove expired shame entries from guild data."""
    expiry_days = guild_data.get("expiry_days")
    if expiry_days is None:
        return
    
    current_time = datetime.now()
    entries_to_remove = []
    
    for entry_id, entry in guild_data["entries"].items():
        entry_date = datetime.fromisoformat(entry["date"])
        expiry_date = entry_date + timedelta(days=expiry_days)
        
        if current_time > expiry_date:
            entries_to_remove.append(entry_id)
    
    for entry_id in entries_to_remove:
        del guild_data["entries"][entry_id]

def is_command_disabled(guild_id, command_name):
    """Check if a command is disabled in a guild."""
    guild_data = get_guild_data(guild_id)
    disabled_commands = guild_data.get("disabled_commands", [])
    return command_name in disabled_commands

def disable_command(guild_id, command_name):
    """Disable a command in a guild."""
    guild_data = get_guild_data(guild_id)
    disabled_commands = guild_data.get("disabled_commands", [])
    if command_name not in disabled_commands:
        disabled_commands.append(command_name)
        guild_data["disabled_commands"] = disabled_commands
        update_guild_data(guild_id, guild_data)

def enable_command(guild_id, command_name):
    """Enable a command in a guild."""
    guild_data = get_guild_data(guild_id)
    disabled_commands = guild_data.get("disabled_commands", [])
    if command_name in disabled_commands:
        disabled_commands.remove(command_name)
        guild_data["disabled_commands"] = disabled_commands
        update_guild_data(guild_id, guild_data)

def get_all_command_names():
    """Get all available bot command names."""
    return sorted([cmd.name for cmd in bot.tree.get_commands() if not isinstance(cmd, discord.app_commands.ContextMenu)])

def is_valid_command(command_name: str) -> bool:
    """Check if a command exists."""
    return command_name.lower() in get_all_command_names()

async def command_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for command names."""
    all_commands = get_all_command_names()
    available = [cmd for cmd in all_commands if cmd not in ["enable", "disable"]]
    filtered = [cmd for cmd in available if cmd.startswith(current.lower())]
    return [app_commands.Choice(name=cmd, value=cmd) for cmd in filtered[:25]]

def calculate_critical_amount(active_users: int) -> int:
    """Calculate the critical amount needed to kick someone."""
    return (active_users // 2) + 1

def refresh_critical_amount(guild_id: int) -> int:
    """Refresh and return the critical amount for a guild."""
    guild = bot.get_guild(guild_id)
    if not guild:
        return 0
    
    active_users = get_active_users_count(guild)
    critical_amount = calculate_critical_amount(active_users)
    last_critical_refresh[guild_id] = datetime.now()
    return critical_amount

@tasks.loop(seconds=30)
async def check_expired_votes():
    """Background task to regularly purge old, expired vote entries."""
    try:
        current_time = datetime.now()
        one_day_ago = current_time - timedelta(hours=24)
        
        for guild_id_str, targets in list(vote_data.items()):
            guild = bot.get_guild(int(guild_id_str))
            if not guild:
                continue
                
            for target_id_str, voters in list(targets.items()):
                expired_voters = []
                
                for voter_id_str, vote_info in list(voters.items()):
                    # Read the timestamp depending on if it's new schema or legacy schema
                    if isinstance(vote_info, dict):
                        ts_str = vote_info.get("timestamp")
                    else:
                        ts_str = vote_info # Legacy string format fallback
                        
                    vote_time = datetime.fromisoformat(ts_str)
                    if vote_time < one_day_ago:
                        expired_voters.append(voter_id_str)
                
                if expired_voters:
                    for voter_id in expired_voters:
                        del vote_data[guild_id_str][target_id_str][voter_id]
                    
                    save_vote_data()
                    
                    target_member = guild.get_member(int(target_id_str)) or await bot.fetch_user(int(target_id_str))
                    
                    broadcast_channel = None
                    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
                        broadcast_channel = guild.system_channel
                        
                    if not broadcast_channel:
                        for channel in guild.text_channels:
                            if channel.permissions_for(guild.me).send_messages:
                                broadcast_channel = channel
                                break
                    
                    if broadcast_channel and target_member:
                        remaining_votes = len(vote_data[guild_id_str].get(target_id_str, {}))
                        critical_amount = refresh_critical_amount(guild.id)
                        
                        await broadcast_channel.send(f"🕒 {len(expired_voters)} vote(s) for **{target_member.name}** have expired. ({remaining_votes}/{critical_amount})", silent=True)
                
                if guild_id_str in vote_data and target_id_str in vote_data[guild_id_str]:
                    if not vote_data[guild_id_str][target_id_str]:
                        del vote_data[guild_id_str][target_id_str]
                        
            if guild_id_str in vote_data and not vote_data[guild_id_str]:
                del vote_data[guild_id_str]
                
        save_vote_data()
        
    except Exception as e:
        print(f"Error in check_expired_votes background loop: {e}")
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        await broadcast_error_log(f"🚨 **Background Loop Failure (`check_expired_votes`):**\n```python\n{tb}\n```")

def check_cooldown(guild_id, user_id):
    """Check if user is on cooldown in guild."""
    if guild_id not in cooldowns:
        return 0
    if user_id not in cooldowns[guild_id]:
        return 0
    
    remaining = cooldowns[guild_id][user_id] - datetime.now().timestamp()
    if remaining <= 0:
        del cooldowns[guild_id][user_id]
        return 0
    return remaining

def set_cooldown(guild_id, user_id, cooldown_seconds):
    """Set cooldown for user in guild."""
    if guild_id not in cooldowns:
        cooldowns[guild_id] = {}
    cooldowns[guild_id][user_id] = datetime.now().timestamp() + cooldown_seconds

def is_bot_manager(interaction: discord.Interaction):
    if interaction.user.guild_permissions.manage_guild:
        return True
    guild_data = get_guild_data(interaction.guild_id)
    manager_role_id = guild_data.get("manager_role")
    if manager_role_id is None:
        return False
    manager_role = interaction.guild.get_role(manager_role_id)
    if manager_role is None:
        return False
    return manager_role in interaction.user.roles

def is_bot_owner(interaction: discord.Interaction):
    return interaction.user.id == BOT_OWNER_ID

def is_moderator(interaction: discord.Interaction):
    return interaction.user.guild_permissions.manage_guild

def is_manager(interaction: discord.Interaction):
    if is_moderator(interaction):
        return True
    guild_data = get_guild_data(interaction.guild_id)
    manager_role_id = guild_data.get("manager_role")
    if manager_role_id is None:
        return False
    manager_role = interaction.guild.get_role(manager_role_id)
    if manager_role is None:
        return False
    return manager_role in interaction.user.roles

@tasks.loop(minutes=5)
async def manage_active_roles_loop():
    """Periodically scans all guilds and syncs active member roles."""
    try:
        data = load_shame_data()
        for guild_id_str, guild_config in data.items():
            role_id = guild_config.get("active_member_role")
            if not role_id:
                continue
                
            guild = bot.get_guild(int(guild_id_str))
            if not guild:
                continue
                
            role = guild.get_role(role_id)
            if not role or not guild.me.guild_permissions.manage_roles or guild.me.top_role <= role:
                continue
                
            window_days = guild_config.get("activity_window_days", 7)
            cutoff = datetime.now() - timedelta(days=window_days)
            
            guild_activity = user_activity.get(guild_id_str, {})
            
            active_user_ids = set()
            for u_id_str, ts_str in guild_activity.items():
                try:
                    if datetime.fromisoformat(ts_str) >= cutoff:
                        active_user_ids.add(int(u_id_str))
                except Exception:
                    pass
            
            broadcast_channel_id = guild_config.get("activity_broadcast_channel")
            broadcast_channel = guild.get_channel(broadcast_channel_id) if broadcast_channel_id else None
            
            for member in guild.members:
                if member.bot:
                    continue
                    
                should_have = member.id in active_user_ids
                has_role = role in member.roles
                
                try:
                    if should_have and not has_role:
                        await member.add_roles(role, reason="Active member threshold matched recent message logs.")
                        if broadcast_channel:
                            await broadcast_channel.send(f"🎉 **{member.name}** has been assigned the **{role.name}** role due to recent message activity!")
                            
                    elif not should_have and has_role:
                        await member.remove_roles(role, reason="User fell out of specified activity threshold parameters.")
                        if broadcast_channel:
                            await broadcast_channel.send(f"📉 **{member.name}** lost the **{role.name}** role due to inactivity.")
                except discord.Forbidden:
                    pass
                    
    except Exception as e:
        print(f"Error in manage_active_roles_loop task frame: {e}")
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        await broadcast_error_log(f"🚨 **Background Task Loop Failure (`manage_active_roles_loop`):**\n```python\n{tb}\n```")

@tasks.loop(hours=1.0)
async def autodelete_sweeper():
    """Background loop that sweeps channels configured for automatic old message deletion."""
    await bot.wait_until_ready()
    
    for guild in bot.guilds:
        guild_data = get_guild_data(guild.id)
        autodelete_config = guild_data.get("autodelete_channels", {})
        
        if not autodelete_config:
            continue
            
        for channel_id_str, days in list(autodelete_config.items()):
            channel_id = int(channel_id_str)
            channel = guild.get_channel(channel_id)
            
            if not channel or not isinstance(channel, discord.TextChannel):
                continue
                
            if channel_id != 1511879285802012833:
                continue

            cutoff_date = datetime.now() - timedelta(days=days)
            
            try:
                await channel.purge(
                    before=cutoff_date, 
                    bulk=True, 
                    reason="[BETA] Automated channel autodelete threshold reached."
                )
            except discord.Forbidden:
                pass
            except discord.HTTPException:
                pass

@tasks.loop(hours=24)
async def discord_backup_loop():
    """Triggers the automated file attachment transfer sequence every 24 hours."""
    await run_discord_channel_backup()

@bot.event
async def on_guild_join(guild):
    get_guild_data(guild.id)
    print(f"Joined guild: {guild.name} (ID: {guild.id})")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
        
    update_user_activity(message.guild.id, message.author.id)
    
    guild_config = get_guild_data(message.guild.id)
    role_id = guild_config.get("active_member_role")
    if role_id:
        role = message.guild.get_role(role_id)
        if role and message.guild.me.guild_permissions.manage_roles and message.guild.me.top_role > role:
            if role not in message.author.roles:
                try:
                    await message.author.add_roles(role, reason="Sent message while active role assignment tracking is active.")
                    broadcast_channel_id = guild_config.get("activity_broadcast_channel")
                    broadcast_channel = message.guild.get_channel(broadcast_channel_id) if broadcast_channel_id else None
                    if broadcast_channel:
                        await broadcast_channel.send(f"🎉 **{message.author.name}** has been assigned the **{role.name}** role due to recent message activity!", silent=True)
                except Exception:
                    pass

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
        
    load_vote_data()
    load_user_activity()
    
    for guild_id_str in vote_data.keys():
        refresh_critical_amount(guild_id_str)
        
    if not check_expired_votes.is_running():
        check_expired_votes.start()
        
    if not discord_backup_loop.is_running():
        discord_backup_loop.start()
        
    if not manage_active_roles_loop.is_running():
        manage_active_roles_loop.start()

    if not autodelete_sweeper.is_running():
        autodelete_sweeper.start()
        
    asyncio.create_task(scan_guild_history_async())
        
    await broadcast_error_log("🟢 **Bot Startup Successful!** Systems initialized and historical scanner task dispatched.")


# ==========================================
# 1. FOUNDATIONAL COMMANDS
# ==========================================
@bot.tree.command(name="info", description="Get bot information")
async def info(interaction: discord.Interaction):
    if is_command_disabled(interaction.guild_id, "info"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
    
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    manager_role_id = guild_data.get("manager_role")
    manager_role = interaction.guild.get_role(manager_role_id) if manager_role_id else None
    manager_role_text = manager_role.name if manager_role else "None"
    
    disabled_cmds = ", ".join(guild_data.get("disabled_commands", [])) or "None"
    
    expiry_timer = guild_data.get("expiry_days", "Not Set")
    shame_channel_id = guild_data.get("shame_channel")
    shame_channel = f"<#{shame_channel_id}>" if shame_channel_id else "Not Set"
    
    votekick_ban_duration = guild_data.get("votekick_ban_duration", 7)
    vk_bc_id = guild_data.get("votekick_broadcast_channel")
    vk_bc = f"<#{vk_bc_id}>" if vk_bc_id else "Not Set"
    
    am_role_id = guild_data.get("active_member_role")
    am_role = interaction.guild.get_role(am_role_id) if am_role_id else None
    am_role_text = am_role.name if am_role else "Not Set"
    
    act_bc_id = guild_data.get("activity_broadcast_channel")
    act_bc = f"<#{act_bc_id}>" if act_bc_id else "Not Set"
    
    act_win = guild_data.get("activity_window_days", 7)
    
    response_text = (
        "**General Stuff**\n"
        f"Version: {BOT_VERSION}\n"
        f"Command Cooldown: {cooldown_seconds}s\n"
        f"Disabled Commands: {disabled_cmds}\n"
        f"Manager Role: {manager_role_text}\n\n"
        "**Shame Stuff**\n"
        f"Shame Entry Expiry Timer: {expiry_timer}\n"
        f"Shame Broadcast Channel: {shame_channel}\n\n"
        "**Vote to Kick Stuff**\n"
        f"Vote to Kick Ban Duration: {votekick_ban_duration}\n"
        f"Vote to Kick Broadcast Channel: {vk_bc}\n\n"
        "**Activity Stuff**\n"
        f"Active Member Role: {am_role_text}\n"
        f"Activity Broadcast Channel: {act_bc}\n"
        f"Activity Window: {act_win}"
    )
    
    await interaction.response.send_message(response_text)

@bot.tree.command(name="cooldown", description="Set command cooldown for the server (Manager only)")
@app_commands.describe(seconds="Cooldown in seconds (0-30)")
async def set_cooldown_cmd(interaction: discord.Interaction, seconds: int):
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_manager(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    if seconds < 0 or seconds > 30:
        await interaction.response.send_message("❌ Cooldown must be between 0 and 30 seconds.", ephemeral=True)
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    guild_data["cooldown"] = seconds
    update_guild_data(interaction.guild_id, guild_data)
    
    if interaction.guild_id in cooldowns:
        cooldowns[interaction.guild_id].clear()
    
    await interaction.response.send_message(f"✅ Command cooldown set to {seconds} seconds\nAll existing cooldowns have been reset.")

@bot.tree.command(name="enable", description="Enable a bot command in this server (Moderator only)")
@app_commands.describe(command="The command to enable")
@app_commands.autocomplete(command=command_autocomplete)
async def enable_cmd(interaction: discord.Interaction, command: str):
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_moderator(interaction):
        await interaction.response.send_message("❌ You need Manage Server permission to use this command.", ephemeral=True)
        return
    if not is_valid_command(command):
        await interaction.response.send_message(f"❌ The command `{command}` does not exist.", ephemeral=True)
        return
    if not is_command_disabled(interaction.guild_id, command.lower()):
        await interaction.response.send_message(f"❌ The command `{command}` is already enabled in this server.", ephemeral=True)
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    enable_command(interaction.guild_id, command.lower())
    await interaction.response.send_message(f"✅ The `{command}` command has been enabled in this server.")

@bot.tree.command(name="disable", description="Disable a bot command in this server (Moderator only)")
@app_commands.describe(command="The command to disable")
@app_commands.autocomplete(command=command_autocomplete)
async def disable_cmd(interaction: discord.Interaction, command: str):
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_moderator(interaction):
        await interaction.response.send_message("❌ You need Manage Server permission to use this command.", ephemeral=True)
        return
    if not is_valid_command(command):
        await interaction.response.send_message(f"❌ The command `{command}` does not exist.", ephemeral=True)
        return
    if command.lower() in ["enable", "disable"]:
        await interaction.response.send_message("❌ You cannot disable the enable or disable commands.", ephemeral=True)
        return
    if is_command_disabled(interaction.guild_id, command.lower()):
        await interaction.response.send_message(f"❌ The command `{command}` is already disabled in this server.", ephemeral=True)
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    disable_command(interaction.guild_id, command.lower())
    await interaction.response.send_message(f"🟠 The `{command}` command has been disabled in this server.")

@bot.tree.command(name="set_manager_role", description="Set the bot manager role (Moderator only)")
@app_commands.describe(role="The role to set as manager")
async def set_manager_role(interaction: discord.Interaction, role: discord.Role):
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_moderator(interaction):
        await interaction.response.send_message("❌ You need Manage Server permission to use this command.", ephemeral=True)
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    guild_data["manager_role"] = role.id
    update_guild_data(interaction.guild_id, guild_data)
    await interaction.response.send_message(f"✅ Manager role set to **{role.name}**")

@bot.tree.command(name="reset_manager_role", description="Reset manager role (Moderator only)")
async def reset_manager_role(interaction: discord.Interaction):
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_moderator(interaction):
        await interaction.response.send_message("❌ You need Manage Server permission to use this command.", ephemeral=True)
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    guild_data["manager_role"] = None
    update_guild_data(interaction.guild_id, guild_data)
    await interaction.response.send_message("✅ Manager role reset to server owner only")

# ==========================================
# 2. BETA COMMANDS
# ==========================================
@bot.tree.command(name="autodelete", description="[BETA] Automatically delete messages older than X days in this channel (Manager only)")
@app_commands.describe(days="Number of days to keep messages. Use 0 to disable autodeletion.")
async def autodelete(interaction: discord.Interaction, days: int):
    BETA_CHANNEL_ID = 1511879285802012833
    if interaction.channel_id != BETA_CHANNEL_ID:
        await interaction.response.send_message(f"❌ This experimental command is currently locked to the BETA testing channel.", ephemeral=True)
        return
    if is_command_disabled(interaction.guild_id, "autodelete"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_manager(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    if days < 0:
        await interaction.response.send_message("❌ Days must be a whole number (0, 1, 2, ...).", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)
    if "autodelete_channels" not in guild_data:
        guild_data["autodelete_channels"] = {}

    channel_key = str(interaction.channel_id)

    if days == 0:
        if channel_key in guild_data["autodelete_channels"]:
            del guild_data["autodelete_channels"][channel_key]
        update_guild_data(interaction.guild_id, guild_data)
        await interaction.response.send_message("🛑 **Autodelete Disabled**\nAutomatic message deletion has been turned off for this channel.", ephemeral=False)
    else:
        guild_data["autodelete_channels"][channel_key] = days
        update_guild_data(interaction.guild_id, guild_data)
        await interaction.response.send_message(f"🗑️ **Autodelete Enabled**\nMessages in this channel older than `{days}` days will now be automatically purged.", ephemeral=False)

    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

# ==========================================
# 3. SHAME COMMANDS
# ==========================================
@bot.tree.command(name="shame_config_set", description="Configure server Shame settings (Manager only)")
@app_commands.describe(
    channel="The text channel where shame broadcasts are targeted",
    expiry_days="The number of days an entry remains visible before expiring"
)
async def shame_config_set(interaction: discord.Interaction, channel: discord.TextChannel = None, expiry_days: int = None):
    if is_command_disabled(interaction.guild_id, "shame_config_set"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_manager(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    if channel is None and expiry_days is None:
        await interaction.response.send_message("⚠️ Provide at least a `channel` or `expiry_days` to update.", ephemeral=True)
        return
    if expiry_days is not None and expiry_days < 1:
        await interaction.response.send_message("❌ Expiry duration must be at least 1 day.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)
    changes = []
    if channel is not None:
        guild_data["shame_channel"] = channel.id
        changes.append(f"• Shame Broadcast Channel set to {channel.mention}")
    if expiry_days is not None:
        guild_data["expiry_days"] = expiry_days
        changes.append(f"• Shame Entry Expiry Timer set to `{expiry_days}` days")

    update_guild_data(interaction.guild_id, guild_data)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    await interaction.response.send_message(f"✅ **Shame Configuration Updated**\n" + "\n".join(changes))

@bot.tree.command(name="shame_config_reset", description="Reset shame configurations to default (Manager only)")
@app_commands.describe(attribute="The setting to reset")
@app_commands.choices(attribute=[
    app_commands.Choice(name="Shame Broadcast Channel", value="channel"),
    app_commands.Choice(name="Expiry Timer", value="timer"),
])
async def shame_config_reset(interaction: discord.Interaction, attribute: str):
    if is_command_disabled(interaction.guild_id, "shame_config_reset"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_manager(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)
    if attribute == "channel":
        guild_data["shame_channel"] = None
        display = "Shame Broadcast Channel"
    elif attribute == "timer":
        guild_data["expiry_days"] = None
        display = "Shame Entry Expiry Timer"

    update_guild_data(interaction.guild_id, guild_data)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    await interaction.response.send_message(f"🔄 **Reset Confirmed**\n`{display}` has been reset to default.")

@bot.tree.command(name="shame", description="Add a user to the hall of shame (Manager only)")
@app_commands.describe(user="The user to add", reason="Reason for shame", date="Optional date (YYYY-MM-DD format)")
async def shame(interaction: discord.Interaction, user: discord.User, reason: str, date: str = None):
    if is_command_disabled(interaction.guild_id, "shame"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_manager(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    remove_expired_entries(guild_data)
    
    if guild_data.get("expiry_days") is None:
        await interaction.response.send_message("❌ Shame entry expiry time must be set before adding entries. Use `/shame_config_set` to configure it.", ephemeral=True)
        return
    
    if guild_data.get("shame_channel") is None:
        await interaction.response.send_message("❌ Shame channel must be set before adding entries. Use `/shame_config_set` to configure it.", ephemeral=True)
        return
    
    if date:
        try:
            entry_date = datetime.strptime(date, "%Y-%m-%d")
            entry_date = entry_date.replace(hour=0, minute=0, second=0, microsecond=0)
            entry_date_iso = entry_date.isoformat()
        except ValueError:
            await interaction.response.send_message("❌ Invalid date format. Please use YYYY-MM-DD (e.g., 2026-05-10)", ephemeral=True)
            return
    else:
        entry_date = datetime.now()
        entry_date_iso = entry_date.isoformat()
    
    expiry_days = guild_data.get("expiry_days")
    entry_date_obj = datetime.fromisoformat(entry_date_iso)
    expiry_date = entry_date_obj + timedelta(days=expiry_days)
    
    if datetime.now() > expiry_date:
        await interaction.response.send_message(f"❌ Cannot add shame entry for {date or 'today'} - it has already expired.", ephemeral=True)
        return
    
    entry_id = len(guild_data["entries"]) + 1
    entry = {
        "user_id": user.id,
        "username": user.name,
        "reason": reason,
        "date": entry_date_iso,
        "added_by": interaction.user.name
    }
    
    guild_data["entries"][str(entry_id)] = entry
    update_guild_data(interaction.guild_id, guild_data)
    
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    response_lines = [
        f"🚨 **{user.name}** has been added to the hall of shame",
        f"**Reason:** {reason}",
        f"**Date:** {entry_date_iso.split('T')[0]}",
        f"**Entry ID:** {entry_id}",
        f"*Added by {interaction.user.name}*"
    ]
    await interaction.response.send_message("\n".join(response_lines))
    
    shame_channel_id = guild_data.get("shame_channel")
    if shame_channel_id:
        shame_channel = interaction.guild.get_channel(shame_channel_id)
        if shame_channel:
            broadcast_text = f"🚨 **Hall O' Shame**\n**{user.name}** has been added to the hall of shame, for: {reason}."
            try:
                await shame_channel.send(broadcast_text, silent=True)
            except discord.Forbidden:
                pass

@bot.tree.command(name="unshame", description="Remove a user from the hall of shame (Manager only)")
@app_commands.describe(entry_id="The entry ID to remove", reason="Optional reason for removal")
async def unshame(interaction: discord.Interaction, entry_id: int, reason: str = None):
    if is_command_disabled(interaction.guild_id, "unshame"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_manager(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    entry_id_str = str(entry_id)
    
    if entry_id_str not in guild_data["entries"]:
        await interaction.response.send_message(f"❌ Entry ID {entry_id} not found.", ephemeral=True)
        return
    
    entry = guild_data["entries"][entry_id_str]
    del guild_data["entries"][entry_id_str]
    update_guild_data(interaction.guild_id, guild_data)
    
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    response_lines = [
        f"✅ **{entry['username']}** has been removed from the hall of shame",
        f"**Original Reason:** {entry['reason']}"
    ]
    if reason:
        response_lines.append(f"**Removal Reason:** {reason}")
    response_lines.append(f"**Entry ID:** {entry_id_str}")
    response_lines.append(f"*Removed by {interaction.user.name}*")
    
    await interaction.response.send_message("\n".join(response_lines))

@bot.tree.command(name="list_my_shame", description="List your hall of shame entries")
async def list_my_shame(interaction: discord.Interaction):
    if is_command_disabled(interaction.guild_id, "list_my_shame"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    remove_expired_entries(guild_data)
    update_guild_data(interaction.guild_id, guild_data)
    
    user_entries = []
    for entry_id, entry in guild_data["entries"].items():
        if entry["user_id"] == interaction.user.id:
            user_entries.append((entry_id, entry))
    
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    if not user_entries:
        await interaction.response.send_message("✅ **Your Hall of Shame**\nYou have no entries in the hall of shame!")
        return
    
    response_lines = [
        "**Your Hall of Shame**",
        f"**Shame Count**: {len(user_entries)}\n"
    ]
    
    expiry_days = guild_data.get("expiry_days", 30)
    
    for idx, (entry_id, entry) in enumerate(user_entries, 1):
        entry_date = datetime.fromisoformat(entry["date"])
        expiry_date = entry_date + timedelta(days=expiry_days)
        entry_timestamp = int(entry_date.timestamp())
        expiry_timestamp = int(expiry_date.timestamp())
        
        response_lines.append(f"**Entry {idx}:**")
        response_lines.append(f"└ <t:{entry_timestamp}:d> - Expires <t:{expiry_timestamp}:R>")
        response_lines.append(f"  *Reason:* {entry['reason']}\n")
    
    await interaction.response.send_message("\n".join(response_lines)[:1995])

@bot.tree.command(name="list_all_shame", description="List all hall of shame entries")
async def list_all_shame(interaction: discord.Interaction):
    if is_command_disabled(interaction.guild_id, "list_all_shame"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.2f} more seconds.", ephemeral=True)
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    remove_expired_entries(guild_data)
    update_guild_data(interaction.guild_id, guild_data)
    
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    if not guild_data["entries"]:
        await interaction.response.send_message("✅ **Hall of Shame**\nThe hall of shame is empty!")
        return
    
    user_shame_count = {}
    for entry_id, entry in guild_data["entries"].items():
        user_id = entry["user_id"]
        if user_id not in user_shame_count:
            user_shame_count[user_id] = {"count": 0, "entries": []}
        user_shame_count[user_id]["count"] += 1
        user_shame_count[user_id]["entries"].append((entry_id, entry))
    
    response_lines = ["**Hall of Shame Counts**\n"]
    expiry_days = guild_data.get("expiry_days", 30)
    
    def sort_key(item):
        user_id, data = item
        count = data["count"]
        most_recent = max((datetime.fromisoformat(entry["date"]) for entry_id, entry in data["entries"]), default=datetime.min)
        return (-count, -most_recent.timestamp())
    
    for user_id, data in sorted(user_shame_count.items(), key=sort_key):
        member = interaction.guild.get_member(user_id)
        user_display = member.name if member else f"User {user_id}"
        count = data["count"]
        response_lines.append(f"**{user_display}** - {count} entries:")
        
        for entry_id, entry in data["entries"]:
            entry_date = datetime.fromisoformat(entry["date"])
            expiry_date = entry_date + timedelta(days=expiry_days)
            entry_timestamp = int(entry_date.timestamp())
            expiry_timestamp = int(expiry_date.timestamp())
            reason = entry.get("reason", "No reason provided")
            response_lines.append(f"└ <t:{entry_timestamp}:d> (expires <t:{expiry_timestamp}:R>) - {reason}")
            
        response_lines.append("")
        
    await interaction.response.send_message("\n".join(response_lines)[:1995])

# ==========================================
# 4. VOTEKICK COMMANDS
# ==========================================
@bot.tree.command(name="votekick_config_set", description="Configure votekick settings (Manager only)")
@app_commands.describe(
    channel="The channel for votekick broadcasts",
    ban_duration="Ban duration in days (0-7)"
)
async def votekick_config_set(interaction: discord.Interaction, channel: discord.TextChannel = None, ban_duration: int = None):
    if is_command_disabled(interaction.guild_id, "votekick_config_set"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_manager(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    if channel is None and ban_duration is None:
        await interaction.response.send_message("⚠️ Provide either a `channel` or `ban_duration` to update.", ephemeral=True)
        return
    if ban_duration is not None and (ban_duration < 0 or ban_duration > 7):
        await interaction.response.send_message("❌ Ban duration must be between 0 (kick only) and 7 days.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)
    changes = []
    if channel is not None:
        guild_data["votekick_broadcast_channel"] = channel.id
        changes.append(f"• Votekick Broadcast Channel set to {channel.mention}")
    if ban_duration is not None:
        guild_data["votekick_ban_duration"] = ban_duration
        changes.append(f"• Votekick Ban Duration set to `{ban_duration}` days")

    update_guild_data(interaction.guild_id, guild_data)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    await interaction.response.send_message(f"✅ **Votekick Configuration Updated**\n" + "\n".join(changes))

@bot.tree.command(name="votekick_config_reset", description="Reset votekick settings (Manager only)")
@app_commands.describe(attribute="The setting to reset")
@app_commands.choices(attribute=[
    app_commands.Choice(name="Broadcast Channel", value="channel"),
    app_commands.Choice(name="Ban Duration", value="duration"),
])
async def votekick_config_reset(interaction: discord.Interaction, attribute: str):
    if is_command_disabled(interaction.guild_id, "votekick_config_reset"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_manager(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)
    if attribute == "channel":
        guild_data["votekick_broadcast_channel"] = None
        display = "Votekick Broadcast Channel"
    elif attribute == "duration":
        guild_data["votekick_ban_duration"] = 7
        display = "Votekick Ban Duration (Reset to 7 Days)"

    update_guild_data(interaction.guild_id, guild_data)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    await interaction.response.send_message(f"🔄 **Reset Confirmed**\n`{display}` has been reset to default.")

@bot.tree.command(name="vote", description="Vote to kick a user")
@app_commands.describe(user="The user to vote for", anonymous="Whether your vote is anonymous (default: True)")
async def vote(interaction: discord.Interaction, user: discord.Member, anonymous: bool = True):
    await interaction.response.defer(ephemeral=True)
    if is_command_disabled(interaction.guild_id, "vote"):
        await interaction.followup.send("❌ This command is disabled in this server.")
        return
    if user.id == interaction.user.id:
        await interaction.followup.send("❌ You cannot vote for yourself.")
        return
    if user.bot:
        await interaction.followup.send("❌ You cannot vote to kick a bot.")
        return
    if interaction.guild.owner_id == user.id:
        await interaction.followup.send("❌ You can't vote to kick the server owner.")
        return
    
    bot_member = interaction.guild.me
    if user.top_role >= bot_member.top_role:
        await interaction.followup.send("❌ I can't kick that person. Please ask a moderator to move my role higher.")
        return
    
    guild_vote_data = get_vote_data(interaction.guild_id)
    has_voted = False
    voted_user_id = None
    for target_id, voters in guild_vote_data.items():
        if str(interaction.user.id) in voters:
            has_voted = True
            voted_user_id = int(target_id)
            break
    
    if has_voted:
        voted_user = interaction.guild.get_member(voted_user_id)
        voted_user_mention = voted_user.mention if voted_user else f"<@{voted_user_id}>"
        await interaction.followup.send(f"❌ You have already voted for {voted_user_mention}. Use `/unvote` to remove your vote first.")
        return
    
    if str(user.id) not in guild_vote_data:
        guild_vote_data[str(user.id)] = {}
    
    # Save both timestamp and exact anonymity selection
    guild_vote_data[str(user.id)][str(interaction.user.id)] = {
        "timestamp": datetime.now().isoformat(),
        "anonymous": anonymous
    }
    save_vote_data()
    
    vote_count = len(guild_vote_data[str(user.id)])
    await interaction.followup.send(f"✅ Your vote has been counted for {user.name}.")
    critical_amount = refresh_critical_amount(interaction.guild_id)
    
    if anonymous:
        await interaction.channel.send(f"🟠 Someone voted for **{user.name}** ({vote_count}/{critical_amount}).", silent=True)
    else:
        await interaction.channel.send(f"🟠 **{interaction.user.name}** voted for **{user.name}** ({vote_count}/{critical_amount}).", silent=True)
    
    if vote_count >= critical_amount:
        guild_data = get_guild_data(interaction.guild_id)
        ban_duration = guild_data.get("votekick_ban_duration", 7)
        
        try:
            if ban_duration > 0:
                await user.ban(reason=f"Vote to kick - reached critical amount (ban for {ban_duration} days)", delete_message_days=0)
                await interaction.channel.send(f"🚨 **{user.name}** has been banned for {ban_duration} days due to reaching the critical vote threshold!", silent=True)
            else:
                await user.kick(reason="Vote to kick - reached critical amount")
                await interaction.channel.send(f"🚨 **{user.name}** has been kicked due to reaching the critical vote threshold!", silent=True)
            
            clear_all_votes_in_guild(interaction.guild_id)
        except discord.Forbidden:
            action_type = f"banned for {ban_duration} days" if ban_duration > 0 else "kicked"
            await interaction.channel.send(f"⚠️ **{user.name}** should have been {action_type}, but I lack permission.", silent=True)

@bot.tree.command(name="unvote", description="Remove your vote")
async def unvote(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if is_command_disabled(interaction.guild_id, "unvote"):
        await interaction.followup.send("❌ This command is disabled in this server.")
        return
    
    guild_vote_data = get_vote_data(interaction.guild_id)
    voted_user_id = None
    for target_id, voters in guild_vote_data.items():
        if str(interaction.user.id) in voters:
            voted_user_id = int(target_id)
            break
    
    if voted_user_id is None:
        await interaction.followup.send("❌ You haven't voted yet.")
        return
    
    voted_user = interaction.guild.get_member(voted_user_id)
    voted_user_name = voted_user.name if voted_user else f"User {voted_user_id}"
    del guild_vote_data[str(voted_user_id)][str(interaction.user.id)]
    
    if not guild_vote_data[str(voted_user_id)]:
        del guild_vote_data[str(voted_user_id)]
    
    save_vote_data()
    vote_count = len(guild_vote_data.get(str(voted_user_id), {}))
    await interaction.followup.send(f"✅ You removed your vote from {voted_user_name}.")
    critical_amount = refresh_critical_amount(interaction.guild_id)
    await interaction.channel.send(f"🟠 Someone unvoted **{voted_user_name}** ({vote_count}/{critical_amount}).", silent=True)

@bot.tree.command(name="votedata", description="View all active kick votes, who has them, and who voted")
async def votedata(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    
    if is_command_disabled(interaction.guild_id, "votedata"):
        await interaction.followup.send("❌ This command is disabled in this server.")
        return
        
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.followup.send(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.")
        return

    guild_vote_data = get_vote_data(interaction.guild_id)
    
    guild_data = get_guild_data(interaction.guild_id)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    if not guild_vote_data:
        await interaction.followup.send("✅ There are currently no active kick votes against anyone in this server.")
        return

    critical_amount = refresh_critical_amount(interaction.guild_id)
    response_lines = ["**Current Kick Vote Standings:**\n"]

    for target_id_str, voters_dict in guild_vote_data.items():
        target_id = int(target_id_str)
        target_member = interaction.guild.get_member(target_id)
        target_name = target_member.name if target_member else f"Unknown User ({target_id})"
        
        vote_count = len(voters_dict)
        voter_displays = []
        anon_count = 0
        
        for voter_id_str, vote_info in voters_dict.items():
            voter_id = int(voter_id_str)
            
            # Defensive check: handle old legacy string formats vs new dict format
            is_anon = True
            if isinstance(vote_info, dict):
                is_anon = vote_info.get("anonymous", True)
            
            if is_anon:
                anon_count += 1
            else:
                voter_member = interaction.guild.get_member(voter_id)
                if voter_member:
                    voter_displays.append(voter_member.name)
                else:
                    voter_displays.append(f"Left Server ({voter_id})")

        voters_string = ""
        if voter_displays or anon_count > 0:
            combined_voters = list(voter_displays)
            if anon_count > 0:
                combined_voters.append(f"Anonymous x{anon_count}" if anon_count > 1 else "Anonymous")
            voters_string = f", voted by: {', '.join(combined_voters)}"
            
        response_lines.append(f"{target_name} - {vote_count}/{critical_amount}{voters_string}")

    final_response = "\n".join(response_lines)
    await interaction.followup.send(final_response[:1995])

# ==========================================
# 5. ACTIVITY COMMANDS
# ==========================================
@bot.tree.command(name="activity_config_set", description="Configure activity settings (Manager only)")
@app_commands.describe(
    role="The active member role",
    channel="The activity broadcast channel",
    window_days="Activity window threshold in days"
)
async def activity_config_set(interaction: discord.Interaction, role: discord.Role = None, channel: discord.TextChannel = None, window_days: int = None):
    if is_command_disabled(interaction.guild_id, "activity_config_set"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_manager(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return
    if role is None and channel is None and window_days is None:
        await interaction.response.send_message("⚠️ Provide a `role`, `channel`, or `window_days` to update.", ephemeral=True)
        return
    if window_days is not None and window_days <= 0:
        await interaction.response.send_message("❌ Activity window must be at least 1 day.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)
    changes = []
    if role is not None:
        guild_data["active_member_role"] = role.id
        changes.append(f"• Active Member Role set to **{role.name}**")
    if channel is not None:
        guild_data["activity_broadcast_channel"] = channel.id
        changes.append(f"• Activity Broadcast Channel set to {channel.mention}")
    if window_days is not None:
        guild_data["activity_window_days"] = window_days
        changes.append(f"• Activity Window set to `{window_days}` days")

    update_guild_data(interaction.guild_id, guild_data)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    await interaction.response.send_message(f"✅ **Activity Configuration Updated**\n" + "\n".join(changes))

@bot.tree.command(name="activity_config_reset", description="Reset activity settings (Manager only)")
@app_commands.describe(attribute="The setting to reset")
@app_commands.choices(attribute=[
    app_commands.Choice(name="Active Member Role", value="role"),
    app_commands.Choice(name="Broadcast Channel", value="channel"),
    app_commands.Choice(name="Activity Window", value="window"),
])
async def activity_config_reset(interaction: discord.Interaction, attribute: str):
    if is_command_disabled(interaction.guild_id, "activity_config_reset"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return
    if not is_manager(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)
    if attribute == "role":
        guild_data["active_member_role"] = None
        display = "Active Member Role"
    elif attribute == "channel":
        guild_data["activity_broadcast_channel"] = None
        display = "Activity Broadcast Channel"
    elif attribute == "window":
        guild_data["activity_window_days"] = 7
        display = "Activity Window (Reset to 7 Days)"

    update_guild_data(interaction.guild_id, guild_data)
    cooldown_seconds = guild_data.get("cooldown", 0)
    
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    await interaction.response.send_message(f"🔄 **Reset Confirmed**\n`{display}` has been reset to default.")

# Run the bot
if __name__ == "__main__":
    bot.run(TOKEN)
