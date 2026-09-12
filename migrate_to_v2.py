#!/usr/bin/env python3
"""
Migration script for Discord Suite Bot v2.0.0

Changes:
1. Rename shame_data.json -> main.json
2. Extract entries to separate halls.json per guild
3. Move aliases to per-server structure under guild 1501351977202876698
4. Remove backward compatibility fields
"""

import json
import os
import shutil
from datetime import datetime

# Target guild for global aliases migration
TARGET_GUILD_ID = "1501351977202876698"

def backup_files():
    """Create backups of all data files"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = f"backup_{timestamp}"
    os.makedirs(backup_dir, exist_ok=True)
    
    files_to_backup = ["shame_data.json", "vote_data.json", "votes.json", "aliases.json"]
    for file in files_to_backup:
        if os.path.exists(file):
            shutil.copy2(file, os.path.join(backup_dir, file))
            print(f"✓ Backed up {file} to {backup_dir}/")
    
    return backup_dir

def load_json(filename):
    """Load JSON file"""
    if not os.path.exists(filename):
        return {}
    with open(filename, 'r') as f:
        return json.load(f)

def save_json(filename, data):
    """Save JSON file with pretty printing"""
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)
    print(f"✓ Saved {filename}")

def migrate_data():
    """Main migration logic"""
    print("=" * 60)
    print("Discord Suite Bot - Migration to v2.0.0")
    print("=" * 60)
    print()
    
    # Step 1: Backup
    print("Step 1: Creating backups...")
    backup_dir = backup_files()
    print(f"✓ Backups created in {backup_dir}/")
    print()
    
    # Step 2: Load current data
    print("Step 2: Loading current data...")
    shame_data = load_json("shame_data.json")
    aliases_data = load_json("aliases.json")
    vote_data = load_json("vote_data.json")
    print(f"✓ Loaded shame_data.json ({len(shame_data)} entries)")
    print(f"✓ Loaded aliases.json ({len(aliases_data)} aliases)")
    print(f"✓ Loaded vote_data.json")
    print()
    
    # Step 3: Restructure data
    print("Step 3: Restructuring data...")
    
    # Create new main.json structure
    main_data = {}
    halls_data = {}
    new_aliases_data = {}
    
    for guild_id, guild_config in shame_data.items():
        # Skip system_metadata
        if guild_id == "system_metadata":
            main_data["system_metadata"] = guild_config
            continue
        
        # Extract entries to halls.json
        entries = guild_config.pop("entries", {})
        halls_data[guild_id] = {
            "entries": entries
        }
        
        # Initialize empty aliases for each guild
        new_aliases_data[guild_id] = {}
        
        # If this is the target guild, add global aliases
        if guild_id == TARGET_GUILD_ID:
            new_aliases_data[guild_id] = {str(k): v for k, v in aliases_data.items()}
            print(f"  ✓ Migrated {len(aliases_data)} global aliases to guild {guild_id}")
        
        main_data[guild_id] = guild_config
    
    print(f"✓ Restructured data for {len(main_data) - 1} guilds")
    print()
    
    # Step 4: Save new files
    print("Step 4: Saving new data files...")
    save_json("main.json", main_data)
    save_json("halls.json", halls_data)
    save_json("aliases.json", new_aliases_data)
    # Rename vote_data.json -> votes.json
    if os.path.exists("vote_data.json"):
        shutil.copy2("vote_data.json", "votes.json")
        print(f"✓ Renamed vote_data.json -> votes.json")
    print()
    
    # Step 5: Summary
    print("Step 5: Migration summary")
    print("-" * 60)
    print(f"✓ Created main.json (guild configs)")
    print(f"✓ Created halls.json (hall entries)")
    print(f"✓ Created aliases.json (per-server aliases)")
    print(f"✓ Renamed vote_data.json -> votes.json")
    print(f"✓ Migrated {len(aliases_data)} aliases to guild {TARGET_GUILD_ID}")
    print()
    print("Old file (shame_data.json) can be deleted after verification.")
    print("Backups are available in:", backup_dir)
    print()
    print("=" * 60)
    print("Migration complete!")
    print("=" * 60)

if __name__ == "__main__":
    try:
        migrate_data()
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
