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
BOT_VERSION = "1.2.0"
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
                # Break strings into chunks if they exceed Discord's 2000 character restriction
                for i in range(0, len(message_content), 1900):
                    chunk = message_content[i:i+1900]
                    await channel.send(chunk)
    except Exception as dev_err:
        print(f"Failed to transmit error logs to Discord: {dev_err}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Intercepts app command errors globally and reports tracebacks to your channel."""
    # Retrieve the full traceback message block
    tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
    tb_text = "".join(tb_lines)
    
    log_payload = (
        f"⚠️ **Application Command Error Intercepted!**\n"
        f"**Command:** `/{interaction.command.name if interaction.command else 'Unknown'}`\n"
        f"**User:** {interaction.user} (`{interaction.user.id}`)\n"
        f"**Guild:** {interaction.guild.name if interaction.guild else 'DMs'} (`{interaction.guild_id}`)\n"
        f"```python\n{tb_text}\n```"
    )
    
    # Send to your private logs
    await broadcast_error_log(log_payload)
    
    # Gracefully respond to the user so they aren't stuck with an infinitely loading application spin
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
        # 1. Fetch current guild data context to read/write backup timestamps
        data = load_shame_data()
        g_id_str = str(ERROR_GUILD_ID)
        
        # Ensure our tracking node structure exists inside the global config
        if g_id_str not in data:
            data[g_id_str] = {}
        
        last_backup_str = data[g_id_str].get("last_dev_channel_backup")
        now = datetime.now()
        
        # 2. Check if a backup was already created in the last 24 hours
        if last_backup_str:
            last_backup_time = datetime.fromisoformat(last_backup_str)
            if now < last_backup_time + timedelta(hours=24):
                print("⏱️ Discord Backup Skipped: Last archive was sent less than 24 hours ago.")
                return

        # 3. Target your private staff channel
        guild = bot.get_guild(ERROR_GUILD_ID)
        if not guild:
            print("❌ Backup Error: Dev guild not found.")
            return
            
        channel = guild.get_channel(ERROR_CHANNEL_ID) or await guild.fetch_channel(ERROR_CHANNEL_ID)
        if not channel:
            print("❌ Backup Error: Dev channel not found.")
            return

        # 4. Gather the existing data files
        files_to_send = []
        for file_name in [DATA_FILE, VOTE_DATA_FILE, USER_ACTIVITY_FILE]:
            if os.path.exists(file_name):
                files_to_send.append(discord.File(file_name))

        if not files_to_send:
            print("⚠️ Backup Warning: No local files exist to transmit.")
            return

        # 5. Broadcast attachments to Discord
        date_stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        await channel.send(
            content=f"📦 **Automated 24-Hour Database Backup**\n📅 Timestamp: `{date_stamp}`\n⚠️ *Keep these files safe for disaster recovery.*",
            files=files_to_send
        )
        print("💾 Success: Live JSON files dispatched to dev channel.")

        # 6. Update tracking timestamp and save atomically
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

@bot.tree.command(name="set_votekick_broadcast_channel", description="Set the channel for votekick broadcasts (Manager only)")
@app_commands.describe(channel="The channel to send votekick notifications to")
async def set_votekick_broadcast_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    """Set the votekick broadcast channel."""
    if is_command_disabled(interaction.guild_id, "set_votekick_broadcast_channel"):
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
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    guild_data["votekick_broadcast_channel"] = channel.id
    update_guild_data(interaction.guild_id, guild_data)

    # Clean text-only message format block
    await interaction.response.send_message(f"✅ **Votekick Broadcast Channel Updated**\nVotekick broadcast channel set to {channel.mention}")

@bot.tree.command(name="set_active_member_role", description="Set the role for active members (Manager only)")
@app_commands.describe(role="The role to assign to active members")
async def set_active_member_role(interaction: discord.Interaction, role: discord.Role):
    """Set the active member role."""
    if is_command_disabled(interaction.guild_id, "set_active_member_role"):
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
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    guild_data["active_member_role"] = role.id
    update_guild_data(interaction.guild_id, guild_data)

    # Clean text-only response format block with plaintext role name
    await interaction.response.send_message(f"✅ **Active Member Role Updated**\nActive member role set to **{role.name}**")

@bot.tree.command(name="set_activity_broadcast_channel", description="Set the channel for activity role updates (Manager only)")
@app_commands.describe(channel="The channel to log active role changes in")
async def set_activity_broadcast_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    """Set the activity broadcast channel."""
    if is_command_disabled(interaction.guild_id, "set_activity_broadcast_channel"):
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
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    guild_data["activity_broadcast_channel"] = channel.id
    update_guild_data(interaction.guild_id, guild_data)

    # Clean text-only message format block
    await interaction.response.send_message(f"✅ **Activity Broadcast Channel Updated**\nActivity broadcast channel set to {channel.mention}")

@bot.tree.command(name="set_activity_window", description="Set the inactivity threshold in days (Manager only)")
@app_commands.describe(days="Number of days of silence before a member becomes inactive")
async def set_activity_window(interaction: discord.Interaction, days: int):
    """Set the activity window."""
    if is_command_disabled(interaction.guild_id, "set_activity_window"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return

    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return

    if not is_manager(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    if days <= 0:
        await interaction.response.send_message("❌ The activity window must be at least 1 day.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    guild_data["activity_window_days"] = days
    update_guild_data(interaction.guild_id, guild_data)

    # Format plain text interaction response block
    await interaction.response.send_message(f"✅ **Activity Window Updated**\nActivity window set to `{days}` days.")
    

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

# Background Task: Manages Active Member Role assignments (Runs every 5 mins)
@tasks.loop(minutes=5)
async def manage_active_roles_loop():
    """Periodically scans all guilds and syncs active member roles based on recent activity windows."""
    try:
        # Load up your protected configuration data matrix
        data = load_shame_data()
        
        for guild_id_str, guild_config in data.items():
            role_id = guild_config.get("active_member_role")
            if not role_id:
                continue
                
            guild = bot.get_guild(int(guild_id_str))
            if not guild:
                continue
                
            role = guild.get_role(role_id)
            # Safeguard: Skip if role was deleted, or if the bot is below it in the hierarchy
            if not role or not guild.me.guild_permissions.manage_roles or guild.me.top_role <= role:
                continue
                
            window_days = guild_config.get("activity_window_days", 7)
            cutoff = datetime.now() - timedelta(days=window_days)
            
            # FIXED: Safe, direct dict collection of this server's user timelines
            guild_activity = user_activity.get(guild_id_str, {})
            
            # Identify who has spoken within the active target window
            active_user_ids = set()
            for u_id_str, ts_str in guild_activity.items():
                try:
                    if datetime.fromisoformat(ts_str) >= cutoff:
                        active_user_ids.add(int(u_id_str))
                except Exception:
                    pass
            
            broadcast_channel_id = guild_config.get("activity_broadcast_channel")
            broadcast_channel = guild.get_channel(broadcast_channel_id) if broadcast_channel_id else None
            
            # Scan members across the server to add/remove roles
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
                    pass  # Ignore if hierarchy prevents editing a specific user (like the server owner)
                    
    except Exception as e:
        print(f"Error in manage_active_roles_loop task frame: {e}")
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        await broadcast_error_log(f"🚨 **Background Task Loop Failure (`manage_active_roles_loop`):**\n```python\n{tb}\n```")

def get_guild_data(guild_id):
    """Get or create guild data."""
    data = load_shame_data()
    guild_id_str = str(guild_id)
    if guild_id_str not in data:
        data[guild_id_str] = {
            "manager_role": None,
            "shame_channel": None,
            "message_log_channel": None,
            "cooldown": 0,
            "expiry_days": None,
            "votekick_ban_duration": 7,  # Default 7 days
            "entries": {},
            "disabled_commands": [],
            "message_markings": {}
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
    """Scans text channel history for all joined guilds in the background 
    to populate user activity data after a JSON wipe. Completely non-blocking.
    """
    await bot.wait_until_ready()
    print("🔍 [History Scanner] Starting background message history sync across all guilds...")
    
    try:
        now = datetime.now()
        # Scan history within a 7-day threshold
        cutoff = now - timedelta(days=7)
        updated_any = False

        for guild in bot.guilds:
            print(f"📊 [History Scanner] Analyzing guild: {guild.name} ({guild.id})")
            guild_id_str = str(guild.id)
            
            # Ensure the structure exists in our live global dictionary
            if guild_id_str not in user_activity:
                user_activity[guild_id_str] = {}

            # Gather viewable text channels
            for channel in guild.text_channels:
                # Skip if we lack permissions to view or read messages
                perms = channel.permissions_for(guild.me)
                if not perms.read_messages or not perms.read_message_history:
                    continue
                
                try:
                    # Non-blocking scan across historical logs up to the cutoff date
                    async for message in channel.history(after=cutoff, limit=1000):
                        if message.author.bot:
                            continue
                        
                        user_id_str = str(message.author.id)
                        msg_time = message.created_at.replace(tzinfo=None) # Keep it naive to match isoformat expectations
                        
                        # Only keep the most recent timestamp found
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
            
            # Recalculate true voting limits immediately for this server once processed
            refresh_critical_amount(guild.id)

        # Commit changes securely to disk via atomic swap if any rows shifted
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
    """Count active users (who sent at least 1 message in the last 7 days) using stored activity data."""
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
                
    # Failsafe: return at least 1 to prevent division by zero errors in critical amount calculations
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
        
        # Clean up empty entries
        users_to_remove = [uid for uid, voters in users.items() if not voters]
        for uid in users_to_remove:
            del users[uid]
        
        if users:
            vote_data[guild_id_str] = users
            save_vote_data()
    
    return affected_users


def clear_all_votes_in_guild(guild_id: int):
    """Clear all votes in a guild (called when any user is kicked in that guild)."""
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


def get_all_command_names():
    """Get all available bot command names (excluding context menu commands)."""
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
    # Filter out enable, disable
    available = [cmd for cmd in all_commands if cmd not in ["enable", "disable"]]
    # Filter by current input
    filtered = [cmd for cmd in available if cmd.startswith(current.lower())]
    return [app_commands.Choice(name=cmd, value=cmd) for cmd in filtered[:25]]


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





def calculate_critical_amount(active_users: int) -> int:
    """Calculate the critical amount needed to kick someone (majority)."""
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
        
        # We must use list() because we will be modifying the dictionary during iteration
        for guild_id_str, targets in list(vote_data.items()):
            guild = bot.get_guild(int(guild_id_str))
            if not guild:
                continue
                
            for target_id_str, voters in list(targets.items()):
                expired_voters = []
                
                for voter_id_str, timestamp_str in list(voters.items()):
                    vote_time = datetime.fromisoformat(timestamp_str)
                    if vote_time < one_day_ago:
                        expired_voters.append(voter_id_str)
                
                # If we have expired votes, handle them
                if expired_voters:
                    for voter_id in expired_voters:
                        del vote_data[guild_id_str][target_id_str][voter_id]
                    
                    save_vote_data()
                    
                    # Get user objects for the broadcast message
                    target_member = guild.get_member(int(target_id_str)) or await bot.fetch_user(int(target_id_str))
                    
                    # Find a channel to broadcast the expiration notice
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
                
                # Clean up empty structures
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
    """Check if user is on cooldown in guild. Returns remaining seconds or 0 if not on cooldown."""
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
    """Check if user is bot manager (has Manage Server permission or designated manager role)."""
    # Check if user has Manage Server permission (includes Server Owner and Administrator)
    if interaction.user.guild_permissions.manage_guild:
        return True
    
    # Check if user has the designated manager role
    guild_data = get_guild_data(interaction.guild_id)
    manager_role_id = guild_data.get("manager_role")
    
    if manager_role_id is None:
        return False
    
    manager_role = interaction.guild.get_role(manager_role_id)
    if manager_role is None:
        return False
    
    return manager_role in interaction.user.roles


def is_bot_owner(interaction: discord.Interaction):
    """Check if user is bot owner."""
    return interaction.user.id == BOT_OWNER_ID


def is_moderator(interaction: discord.Interaction):
    """Check if user is a moderator (has Manage Server permission in any way)."""
    return interaction.user.guild_permissions.manage_guild


def is_manager(interaction: discord.Interaction):
    """Check if user is a manager (moderator or has manager role)."""
    # Check if user is a moderator
    if is_moderator(interaction):
        return True
    
    # Check if user has the designated manager role
    guild_data = get_guild_data(interaction.guild_id)
    manager_role_id = guild_data.get("manager_role")
    
    if manager_role_id is None:
        return False
    
    manager_role = interaction.guild.get_role(manager_role_id)
    if manager_role is None:
        return False
    
    return manager_role in interaction.user.roles


@bot.event
async def on_guild_join(guild):
    """Initialize guild data on join."""
    get_guild_data(guild.id)
    print(f"Joined guild: {guild.name} (ID: {guild.id})")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
        
    # Track metrics live on incoming messages using your atomic handlers
    update_user_activity(message.guild.id, message.author.id)
    
    # Process instant active role assignment if configured
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

# Background Task: Safely runs the 24-hour disaster recovery backup file sequence
@tasks.loop(hours=24)
async def discord_backup_loop():
    """Triggers the automated file attachment transfer sequence every 24 hours."""
    await run_discord_channel_backup()

@bot.event
async def on_ready():
    """Bot ready event."""
    print(f'{bot.user} has connected to Discord!')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
        
    # Load data structures on startup
    load_vote_data()
    load_user_activity()
    
    # Pass key strings straight to match utility lookup schemas
    for guild_id_str in vote_data.keys():
        refresh_critical_amount(guild_id_str)
        
    # Boot background execution loops safely
    if not check_expired_votes.is_running():
        check_expired_votes.start()
        
    if not discord_backup_loop.is_running():
        discord_backup_loop.start()
        
    if not manage_active_roles_loop.is_running():
        manage_active_roles_loop.start()

    # KICK OFF NON-BLOCKING HISTORICAL RECONCILIATION TASKS HERE
    asyncio.create_task(scan_guild_history_async())
        
    await broadcast_error_log("🟢 **Bot Startup Successful!** Systems initialized and historical scanner task dispatched.")


@bot.tree.command(name="info", description="Get bot information")
async def info(interaction: discord.Interaction):
    """Get bot info."""
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
    
    # Manager Role
    manager_role_id = guild_data.get("manager_role")
    manager_role = interaction.guild.get_role(manager_role_id) if manager_role_id else None
    manager_role_text = manager_role.name if manager_role else "None"
    
    # Disabled Commands
    disabled_cmds = ", ".join(guild_data.get("disabled_commands", [])) or "None"
    
    # Shame Stuff
    expiry_timer = guild_data.get("expiry_days", "Not Set")
    shame_channel_id = guild_data.get("shame_channel")
    shame_channel = f"<#{shame_channel_id}>" if shame_channel_id else "Not Set"
    
    # Votekick Stuff
    votekick_ban_duration = guild_data.get("votekick_ban_duration", 7)
    vk_bc_id = guild_data.get("votekick_broadcast_channel")
    vk_bc = f"<#{vk_bc_id}>" if vk_bc_id else "Not Set"
    
    # Marking Stuff
    ml_id = guild_data.get("message_log_channel")
    ml_channel = f"<#{ml_id}>" if ml_id else "Not Set"
    
    # Activity Stuff
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
        "**Marking Stuff**\n"
        f"Marking Broadcast Channel: {ml_channel}\n\n"
        "**Activity Stuff**\n"
        f"Active Member Role: {am_role_text}\n"
        f"Activity Broadcast Channel: {act_bc}\n"
        f"Activity Window: {act_win}"
    )
    
    await interaction.response.send_message(response_text)


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


@bot.tree.command(name="shame", description="Add a user to the hall of shame (Manager only)")
@app_commands.describe(user="The user to add", reason="Reason for shame", date="Optional date (YYYY-MM-DD format)")
async def shame(interaction: discord.Interaction, user: discord.User, reason: str, date: str = None):
    """Add user to shame list."""
    # Check if command is disabled
    if is_command_disabled(interaction.guild_id, "shame"):
        await interaction.response.send_message(
            "❌ This command is disabled in this server.",
            ephemeral=True
        )
        return
    
    # Check cooldown FIRST (applies to everyone)
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(
            f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.",
            ephemeral=True
        )
        return
    
    # Check permissions (Manager only)
    if not is_manager(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True
        )
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    
    # Remove expired entries
    remove_expired_entries(guild_data)
    
    # Check if expiry_days is set
    if guild_data.get("expiry_days") is None:
        await interaction.response.send_message(
            "❌ Shame entry expiry time must be set before adding entries. Use `/set_expiry_timer` to configure it.",
            ephemeral=True
        )
        return
    
    # Check if shame_channel is set
    if guild_data.get("shame_channel") is None:
        await interaction.response.send_message(
            "❌ Shame channel must be set before adding entries. Use `/set_shame_channel` to configure it.",
            ephemeral=True
        )
        return
    
    # Parse date if provided
    if date:
        try:
            entry_date = datetime.strptime(date, "%Y-%m-%d")
            # Set time to 12:00 AM (midnight)
            entry_date = entry_date.replace(hour=0, minute=0, second=0, microsecond=0)
            entry_date_iso = entry_date.isoformat()
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date format. Please use YYYY-MM-DD (e.g., 2026-05-10)",
                ephemeral=True
            )
            return
    else:
        entry_date = datetime.now()
        entry_date_iso = entry_date.isoformat()
    
    # Check if the date is too old (already expired)
    expiry_days = guild_data.get("expiry_days")
    entry_date_obj = datetime.fromisoformat(entry_date_iso)
    expiry_date = entry_date_obj + timedelta(days=expiry_days)
    
    if datetime.now() > expiry_date:
        await interaction.response.send_message(
            f"❌ Cannot add shame entry for {date or 'today'} - it has already expired (expiry window is {expiry_days} days).",
            ephemeral=True
        )
        return
    
    # Create shame entry - storage records keep user.name style usernames
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
    
    # Set cooldown
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    # Format plain text interaction response block
    response_lines = [
        f"🚨 **{user.name}** has been added to the hall of shame",
        f"**Reason:** {reason}",
        f"**Date:** {entry_date_iso.split('T')[0]}",
        f"**Entry ID:** {entry_id}",
        f"*Added by {interaction.user.name}*"
    ]
    await interaction.response.send_message("\n".join(response_lines))
    
    # Send plain text broadcast alert to the shame channel if configured
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
    """Remove user from shame list."""
    # Check if command is disabled
    if is_command_disabled(interaction.guild_id, "unshame"):
        await interaction.response.send_message(
            "❌ This command is disabled in this server.",
            ephemeral=True
        )
        return
    
    # Check cooldown FIRST (applies to everyone)
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(
            f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.",
            ephemeral=True
        )
        return
    
    # Check permissions (Manager only)
    if not is_manager(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True
        )
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    entry_id_str = str(entry_id)
    
    if entry_id_str not in guild_data["entries"]:
        await interaction.response.send_message(
            f"❌ Entry ID {entry_id} not found.",
            ephemeral=True
        )
        return
    
    entry = guild_data["entries"][entry_id_str]
    del guild_data["entries"][entry_id_str]
    update_guild_data(interaction.guild_id, guild_data)
    
    # Set cooldown
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    # Build a plain-text response matching the clean markdown layout
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
    """List user's shame entries."""
    # Check if command is disabled
    if is_command_disabled(interaction.guild_id, "list_my_shame"):
        await interaction.response.send_message(
            "❌ This command is disabled in this server.",
            ephemeral=True
        )
        return
    
    # Check cooldown FIRST (applies to everyone)
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(
            f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.",
            ephemeral=True
        )
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    
    # Remove expired entries
    remove_expired_entries(guild_data)
    update_guild_data(interaction.guild_id, guild_data)
    
    user_entries = []
    
    for entry_id, entry in guild_data["entries"].items():
        if entry["user_id"] == interaction.user.id:
            user_entries.append((entry_id, entry))
    
    # Set cooldown
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    if not user_entries:
        await interaction.response.send_message("✅ **Your Hall of Shame**\nYou have no entries in the hall of shame!")
        return
    
    # Start building the text-only response block
    response_lines = [
        "**Your Hall of Shame**",
        f"**Shame Count**: {len(user_entries)}\n"
    ]
    
    expiry_days = guild_data.get("expiry_days", 30)
    
    for idx, (entry_id, entry) in enumerate(user_entries, 1):
        entry_date = datetime.fromisoformat(entry["date"])
        expiry_date = entry_date + timedelta(days=expiry_days)
        
        # Convert to Unix timestamp
        entry_timestamp = int(entry_date.timestamp())
        expiry_timestamp = int(expiry_date.timestamp())
        
        response_lines.append(f"**Entry {idx}:**")
        response_lines.append(f"└ <t:{entry_timestamp}:d> - Expires <t:{expiry_timestamp}:R>")
        response_lines.append(f"  *Reason:* {entry['reason']}\n")
    
    # Join everything up safely into a standard plain text message
    final_response = "\n".join(response_lines)
    
    # Slice to 1995 characters to safely prevent breaching Discord's 2000 character limits
    await interaction.response.send_message(final_response[:1995])


@bot.tree.command(name="list_all_shame", description="List all hall of shame entries")
async def list_all_shame(interaction: discord.Interaction):
    """List all shame entries."""
    # Check if command is disabled
    if is_command_disabled(interaction.guild_id, "list_all_shame"):
        await interaction.response.send_message(
            "❌ This command is disabled in this server.",
            ephemeral=True
        )
        return
    
    # Check cooldown FIRST (applies to everyone)
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(
            f"⏱️ You're on cooldown. Wait {remaining:.2f} more seconds.",
            ephemeral=True
        )
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    
    # Remove expired entries
    remove_expired_entries(guild_data)
    update_guild_data(interaction.guild_id, guild_data)
    
    # Set cooldown
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    if not guild_data["entries"]:
        await interaction.response.send_message("✅ **Hall of Shame**\nThe hall of shame is empty!")
        return
    
    # Group by user
    user_shame_count = {}
    for entry_id, entry in guild_data["entries"].items():
        user_id = entry["user_id"]
        if user_id not in user_shame_count:
            user_shame_count[user_id] = {"count": 0, "entries": []}
        user_shame_count[user_id]["count"] += 1
        user_shame_count[user_id]["entries"].append((entry_id, entry))
    
    response_lines = ["**Hall of Shame Counts**\n"]
    expiry_days = guild_data.get("expiry_days", 30)
    
    # Sort by count (descending), then by most recent entry (descending)
    def sort_key(item):
        user_id, data = item
        count = data["count"]
        # Get the most recent entry date
        most_recent = max(
            (datetime.fromisoformat(entry["date"]) for entry_id, entry in data["entries"]),
            default=datetime.min
        )
        return (-count, -most_recent.timestamp())
    
    for user_id, data in sorted(user_shame_count.items(), key=sort_key):
        # Get the member from the guild to get their plaintext name
        member = interaction.guild.get_member(user_id)
        if member:
            user_display = member.name
        else:
            user_display = f"User {user_id}"
        
        count = data["count"]
        
        # Format the user header line using plaintext format rules
        response_lines.append(f"**{user_display}** - {count} entries:")
        
        for entry_id, entry in data["entries"]:
            entry_date = datetime.fromisoformat(entry["date"])
            expiry_date = entry_date + timedelta(days=expiry_days)
            
            # Convert to Unix timestamps (keeps Discord's time formatting engine alive without mentioning users)
            entry_timestamp = int(entry_date.timestamp())
            expiry_timestamp = int(expiry_date.timestamp())
            
            reason = entry.get("reason", "No reason provided")
            response_lines.append(f"└ <t:{entry_timestamp}:d> (expires <t:{expiry_timestamp}:R>) - {reason}")
            
        # Add a spacing gap between users
        response_lines.append("")
        
    # Join everything up safely into a standard text message
    final_response = "\n".join(response_lines)
    
    # Slice to 1995 characters just in case it overflows Discord's 2000 character limit
    await interaction.response.send_message(final_response[:1995])


@bot.tree.command(name="cooldown", description="Set command cooldown for the server (Manager only)")
@app_commands.describe(seconds="Cooldown in seconds (0-30)")
async def set_cooldown_cmd(interaction: discord.Interaction, seconds: int):
    """Set cooldown."""
    # Check cooldown FIRST (applies to everyone)
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(
            f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.",
            ephemeral=True
        )
        return
    
    # Check permissions (Manager only)
    if not is_manager(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True
        )
        return
    
    if seconds < 0 or seconds > 30:
        await interaction.response.send_message(
            "❌ Cooldown must be between 0 and 30 seconds.",
            ephemeral=True
        )
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    guild_data["cooldown"] = seconds
    update_guild_data(interaction.guild_id, guild_data)
    
    # Reset all cooldowns for this guild
    if interaction.guild_id in cooldowns:
        cooldowns[interaction.guild_id].clear()
    
    await interaction.response.send_message(f"✅ Command cooldown set to {seconds} seconds\nAll existing cooldowns have been reset.")


@bot.tree.command(name="set_shame_channel", description="Set the channel for shame announcements (Manager only)")
@app_commands.describe(channel="The channel to use for announcements")
async def set_shame_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    """Set shame channel."""
    # Check cooldown FIRST (applies to everyone)
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(
            f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.",
            ephemeral=True
        )
        return
    
    # Check permissions (Manager only)
    if not is_manager(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True
        )
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    
    # Set cooldown
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    guild_data["shame_channel"] = channel.id
    update_guild_data(interaction.guild_id, guild_data)
    
    await interaction.response.send_message(f"✅ Shame announcements will be sent to {channel.mention}")


@bot.tree.command(name="set_expiry_timer", description="Set expiration duration for shame entries (Manager only)")
@app_commands.describe(days="Number of days before entries expire")
async def set_expiry_timer(interaction: discord.Interaction, days: int):
    """Set expiry timer."""
    # Check cooldown FIRST (applies to everyone)
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(
            f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.",
            ephemeral=True
        )
        return
    
    # Check permissions (Manager only)
    if not is_manager(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True
        )
        return
    
    if days < 1:
        await interaction.response.send_message(
            "❌ Expiry duration must be at least 1 day.",
            ephemeral=True
        )
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    
    # Set cooldown
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    guild_data["expiry_days"] = days
    update_guild_data(interaction.guild_id, guild_data)
    
    await interaction.response.send_message(f"✅ Shame entries will expire after {days} days")


@bot.tree.command(name="set_votekick_ban_duration", description="Set ban duration for vote-to-kick (Manager only)")
@app_commands.describe(days="Number of days to ban (0-7, default: 7)")
async def set_votekick_ban_duration(interaction: discord.Interaction, days: int):
    """Set votekick ban duration."""
    # Check cooldown FIRST (applies to everyone)
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(
            f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.",
            ephemeral=True
        )
        return
    
    # Check permissions (Manager only)
    if not is_manager(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True
        )
        return
    
    if days < 0 or days > 7:
        await interaction.response.send_message(
            "❌ Ban duration must be between 0 (kick only) and 7 days (ban for a week).",
            ephemeral=True
        )
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    
    # Set cooldown
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    guild_data["votekick_ban_duration"] = days
    update_guild_data(interaction.guild_id, guild_data)
    
    if days == 0:
        await interaction.response.send_message("✅ Vote-to-kick will now only kick users (no ban).")
    else:
        await interaction.response.send_message(f"✅ Vote-to-kick will now ban users for {days} days.")

@bot.tree.command(name="vote", description="Vote to kick a user")
@app_commands.describe(user="The user to vote for", anonymous="Whether your vote is anonymous (default: True)")
async def vote(interaction: discord.Interaction, user: discord.Member, anonymous: bool = True):
    """Vote to kick a user."""
    # Instantly defer to prevent the 3-second "application did not respond" timeout
    await interaction.response.defer(ephemeral=True)
    
    # Check if command is disabled
    if is_command_disabled(interaction.guild_id, "vote"):
        await interaction.followup.send("❌ This command is disabled in this server.")
        return
    
    # Check if user is voting for themselves
    if user.id == interaction.user.id:
        await interaction.followup.send("❌ You cannot vote for yourself.")
        return
    
    # Check if user is a bot
    if user.bot:
        await interaction.followup.send("❌ You cannot vote to kick a bot.")
        return
    
    # Check if user is the server owner
    if interaction.guild.owner_id == user.id:
        await interaction.followup.send("❌ You can't vote to kick the server owner.")
        return
    
    # Check if user has higher role than bot
    bot_member = interaction.guild.me
    if user.top_role >= bot_member.top_role:
        await interaction.followup.send("❌ I can't kick that person. Please ask a moderator to move my role higher.")
        return
    
    # Get vote data for this guild
    guild_vote_data = get_vote_data(interaction.guild_id)
    
    # Check if user has already voted for ANY user in this guild
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
    
    # Record vote with current timestamp
    if str(user.id) not in guild_vote_data:
        guild_vote_data[str(user.id)] = {}
    
    guild_vote_data[str(user.id)][str(interaction.user.id)] = datetime.now().isoformat()
    save_vote_data()
    
    # Get vote count
    vote_count = len(guild_vote_data[str(user.id)])
    
    # Send ephemeral confirmation using followup
    await interaction.followup.send(f"✅ Your vote has been counted for {user.name}.")
    
    # Refresh critical amount
    critical_amount = refresh_critical_amount(interaction.guild_id)
    
    # Send public message
    if anonymous:
        await interaction.channel.send(f"🟠 Someone voted for **{user.name}** ({vote_count}/{critical_amount}).", silent=True)
    else:
        await interaction.channel.send(f"🟠 **{interaction.user.name}** voted for **{user.name}** ({vote_count}/{critical_amount}).", silent=True)
    
    # Check if user should be kicked
    if vote_count >= critical_amount:
        guild_data = get_guild_data(interaction.guild_id)
        ban_duration = guild_data.get("votekick_ban_duration", 7)
        
        try:
            if ban_duration > 0:
                # Ban for the configured duration
                await user.ban(reason=f"Vote to kick - reached critical amount (ban for {ban_duration} days)", delete_message_days=0)
                await interaction.channel.send(f"🚨 **{user.name}** has been banned for {ban_duration} days due to reaching the critical vote threshold!", silent=True)
            else:
                # Just kick
                await user.kick(reason="Vote to kick - reached critical amount")
                await interaction.channel.send(f"🚨 **{user.name}** has been kicked due to reaching the critical vote threshold!", silent=True)
            
            # RESET: Clear all votes in this guild when someone is kicked/banned
            clear_all_votes_in_guild(interaction.guild_id)
        except discord.Forbidden:
            action_type = f"banned for {ban_duration} days" if ban_duration > 0 else "kicked"
            await interaction.channel.send(f"⚠️ **{user.name}** should have been {action_type}, but I lack permission.", silent=True)


@bot.tree.command(name="unvote", description="Remove your vote")
async def unvote(interaction: discord.Interaction):
    """Remove your vote."""
    # Instantly defer to prevent the 3-second timeout
    await interaction.response.defer(ephemeral=True)
    
    # Check if command is disabled
    if is_command_disabled(interaction.guild_id, "unvote"):
        await interaction.followup.send("❌ This command is disabled in this server.")
        return
    
    # Get vote data for this guild
    guild_vote_data = get_vote_data(interaction.guild_id)
    
    # Find which user this person voted for
    voted_user_id = None
    for target_id, voters in guild_vote_data.items():
        if str(interaction.user.id) in voters:
            voted_user_id = int(target_id)
            break
    
    if voted_user_id is None:
        await interaction.followup.send("❌ You haven't voted yet.")
        return
    
    # Remove vote
    voted_user = interaction.guild.get_member(voted_user_id)
    voted_user_name = voted_user.name if voted_user else f"User {voted_user_id}"
    
    del guild_vote_data[str(voted_user_id)][str(interaction.user.id)]
    
    # Clean up if no more votes for this user
    if not guild_vote_data[str(voted_user_id)]:
        del guild_vote_data[str(voted_user_id)]
    
    save_vote_data()
    
    # Get new vote count
    vote_count = len(guild_vote_data.get(str(voted_user_id), {}))
    
    # Send ephemeral confirmation via followup
    await interaction.followup.send(f"✅ You removed your vote from {voted_user_name}.")
    
    # Refresh critical amount
    critical_amount = refresh_critical_amount(interaction.guild_id)
    
    # Send public message
    await interaction.channel.send(f"🟠 Someone unvoted **{voted_user_name}** ({vote_count}/{critical_amount}).", silent=True)


@bot.tree.command(name="disable", description="Disable a bot command in this server (Moderator only)")
@app_commands.describe(command="The command to disable")
@app_commands.autocomplete(command=command_autocomplete)
async def disable_cmd(interaction: discord.Interaction, command: str):
    """Disable a command."""
    # Check cooldown FIRST (applies to everyone)
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(
            f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.",
            ephemeral=True
        )
        return
    
    # Check permissions (Moderator only)
    if not is_moderator(interaction):
        await interaction.response.send_message(
            "❌ You need Manage Server permission to use this command.",
            ephemeral=True
        )
        return
    
    # Check if command exists
    if not is_valid_command(command):
        await interaction.response.send_message(
            f"❌ The command `{command}` does not exist.",
            ephemeral=True
        )
        return
    
    # Prevent disabling enable/disable commands
    if command.lower() in ["enable", "disable"]:
        await interaction.response.send_message(
            "❌ You cannot disable the enable or disable commands.",
            ephemeral=True
        )
        return
    
    # Check if command is already disabled
    if is_command_disabled(interaction.guild_id, command.lower()):
        await interaction.response.send_message(
            f"❌ The command `{command}` is already disabled in this server.",
            ephemeral=True
        )
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    
    # Set cooldown
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    disable_command(interaction.guild_id, command.lower())
    
    await interaction.response.send_message(f"🟠 The `{command}` command has been disabled in this server.")


@bot.tree.command(name="enable", description="Enable a bot command in this server (Moderator only)")
@app_commands.describe(command="The command to enable")
@app_commands.autocomplete(command=command_autocomplete)
async def enable_cmd(interaction: discord.Interaction, command: str):
    """Enable a command."""
    # Check cooldown FIRST (applies to everyone)
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(
            f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.",
            ephemeral=True
        )
        return
    
    # Check permissions (Moderator only)
    if not is_moderator(interaction):
        await interaction.response.send_message(
            "❌ You need Manage Server permission to use this command.",
            ephemeral=True
        )
        return
    
    # Check if command exists
    if not is_valid_command(command):
        await interaction.response.send_message(
            f"❌ The command `{command}` does not exist.",
            ephemeral=True
        )
        return
    
    # Check if command is already enabled
    if not is_command_disabled(interaction.guild_id, command.lower()):
        await interaction.response.send_message(
            f"❌ The command `{command}` is already enabled in this server.",
            ephemeral=True
        )
        return
    
    guild_data = get_guild_data(interaction.guild_id)
    
    # Set cooldown
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    
    enable_command(interaction.guild_id, command.lower())
    
    await interaction.response.send_message(f"✅ The `{command}` command has been enabled in this server.")

# --- Message marking helpers ---

MARK_TYPE_LABELS = {
    "ragebait": ("⚠️", "Ragebait", discord.Color.orange()),
}


async def log_marking_action(guild: discord.Guild, guild_data: dict, action: str, mark_type: str, message: discord.Message, actor: discord.Member):
    """Send a plain text log message to the message log channel if configured."""
    log_channel_id = guild_data.get("message_log_channel")
    if not log_channel_id:
        return
    log_channel = guild.get_channel(log_channel_id)
    if not log_channel:
        return

    emoji, label, _ = MARK_TYPE_LABELS.get(mark_type, ("🏷️", mark_type.capitalize(), None))
    
    log_text = (
        f"{emoji} **Message {action.capitalize()} as {label}**\n"
        f"**Author:** {message.author.name}\n"
        f"**Channel:** {message.channel.mention}\n"
        f"**{action.capitalize()} by:** {actor.name}\n"
        f"**Message:** {message.content[:1000] if message.content else '[No text content]'}\n"
        f"**Jump:** [Go to message]({message.jump_url})\n"
        f"**Message ID:** `{message.id}`"
    )
    
    try:
        await log_channel.send(log_text, silent=True)
    except discord.Forbidden:
        pass


async def apply_mark(interaction: discord.Interaction, message: discord.Message, mark_type: str):
    """Core logic for marking a message. Sends ephemeral reply, stores data, logs."""
    guild_data = get_guild_data(interaction.guild_id)

    # Set cooldown
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    markings = guild_data.setdefault("message_markings", {})
    msg_key = str(message.id)
    if msg_key not in markings:
        markings[msg_key] = {
            "channel_id": message.channel.id,
            "author_id": message.author.id,
            "marks": []
        }

    # Check if this mark type already exists
    existing = [m for m in markings[msg_key]["marks"] if m["type"] == mark_type]
    if existing:
        await interaction.response.send_message(
            f"❌ This message is already marked as **{mark_type}**.",
            ephemeral=True
        )
        return

    markings[msg_key]["marks"].append({
        "type": mark_type,
        "marked_by": interaction.user.id,
        "marked_at": datetime.now().isoformat()
    })
    update_guild_data(interaction.guild_id, guild_data)

    # Acknowledge quickly first
    await interaction.response.send_message(
        f"✅ Message marked as **{mark_type}**.",
        ephemeral=True
    )

    # Add reactions for ragebait (after acknowledgment)
    if mark_type == "ragebait":
        reactions_to_add = ["🇷", "🇦", "🇬", "🇪", "🇧", "🅰️", "🇮", "🇹"]
        for reaction in reactions_to_add:
            try:
                await message.add_reaction(reaction)
            except discord.Forbidden:
                pass

    await log_marking_action(interaction.guild, guild_data, "marked", mark_type, message, interaction.user)


@bot.tree.command(name="mark_message", description="Mark a message with a specific type")
@app_commands.describe(
    message_id="The ID of the message to mark",
    mark_type="Type of mark to apply"
)
@app_commands.choices(mark_type=[
    app_commands.Choice(name="Ragebait", value="ragebait"),
])
async def mark_message(interaction: discord.Interaction, message_id: str, mark_type: str):
    """Mark a message with a specific type."""
    if is_command_disabled(interaction.guild_id, "mark_message"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return

    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return

    try:
        message_id_int = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID format.", ephemeral=True)
        return

    try:
        message = await interaction.channel.fetch_message(message_id_int)
    except discord.NotFound:
        await interaction.response.send_message("❌ Message not found. Make sure you're in the same channel as the message.", ephemeral=True)
        return
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to read messages in this channel.", ephemeral=True)
        return

    await apply_mark(interaction, message, mark_type)


@bot.tree.context_menu(name="Mark as Ragebait")
async def mark_message_context(interaction: discord.Interaction, message: discord.Message):
    """Mark a message as ragebait via right-click context menu."""
    if is_command_disabled(interaction.guild_id, "mark_message"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return

    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return

    await apply_mark(interaction, message, "ragebait")


@bot.tree.context_menu(name="Unmark as Ragebait")
async def unmark_message_context(interaction: discord.Interaction, message: discord.Message):
    """Unmark a message as ragebait via right-click context menu."""
    if is_command_disabled(interaction.guild_id, "unmark_message"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return

    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return

    # Get message ID
    message_id_int = message.id

    guild_data = get_guild_data(interaction.guild_id)

    # Set cooldown
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    markings = guild_data.get("message_markings", {})
    msg_key = str(message_id_int)

    if msg_key not in markings or not any(m["type"] == "ragebait" for m in markings[msg_key]["marks"]):
        await interaction.response.send_message(
            "❌ This message doesn't have a **ragebait** mark.",
            ephemeral=True
        )
        return

    markings[msg_key]["marks"] = [m for m in markings[msg_key]["marks"] if m["type"] != "ragebait"]

    # Clean up entry if no marks remain
    if not markings[msg_key]["marks"]:
        del markings[msg_key]

    update_guild_data(interaction.guild_id, guild_data)

    # Fetch message for reaction removal and logging (best effort)
    try:
        message = await interaction.channel.fetch_message(message_id_int)
    except Exception:
        message = None

    await interaction.response.send_message(
        "✅ Removed **ragebait** mark from message.",
        ephemeral=True
    )

    # Remove reactions for ragebait
    if message:
        reactions_to_remove = ["🇷", "🇦", "🇬", "🇪", "🇧", "🅰️", "🇮", "🇹"]
        for reaction in reactions_to_remove:
            try:
                await message.remove_reaction(reaction, bot.user)
            except discord.Forbidden:
                pass

    # Log the unmarking
    if message:
        await log_marking_action(interaction.guild, guild_data, "unmarked", "ragebait", message, interaction.user)


@bot.tree.command(name="unmark_message", description="Remove a mark from a message")
@app_commands.describe(
    message_id="The ID of the message to unmark",
    mark_type="Type of mark to remove"
)
@app_commands.choices(mark_type=[
    app_commands.Choice(name="Ragebait", value="ragebait"),
])
async def unmark_message(interaction: discord.Interaction, message_id: str, mark_type: str):
    """Remove a mark from a message."""
    if is_command_disabled(interaction.guild_id, "unmark_message"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return

    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return

    try:
        message_id_int = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID format.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)

    # Set cooldown
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    markings = guild_data.get("message_markings", {})
    msg_key = str(message_id_int)

    if msg_key not in markings or not any(m["type"] == mark_type for m in markings[msg_key]["marks"]):
        await interaction.response.send_message(
            f"❌ This message doesn't have a **{mark_type}** mark.",
            ephemeral=True
        )
        return

    markings[msg_key]["marks"] = [m for m in markings[msg_key]["marks"] if m["type"] != mark_type]

    # Clean up entry if no marks remain
    if not markings[msg_key]["marks"]:
        del markings[msg_key]

    update_guild_data(interaction.guild_id, guild_data)

    # Fetch message for reaction removal and logging (best effort)
    try:
        message = await interaction.channel.fetch_message(message_id_int)
    except Exception:
        message = None

    await interaction.response.send_message(
        f"✅ Removed **{mark_type}** mark from message.",
        ephemeral=True
    )

    # Remove reactions for ragebait
    if mark_type == "ragebait" and message:
        reactions_to_remove = ["🇷", "🇦", "🇬", "🇪", "🇧", "🅰️", "🇮", "🇹"]
        for reaction in reactions_to_remove:
            try:
                await message.remove_reaction(reaction, bot.user)
            except discord.Forbidden:
                pass

    # Log the unmarking
    if message:
        await log_marking_action(interaction.guild, guild_data, "unmarked", mark_type, message, interaction.user)


@bot.tree.command(name="message_info", description="View all markings on a message")
@app_commands.describe(message_id="The ID of the message to inspect")
async def message_info(interaction: discord.Interaction, message_id: str):
    """Get all markings on a message."""
    if is_command_disabled(interaction.guild_id, "message_info"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return

    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return

    try:
        message_id_int = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID format.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)
    markings = guild_data.get("message_markings", {})
    msg_key = str(message_id_int)

    if msg_key not in markings or not markings[msg_key]["marks"]:
        await interaction.response.send_message("ℹ️ This message has no markings.", ephemeral=True)
        return

    entry = markings[msg_key]

    # Resolve author display name using plaintext
    author = interaction.guild.get_member(entry["author_id"])
    author_str = author.name if author else f"User {entry['author_id']}"

    # Resolve channel clickable reference (channels remain clickable as #channel-name)
    channel = interaction.guild.get_channel(entry["channel_id"])
    channel_str = channel.mention if channel else f"<#{entry['channel_id']}>"

    # Start building the plain text response block
    response_lines = [
        "ℹ️ **Message Markings**",
        f"**Author:** {author_str}",
        f"**Channel:** {channel_str}\n",
        "**Marks:**"
    ]

    for mark in entry["marks"]:
        emoji, label, _ = MARK_TYPE_LABELS.get(mark["type"], ("🏷️", mark["type"].capitalize(), None))
        marked_by = interaction.guild.get_member(mark["marked_by"])
        marked_at = datetime.fromisoformat(mark["marked_at"])
        
        # Convert user to plaintext name
        marked_by_str = marked_by.name if marked_by else f'User {mark["marked_by"]}'
        
        response_lines.append(f"└ {emoji} **{label}** — by {marked_by_str} <t:{int(marked_at.timestamp())}:R>")

    response_lines.append(f"\n*Message ID: {message_id_int}*")

    # Safely join and slice to 1995 to prevent overflow errors
    final_response = "\n".join(response_lines)
    await interaction.response.send_message(final_response[:1995], ephemeral=True)


@bot.tree.command(name="set_message_log_channel", description="Set the channel for message marking logs (Manager only)")
@app_commands.describe(channel="The channel to send marking logs to")
async def set_message_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    """Set the message log channel."""
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return

    if not is_manager(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)

    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    guild_data["message_log_channel"] = channel.id
    update_guild_data(interaction.guild_id, guild_data)

    await interaction.response.send_message(
        f"✅ Message log channel set to {channel.mention}.",
        ephemeral=True
    )


# Run the bot
if __name__ == "__main__":
    bot.run(TOKEN)
