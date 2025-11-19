#!/usr/bin/env python3

import os
import sys
import argparse
import json
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def main():
    logger.info("Starting tapevault.py")
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Tape Vault Management System')
    parser.add_argument('--config', help='Configuration file path')
    parser.add_argument('--action', choices=['list', 'add', 'remove', 'backup'], help='Action to perform')
    parser.add_argument('--tape-id', help='Tape ID for operations')
    
    args = parser.parse_args()
    
    # Load configuration
    config = load_config(args.config)
    
    # Perform action
    if args.action == 'list':
        list_tapes(config)
    elif args.action == 'add':
        add_tape(config, args.tape_id)
    elif args.action == 'remove':
        remove_tape(config, args.tape_id)
    elif args.action == 'backup':
        backup_tapes(config)
    else:
        logger.warning("No action specified. Use --action to specify an action.")
        
    logger.info("Finished tapevault.py")


def load_config(config_path):
    """Load configuration from file or use defaults."""
    default_config = {
        "storage_path": "/var/lib/tapevault",
        "backup_path": "/backup/tapevault",
        "log_level": "INFO"
    }
    
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            return {**default_config, **config}
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return default_config
    else:
        return default_config


def list_tapes(config):
    """List all tapes in the vault."""
    storage_path = config["storage_path"]
    if os.path.exists(storage_path):
        tapes = os.listdir(storage_path)
        logger.info(f"Found {len(tapes)} tapes:")
        for tape in tapes:
            print(tape)
    else:
        logger.warning(f"Storage path {storage_path} does not exist")


def add_tape(config, tape_id):
    """Add a new tape to the vault."""
    storage_path = config["storage_path"]
    tape_path = os.path.join(storage_path, tape_id)
    
    try:
        os.makedirs(storage_path, exist_ok=True)
        os.makedirs(tape_path, exist_ok=True)
        
        # Create metadata file
        metadata_file = os.path.join(tape_path, "metadata.json")
        metadata = {
            "id": tape_id,
            "created": datetime.now().isoformat(),
            "status": "active"
        }
        
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        logger.info(f"Added tape {tape_id} to vault")
    except Exception as e:
        logger.error(f"Error adding tape {tape_id}: {e}")


def remove_tape(config, tape_id):
    """Remove a tape from the vault."""
    storage_path = config["storage_path"]
    tape_path = os.path.join(storage_path, tape_id)
    
    if os.path.exists(tape_path):
        try:
            import shutil
            shutil.rmtree(tape_path)
            logger.info(f"Removed tape {tape_id} from vault")
        except Exception as e:
            logger.error(f"Error removing tape {tape_id}: {e}")
    else:
        logger.warning(f"Tape {tape_id} not found in vault")


def backup_tapes(config):
    """Backup all tapes to backup location."""
    storage_path = config["storage_path"]
    backup_path = config["backup_path"]
    
    if os.path.exists(storage_path):
        try:
            import shutil
            os.makedirs(backup_path, exist_ok=True)
            
            # Create timestamped backup directory
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = os.path.join(backup_path, f"backup_{timestamp}")
            
            shutil.copytree(storage_path, backup_dir)
            logger.info(f"Backed up tapes to {backup_dir}")
        except Exception as e:
            logger.error(f"Error during backup: {e}")
    else:
        logger.warning(f"Storage path {storage_path} does not exist")


if __name__ == "__main__":
    main()
