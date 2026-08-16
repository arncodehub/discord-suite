import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta, timezone
import asyncio
import traceback
import re
import sys

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
BOT_VERSION = "1.8.2"
BOT_OWNER_ID = 807087691522375681  # Set this to your Discord ID for owner commands

# Data storage files
DATA_FILE = "shame_data.json"
VOTE_DATA_FILE = "vote_data.json"

# -----------------------------
# Wordle Auto Role Constants
# -----------------------------
WORDLE_GUILD_ID = 1367634363474251957

WORDLE_CHANNEL_ID = 1436390213466198137
WORDLE_COMMAND_CHANNEL_ID = 1437902486328447067

WORDLE_BOT_ID = 1211781489931452447

WORDLE_PRO_ROLE_ID = 1508990442736324730
WORDLE_FAILURE_ROLE_ID = 1503904966539219064

WORDLE_SCAN_DAYS = 7

# Cooldown tracking: {guild_id: {user_id: timestamp}}
cooldowns = {}

# Vote data: {guild_id: {target_user_id: {voter_id: vote_timestamp}}}
vote_data = {}

# Active users cache (populated every 5 mins): {guild_id: {user_id_set}}
active_users_cache = {}

# Pending developer DM logs awaiting a successful send
pending_dev_dm_logs = []

# Last critical amount refresh time per guild: {guild_id: datetime}
last_critical_refresh = {}

critical_amounts = {}

# Unvote tracking: {guild_id: datetime} - tracks last unvote time per guild
last_unvote_time = {}

# Track if initial message history sync has completed
initial_sync_completed = False

# --- New Globals for Rate Limiting ---
RATE_LIMIT_FILE = "rate_limit.json"
rate_limits_config = {}

# Tracks timestamps to enforce cross-command rate limits
# Format: {guild_id: {user_id: {command_name: datetime_object}}}
command_timestamps = {}

# Wordle groups
wordle_group = app_commands.Group(
    name="wordle",
    description="Wordle commands."
)


wordle_autorole_group = app_commands.Group(
    name="autorole",
    description="Manage automatic Wordle roles.",
    parent=wordle_group
)

def initialize_rate_limits():
    """Loads and strictly validates the rate limits configuration."""
    global rate_limits_config
    
    # Create file if it doesn't exist
    if not os.path.exists(RATE_LIMIT_FILE):
        with open(RATE_LIMIT_FILE, 'w') as f:
            json.dump({}, f, indent=4)
        print(f"Created {RATE_LIMIT_FILE} with default empty configuration.")
        return

    # Load file
    with open(RATE_LIMIT_FILE, 'r') as f:
        try:
            rate_limits_config = json.load(f)
        except json.JSONDecodeError as e:
            print(f"CRITICAL ERROR: Failed to parse {RATE_LIMIT_FILE}. Invalid JSON format.\n{e}")
            sys.exit(1)  # Halt program

    # Validate schema
    for cmd, config in rate_limits_config.items():
        if not isinstance(config, dict):
            print(f"CRITICAL ERROR: Config for '{cmd}' must be a JSON object.")
            sys.exit(1)
        
        prev_cmd = config.get("previousCommand")
        seconds = config.get("seconds")
        
        if not isinstance(prev_cmd, str):
            print(f"CRITICAL ERROR: Rate limit for '{cmd}' requires 'previousCommand' to be a string.")
            sys.exit(1)
        if not isinstance(seconds, int) or seconds <= 0:
            print(f"CRITICAL ERROR: Rate limit for '{cmd}' requires 'seconds' to be a whole number > 0.")
            sys.exit(1)

def refresh_critical_amount(guild_id):
    """Calculates and updates the cached critical vote threshold for a guild."""
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return
    active_count = get_active_users_count(guild)
    # Simple majority math for votekick threshold
    critical_amounts[str(guild_id)] = max(2, (active_count // 2) + 1)
    last_critical_refresh[str(guild_id)] = datetime.now()

def get_critical_amount(guild_id) -> int:
    """Returns the cached critical amount."""
    guild_id_str = str(guild_id)
    if guild_id_str not in critical_amounts:
        refresh_critical_amount(guild_id)
    return critical_amounts.get(guild_id_str, 2)

def is_transient_network_error(error: Exception) -> bool:
    """Returns True for errors that may resolve on a later retry."""
    if isinstance(error, (asyncio.TimeoutError, OSError)):
        return True

    if isinstance(error, discord.HTTPException):
        return error.status in {429, 500, 502, 503, 504}

    return False


@tasks.loop(minutes=5)
async def retry_pending_dev_dm_logs():
    """Retries developer DM logs that failed due to transient network errors."""
    if not pending_dev_dm_logs or not bot.is_ready():
        return

    try:
        owner = bot.get_user(BOT_OWNER_ID) or await bot.fetch_user(BOT_OWNER_ID)

        if not owner:
            return

        while pending_dev_dm_logs:
            chunk = pending_dev_dm_logs[0]

            try:
                await owner.send(chunk, silent=True)
                pending_dev_dm_logs.pop(0)

            except Exception as e:
                if is_transient_network_error(e):
                    print(f"⏳ Developer DM retry failed; will retry later: {e}")
                    return

                print(f"❌ Dropping developer DM after permanent failure: {e}")
                pending_dev_dm_logs.pop(0)

    except Exception as e:
        if is_transient_network_error(e):
            print(f"⏳ Developer DM retry connection failed: {e}")
        else:
            print(f"❌ Developer DM retry failed: {e}")


@retry_pending_dev_dm_logs.before_loop
async def before_retry_pending_dev_dm_logs():
    await bot.wait_until_ready()

async def broadcast_error_log(message_content: str):
    """Safely sends logs to the bot owner's DMs, retrying transient failures."""
    chunks = [
        message_content[i:i + 1900]
        for i in range(0, len(message_content), 1900)
    ]

    if not chunks:
        return

    try:
        if not bot.is_ready():
            pending_dev_dm_logs.extend(chunks)
            return

        owner = bot.get_user(BOT_OWNER_ID) or await bot.fetch_user(BOT_OWNER_ID)

        if not owner:
            print("❌ Failed to find bot owner for developer DM.")
            return

        for index, chunk in enumerate(chunks):
            try:
                await owner.send(chunk, silent=True)
            except Exception as e:
                if is_transient_network_error(e):
                    pending_dev_dm_logs.extend(chunks[index:])
                    print(
                        f"📥 Developer DM queued for retry after transient "
                        f"network error: {e}"
                    )
                else:
                    print(f"❌ Failed to transmit developer DM: {e}")
                return

    except Exception as e:
        if is_transient_network_error(e):
            pending_dev_dm_logs.extend(chunks)
            print(f"📥 Developer DM queued for retry: {e}")
        else:
            print(f"❌ Failed to transmit developer DM: {e}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        return

    if interaction.guild is None and interaction.command and interaction.command.name != "info":
        try:
            error_msg = "❌ This command can only be used in servers."
            if interaction.response.is_done():
                await interaction.followup.send(error_msg, ephemeral=True)
            else:
                await interaction.response.send_message(error_msg, ephemeral=True)
        except Exception:
            pass
        return

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

@bot.tree.interaction_check
async def global_dm_check(interaction: discord.Interaction) -> bool:
    if interaction.guild is None and interaction.command and interaction.command.name != "info":
        await interaction.response.send_message("❌ This command can only be used in servers.", ephemeral=True)
        return False
    return True

async def run_discord_channel_backup():
    """Backup data files straight to the developer's DM if 24 hours have passed."""
    try:
        data = load_shame_data()
        
        if "system_metadata" not in data:
            data["system_metadata"] = {}
        
        last_backup_str = data["system_metadata"].get("last_dev_dm_backup")
        now = datetime.now()
        
        if last_backup_str:
            last_backup_time = datetime.fromisoformat(last_backup_str)
            if now < last_backup_time + timedelta(hours=24):
                print("⏱️ Discord Backup Skipped: Last archive was sent less than 24 hours ago.")
                return

        owner = bot.get_user(BOT_OWNER_ID) or await bot.fetch_user(BOT_OWNER_ID)
        if not owner:
            print("❌ Backup Error: Bot owner not found.")
            return

        files_to_send = []
        for file_name in [DATA_FILE, VOTE_DATA_FILE]:
            if os.path.exists(file_name):
                files_to_send.append(discord.File(file_name))

        if not files_to_send:
            print("⚠️ Backup Warning: No local files exist to transmit.")
            return

        date_stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        await owner.send(
            content=f"📦 **Automated 24-Hour Database Backup**\n📅 Timestamp: `{date_stamp}`\n⚠️ *Keep these files safe for disaster recovery.*",
            files=files_to_send,
            silent=True
        )
        print("💾 Success: Live JSON files dispatched to dev DM.")

        data["system_metadata"]["last_dev_dm_backup"] = now.isoformat()
        save_shame_data(data)

    except Exception as e:
        print(f"Error handling live DM backup: {e}")
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        await broadcast_error_log(f"⚠️ **Discord Backup Engine Failed:**\n```python\n{tb}\n```")

def load_shame_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, PermissionError) as e:
            asyncio.create_task(broadcast_error_log(f"🚨 **Corrupted `{DATA_FILE}` found!** Rebuilt as empty.\nError: `{e}`"))
            return {}
    return {}

def save_shame_data(data):
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
            "activity_message_threshold": 1,
            "wordle_autorole_enabled": False,
        }
        save_shame_data(data)
    return data[guild_id_str]

def get_all_data():
    return load_shame_data()

def update_guild_data(guild_id, guild_data):
    data = load_shame_data()
    data[str(guild_id)] = guild_data
    save_shame_data(data)

def load_vote_data():
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
    guild_id_str = str(guild_id)
    if guild_id_str not in vote_data:
        vote_data[guild_id_str] = {}
        save_vote_data()
    return vote_data[guild_id_str]

def get_active_users_count(guild: discord.Guild) -> int:
    """Count active users using the in-memory cache populated by the 5-min scanner."""
    return max(1, len(active_users_cache.get(guild.id, set())))

def remove_expired_votes():
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
    guild_id_str = str(guild_id)
    if guild_id_str in vote_data:
        del vote_data[guild_id_str]
        save_vote_data()

def remove_expired_entries(guild_data):
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
    guild_data = get_guild_data(guild_id)
    return command_name in guild_data.get("disabled_commands", [])

def get_all_command_names():
    return sorted([cmd.name for cmd in bot.tree.get_commands() if not isinstance(cmd, discord.app_commands.ContextMenu)])

def is_valid_command(command_name: str) -> bool:
    return command_name in get_all_command_names()

def is_manager(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.manage_guild:
        return True
    guild_data = get_guild_data(interaction.guild_id)
    manager_role_id = guild_data.get("manager_role")
    if manager_role_id:
        role = interaction.guild.get_role(manager_role_id)
        if role in interaction.user.roles:
            return True
    return False

def is_moderator(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.manage_guild

MENTION_REGEX = re.compile(r"<@!?(\d+)>")
DISPLAY_REGEX = re.compile(r"@([^\n<>]+)")

def parse_wordle_result_line(line: str):
    """
    Returns

    (
        mentioned_user_ids,
        display_names
    )
    """

    ids = [int(x) for x in MENTION_REGEX.findall(line)]

    displays = [
        x.strip()
        for x in DISPLAY_REGEX.findall(line)
        if "<@" not in x
    ]

    return ids, displays

def resolve_display_name(display_name: str, guild: discord.Guild):
    """
    Returns

    None
        no member found

    discord.Member
        unique match

    "duplicate"
        duplicate display names
    """

    matches = [
        m
        for m in guild.members
        if m.display_name == display_name
    ]

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        return "duplicate"

    return None

def parse_wordle_message(message: discord.Message, guild: discord.Guild):
    """
    Returns

    (
        failures,
        pros,
        duplicate_found
    )
    """

    failures = set()
    pros = set()

    duplicate_found = False

    content = message.content

    if not content.startswith("**Your group is on a"):
        return failures, pros, duplicate_found

    for raw_line in content.splitlines():

        line = raw_line.strip()

        if line.startswith("👑"):

            ids, displays = parse_wordle_result_line(line)

            pros.update(ids)

            for display in displays:

                result = resolve_display_name(display, guild)

                if result == "duplicate":
                    duplicate_found = True

                elif result is not None:
                    pros.add(result.id)

        elif line.startswith("X/6"):

            ids, displays = parse_wordle_result_line(line)

            failures.update(ids)

            for display in displays:

                result = resolve_display_name(display, guild)

                if result == "duplicate":
                    duplicate_found = True

                elif result is not None:
                    failures.add(result.id)

    return failures, pros, duplicate_found

async def collect_wordle_results(guild: discord.Guild):
    """
    Returns

    (
        failures,
        pros,
        duplicate_found
    )
    """

    failures = set()
    pros = set()

    duplicate_found = False

    channel = guild.get_channel(WORDLE_CHANNEL_ID)

    if channel is None:
        return failures, pros, duplicate_found

    permissions = channel.permissions_for(guild.me)

    if not permissions.read_messages:
        return failures, pros, duplicate_found

    if not permissions.read_message_history:
        return failures, pros, duplicate_found
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=WORDLE_SCAN_DAYS)

    try:

        async for message in channel.history(after=cutoff, limit=None):

            if message.author.id != WORDLE_BOT_ID:
                continue

            f, p, dup = parse_wordle_message(message, guild)

            failures.update(f)
            pros.update(p)

            if dup:
                duplicate_found = True

    except discord.Forbidden:
        pass

    pros -= failures

    return failures, pros, duplicate_found

async def synchronize_wordle_roles(guild: discord.Guild):
    """
    Synchronizes Wordle roles.

    Returns

    duplicate_found
    """

    failure_role = guild.get_role(WORDLE_FAILURE_ROLE_ID)
    pro_role = guild.get_role(WORDLE_PRO_ROLE_ID)

    log_channel = guild.get_channel(WORDLE_COMMAND_CHANNEL_ID)

    if failure_role is None or pro_role is None:
        return False

    me = guild.me

    if me is None:
        return False

    if not me.guild_permissions.manage_roles:
        return False

    if me.top_role <= failure_role:
        return False

    if me.top_role <= pro_role:
        return False

    failures, pros, duplicate_found = await collect_wordle_results(guild)

    for member in guild.members:

        if member.bot:
            continue

        should_have_failure = member.id in failures
        should_have_pro = member.id in pros

        has_failure = failure_role in member.roles
        has_pro = pro_role in member.roles

        try:

            if should_have_failure:

                if not has_failure:
                    await member.add_roles(
                        failure_role,
                        reason="Wordle auto role"
                    )

                    if log_channel:
                        await log_channel.send(
                            f"😭 `{member.name}` is now a `{failure_role.name}`.",
                            silent=True
                        )

                if has_pro:
                    await member.remove_roles(
                        pro_role,
                        reason="Wordle auto role"
                    )

                    if log_channel:
                        await log_channel.send(
                            f"🛠️ `{member.name}` is no longer a `{pro_role.name}`.",
                            silent=True
                        )

            elif should_have_pro:

                if not has_pro:
                    await member.add_roles(
                        pro_role,
                        reason="Wordle auto role"
                    )

                    if log_channel:
                        await log_channel.send(
                            f"👑 `{member.name}` is now a `{pro_role.name}`.",
                            silent=True
                        )

                if has_failure:
                    await member.remove_roles(
                        failure_role,
                        reason="Wordle auto role"
                    )

                    if log_channel:
                        await log_channel.send(
                            f"🛠️ `{member.name}` is no longer a `{failure_role.name}`.",
                            silent=True
                        )

            else:

                if has_failure:
                    await member.remove_roles(
                        failure_role,
                        reason="Wordle auto role"
                    )

                    if log_channel:
                        await log_channel.send(
                            f"🛠️ `{member.name}` is no longer a `{failure_role.name}`.",
                            silent=True
                        )

                if has_pro:
                    await member.remove_roles(
                        pro_role,
                        reason="Wordle auto role"
                    )

                    if log_channel:
                        await log_channel.send(
                            f"🛠️ `{member.name}` is no longer a `{pro_role.name}`.",
                            silent=True
                        )

        except discord.Forbidden:
            pass

    if duplicate_found and log_channel:

        duplicate_warning = (
            "⚠️ Duplicate display names found in rare ping fail case, "
            "some users may miss out on some roles."
        )

        last_message = None

        async for msg in log_channel.history(limit=10):

            if msg.author.id == guild.me.id:

                last_message = msg
                break


        if last_message and last_message.content.endswith(duplicate_warning):

            match = re.match(
                r"\[x(\d+)\] (.+)",
                last_message.content
            )

            if match:

                count = int(match.group(1)) + 1

            else:

                count = 2


            await last_message.edit(
                content=f"[x{count}] {duplicate_warning}"
            )

        else:

            await log_channel.send(
                duplicate_warning,
                silent=True
            )

    return duplicate_found

def is_wordle_command_channel(interaction: discord.Interaction):
    return interaction.channel_id == WORDLE_COMMAND_CHANNEL_ID

def set_cooldown(guild_id, user_id, seconds):
    if guild_id not in cooldowns:
        cooldowns[guild_id] = {}
    cooldowns[guild_id][user_id] = datetime.now() + timedelta(seconds=seconds)

def check_cooldown(guild_id, user_id):
    if guild_id not in cooldowns:
        return 0
    if user_id not in cooldowns[guild_id]:
        return 0
    remaining = (cooldowns[guild_id][user_id] - datetime.now()).total_seconds()
    if remaining <= 0:
        del cooldowns[guild_id][user_id]
        return 0
    return remaining

@tasks.loop(minutes=1)
async def check_expired_votes():
    try:
        current_time = datetime.now()
        one_day_ago = current_time - timedelta(hours=24)
        
        for guild_id_str in list(vote_data.keys()):
            guild_id = int(guild_id_str)
            guild = bot.get_guild(guild_id)
            if not guild:
                continue
                
            guild_data = get_guild_data(guild_id)
            vk_bc_id = guild_data.get("votekick_broadcast_channel")
            critical_amount = get_critical_amount(guild_id)
            
            for target_id_str in list(vote_data[guild_id_str].keys()):
                expired_voters = []
                for voter_id_str, vote_info in list(vote_data[guild_id_str][target_id_str].items()):
                    # Handle both the new dict format and legacy string formats safely
                    if isinstance(vote_info, dict):
                        ts_str = vote_info.get("timestamp")
                    else:
                        ts_str = vote_info
                        
                    vote_time = datetime.fromisoformat(ts_str)
                    if vote_time < one_day_ago:
                        expired_voters.append(voter_id_str)
                        
                if expired_voters:
                    for voter_id in expired_voters:
                        del vote_data[guild_id_str][target_id_str][voter_id]
                    save_vote_data()
                    
                    target_member = guild.get_member(int(target_id_str)) or await bot.fetch_user(int(target_id_str))
                    
                    broadcast_channel = None
                    if vk_bc_id:
                        custom_channel = guild.get_channel(vk_bc_id)
                        if custom_channel and custom_channel.permissions_for(guild.me).send_messages:
                            broadcast_channel = custom_channel
                    if not broadcast_channel and guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
                        broadcast_channel = guild.system_channel
                    if not broadcast_channel:
                        for channel in guild.text_channels:
                            if channel.permissions_for(guild.me).send_messages:
                                broadcast_channel = channel
                                break
                                
                    if broadcast_channel:
                        remaining_votes = len(vote_data[guild_id_str][target_id_str])
                        await broadcast_channel.send(f"⏱️ Some votes for `{target_member.name}` have expired. ({remaining_votes}/{critical_amount})", silent=True)
                        
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

async def synchronize_active_member_roles():
    """Scans history and safely synchronizes Active Member roles."""
    global initial_sync_completed
    await bot.wait_until_ready()

    try:
        for guild in bot.guilds:
            guild_config = get_guild_data(guild.id)
            window_days = guild_config.get("activity_window_days", 7)
            threshold = guild_config.get("activity_window_messages", 1)
            cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

            user_message_counts = {}

            # Phase 1: completely scan history before touching any roles.
            for channel in guild.text_channels:
                perms = channel.permissions_for(guild.me)

                if not perms.read_messages or not perms.read_message_history:
                    continue

                try:
                    async for message in channel.history(after=cutoff, limit=None):
                        if message.author.bot:
                            continue

                        uid = message.author.id
                        user_message_counts[uid] = (
                            user_message_counts.get(uid, 0) + 1
                        )

                except discord.Forbidden:
                    continue

                except Exception as e:
                    # A failed history scan means our desired-state calculation
                    # is incomplete. Abort before making ANY role changes.
                    print(
                        f"🛑 Aborting Active Member synchronization for "
                        f"{guild.name}: failed to scan #{channel.name}: {e}"
                    )
                    return

            # Phase 2: construct the complete desired state.
            active_user_ids = {
                uid
                for uid, count in user_message_counts.items()
                if count >= threshold
            }

            active_users_cache[guild.id] = active_user_ids

            role_id = guild_config.get("active_member_role")

            if not role_id:
                refresh_critical_amount(guild.id)
                continue

            role = guild.get_role(role_id)

            if (
                not role
                or not guild.me.guild_permissions.manage_roles
                or guild.me.top_role <= role
            ):
                refresh_critical_amount(guild.id)
                continue

            broadcast_channel_id = guild_config.get(
                "activity_broadcast_channel"
            )
            broadcast_channel = (
                guild.get_channel(broadcast_channel_id)
                if broadcast_channel_id
                else None
            )

            # Phase 3: compare desired state against current state and
            # perform ONLY necessary role edits.
            for member in guild.members:
                if member.bot:
                    continue

                should_have = member.id in active_user_ids
                has_role = role in member.roles

                if should_have and not has_role:
                    try:
                        await member.add_roles(
                            role,
                            reason=f"Met active threshold ({threshold} msgs)."
                        )

                    except Exception as e:
                        # Abort immediately. Do not continue issuing role
                        # requests after a failed role operation.
                        print(
                            f"🛑 Aborting Active Member synchronization for "
                            f"{guild.name}: failed to add role to "
                            f"{member.name}: {e}"
                        )
                        return

                    if broadcast_channel:
                        try:
                            await broadcast_channel.send(
                                f"📈 `{member.name}` has been assigned "
                                f"the `{role.name}` role due to meeting "
                                f"the activity threshold!",
                                silent=True
                            )
                        except Exception as e:
                            print(
                                f"⚠️ Failed to send Active Member "
                                f"notification for {member.name}: {e}"
                            )

                elif not should_have and has_role:
                    try:
                        await member.remove_roles(
                            role,
                            reason=(
                                "User fell below the activity "
                                "threshold parameters."
                            )
                        )

                    except Exception as e:
                        # Abort immediately. Do not continue issuing role
                        # requests after a failed role operation.
                        print(
                            f"🛑 Aborting Active Member synchronization for "
                            f"{guild.name}: failed to remove role from "
                            f"{member.name}: {e}"
                        )
                        return

                    if broadcast_channel:
                        try:
                            await broadcast_channel.send(
                                f"📉 `{member.name}` lost the "
                                f"`{role.name}` role due to inactivity.",
                                silent=True
                            )
                        except Exception as e:
                            print(
                                f"⚠️ Failed to send Active Member "
                                f"notification for {member.name}: {e}"
                            )

            refresh_critical_amount(guild.id)

    except Exception as e:
        print(
            f"Error in manage_active_roles_loop task frame: {e}"
        )

        tb = "".join(
            traceback.format_exception(
                type(e),
                e,
                e.__traceback__
            )
        )

        await broadcast_error_log(
            f"🚨 **Background Task Loop Failure "
            f"(`manage_active_roles_loop`):**\n"
            f"```python\n{tb}\n```"
        )
    
    # Mark initial sync as completed
    initial_sync_completed = True

@bot.tree.interaction_check
async def global_interaction_check(interaction: discord.Interaction) -> bool:
    # 1. Existing DM validation logic
    if interaction.guild is None and interaction.command and interaction.command.name != "info":
        await interaction.response.send_message("❌ This command can only be used in servers.", ephemeral=True)
        return False

    # 2. Dynamic Rate Limiting Engine
    if interaction.guild and interaction.command:
        guild_id = interaction.guild_id
        user_id = interaction.user.id
        cmd_name = interaction.command.name

        # Check if the triggered command has a configured rate limit
        if cmd_name in rate_limits_config:
            rule = rate_limits_config[cmd_name]
            prev_cmd = rule["previousCommand"]
            req_sec = rule["seconds"]
            error_msg = rule.get("errorMessage")

            # Did this user run the required previous command in this server?
            if guild_id in command_timestamps and user_id in command_timestamps[guild_id]:
                last_prev_cmd_time = command_timestamps[guild_id][user_id].get(prev_cmd)
                
                if last_prev_cmd_time:
                    elapsed = (datetime.now() - last_prev_cmd_time).total_seconds()
                    
                    # Cut them off if they are moving too fast
                    if elapsed < req_sec:
                        remaining = req_sec - elapsed
                        ready_time = int((datetime.now() + timedelta(seconds=remaining)).timestamp())
                        
                        response_text = f"Sorry, try again <t:{ready_time}:R>."
                        if error_msg:
                            response_text += f"\nNOTE: {error_msg}"
                            
                        await interaction.response.send_message(response_text, ephemeral=True)
                        return False 
        
        # 3. Log the successful command execution timestamp
        if guild_id not in command_timestamps:
            command_timestamps[guild_id] = {}
        if user_id not in command_timestamps[guild_id]:
            command_timestamps[guild_id][user_id] = {}
            
        command_timestamps[guild_id][user_id][cmd_name] = datetime.now()

    return True

@tasks.loop(minutes=10)
async def manage_active_roles_loop():
    await synchronize_active_member_roles()

@tasks.loop(hours=24)
async def discord_backup_loop():
    await run_discord_channel_backup()

@tasks.loop(minutes=30)
async def wordle_autorole_loop():
    try:

        guild = bot.get_guild(WORDLE_GUILD_ID)

        if guild is None:
            return

        guild_data = get_guild_data(guild.id)

        if not guild_data.get("wordle_autorole_enabled", False):
            return

        await synchronize_wordle_roles(guild)

    except Exception as e:

        print(f"Error in Wordle auto role loop: {e}")

        tb = "".join(
            traceback.format_exception(
                type(e),
                e,
                e.__traceback__
            )
        )

        await broadcast_error_log(
            f"🚨 **Background Task Failure (`wordle_autorole_loop`)**\n"
            f"```python\n{tb}\n```"
        )

@wordle_autorole_loop.before_loop
async def before_wordle_autorole_loop():

    await bot.wait_until_ready()

@bot.event
async def on_guild_join(guild):
    get_guild_data(guild.id)
    print(f"Joined guild: {guild.name} (ID: {guild.id})")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    
    load_vote_data()
    
    # Pre-calculate the critical amount for all connected servers on boot
    for guild in bot.guilds:
        refresh_critical_amount(guild.id)
        
    if not check_expired_votes.is_running():
        check_expired_votes.start()

    if not manage_active_roles_loop.is_running():
        manage_active_roles_loop.start()

    if not retry_pending_dev_dm_logs.is_running():
        retry_pending_dev_dm_logs.start()
        
    if not discord_backup_loop.is_running():
        discord_backup_loop.start()

    if not wordle_autorole_loop.is_running():
        wordle_autorole_loop.start()

    bot.tree.add_command(wordle_group)

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} application commands globally.")
    except Exception as e:
        print(f"Failed to sync application tree parameters: {e}")
        
    await broadcast_error_log("🟢 **Bot Startup Successful!** Systems initialized and historical scanner task dispatched.")

@bot.tree.command(name="info", description="Display configuration settings and statistics parameters.")
async def info(interaction: discord.Interaction):
    if interaction.guild is None:
        response_text = (
            "ℹ️ **Global Bot Status Summary**\n"
            f"Version: {BOT_VERSION}\n\n"
            "For more detailed information for a specific server, use this same command in that specific server."
        )
        await interaction.response.send_message(response_text)
        return

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
    manager_role_text = f"`@{manager_role.name}`" if manager_role else "Not Set"

    am_role_id = guild_data.get("active_member_role")
    am_role = interaction.guild.get_role(am_role_id) if am_role_id else None
    am_role_text = f"`@{am_role.name}`" if am_role else "Not Set"

    shame_channel_id = guild_data.get("shame_channel")
    shame_channel = f"<#{shame_channel_id}>" if shame_channel_id else "Not Set"

    vk_bc_id = guild_data.get("votekick_broadcast_channel")
    vk_bc = f"<#{vk_bc_id}>" if vk_bc_id else "Not Set"
    
    # Calculate Critical Amount (adjust math below if you use a specific percentage)
    active_count = get_active_users_count(interaction.guild)
    # Example math: Critical amount is 10% of active members, minimum of 3. Update to match your actual formula!
    critical_amount = max(3, int(active_count * 0.10)) 
    act_bc_id = guild_data.get("activity_broadcast_channel")
    act_bc = f"<#{act_bc_id}>" if act_bc_id else "Not Set"

    expiry_days = guild_data.get("expiry_days")
    expiry_timer = f"{expiry_days} Days" if expiry_days is not None else "Infinite"
    votekick_ban_duration = f"{guild_data.get('votekick_ban_duration', 7)} Days"

    disabled_cmds_list = guild_data.get("disabled_commands", [])
    disabled_cmds = ", ".join([f"`/{c}`" for c in disabled_cmds_list]) if disabled_cmds_list else "None"

    active_members = get_active_users_count(interaction.guild)
    critical_amount = get_critical_amount(interaction.guild_id)
    act_win = guild_data.get("activity_window_days", 7)
    act_thresh = guild_data.get("activity_message_threshold", 1)

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
        f"Vote to Kick Broadcast Channel: {vk_bc}\n"
        f"Active Members (Last {act_win} Days): {active_members}\n"
        f"Required Votes: {critical_amount}\n\n"
        "**Activity Stuff**\n"
        f"Active Member Role: {am_role_text}\n"
        f"Activity Requirement Window: {act_win} Days\n"
        f"Activity Requirement Threshold: {act_thresh} Messages\n"
        f"Activity Broadcast Channel: {act_bc}"
    )

    await interaction.response.send_message(response_text)

@bot.tree.command(name="shame", description="Add an entry to the hall of shame")
@app_commands.guild_only()
async def shame(interaction: discord.Interaction, user: discord.Member, reason: str):
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

    if user.bot:
        await interaction.response.send_message("❌ You cannot shame a bot.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    remove_expired_entries(guild_data)

    existing_ids = [int(k) for k in guild_data["entries"].keys()]
    next_id = str(max(existing_ids) + 1) if existing_ids else "1"

    guild_data["entries"][next_id] = {
        "user_id": user.id,
        "username": user.name,
        "reason": reason,
        "date": datetime.now().isoformat(),
        "shamed_by": interaction.user.name
    }
    update_guild_data(interaction.guild_id, guild_data)

    response_lines = [
        f"🚨 **{user.name}** has been added to the hall of shame!",
        f"**Reason:** {reason}",
        f"**Entry ID:** {next_id}"
    ]

    shame_channel_id = guild_data.get("shame_channel")
    if shame_channel_id:
        shame_channel = interaction.guild.get_channel(shame_channel_id)
        if shame_channel and shame_channel.permissions_for(interaction.guild.me).send_messages:
            try:
                await shame_channel.send("\n".join(response_lines))
                await interaction.response.send_message(f"✅ Successfully shamed {user.name} and logged into the broadcast channel.", ephemeral=True)
                return
            except discord.Forbidden:
                pass

    await interaction.response.send_message("\n".join(response_lines))

@bot.tree.command(name="unshame", description="Remove an entry from the hall of shame")
@app_commands.guild_only()
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
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    entry_id_str = str(entry_id)
    if entry_id_str not in guild_data["entries"]:
        await interaction.response.send_message("❌ Entry ID not found.", ephemeral=True)
        return

    entry = guild_data["entries"][entry_id_str]
    del guild_data["entries"][entry_id_str]
    update_guild_data(interaction.guild_id, guild_data)

    response_lines = [
        f"✅ **{entry['username']}** has been removed from the hall of shame",
        f"**Original Reason:** {entry['reason']}"
    ]
    if reason:
        response_lines.append(f"**Removal Reason:** {reason}")
    response_lines.append(f"**Entry ID:** {entry_id_str}")
    response_lines.append(f"*Removed by `{interaction.user.name}`*")

    await interaction.response.send_message("\n".join(response_lines))

@bot.tree.command(name="list_my_shame", description="List your hall of shame entries")
@app_commands.guild_only()
async def list_my_shame(interaction: discord.Interaction):
    if is_command_disabled(interaction.guild_id, "list_my_shame"):
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

    remove_expired_entries(guild_data)

    user_entries = {k: v for k, v in guild_data["entries"].items() if v["user_id"] == interaction.user.id}

    if not user_entries:
        await interaction.response.send_message("✅ You have no entries in the hall of shame.", ephemeral=True)
        return

    expiry_days = guild_data.get("expiry_days")
    response_lines = [f"📋 **Hall of Shame Entries for {interaction.user.name}:**\n"]

    for eid, entry in user_entries.items():
        entry_date = datetime.fromisoformat(entry["date"])
        entry_timestamp = int(entry_date.timestamp())
        reason = entry.get("reason", "No reason provided")
        
        if expiry_days is not None:
            expiry_date = entry_date + timedelta(days=expiry_days)
            expiry_timestamp = int(expiry_date.timestamp())
            response_lines.append(f"🔹 **ID: {eid}** - {reason}\n└ Added <t:{entry_timestamp}:d> (Expires <t:{expiry_timestamp}:R>)")
        else:
            response_lines.append(f"🔹 **ID: {eid}** - {reason}\n└ Added <t:{entry_timestamp}:d> (Permanent Entry)")

    await interaction.response.send_message("\n".join(response_lines), ephemeral=True)

@bot.tree.command(name="list_all_shame", description="Lists all entries in the hall o' shame.")
@app_commands.guild_only()
async def list_all_shame(interaction: discord.Interaction):
    if is_command_disabled(interaction.guild_id, "shameboard"):
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

    remove_expired_entries(guild_data)

    if not guild_data["entries"]:
        await interaction.response.send_message("🕊️ The Hall of Shame is currently clean and empty.", ephemeral=True)
        return

    user_counts = {}
    for entry in guild_data["entries"].values():
        uid = entry["user_id"]
        if uid not in user_counts:
            user_counts[uid] = {"username": entry["username"], "count": 0}
        user_counts[uid]["count"] += 1

    sorted_users = sorted(user_counts.items(), key=lambda x: x[1]["count"], reverse=True)
    expiry_days = guild_data.get("expiry_days")

    response_lines = ["🏆 **Server Hall of Shame Leaderboard** 🏆\n"]
    for index, (uid, info_dict) in enumerate(sorted_users, 1):
        medal = "🥇 " if index == 1 else "🥈 " if index == 2 else "🥉 " if index == 3 else f"**#{index}** "
        response_lines.append(f"{medal}`{info_dict['username']}` — {info_dict['count']} active entry/entries")
        
        for eid, entry in guild_data["entries"].items():
            if entry["user_id"] == uid:
                entry_date = datetime.fromisoformat(entry["date"])
                expiry_date = entry_date + timedelta(days=expiry_days) if expiry_days is not None else None
                entry_timestamp = int(entry_date.timestamp())
                expiry_timestamp = int(expiry_date.timestamp()) if expiry_date else None
                reason = entry.get("reason", "No reason provided")
                
                if expiry_timestamp:
                    response_lines.append(f"  └ `ID: {eid}` <t:{entry_timestamp}:d> (expires <t:{expiry_timestamp}:R>) - *{reason}*")
                else:
                    response_lines.append(f"  └ `ID: {eid}` <t:{entry_timestamp}:d> (Permanent) - *{reason}*")
        response_lines.append("")

    await interaction.response.send_message("\n".join(response_lines))

@bot.tree.command(name="vote", description="Adds a vote to kick a member, which expires after a day.")
@app_commands.describe(user="The user to vote for", anonymous="Whether your vote is anonymous (default: True)")
@app_commands.guild_only()
async def vote(interaction: discord.Interaction, user: discord.Member, anonymous: bool = True):
    if is_command_disabled(interaction.guild_id, "votekick"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
        
    await interaction.response.defer(ephemeral=True)

    # Check if initial message history sync has completed
    if not initial_sync_completed:
        await interaction.followup.send("⏳ I am still reading messages, try again later.")
        return

    # Check if an unvote occurred in the last 60 seconds
    if interaction.guild_id in last_unvote_time:
        time_since_unvote = (datetime.now() - last_unvote_time[interaction.guild_id]).total_seconds()
        if time_since_unvote < 60:
            unblock_time = last_unvote_time[interaction.guild_id] + timedelta(seconds=60)
            unblock_timestamp = int(unblock_time.timestamp())
            await interaction.followup.send(
                f"You need to slow down, try voting again <t:{unblock_timestamp}:R>. "
                f"This is all the fault of <@1137904269664718948> for spamming "
                f"[here](https://discord.com/channels/1501359553823117412/1512195844978507909/1538335972121645076) by the way."
            )
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

    if user.bot:
        await interaction.followup.send("❌ You cannot vote to kick a bot.")
        return

    if user.id == interaction.user.id:
        await interaction.followup.send("❌ You cannot vote to kick yourself.")
        return

    if user.guild_permissions.administrator or user.id == interaction.guild.owner_id:
        await interaction.followup.send("❌ You cannot vote to kick the server owner or an administrator.")
        return
        
    if user.top_role >= interaction.guild.me.top_role:
        await interaction.followup.send("❌ I can't kick that person. Please ask a moderator to move my role higher.")
        return

    target_id_str = str(user.id)
    voter_id_str = str(interaction.user.id)
    current_time = datetime.now()
    critical_amount = get_critical_amount(interaction.guild_id)

    if target_id_str not in guild_vote_data:
        guild_vote_data[target_id_str] = {}

    # Save timestamp and anonymity selection
    guild_vote_data[target_id_str][voter_id_str] = {
        "timestamp": current_time.isoformat(),
        "anonymous": anonymous
    }
    save_vote_data()

    current_votes = len(guild_vote_data[target_id_str])
    await interaction.followup.send(f"✅ Your vote has been counted for {user.name} ({current_votes}/{critical_amount}).")
    
    # Broadcast to the channel
    if anonymous:
        await interaction.channel.send(f"🟠 Someone voted for `{user.name}` ({current_votes}/{critical_amount}).", silent=True)
    else:
        await interaction.channel.send(f"🟠 `{interaction.user.name}` voted for `{user.name}` ({current_votes}/{critical_amount}).", silent=True)

    if current_votes >= critical_amount:
        del guild_vote_data[target_id_str]
        save_vote_data()

        ban_duration_days = guild_data.get("votekick_ban_duration", 7)
        
        try:
            if ban_duration_days > 0:
                await interaction.guild.ban(user, delete_message_days=0, reason=f"Vote to kick - reached critical amount ({current_votes}/{critical_amount})")
                await interaction.channel.send(f"🚨 **{user.name}** has been banned for {ban_duration_days} days due to reaching the critical vote threshold!", silent=True)
                
                # Setup auto-unban
                async def scheduled_unban_callback():
                    await asyncio.sleep(ban_duration_days * 86400)
                    try:
                        await interaction.guild.unban(discord.Object(id=user.id), reason="Vote-kick ban duration expired.")
                    except Exception:
                        pass
                asyncio.create_task(scheduled_unban_callback())
            else:
                await interaction.guild.kick(user, reason=f"Vote to kick - reached critical amount ({current_votes}/{critical_amount})")
                await interaction.channel.send(f"🚨 **{user.name}** has been kicked due to reaching the critical vote threshold!", silent=True)
            
        except discord.Forbidden:
            action_type = f"banned for {ban_duration_days} days" if ban_duration_days > 0 else "kicked"
            await interaction.channel.send(f"⚠️ **{user.name}** should have been {action_type}, but I lack the required permissions.", silent=True)

@bot.tree.command(name="unvote", description="Removes your vote.")
@app_commands.guild_only()
async def unvote(interaction: discord.Interaction):
    if is_command_disabled(interaction.guild_id, "unvote"):
        await interaction.response.send_message("❌ This command is disabled.", ephemeral=True)
        return
        
    guild_vote_data = get_vote_data(interaction.guild_id)
    voter_id_str = str(interaction.user.id)
    vote_removed = False
    
    target_user_id = None
    is_anon = True
    
    for target_id, voters in list(guild_vote_data.items()):
        if voter_id_str in voters:
            # Capture vote properties before deleting
            if isinstance(voters[voter_id_str], dict):
                is_anon = voters[voter_id_str].get("anonymous", True)
                
            del guild_vote_data[target_id][voter_id_str]
            vote_removed = True
            target_user_id = target_id
            
            if not guild_vote_data[target_id]:
                del guild_vote_data[target_id]
            break
                
    if vote_removed:
        save_vote_data()
        
        # Track unvote time
        last_unvote_time[interaction.guild_id] = datetime.now()
        
        await interaction.response.send_message("✅ Your active vote has been successfully removed.", ephemeral=True)
        
        # --- Broadcast Engine ---
        guild_data = get_guild_data(interaction.guild_id)
        vk_bc_id = guild_data.get("votekick_broadcast_channel")
        
        broadcast_channel = None
        if vk_bc_id:
            custom_channel = interaction.guild.get_channel(vk_bc_id)
            if custom_channel and custom_channel.permissions_for(interaction.guild.me).send_messages:
                broadcast_channel = custom_channel
        
        # Fallback to the channel where interaction happened if no designated channel exists
        if not broadcast_channel:
            broadcast_channel = interaction.channel
            
        if broadcast_channel and broadcast_channel.permissions_for(interaction.guild.me).send_messages:
            target_member = interaction.guild.get_member(int(target_user_id))
            target_name = f"`{target_member.name}`" if target_member else f"Unknown ({target_user_id})"
            current_votes = len(guild_vote_data.get(target_user_id, {}))
            critical_amount = get_critical_amount(interaction.guild_id)
            
            if is_anon:
                await broadcast_channel.send(f"⚪ Someone withdrew their vote for {target_name} ({current_votes}/{critical_amount}).", silent=True)
            else:
                await broadcast_channel.send(f"⚪ `{interaction.user.name}` withdrew their vote for {target_name} ({current_votes}/{critical_amount}).", silent=True)
    else:
        await interaction.response.send_message("ℹ️ You do not currently have any active votes.", ephemeral=True)

@bot.tree.command(name="votedata", description="Gets information on what kick votes are out there.")
@app_commands.guild_only()
async def votedata(interaction: discord.Interaction):
    if is_command_disabled(interaction.guild_id, "votedata"):
        await interaction.response.send_message("❌ This command is disabled.", ephemeral=True)
        return

    guild_vote_data = get_vote_data(interaction.guild_id)
    if not guild_vote_data:
        await interaction.response.send_message("🕊️ There are currently no active kick votes in this server.")
        return

    critical_amount = get_critical_amount(interaction.guild_id)
    lines = [f"🗳️ **Active Vote Data (Critical Amount: {critical_amount}):**\n"]
    
    for target_id_str, voters_dict in guild_vote_data.items():
        member = interaction.guild.get_member(int(target_id_str))
        name = f"`{member.name}`" if member else f"Unknown User ({target_id_str})"
        
        voter_displays = []
        anon_count = 0
        
        for voter_id_str, vote_info in voters_dict.items():
            is_anon = True
            if isinstance(vote_info, dict):
                is_anon = vote_info.get("anonymous", True)
            
            if is_anon:
                anon_count += 1
            else:
                voter_member = interaction.guild.get_member(int(voter_id_str))
                if voter_member:
                    voter_displays.append(f"`{voter_member.name}`")
                else:
                    voter_displays.append(f"Left Server ({voter_id_str})")

        voters_string = ""
        if voter_displays or anon_count > 0:
            combined_voters = list(voter_displays)
            if anon_count > 0:
                combined_voters.append(f"Anonymous x{anon_count}" if anon_count > 1 else "Anonymous")
            voters_string = f" (Voted by: {', '.join(combined_voters)})"
            
        lines.append(f"• **{name}**: {len(voters_dict)} vote(s){voters_string}")
        
    await interaction.response.send_message("\n".join(lines))

@bot.tree.command(name="cooldown", description="Sets the command cooldown.")
@app_commands.guild_only()
async def cooldown(interaction: discord.Interaction, seconds: int):
    if is_command_disabled(interaction.guild_id, "config_cooldown"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
        
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return

    if not is_manager(interaction):
        await interaction.response.send_message("❌ You need Manage Server permission to use this command.", ephemeral=True)
        return

    if seconds < 0 or seconds > 30:
        await interaction.response.send_message("❌ Cooldown must be between 0 and 30 seconds.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)
    guild_data["cooldown"] = seconds
    update_guild_data(interaction.guild_id, guild_data)

    if interaction.guild_id in cooldowns:
        cooldowns[interaction.guild_id].clear()

    await interaction.response.send_message(f"⏱️ **Cooldown Threshold Synchronized:** Global command throttle adjusted to `{seconds}s` for this guild context.")

# --- MANAGER ROLE SETUP ---
@bot.tree.command(name="set_manager_role", description="Sets the manager role.")
@app_commands.guild_only()
async def set_manager_role(interaction: discord.Interaction, role: discord.Role):
    if not is_moderator(interaction):
        await interaction.response.send_message("❌ Only Moderators can use this.", ephemeral=True)
        return
    guild_data = get_guild_data(interaction.guild_id)
    guild_data["manager_role"] = role.id
    update_guild_data(interaction.guild_id, guild_data)
    await interaction.response.send_message(f"✅ Manager role set to {role.mention}")

@bot.tree.command(name="reset_manager_role", description="Resets the manager role.")
@app_commands.guild_only()
async def reset_manager_role(interaction: discord.Interaction):
    if not is_moderator(interaction):
        await interaction.response.send_message("❌ Only Moderators can use this.", ephemeral=True)
        return
    guild_data = get_guild_data(interaction.guild_id)
    guild_data["manager_role"] = None
    update_guild_data(interaction.guild_id, guild_data)
    await interaction.response.send_message("✅ Manager role reset. Only Moderators can manage the bot now.")

# --- SHAME CONFIG ---
@bot.tree.command(name="shame_config_set", description="Sets the shame channel and shame expiry timer.")
@app_commands.guild_only()
async def shame_config_set(interaction: discord.Interaction, channel: discord.TextChannel = None, expiry_days: int = None):
    if not is_manager(interaction):
        await interaction.response.send_message("❌ Only Managers can use this.", ephemeral=True)
        return
    guild_data = get_guild_data(interaction.guild_id)
    if channel: guild_data["shame_channel"] = channel.id
    if expiry_days is not None: guild_data["expiry_days"] = None if expiry_days <= 0 else expiry_days
    update_guild_data(interaction.guild_id, guild_data)
    await interaction.response.send_message("✅ Shame configuration updated.")

@bot.tree.command(name="shame_config_reset", description="Resets the shame feature configuration.")
@app_commands.guild_only()
async def shame_config_reset(interaction: discord.Interaction):
    if not is_manager(interaction):
        await interaction.response.send_message("❌ Only Managers can use this.", ephemeral=True)
        return
    guild_data = get_guild_data(interaction.guild_id)
    guild_data["shame_channel"] = None
    guild_data["expiry_days"] = None
    update_guild_data(interaction.guild_id, guild_data)
    await interaction.response.send_message("✅ Shame configuration reset.")

# --- VOTEKICK CONFIG ---
@bot.tree.command(name="votekick_config_set", description="Sets votekick broadcast channel and ban duration.")
@app_commands.guild_only()
async def votekick_config_set(interaction: discord.Interaction, broadcast_channel: discord.TextChannel = None, ban_duration: int = None):
    if not is_manager(interaction):
        await interaction.response.send_message("❌ Only Managers can use this.", ephemeral=True)
        return
    guild_data = get_guild_data(interaction.guild_id)
    if broadcast_channel: guild_data["votekick_broadcast_channel"] = broadcast_channel.id
    if ban_duration is not None: guild_data["votekick_ban_duration"] = max(1, ban_duration)
    update_guild_data(interaction.guild_id, guild_data)
    await interaction.response.send_message("✅ Votekick configuration updated.")

@bot.tree.command(name="votekick_config_reset", description="Resets the votekick feature configuration.")
@app_commands.guild_only()
async def votekick_config_reset(interaction: discord.Interaction):
    if not is_manager(interaction):
        await interaction.response.send_message("❌ Only Managers can use this.", ephemeral=True)
        return
    guild_data = get_guild_data(interaction.guild_id)
    guild_data["votekick_broadcast_channel"] = None
    guild_data["votekick_ban_duration"] = 7
    update_guild_data(interaction.guild_id, guild_data)
    await interaction.response.send_message("✅ Votekick configuration reset.")

# --- ACTIVITY CONFIG ---
@bot.tree.command(name="activity_config_set", description="Sets active member role, activity window, threshold, and broadcast channel.")
@app_commands.guild_only()
@app_commands.describe(
    role="The active member role",
    window_days="Activity window threshold in days",
    message_threshold="Number of messages required in the window",
    broadcast_channel="The activity broadcast channel"
)
async def activity_config_set(
    interaction: discord.Interaction, 
    role: discord.Role = None, 
    window_days: int = None, 
    message_threshold: int = None,
    broadcast_channel: discord.TextChannel = None
):
    if not is_manager(interaction):
        await interaction.response.send_message("❌ Only Managers can use this.", ephemeral=True)
        return
        
    guild_data = get_guild_data(interaction.guild_id)
    current_role_id = guild_data.get("active_member_role")

    # ==========================================
    # SECURITY LOCK: Privilege Escalation Checks
    # ==========================================
    if role is not None and role.id != current_role_id:
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ **Security Lock:** You must natively possess the 'Manage Roles' permission to configure a new active role.", ephemeral=True)
            return
        if not interaction.guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ **Security Lock:** I do not have the 'Manage Roles' permission required to assign this role.", ephemeral=True)
            return
        if interaction.user.id != interaction.guild.owner_id and interaction.user.top_role <= role:
            await interaction.response.send_message("❌ **Security Lock:** Your highest role must be strictly above the role you are trying to configure.", ephemeral=True)
            return
        if interaction.guild.me.top_role <= role:
            await interaction.response.send_message("❌ **Security Lock:** My highest role must be strictly above the role you are trying to configure.", ephemeral=True)
            return
        if role.permissions.value != 0:
            await interaction.response.send_message("❌ **Security Lock:** The target role must have absolutely NO server-level permissions.", ephemeral=True)
            return
    # ==========================================

    if role: guild_data["active_member_role"] = role.id
    if window_days is not None: guild_data["activity_window_days"] = max(1, window_days)
    if message_threshold is not None: guild_data["activity_message_threshold"] = max(1, message_threshold)
    if broadcast_channel: guild_data["activity_broadcast_channel"] = broadcast_channel.id
    
    update_guild_data(interaction.guild_id, guild_data)

    # Acknowledge the interaction before starting the potentially
    # long-running Active Member synchronization.
    await interaction.response.defer(ephemeral=True)

    await synchronize_active_member_roles()  # Trigger instant sync

    await interaction.followup.send(
        "✅ Activity configuration updated.",
        ephemeral=True
    )

@bot.tree.command(name="activity_config_reset", description="Resets the activity feature configuration.")
@app_commands.guild_only()
async def activity_config_reset(interaction: discord.Interaction):
    if not is_manager(interaction):
        await interaction.response.send_message("❌ Only Managers can use this.", ephemeral=True)
        return
    guild_data = get_guild_data(interaction.guild_id)
    guild_data["active_member_role"] = None
    guild_data["activity_window_days"] = 7
    guild_data["activity_message_threshold"] = 1
    guild_data["activity_broadcast_channel"] = None
    update_guild_data(interaction.guild_id, guild_data)
    await interaction.response.send_message("✅ Activity configuration reset.")

@bot.tree.command(name="disable", description="Disables a command, stopping people in your server from using it.")
@app_commands.guild_only()
async def disable(interaction: discord.Interaction, command: str):
    if is_command_disabled(interaction.guild_id, "command_restrict"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
        
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

    if command.lower() in ["command_restrict", "command_unrestrict", "info"]:
        await interaction.response.send_message(f"❌ Core engine control primitive `{command}` cannot be altered.", ephemeral=True)
        return

    guild_data = get_guild_data(interaction.guild_id)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    disabled_commands = guild_data.get("disabled_commands", [])

    if command in disabled_commands:
        await interaction.response.send_message(f"ℹ️ Command `/{command}` is already under active server-side execution bans.", ephemeral=True)
        return

    disabled_commands.append(command)
    guild_data["disabled_commands"] = disabled_commands
    update_guild_data(interaction.guild_id, guild_data)

    await interaction.response.send_message(f"🔒 **Access Restriction Imposed:** `/{command}` has been completely disabled for standard members within this guild.")

@bot.tree.command(name="enable", description="Enables a command, allowing people in your server to use it.")
@app_commands.guild_only()
async def enable(interaction: discord.Interaction, command: str):
    if is_command_disabled(interaction.guild_id, "command_unrestrict"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
        
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

    guild_data = get_guild_data(interaction.guild_id)
    cooldown_seconds = guild_data.get("cooldown", 0)
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)

    disabled_commands = guild_data.get("disabled_commands", [])

    if command not in disabled_commands:
        await interaction.response.send_message(f"ℹ️ Command `/{command}` is already running under normal inherited accessibility settings.", ephemeral=True)
        return

    disabled_commands.remove(command)
    guild_data["disabled_commands"] = disabled_commands
    update_guild_data(interaction.guild_id, guild_data)

    await interaction.response.send_message(f"🔓 **Access Restriction Lifted:** `/{command}` is now available for registration and use by normal endpoints again.")

@wordle_autorole_group.command(
    name="on",
    description="Enable automatic Wordle roles."
)
async def wordle_autorole_on(
    interaction: discord.Interaction
):

    if not is_moderator(interaction):

        await interaction.response.send_message(
            "❌ You do not have permission to use this command.",
            ephemeral=True
        )
        return


    if not is_wordle_command_channel(interaction):

        await interaction.response.send_message(
            "❌ This command can only be used in <#1437902486328447067>.",
            ephemeral=True
        )
        return


    guild_data = get_guild_data(interaction.guild.id)

    guild_data["wordle_autorole_enabled"] = True

    save_shame_data()


    await interaction.response.send_message(
        "✅ Wordle auto role scanning is now ON."
    )

@wordle_autorole_group.command(
    name="off",
    description="Disable automatic Wordle roles."
)
async def wordle_autorole_off(
    interaction: discord.Interaction
):

    if not is_moderator(interaction):

        await interaction.response.send_message(
            "❌ You do not have permission to use this command.",
            ephemeral=True
        )
        return


    if not is_wordle_command_channel(interaction):

        await interaction.response.send_message(
            "❌ This command can only be used in <#1437902486328447067>.",
            ephemeral=True
        )
        return


    guild_data = get_guild_data(interaction.guild.id)

    guild_data["wordle_autorole_enabled"] = False

    save_shame_data(guild_data)

    await interaction.response.send_message(
        "✅ Wordle auto role scanning is now OFF."
    )

@wordle_autorole_group.command(
    name="scan",
    description="Immediately scan recent Wordle results."
)
async def wordle_autorole_scan(
    interaction: discord.Interaction
):

    if not is_moderator(interaction):

        await interaction.response.send_message(
            "❌ You do not have permission to use this command.",
            ephemeral=True
        )
        return


    if not is_wordle_command_channel(interaction):

        await interaction.response.send_message(
            "❌ This command can only be used in <#1437902486328447067>.",
            ephemeral=True
        )
        return


    await interaction.response.defer(
        ephemeral=True
    )


    await synchronize_wordle_roles(
        interaction.guild
    )


    await interaction.followup.send(
        "🔎 #general was scanned and Wordle roles have been automatically updated.",
        ephemeral=True
    )    

# Initialize this immediately
initialize_rate_limits()

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("🚨 Critical Setup Error: DISCORD_TOKEN environmental string injection variable missing!")
        