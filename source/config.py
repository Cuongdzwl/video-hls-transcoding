#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuration Loader - Centralized Environment Configuration
=============================================================

Loads and provides access to environment variables with defaults.
All path configurations should be loaded through this module.

Author: GitHub Copilot
Date: October 29, 2025
"""

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
# Load .env file if it exists
load_dotenv()


def get_edited_path() -> Path:
    """Get the edited videos directory path from environment"""
    path_str = os.getenv('LOCAL_EDITED_PATH', './edited')
    path = Path(path_str)
    path.mkdir(exist_ok=True)
    return path


def get_output_path() -> Path:
    """Get the output directory path from environment"""
    path_str = os.getenv('LOCAL_OUTPUT_PATH', './output')
    path = Path(path_str)
    path.mkdir(exist_ok=True)
    return path


def get_log_path() -> Path:
    """Get the log directory path from environment"""
    path_str = os.getenv('LOCAL_LOG_PATH', './log')
    path = Path(path_str)
    path.mkdir(exist_ok=True)
    return path


def get_ssh_config() -> dict:
    """Get SSH configuration from environment"""
    return {
        'host': os.getenv('SSH_HOST', ''),
        'port': int(os.getenv('SSH_PORT', '22')),
        'user': os.getenv('SSH_USER', ''),
        'key_path': os.getenv('SSH_KEY_PATH', '~/.ssh/id_rsa'),
    }


def get_server_paths() -> dict:
    """Get server paths from environment"""
    return {
        'base_path': os.getenv('SERVER_BASE_PATH', '/var/www/videos'),
        'backup_path': os.getenv('SERVER_BACKUP_PATH', '/var/www/videos_backup'),
    }


def get_backup_config() -> dict:
    """Get backup configuration from environment"""
    return {
        'auto_backup': os.getenv('AUTO_BACKUP', 'true').lower() == 'true',
        'timestamp_format': os.getenv('BACKUP_TIMESTAMP_FORMAT', '%Y%m%d_%H%M%S'),
    }


def get_r2_config() -> dict:
    """Get Cloudflare R2 configuration from environment"""
    return {
        'account_id': os.getenv('R2_ACCOUNT_ID', ''),
        'access_key_id': os.getenv('R2_ACCESS_KEY_ID', ''),
        'secret_access_key': os.getenv('R2_SECRET_ACCESS_KEY', ''),
        'bucket': os.getenv('R2_BUCKET', ''),
        'prefix': os.getenv('R2_PREFIX', ''),
        'public_base_url': os.getenv('R2_PUBLIC_BASE_URL', ''),
    }


def get_all_config() -> dict:
    """Get all configuration as a dictionary"""
    return {
        'edited_path': get_edited_path(),
        'output_path': get_output_path(),
        'log_path': get_log_path(),
        'ssh': get_ssh_config(),
        'server': get_server_paths(),
        'backup': get_backup_config(),
        'r2': get_r2_config(),
    }


# Convenience exports
EDITED_PATH = get_edited_path()
OUTPUT_PATH = get_output_path()
LOG_PATH = get_log_path()
