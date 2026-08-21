#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloudflare R2 Video Folder Uploader
===================================

Upload processed HLS/DASH output folders to Cloudflare R2 using the
S3-compatible API.

Usage:
  python source/upload_r2.py --folder ./output/video-id
  python source/upload_r2.py --folder ./output/video-id --dry-run
  python source/upload_r2.py --test
"""

import argparse
import mimetypes
import posixpath
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Iterable, Optional, Tuple

from logger import get_logger
from config import get_log_path, get_output_path, get_r2_config

logger = get_logger("upload_r2")


CONTENT_TYPES: Dict[str, str] = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".mpd": "application/dash+xml",
    ".cmfv": "video/mp4",
    ".cmfa": "audio/mp4",
    ".cmft": "application/octet-stream",
    ".m4s": "video/iso.segment",
    ".mp4": "video/mp4",
}


def setup_file_logging() -> Path:
    """Setup file logging for R2 upload operations."""
    log_dir = get_log_path()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"upload_r2_{timestamp}.log"

    import logging
    file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)

    logger.info(f"Log file created: {log_file}")
    return log_file


def normalize_prefix(prefix: str) -> str:
    """Normalize an R2 object key prefix without leading or trailing slashes."""
    return prefix.strip().strip("/")


def should_skip_file(path: Path) -> bool:
    """Return True for local metadata/temp files that should not be uploaded."""
    name = path.name
    return name.startswith("._") or name.startswith("packager-tempfile-")


def iter_upload_files(folder: Path) -> Iterable[Path]:
    """Yield regular files in stable order, excluding known local metadata files."""
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        if should_skip_file(path):
            logger.debug(f"Skipping local metadata/temp file: {path}")
            continue
        yield path


def get_content_type(path: Path) -> str:
    """Get an upload content type for HLS/DASH assets."""
    suffix = path.suffix.lower()
    if suffix in CONTENT_TYPES:
        return CONTENT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def build_object_key(folder: Path, file_path: Path, prefix: str) -> str:
    """Build an R2 object key preserving paths below the parent output folder."""
    relative_key = file_path.relative_to(folder.parent).as_posix()
    prefix = normalize_prefix(prefix)
    if prefix:
        return posixpath.join(prefix, relative_key)
    return relative_key


def build_public_url(public_base_url: str, key: str) -> str:
    """Build a public URL for logging when the bucket has a public base URL."""
    base = public_base_url.rstrip("/")
    return f"{base}/{key}" if base else ""


class R2Uploader:
    """Upload folders to Cloudflare R2 through boto3."""

    def __init__(
        self,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        prefix: str = "",
        public_base_url: str = "",
    ):
        self.account_id = account_id
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket = bucket
        self.prefix = normalize_prefix(prefix)
        self.public_base_url = public_base_url
        self.endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
        self._client = None

    def validate_config(self) -> bool:
        """Validate required R2 settings are present."""
        missing = []
        if not self.account_id:
            missing.append("R2_ACCOUNT_ID")
        if not self.access_key_id:
            missing.append("R2_ACCESS_KEY_ID")
        if not self.secret_access_key:
            missing.append("R2_SECRET_ACCESS_KEY")
        if not self.bucket:
            missing.append("R2_BUCKET")

        if missing:
            logger.error(f"Missing required R2 configuration: {', '.join(missing)}")
            logger.info("Create a .env file or provide CLI overrides.")
            return False
        return True

    def client(self):
        """Create the boto3 S3 client lazily so dry-run does not require boto3."""
        if self._client is None:
            try:
                import boto3
            except ImportError:
                logger.error("Missing dependency: boto3")
                logger.info("Install dependencies with: pip install -r requirements.txt")
                raise

            self._client = boto3.client(
                service_name="s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name="auto",
            )
        return self._client

    def test_connection(self) -> bool:
        """Validate that the configured bucket can be reached."""
        if not self.validate_config():
            return False

        logger.phase_start(f"Testing R2 bucket access: {self.bucket}")
        try:
            self.client().head_bucket(Bucket=self.bucket)
            self.client().list_objects_v2(Bucket=self.bucket, MaxKeys=1)
            logger.phase_end("R2 bucket access successful")
            return True
        except Exception as e:
            logger.error(f"R2 bucket access failed: {e}")
            return False

    def plan_folder(self, folder: Path) -> Tuple[bool, list]:
        """Build upload entries for a folder."""
        if not folder.exists():
            logger.error(f"Local folder not found: {folder}")
            return False, []
        if not folder.is_dir():
            logger.error(f"Path is not a directory: {folder}")
            return False, []

        entries = []
        for file_path in iter_upload_files(folder):
            key = build_object_key(folder, file_path, self.prefix)
            content_type = get_content_type(file_path)
            entries.append((file_path, key, content_type))

        if not entries:
            logger.error(f"No uploadable files found in: {folder}")
            return False, []

        return True, entries

    def upload_folder(self, folder: Path, dry_run: bool = False) -> bool:
        """Upload a local output folder to R2."""
        if not dry_run and not self.validate_config():
            return False

        ok, entries = self.plan_folder(folder)
        if not ok:
            return False

        logger.info("=" * 70)
        logger.phase_start(f"Uploading to R2: {folder.name}")
        logger.info("=" * 70)
        logger.metadata(f"Endpoint: {self.endpoint_url}")
        logger.metadata(f"Bucket: {self.bucket}")
        logger.metadata(f"Prefix: {self.prefix or '[none]'}")
        logger.metadata(f"Files: {len(entries)}")
        logger.info("")

        if dry_run:
            logger.info("Dry run enabled; no files will be uploaded.")
            if not self.bucket:
                logger.warning("R2 bucket is not configured; dry-run output will show an empty bucket name.")

        for file_path, key, content_type in entries:
            size = file_path.stat().st_size
            logger.info(f"{'DRY-RUN ' if dry_run else ''}{file_path} -> r2://{self.bucket}/{key} ({content_type}, {size} bytes)")
            if dry_run:
                continue

            try:
                self.client().upload_file(
                    Filename=str(file_path),
                    Bucket=self.bucket,
                    Key=key,
                    ExtraArgs={"ContentType": content_type},
                )
            except Exception as e:
                logger.error(f"Upload failed for {file_path}: {e}")
                return False

        logger.phase_end("R2 upload completed successfully")

        master_urls = [
            build_public_url(self.public_base_url, key)
            for _, key, _ in entries
            if self.public_base_url and key.endswith(".m3u8") and Path(key).stem == folder.name
        ]
        for url in master_urls:
            logger.info(f"Public playlist URL: {url}")

        return True


def load_config() -> dict:
    """Load R2 configuration from environment."""
    config = get_r2_config()
    config['local_output_path'] = str(get_output_path())
    return config


def log_config_summary(config: dict, uploader: R2Uploader):
    """Log configuration summary without secrets."""
    logger.info("=" * 70)
    logger.info("R2 UPLOAD CONFIGURATION")
    logger.info("=" * 70)
    logger.metadata(f"Endpoint: {uploader.endpoint_url}")
    logger.metadata(f"Bucket: {uploader.bucket or '[not set]'}")
    logger.metadata(f"Prefix: {uploader.prefix or '[none]'}")
    logger.metadata(f"Access Key: {'[configured]' if uploader.access_key_id else '[not set]'}")
    logger.metadata(f"Secret Key: {'[configured]' if uploader.secret_access_key else '[not set]'}")
    logger.metadata(f"Public Base URL: {uploader.public_base_url or '[not set]'}")
    logger.metadata(f"Local Output Path: {config['local_output_path']}")
    logger.info("=" * 70)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Upload output video folders to Cloudflare R2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python source/upload_r2.py --folder output/video123
  python source/upload_r2.py --folder output/video123 --dry-run
  python source/upload_r2.py --test

Configuration:
  Create a .env file with R2 credentials (see .env.example)
        """
    )

    parser.add_argument(
        "--folder", "-f",
        type=Path,
        help="Local output folder path to upload"
    )
    parser.add_argument("--bucket", help="R2 bucket name (overrides .env)")
    parser.add_argument("--prefix", help="R2 object key prefix (overrides .env)")
    parser.add_argument("--account-id", help="Cloudflare account ID (overrides .env)")
    parser.add_argument("--access-key-id", help="R2 access key ID (overrides .env)")
    parser.add_argument("--secret-access-key", help="R2 secret access key (overrides .env)")
    parser.add_argument(
        "--test", "-t",
        action="store_true",
        help="Test R2 bucket access only"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned uploads without writing to R2"
    )

    args = parser.parse_args()

    log_file = setup_file_logging()
    logger.info(f"R2 upload session started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    config = load_config()
    uploader = R2Uploader(
        account_id=args.account_id or config['account_id'],
        access_key_id=args.access_key_id or config['access_key_id'],
        secret_access_key=args.secret_access_key or config['secret_access_key'],
        bucket=args.bucket or config['bucket'],
        prefix=args.prefix if args.prefix is not None else config['prefix'],
        public_base_url=config['public_base_url'],
    )

    log_config_summary(config, uploader)

    if args.test:
        logger.info("Operation: R2 Connection Test\n")
        success = uploader.test_connection()
        logger.info(f"Session log saved to: {log_file}")
        sys.exit(0 if success else 1)

    if args.folder:
        logger.info("Operation: Upload Folder")
        logger.metadata(f"Local Folder: {args.folder}")
        logger.metadata(f"Remote Folder Key: {build_object_key(args.folder, args.folder / 'placeholder', uploader.prefix).rsplit('/', 1)[0]}")
        logger.info("")

        success = uploader.upload_folder(args.folder, dry_run=args.dry_run)
        logger.info(f"Session log saved to: {log_file}")
        sys.exit(0 if success else 1)

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
