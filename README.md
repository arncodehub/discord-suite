# Suite Bot

A Discord bot for managing a "Hall of Shame" with additional moderation features — track user infractions, vote-based kicks, message marking, active member roles, and more.

## What is Suite Bot?

Suite Bot is a feature-rich Discord bot designed to help moderate your server with a "Hall of Shame" system. It allows managers to track infractions, vote for user kicks, mark messages, automatically manage active member roles, and more. Perfect for tracking funny moments, rule violations, and maintaining community standards.

## Features

- **Hall of Shame Management**: Add/remove users with reasons and track entries over time
- **Automatic Expiration**: Entries automatically expire after a configurable number of days
- **Vote-to-Kick System**: Community-driven moderation with majority-based kicks (kick or ban for up to 1 week)
- **Vote-to-Kick Broadcast Channel**: Route all public vote announcements to a dedicated channel
- **Active Member Roles**: Automatically grant or remove a role based on recent message activity via an hourly historical scan
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