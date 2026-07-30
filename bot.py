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
BOT_VERSION = "1.6.2"
BOT_OWNER_ID = 807087691522375681  # Set this to your Discord ID for owner commands

# Data storage files
DATA_FILE = "shame_data.json"
VOTE_DATA_FILE = "vote_data.json"

# Cooldown tracking: {guild_id: {user_id: timestamp}}
cooldowns = {}

# Vote data: {guild_id: {target_user_id: {voter_id: vote_timestamp}}}
vote_data = {}

# Active users cache (populated hourly): {guild_id: {user_id_set}}
active_users_cache = {}

# Last critical amount refresh time per guild: {guild_id: datetime}
last_critical_refresh = {}

# Cached critical amounts per guild: {guild_id: int}
critical_amounts = {}

def refresh_critical_amount(guild_id: int):
    """Calculates and updates the cached critical vote threshold for a guild."""
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    active_count = get_active_users_count(guild)
    critical_amounts[guild_id] = max(2, int(active_count * 0.10))
    last_critical_refresh[guild_id] = datetime.now()

def get_critical_amount(guild_id: int) -> int:
    """Returns the cached critical amount or recalculates if expired (1 hour)."""
    now = datetime.now()
    last_refresh = last_critical_refresh.get(guild_id)
    
    if guild_id not in critical_amounts or not last_refresh or now - last_refresh > timedelta(hours=1):
        refresh_critical_amount(guild_id)
        
    return critical_amounts.get(guild_id, 2)

async def broadcast_error_log(message_content: str):
    """Broadcasts traceback details safely to the bot owner's DMs."""
    try:
        if not bot.is_ready():
            return
        owner = bot.get_user(BOT_OWNER_ID) or await bot.fetch_user(BOT_OWNER_ID)
        if owner:
            for i in range(0, len(message_content), 1900):
                chunk = message_content[i:i+1900]
                await owner.send(chunk, silent=True)
    except Exception as dev_err:
        print(f"Failed to transmit error logs to Discord owner DM: {dev_err}")

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
    """Count active users using the hourly cached active users set."""
    active_count = len(active_users_cache.get(guild.id, set()))
    return max(1, active_count)

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
                for voter_id_str, ts_str in vote_data[guild_id_str][target_id_str].items():
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

@tasks.loop(hours=1)
async def manage_active_roles_loop():
    """Scans history hourly to compute active members and assign roles based on message threshold."""
    try:
        await bot.wait_until_ready()
        print("🕒 [Role Sync Engine] Commencing hourly historical scan & role evaluation...")
        
        for guild in bot.guilds:
            guild_config = get_guild_data(guild.id)
            window_days = guild_config.get("activity_window_days", 7)
            threshold = guild_config.get("activity_message_threshold", 1)
            cutoff = datetime.now() - timedelta(days=window_days)
            
            user_message_counts = {}
            
            for channel in guild.text_channels:
                perms = channel.permissions_for(guild.me)
                if not perms.read_messages or not perms.read_message_history:
                    continue
                try:
                    async for message in channel.history(after=cutoff, limit=None):
                        if message.author.bot:
                            continue
                        user_id = message.author.id
                        user_message_counts[user_id] = user_message_counts.get(user_id, 0) + 1
                except discord.Forbidden:
                    continue
                except Exception as channel_err:
                    print(f"⚠️ [Role Sync Engine] Could not read channel {channel.name}: {channel_err}")

            active_user_ids = {uid for uid, count in user_message_counts.items() if count >= threshold}
            active_users_cache[guild.id] = active_user_ids
            
            role_id = guild_config.get("active_member_role")
            if not role_id:
                refresh_critical_amount(guild.id)
                continue
                
            role = guild.get_role(role_id)
            if not role:
                refresh_critical_amount(guild.id)
                continue
            
            broadcast_channel_id = guild_config.get("activity_broadcast_channel")
            broadcast_channel = guild.get_channel(broadcast_channel_id) if broadcast_channel_id else None
            
            for member in guild.members:
                if member.bot:
                    continue
                should_have = member.id in active_user_ids
                has_role = role in member.roles
                
                try:
                    if should_have and not has_role:
                        await member.add_roles(role, reason=f"Met active threshold ({threshold} msgs).")
                        if broadcast_channel:
                            await broadcast_channel.send(f"🎉 `{member.name}` has been assigned the `{role.name}` role due to meeting the activity threshold!", silent=True)
                    elif not should_have and has_role:
                        await member.remove_roles(role, reason="User fell below the activity threshold parameters.")
                        if broadcast_channel:
                            await broadcast_channel.send(f"📉 `{member.name}` lost the `{role.name}` role due to inactivity.", silent=True)
                except discord.Forbidden:
                    pass
                except Exception as e:
                    print(f"Failed to synchronize role updates for {member.name}: {e}")
                    
            refresh_critical_amount(guild.id)
            
    except Exception as e:
        print(f"Error in manage_active_roles_loop task frame: {e}")
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        await broadcast_error_log(f"🚨 **Background Task Loop Failure (`manage_active_roles_loop`):**\n```python\n{tb}\n```")

@tasks.loop(hours=24)
async def discord_backup_loop():
    await run_discord_channel_backup()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    
    load_vote_data()
    
    if not check_expired_votes.is_running():
        check_expired_votes.start()
    if not manage_active_roles_loop.is_running():
        manage_active_roles_loop.start()
    if not discord_backup_loop.is_running():
        discord_backup_loop.start()
        
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} application commands globally.")
    except Exception as e:
        print(f"Failed to sync application tree parameters: {e}")

@bot.event
async def on_guild_join(guild):
    get_guild_data(guild.id)
    print(f"Joined guild: {guild.name} (ID: {guild.id})")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    await bot.process_commands(message)

# --- APPLICATION SLASH COMMANDS ---

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
        f"Required Votes (10% threshold): {critical_amount}\n\n"
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

@bot.tree.command(name="shameboard", description="View the server's hall of shame database hierarchy leaderboard.")
@app_commands.guild_only()
async def shameboard(interaction: discord.Interaction):
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

@bot.tree.command(name="votekick", description="Initiate a democratic kick election against a problematic member.")
@app_commands.guild_only()
async def votekick(interaction: discord.Interaction, user: discord.Member):
    if is_command_disabled(interaction.guild_id, "votekick"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
        
    await interaction.response.defer()

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
        await interaction.followup.send("❌ You cannot invoke democracy against artificial synthetic logic instances.")
        return

    if user.id == interaction.user.id:
        await interaction.followup.send("❌ You cannot cast an electoral execution protocol upon yourself.")
        return

    if user.guild_permissions.administrator or user.id == interaction.guild.owner_id:
        await interaction.followup.send("❌ Target member holds sovereign immune administrative clearance privileges.")
        return

    target_id_str = str(user.id)
    voter_id_str = str(interaction.user.id)
    current_time = datetime.now()

    critical_amount = get_critical_amount(interaction.guild_id)

    if target_id_str not in guild_vote_data:
        guild_vote_data[target_id_str] = {}

    if voter_id_str in guild_vote_data[target_id_str]:
        guild_vote_data[target_id_str][voter_id_str] = current_time.isoformat()
        save_vote_data()
        current_votes = len(guild_vote_data[target_id_str])
        await interaction.followup.send(f"🔄 Your active vote tracking timestamp for `{user.name}` has been successfully updated. ({current_votes}/{critical_amount})")
        return

    guild_vote_data[target_id_str][voter_id_str] = current_time.isoformat()
    save_vote_data()

    current_votes = len(guild_vote_data[target_id_str])

    if current_votes >= critical_amount:
        del guild_vote_data[target_id_str]
        save_vote_data()

        ban_duration_days = guild_data.get("votekick_ban_duration", 7)
        
        try:
            try:
                await user.send(f"🚨 You have been democratically exiled from **{interaction.guild.name}** via vote-kick consensus matching for {ban_duration_days} days.")
            except Exception:
                pass

            await interaction.guild.ban(user, delete_message_days=0, reason=f"Democratically banned via Votekick threshold matching ({current_votes}/{critical_amount})")
            
            existing_ids = [int(k) for k in guild_data["entries"].keys()]
            next_id = str(max(existing_ids) + 1) if existing_ids else "1"
            
            guild_data["entries"][next_id] = {
                "user_id": user.id,
                "username": user.name,
                "reason": f"Democratically exiled for {ban_duration_days} days via votekick processing.",
                "date": current_time.isoformat(),
                "shamed_by": "System Consensus Process"
            }
            update_guild_data(interaction.guild_id, guild_data)

            await interaction.followup.send(f"🔨 **Exile Protocol Finalized!** `{user.name}` has accumulated sufficient consensus markers ({current_votes}/{critical_amount}) and has been banned for {ban_duration_days} days.")
            
            async def scheduled_unban_callback():
                await asyncio.sleep(ban_duration_days * 86400)
                try:
                    await interaction.guild.unban(discord.Object(id=user.id), reason="Democratic vote-kick ban duration cycle completed.")
                except Exception:
                    pass
            
            asyncio.create_task(scheduled_unban_callback())

        except discord.Forbidden:
            await interaction.followup.send("❌ **Execution Error:** Bot lacks permission hierarchies required to ban this target.")
    else:
        await interaction.followup.send(f"🗳️ **Consensus Registered!** `{interaction.user.name}` voted to kick `{user.name}`. Progress: ({current_votes}/{critical_amount} required matching markers). Votes expire in 24 hours.")

@bot.tree.command(name="config_setup", description="Configure server setup roles and parameters.")
@app_commands.guild_only()
async def config_setup(
    interaction: discord.Interaction, 
    manager_role: discord.Role = None, 
    shame_channel: discord.TextChannel = None, 
    votekick_broadcast_channel: discord.TextChannel = None,
    activity_broadcast_channel: discord.TextChannel = None,
    active_member_role: discord.Role = None,
    expiry_days: int = None,
    votekick_ban_duration: int = None,
    activity_window_days: int = None,
    activity_message_threshold: int = None
):
    if is_command_disabled(interaction.guild_id, "config_setup"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
        
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

    changes = []
    if manager_role is not None:
        guild_data["manager_role"] = manager_role.id
        changes.append(f"Manager Role -> `@{manager_role.name}`")
    if shame_channel is not None:
        guild_data["shame_channel"] = shame_channel.id
        changes.append(f"Shame Broadcast Channel -> <#{shame_channel.id}>")
    if votekick_broadcast_channel is not None:
        guild_data["votekick_broadcast_channel"] = votekick_broadcast_channel.id
        changes.append(f"Votekick Broadcast Channel -> <#{votekick_broadcast_channel.id}>")
    if activity_broadcast_channel is not None:
        guild_data["activity_broadcast_channel"] = activity_broadcast_channel.id
        changes.append(f"Activity Broadcast Channel -> <#{activity_broadcast_channel.id}>")
    if active_member_role is not None:
        guild_data["active_member_role"] = active_member_role.id
        changes.append(f"Active Member Role -> `@{active_member_role.name}`")
    if expiry_days is not None:
        guild_data["expiry_days"] = None if expiry_days <= 0 else expiry_days
        changes.append(f"Shame Expiry Days -> `{guild_data['expiry_days']}`")
    if votekick_ban_duration is not None:
        guild_data["votekick_ban_duration"] = max(1, votekick_ban_duration)
        changes.append(f"Votekick Ban Duration -> `{guild_data['votekick_ban_duration']} Days`")
    if activity_window_days is not None:
        guild_data["activity_window_days"] = max(1, activity_window_days)
        changes.append(f"Activity Window Days -> `{guild_data['activity_window_days']} Days`")
    if activity_message_threshold is not None:
        guild_data["activity_message_threshold"] = max(1, activity_message_threshold)
        changes.append(f"Activity Message Threshold -> `{guild_data['activity_message_threshold']} Messages`")

    if not changes:
        await interaction.response.send_message("ℹ️ No modification arguments were passed. System properties intact.", ephemeral=True)
        return

    update_guild_data(interaction.guild_id, guild_data)
    refresh_critical_amount(interaction.guild_id)
    
    await interaction.response.send_message(f"⚙️ **Configuration Properties Updated:**\n" + "\n".join([f"🔹 {c}" for c in changes]))

@bot.tree.command(name="config_cooldown", description="Modify the system anti-spam execution throttle timeframe limits.")
@app_commands.guild_only()
async def config_cooldown(interaction: discord.Interaction, seconds: int):
    if is_command_disabled(interaction.guild_id, "config_cooldown"):
        await interaction.response.send_message("❌ This command is disabled in this server.", ephemeral=True)
        return
        
    remaining = check_cooldown(interaction.guild_id, interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏱️ You're on cooldown. Wait {remaining:.1f} more seconds.", ephemeral=True)
        return

    if not is_moderator(interaction):
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

@bot.tree.command(name="activity_config_reset", description="Reset configuration elements back to native default fallbacks.")
@app_commands.guild_only()
@app_commands.choices(attribute=[
    app_commands.Choice(name="Active Member Role", value="role"),
    app_commands.Choice(name="Activity Broadcast Channel", value="channel"),
    app_commands.Choice(name="Activity Evaluation Window (7 Days)", value="window"),
    app_commands.Choice(name="Activity Message Threshold (1 Msg)", value="threshold")
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
    elif attribute == "threshold":
        guild_data["activity_message_threshold"] = 1
        display = "Activity Message Threshold (Reset to 1 Msg)"

    update_guild_data(interaction.guild_id, guild_data)
    cooldown_seconds = guild_data.get("cooldown", 0)
    
    if cooldown_seconds > 0:
        set_cooldown(interaction.guild_id, interaction.user.id, cooldown_seconds)
    await interaction.response.send_message(f"🔄 **Reset Confirmed**\n`{display}` has been returned to its standard system fallback value state.")

@bot.tree.command(name="command_restrict", description="Restrict specific modular slash command usage accessibility flags.")
@app_commands.guild_only()
async def command_restrict(interaction: discord.Interaction, command: str):
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

@bot.tree.command(name="command_unrestrict", description="Restore standard access configurations to a restricted slash command tool.")
@app_commands.guild_only()
async def command_unrestrict(interaction: discord.Interaction, command: str):
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

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("🚨 Critical Setup Error: DISCORD_TOKEN environmental string injection variable missing!")
        