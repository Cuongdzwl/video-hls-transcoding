#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Upload Server - SSH/SCP Video Folder Uploader
==============================================

Upload processed video folders to remote server via SSH/SCP.
Supports automatic backup with timestamps.

Features:
- SSH key-based authentication
- Automatic backup before overwrite
- Configuration via .env file
- Structured logging
- File logging

Usage:
  python source/upload_server.py --folder ./output/video-id

Author: Cuongdz
Date: October 29, 2025
"""

import argparse
import os
import sys
import subprocess
import shlex
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple
from dotenv import load_dotenv

# Import shared modules
from logger import get_logger, Colors
from config import get_ssh_config, get_server_paths, get_backup_config, get_log_path, get_output_path

logger = get_logger("upload_server")


class SSHUploader:
    """Handle SSH/SCP operations for uploading folders"""
    
    def __init__(
        self,
        host: str,
        user: str,
        port: int = 22,
        key_path: Optional[str] = None,
        server_base_path: str = "/var/www/videos",
        server_backup_path: str = "/var/www/videos_backup",
        auto_backup: bool = True,
        backup_format: str = "%Y%m%d_%H%M%S"
    ):
        self.host = host
        self.user = user
        self.port = port
        self.key_path = os.path.expanduser(key_path) if key_path else None
        self.server_base_path = server_base_path
        self.server_backup_path = server_backup_path
        self.auto_backup = auto_backup
        self.backup_format = backup_format
        
        # Validate SSH key if provided
        if self.key_path and not os.path.exists(self.key_path):
            logger.error(f"SSH key not found: {self.key_path}")
            sys.exit(1)
    
    def _build_ssh_cmd(self) -> list:
        """Build base SSH command with authentication"""
        cmd = ["ssh"]
        
        if self.port != 22:
            cmd.extend(["-p", str(self.port)])
        
        if self.key_path:
            cmd.extend(["-i", self.key_path])
        
        # Add common SSH options
        cmd.extend([
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR"
        ])
        
        cmd.append(f"{self.user}@{self.host}")
        
        return cmd
    
    def _build_scp_cmd(self) -> list:
        """Build base SCP command with authentication"""
        cmd = ["scp", "-r"]  # Recursive
        
        if self.port != 22:
            cmd.extend(["-P", str(self.port)])
        
        if self.key_path:
            cmd.extend(["-i", self.key_path])
        
        # Add common SCP options
        cmd.extend([
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR"
        ])
        
        return cmd
    
    def _run_ssh_command(self, remote_command: str) -> Tuple[bool, str]:
        """Execute command on remote server via SSH"""
        cmd = self._build_ssh_cmd()
        cmd.append(remote_command)
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.returncode == 0, result.stdout.strip()
        except subprocess.TimeoutExpired:
            return False, "SSH command timed out"
        except Exception as e:
            return False, str(e)
    
    def test_connection(self) -> bool:
        """Test SSH connection to server"""
        logger.phase_start(f"Testing SSH connection to {self.user}@{self.host}:{self.port}")
        
        success, output = self._run_ssh_command("echo 'Connection successful'")
        
        if success:
            logger.phase_end("SSH connection successful")
            return True
        else:
            logger.error(f"SSH connection failed: {output}")
            return False
    
    def folder_exists(self, folder_path: str) -> bool:
        """Check if folder exists on remote server"""
        success, _ = self._run_ssh_command(f"test -d '{folder_path}' && echo 'exists'")
        return success
    
    def backup_existing_folder(self, folder_name: str) -> Tuple[bool, str]:
        """Backup existing folder on server with timestamp
        
        Returns:
            Tuple[bool, str]: (success, backup_path)
        """
        source_path = f"{self.server_base_path}/{folder_name}"
        timestamp = datetime.now().strftime(self.backup_format)
        backup_name = f"{folder_name}_{timestamp}"
        backup_path = f"{self.server_backup_path}/{backup_name}"
        
        logger.phase_start(f"Backing up existing folder: {folder_name}")
        logger.info(f"{source_path} → {backup_path}")
        
        # Create backup directory if it doesn't exist
        success, _ = self._run_ssh_command(f"mkdir -p '{self.server_backup_path}'")
        if not success:
            logger.error("Failed to create backup directory")
            return False, ""
        
        # Move existing folder to backup location
        success, output = self._run_ssh_command(f"mv '{source_path}' '{backup_path}'")
        
        if success:
            logger.phase_end(f"Backup created: {backup_name}")
            return True, backup_path
        else:
            logger.error(f"Backup failed: {output}")
            return False, ""
    
    def upload_folder(self, local_folder: Path) -> Tuple[bool, str]:
        """Upload folder to server via SCP
        
        Returns:
            Tuple[bool, str]: (success, backup_path if backup was created, else empty string)
        """
        if not local_folder.exists():
            logger.error(f"Local folder not found: {local_folder}")
            return False, ""
        
        if not local_folder.is_dir():
            logger.error(f"Path is not a directory: {local_folder}")
            return False, ""
        
        # Use local folder name as remote folder name
        folder_name = local_folder.name
        remote_path = f"{self.server_base_path}/{folder_name}"
        backup_path = ""
        
        logger.info("=" * 70)
        logger.phase_start(f"Uploading: {local_folder.name} → {folder_name}")
        logger.info("=" * 70)
        
        # Check if folder exists on server
        if self.folder_exists(remote_path):
            if self.auto_backup:
                logger.warning(f"Folder already exists on server: {folder_name}")
                success, backup_path = self.backup_existing_folder(folder_name)
                if not success:
                    return False, ""
            else:
                logger.error(f"Folder already exists on server: {folder_name}")
                logger.info("Use --backup to automatically backup existing folders")
                return False, ""
        
        # Ensure base directory exists
        logger.info(f"Ensuring server directory exists: {self.server_base_path}")
        success, _ = self._run_ssh_command(f"mkdir -p '{self.server_base_path}'")
        if not success:
            logger.error("Failed to create server directory")
            return False, ""
        
        # Build SCP command
        cmd = self._build_scp_cmd()
        cmd.append(str(local_folder))
        cmd.append(f"{self.user}@{self.host}:{self.server_base_path}/")
        
        # Execute SCP upload
        logger.phase_start("Starting upload...")
        logger.command(' '.join(shlex.quote(c) if ' ' in c else c for c in cmd))
        
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            
            if result.returncode == 0:
                logger.phase_end("Upload completed successfully!")
                logger.metadata(f"Remote path: {remote_path}")
                
                # Verify upload
                if self.folder_exists(remote_path):
                    logger.phase_end("Verified: Folder exists on server")
                    return True, backup_path
                else:
                    logger.warning("Could not verify folder on server")
                    return True, backup_path
            else:
                logger.error(f"Upload failed with exit code {result.returncode}")
                if result.stdout:
                    logger.error(f"Output: {result.stdout}")
                return False, ""
                
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return False, ""
    
    def list_backups(self) -> None:
        """List all backup folders on server"""
        logger.phase_start("Listing backups on server...")
        
        success, output = self._run_ssh_command(
            f"ls -lht '{self.server_backup_path}' 2>/dev/null || echo 'No backups found'"
        )
        
        if success and output:
            logger.info("\n" + output)
        else:
            logger.warning("No backups found")


def setup_file_logging():
    """Setup file logging for upload operations"""
    log_dir = get_log_path()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"upload_{timestamp}.log"
    
    import logging
    file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)
    
    logger.info(f"Log file created: {log_file}")
    return log_file


def load_config() -> dict:
    """Load configuration from environment variables"""
    config = {}
    
    # Get configuration from centralized config module
    config.update(get_ssh_config())
    config.update(get_server_paths())
    config.update(get_backup_config())
    config['local_output_path'] = str(get_output_path())
    
    # Map to old keys for compatibility
    config['ssh_host'] = config.pop('host')
    config['ssh_port'] = config.pop('port')
    config['ssh_user'] = config.pop('user')
    config['ssh_key_path'] = config.pop('key_path')
    config['server_base_path'] = config.pop('base_path')
    config['server_backup_path'] = config.pop('backup_path')
    config['backup_format'] = config.pop('timestamp_format')
    
    return config


def log_config_summary(config: dict, host: str, user: str, port: int, 
                       server_base_path: str, server_backup_path: str, 
                       auto_backup: bool, key_path: str):
    """Log configuration summary (excluding credentials)"""
    logger.info("=" * 70)
    logger.info("UPLOAD CONFIGURATION")
    logger.info("=" * 70)
    
    # SSH Configuration (hide key path for security)
    logger.metadata(f"SSH Host: {host}")
    logger.metadata(f"SSH Port: {port}")
    logger.metadata(f"SSH User: {user}")
    logger.metadata(f"SSH Key: {'[configured]' if key_path else '[not set]'}")
    
    # Server Paths
    logger.metadata(f"Server Base Path: {server_base_path}")
    logger.metadata(f"Server Backup Path: {server_backup_path}")
    
    # Local Paths
    logger.metadata(f"Local Output Path: {config['local_output_path']}")
    
    # Options
    logger.metadata(f"Auto Backup: {'enabled' if auto_backup else 'disabled'}")
    logger.metadata(f"Backup Format: {config['backup_format']}")
    
    logger.info("=" * 70)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Upload folders to remote server via SSH/SCP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Upload a folder (uses .env configuration)
  python source/upload_server.py --folder output/video123
  
  # Test connection only
  python source/upload_server.py --test
  
  # List backups on server
  python source/upload_server.py --list-backups
  
  # Override .env settings
  python source/upload_server.py --folder output/video123 --host example.com --user admin
  
  # Disable auto-backup
  python source/upload_server.py --folder output/video123 --no-backup

Configuration:
  Create a .env file with your SSH credentials (see .env.example)
        """
    )
    
    parser.add_argument(
        "--folder", "-f",
        type=Path,
        help="Local folder path to upload"
    )
    
    parser.add_argument(
        "--host",
        help="SSH host (overrides .env)"
    )
    
    parser.add_argument(
        "--port",
        type=int,
        help="SSH port (overrides .env)"
    )
    
    parser.add_argument(
        "--user", "-u",
        help="SSH user (overrides .env)"
    )
    
    parser.add_argument(
        "--key", "-k",
        help="SSH key path (overrides .env)"
    )
    
    parser.add_argument(
        "--server-path",
        help="Server base path (overrides .env)"
    )
    
    parser.add_argument(
        "--backup-path",
        help="Server backup path (overrides .env)"
    )
    
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Enable auto-backup of existing folders"
    )
    
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Disable auto-backup (fail if folder exists)"
    )
    
    parser.add_argument(
        "--test", "-t",
        action="store_true",
        help="Test SSH connection only"
    )
    
    parser.add_argument(
        "--list-backups", "-l",
        action="store_true",
        help="List backup folders on server"
    )
    
    args = parser.parse_args()
    
    # Setup file logging
    log_file = setup_file_logging()
    logger.info(f"Upload session started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Load configuration
    config = load_config()
    
    # Override with command line arguments
    host = args.host or config['ssh_host']
    port = args.port or config['ssh_port']
    user = args.user or config['ssh_user']
    key_path = args.key or config['ssh_key_path']
    server_base_path = args.server_path or config['server_base_path']
    server_backup_path = args.backup_path or config['server_backup_path']
    
    # Handle backup flag
    if args.no_backup:
        auto_backup = False
    elif args.backup:
        auto_backup = True
    else:
        auto_backup = config['auto_backup']
    
    # Validate required settings
    if not host or not user:
        logger.error("Missing required configuration: SSH_HOST and SSH_USER")
        logger.info("Create a .env file or provide --host and --user arguments")
        logger.info("See .env.example for configuration template")
        sys.exit(1)
    
    # Log configuration summary (excluding credentials)
    log_config_summary(config, host, user, port, server_base_path, 
                      server_backup_path, auto_backup, key_path)
    
    # Initialize uploader
    uploader = SSHUploader(
        host=host,
        user=user,
        port=port,
        key_path=key_path,
        server_base_path=server_base_path,
        server_backup_path=server_backup_path,
        auto_backup=auto_backup,
        backup_format=config['backup_format']
    )
    
    # Handle different modes
    if args.test:
        # Test connection mode
        logger.info(f"Operation: Connection Test\n")
        if uploader.test_connection():
            logger.info("=" * 70)
            logger.phase_end("SSH CONNECTION IS WORKING!")
            logger.info("=" * 70 + "\n")
            logger.info(f"Session log saved to: {log_file}")
            sys.exit(0)
        else:
            logger.info("=" * 70)
            logger.error("SSH CONNECTION FAILED!")
            logger.info("=" * 70 + "\n")
            logger.info(f"Session log saved to: {log_file}")
            sys.exit(1)
    
    elif args.list_backups:
        # List backups mode
        logger.info(f"Operation: List Backups\n")
        if not uploader.test_connection():
            logger.info(f"Session log saved to: {log_file}")
            sys.exit(1)
        uploader.list_backups()
        logger.info(f"\nSession log saved to: {log_file}")
        sys.exit(0)
    
    elif args.folder:
        # Upload mode
        logger.info(f"Operation: Upload Folder")
        logger.metadata(f"Local Folder: {args.folder}")
        logger.metadata(f"Remote Name: {args.folder.name}")
        logger.info("")
        
        if not uploader.test_connection():
            logger.info(f"Session log saved to: {log_file}")
            sys.exit(1)
        
        success, backup_path = uploader.upload_folder(args.folder)
        
        if success:
            logger.info("\n" + "=" * 70)
            logger.phase_end("UPLOAD COMPLETED SUCCESSFULLY")
            logger.info("=" * 70 + "\n")
            if backup_path:
                logger.info(f"Backup created at: {backup_path}")
                # Print backup path to stdout for parent process to capture
                print(f"BACKUP_PATH:{backup_path}")
            logger.info(f"Session log saved to: {log_file}")
            sys.exit(0)
        else:
            logger.info("\n" + "=" * 70)
            logger.error("UPLOAD FAILED")
            logger.info("=" * 70 + "\n")
            logger.info(f"Session log saved to: {log_file}")
            sys.exit(1)
            sys.exit(1)
    
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
