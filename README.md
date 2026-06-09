# Suite Bot

A Discord bot for managing a "Hall of Shame" with additional moderation features — track user infractions, vote-based kicks, message marking, active member roles, and more.

## What is Suite Bot?

Suite Bot is a feature-rich Discord bot designed to help moderate your server with a "Hall of Shame" system. It allows managers to track infractions, vote for user kicks, mark messages, automatically manage active member roles, and more. Perfect for tracking funny moments, rule violations, and maintaining community standards.

## Features

- **Hall of Shame Management**: Add/remove users with reasons and track entries over time
- **Automatic Expiration**: Entries automatically expire after a configurable number of days
- **Vote-to-Kick System**: Community-driven moderation with majority-based kicks (kick or ban for up to 1 week)
- **Vote-to-Kick Broadcast Channel**: Route all public vote announcements to a dedicated channel
- **Active Member Roles**: Automatically grant or remove a role based on recent message activity
- **Message Marking**: Mark messages as ragebait for moderator review
- **Command Cooldowns**: Set cooldowns to prevent command spam
- **Permission Management**: Designate manager roles or restrict manager commands to moderators
- **Command Enable/Disable**: Toggle commands on/off per server
- **View Shame Records**: Users can view their own shame entries or see the full leaderboard
- **Past Date Support**: Add shame entries for past dates (useful for retroactive entries)
- **Shame Channel Announcements**: Post shame announcements to a dedicated channel
- **Configurable Ban Duration**: Set ban duration from 0 (kick only) to 7 days for vote-to-kick

## Setup

### Prerequisites

- Python 3.8+
- discord.py 2.4.0+
- python-dotenv 1.0.0

### Installation

1. Clone or download this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create a `.env` file in the root directory with your Discord bot token:
   ```
   DISCORD_TOKEN=your_bot_token_here
   ```

4. Run the bot:
   ```bash
   python bot.py
   ```

The bot requires the **Message Content** intent to track user activity for vote-to-kick and active member roles.

## Commands

### User Commands

These commands are available to all users.

#### `/info`

Display current server settings and bot status, including:

- Manager role
- Shame channel
- Activity broadcast channel
- Bot latency
- Command cooldown
- Shame entry expiry
- Vote-to-kick ban duration
- Activity window
- Critical amount (votes needed for a kick)

#### `/list_my_shame`

View all your shame entries in the current server, including expiration dates.

#### `/list_all_shame`

View the complete hall of shame for the server, sorted by shame count.

#### `/vote <user> [anonymous]`

Vote to kick a user from the server. Votes expire after 24 hours. Requires a majority of active members to kick.

Public vote announcements (vote count, kick/ban results, etc.) are sent to the configured vote-kick broadcast channel. Your confirmation is ephemeral and only visible to you in the channel where you ran the command.

**Parameters:**
- `user` (required): The Discord user to vote for
- `anonymous` (optional): Whether your vote is anonymous (default: True)

**Example:**
```
/vote @User
/vote @User false
```

#### `/unvote`

Remove your vote from a user you previously voted for. Public unvote announcements follow the same broadcast channel rules as `/vote`.

#### `/mark_message <message_id> <mark_type>`

Mark a message with a specific type.

**Parameters:**
- `message_id` (required): The ID of the message to mark
- `mark_type` (required): Type of mark (e.g., ragebait)

#### `Mark as Ragebait` (right-click on message)

Quickly mark a message as ragebait via the right-click context menu. Equivalent to `/mark_message`. Disabling `/mark_message` also disables this context menu.

#### `/unmark_message <message_id> <mark_type>`

Remove a mark from a message.

**Parameters:**
- `message_id` (required): The ID of the message to unmark
- `mark_type` (required): Type of mark to remove

#### `Unmark as Ragebait` (right-click on message)

Remove ragebait mark from a message via right-click context menu. Equivalent to `/unmark_message`. Disabling `/unmark_message` also disables this context menu.

#### `/message_info <message_id>`

View all markings on a specific message.

**Parameters:**
- `message_id` (required): The ID of the message to inspect

### Moderator Commands

These commands require the **Manage Server** permission.

#### `/disable <command>`

Disable a bot command in this server.

**Parameters:**
- `command` (required): The command to disable (supports autocomplete)

#### `/enable <command>`

Enable a bot command in this server.

**Parameters:**
- `command` (required): The command to enable (supports autocomplete)

#### `/set_manager_role <role>`

Set a role that can use manager commands alongside moderators.

**Parameters:**
- `role` (required): The Discord role to grant manager permissions

#### `/reset_manager_role`

Clear the manager role. When no manager role is set, only moderators can use manager commands.

### Manager Commands

These commands can be used by **Moderators** OR users with the designated **manager role**.

#### `/shame <user> <reason> [date]`

Add a user to the hall of shame. Requires a shame channel and expiry timer to be configured first.

**Parameters:**
- `user` (required): The Discord user to add
- `reason` (required): The reason for the shame entry
- `date` (optional): Date in YYYY-MM-DD format (e.g., 2026-05-10). If not provided, uses today's date.

**Example:**
```
/shame @User "Broke the rules" 2026-05-10
/shame @User "Late to meeting"
```

#### `/unshame <entry_id> [reason]`

Remove a user from the hall of shame.

**Parameters:**
- `entry_id` (required): The ID of the entry to remove (shown when adding shame)
- `reason` (optional): Reason for removal

#### `/set_shame_channel <channel>`

Set the channel where shame announcements are posted.

**Parameters:**
- `channel` (required): The Discord channel for announcements

#### `/set_expiry_timer <days>`

Set how many days before shame entries automatically expire.

**Parameters:**
- `days` (required): Number of days (minimum 1)

#### `/cooldown <seconds>`

Set a cooldown between commands to prevent spam.

**Parameters:**
- `seconds` (required): Cooldown duration in seconds (0-30)

#### `/set_message_log_channel <channel>`

Set the channel where message marking logs are sent.

**Parameters:**
- `channel` (required): The Discord channel for log messages

#### `/set_votekick_ban_duration <days>`

Set the ban duration for vote-to-kick. Set to 0 for kick only, or 1-7 days to ban.

**Parameters:**
- `days` (required): Number of days to ban (0-7, default: 7)

**Example:**
```
/set_votekick_ban_duration 7
/set_votekick_ban_duration 0
```

#### `/set_votekick_broadcast_channel [channel]`

Set the channel for all public vote-kick announcements (votes, unvotes, kick/ban results, and vote expirations).

If no channel is provided, clears the setting and announcements fall back to the last channel where `/vote` or `/unvote` was used.

**Parameters:**
- `channel` (optional): The Discord channel for vote-kick announcements

#### `/set_active_member_role <role>`

Set the role automatically assigned to members who have sent a message within the activity window.

**Parameters:**
- `role` (required): The Discord role for active members

#### `/set_activity_window <days>`

Set how many days of message activity count as "active" for active member roles and vote-to-kick critical amount.

**Parameters:**
- `days` (required): Number of days (minimum 1, default: 7)

#### `/set_activity_broadcast_channel [channel]`

Set the channel where active member role grant/remove notifications are posted.

**Parameters:**
- `channel` (optional): The Discord channel for role update notifications. Omit to disable broadcasts.

## Configuration

All configuration is stored in JSON files and managed through Discord commands. The bot automatically creates these files on first run.

### Configuration Options

Stored in `shame_data.json` per guild:

- **manager_role**: Role ID for managers (`null` = only moderators can use manager commands)
- **shame_channel**: Channel ID for shame announcements
- **message_log_channel**: Channel ID for message marking logs
- **cooldown**: Command cooldown in seconds (0-30)
- **expiry_days**: Days before shame entries expire (`null` until set via `/set_expiry_timer`)
- **votekick_ban_duration**: Days to ban users on vote-to-kick (0-7, default: 7)
- **votekick_broadcast_channel**: Channel ID for public vote-kick announcements (`null` = use last-used channel)
- **last_votekick_channel**: Last channel where `/vote` or `/unvote` was used (internal fallback)
- **active_member_role**: Role ID for active member role assignment (`null` = feature disabled)
- **activity_window**: Days of activity to count as active (default: 7)
- **activity_broadcast_channel**: Channel ID for active member role notifications (`null` = disabled)
- **disabled_commands**: List of disabled command names
- **message_markings**: Message markings storage (internal use)
- **entries**: Shame entry records

### Vote-to-Kick Critical Amount

The critical amount is the number of votes required to kick someone: `(active_members // 2) + 1` (simple majority).

Active members are counted as non-bot guild members who sent at least one message within the configured **activity window**. Activity is tracked in real time while the bot is online — past message history is not scanned.

## Data Storage

All shame entries, configuration, vote data, and user activity are stored in `shame_data.json`, `vote_data.json`, and `user_activity.json`. These files are automatically created and updated by the bot.

### Data Structure

**shame_data.json:**
- Guild-specific settings (manager role, channels, cooldown, expiry, vote-to-kick, activity settings)
- Shame entries with user info, reasons, dates, and metadata
- Disabled commands list
- Message markings

**vote_data.json:**
- Vote records with timestamps (24-hour expiration per vote)

**user_activity.json:**
- User activity tracking per guild (`user_id` → last message timestamp)
- Used for active member roles and vote-to-kick critical amount
- Updated when a non-bot user sends a message while the bot is online

## Permissions

- **All Users**: `/info`, `/list_my_shame`, `/list_all_shame`, `/vote`, `/unvote`, `/mark_message`, `/unmark_message`, `/message_info`, and the ragebait context menus
- **Moderators** (Manage Server): `/disable`, `/enable`, `/set_manager_role`, `/reset_manager_role`, and all Manager commands
- **Managers** (Moderators OR designated manager role): `/shame`, `/unshame`, `/set_shame_channel`, `/set_expiry_timer`, `/cooldown`, `/set_message_log_channel`, `/set_votekick_ban_duration`, `/set_votekick_broadcast_channel`, `/set_active_member_role`, `/set_activity_window`, `/set_activity_broadcast_channel`

## Troubleshooting

### Bot doesn't respond to commands
- Ensure the bot has permission to send messages in the channel
- Check that slash commands are enabled in your server
- Verify the bot token is correct in `.env`

### Commands not appearing
- Try re-inviting the bot to your server
- Ensure the bot has the `applications.commands` scope

### Entries not expiring
- Check the shame entry expiry setting with `/info`
- Entries expire based on their creation date, not when they're viewed
- An expiry timer must be set with `/set_expiry_timer` before entries can be added

### Vote kicks not working
- Ensure the bot has the **Kick Members** permission (and **Ban Members** if using ban duration > 0)
- Check that the vote count meets or exceeds the critical amount shown in `/info`
- Votes expire after 24 hours
- Check the vote-to-kick ban duration with `/info`
- Ensure the bot can post in the vote-kick broadcast channel (or the last channel where vote commands were used)

### Critical amount seems wrong
- Critical amount is based on active members within the **activity window**, not total server members or role holders
- Only non-bot members still in the server who messaged recently (while the bot was online) are counted
- Check the activity window with `/info` or adjust it with `/set_activity_window`

### Active member role not updating
- Ensure `/set_active_member_role` has been configured
- Role checks run every 5 minutes and when a user sends a message
- The bot must have permission to manage the configured role (and its role must be above the target role)

## License

This project is open source and available for personal use.
