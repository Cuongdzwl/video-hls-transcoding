#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Batch Video Processor - Queue-based Video Processing Pipeline
==============================================================

Complete video processing pipeline with CSV queue management:
1. Input validation (file path or CSV queue)
2. Optional video editing with filters
3. HLS/DASH conversion with adaptive streaming
4. Server upload with backup

Features:
- Single file or CSV batch processing
- UUID generation and tracking
- Status tracking in CSV
- Auto-confirm mode (-y flag)
- Preset validation and confirmation
- Complete pipeline: edit → convert → upload
- Error handling with status logging

Usage:
  # Single file processing
  python batch_processor.py --input video.mkv
  
  # CSV batch processing
  python batch_processor.py --csv queue.csv
  
  # Auto-confirm (skip all prompts)
  python batch_processor.py --csv queue.csv -y

Author: GitHub Copilot
Date: October 29, 2025
"""

import argparse
import csv
import json
import os
import sys
import subprocess
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Callable
from datetime import datetime
from dataclasses import dataclass

# All other project modules live in ./source
SOURCE_DIR = Path(__file__).resolve().parent / "source"
sys.path.insert(0, str(SOURCE_DIR))

# Import shared modules
from logger import get_logger
from config import get_edited_path, get_output_path, get_log_path

logger = get_logger("batch_processor")

# Load paths from config (may be overridden at runtime by --manifest)
EDITED_PATH = get_edited_path()
OUTPUT_PATH = get_output_path()
LOG_PATH = get_log_path()

# Environment passed to subprocesses (mkv_edit.py, mkv_to_m3u8_converter.py,
# upload_server.py, upload_r2.py) so they see the same edited/output/log paths
# this process resolved (from CLI defaults or --manifest). Set in main().
SUBPROCESS_ENV: Optional[Dict[str, str]] = None


def get_subprocess_env() -> Optional[Dict[str, str]]:
    """Environment to use for subprocess.run calls so child scripts share our paths"""
    return SUBPROCESS_ENV


def refresh_subprocess_env() -> None:
    """Rebuild SUBPROCESS_ENV from the current EDITED_PATH/OUTPUT_PATH/LOG_PATH"""
    global SUBPROCESS_ENV
    env = os.environ.copy()
    env["LOCAL_EDITED_PATH"] = str(EDITED_PATH)
    env["LOCAL_OUTPUT_PATH"] = str(OUTPUT_PATH)
    env["LOCAL_LOG_PATH"] = str(LOG_PATH)
    SUBPROCESS_ENV = env


@dataclass
class VideoTask:
    """Represents a video processing task"""
    file_path: Path
    video_id: str
    status: str = "pending"  # pending, editing, edited, converting, converted, uploading, completed
    error_message: str = ""
    start_time: Optional[float] = None  # Seconds to cut from start
    end_time: Optional[float] = None    # Seconds to cut from end
    audio_delay: Optional[int] = None   # Audio delay in milliseconds (positive = delay, negative = advance)
    backup_path: str = ""  # Server backup path if backup was created
    original_path: Optional[Path] = None  # Original file path (preserved even if file_path changes during editing)
    
    def __post_init__(self):
        """Initialize original_path if not set"""
        if self.original_path is None:
            self.original_path = self.file_path
    
    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary for CSV writing"""
        return {
            "path": str(self.file_path),
            "uuid": self.video_id,
            "status": self.status,
            "error": self.error_message,
            "start_time": str(self.start_time) if self.start_time is not None else "",
            "end_time": str(self.end_time) if self.end_time is not None else "",
            "audio_delay": str(self.audio_delay) if self.audio_delay is not None else "",
            "backup_path": self.backup_path,
            "original_path": str(self.original_path) if self.original_path else ""
        }


def setup_file_logging() -> Path:
    """Setup file logging for batch processing"""
    log_dir = LOG_PATH
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"batch_{timestamp}.log"
    
    import logging
    file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)
    
    logger.info(f"Log file created: {log_file}")
    return log_file


def validate_video_file(file_path: Path) -> bool:
    """Validate video file exists and is MKV format"""
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        return False
    
    if not file_path.is_file():
        logger.error(f"Not a file: {file_path}")
        return False
    
    if file_path.suffix.lower() != '.mkv':
        logger.error(f"Invalid file type: {file_path.suffix} (expected .mkv)")
        return False
    
    logger.info(f"✓ Valid MKV file: {file_path.name}")
    return True


def generate_uuid() -> str:
    """Generate a new UUID for video"""
    return str(uuid.uuid4())


def task_needs_trim_or_sync(task: VideoTask) -> bool:
    """True if the task has trim or audio-sync parameters that require the edit step"""
    return bool(
        (task.start_time is not None and task.start_time > 0)
        or (task.end_time is not None and task.end_time > 0)
        or (task.audio_delay is not None and task.audio_delay != 0)
    )


def get_next_task_stage(task: VideoTask, edit_params: Optional[List[str]] = None, skip_edit: bool = False) -> str:
    """Return the next pipeline stage for a task based on its checkpoint status."""
    if task.status == "completed":
        return "completed"
    if task.status == "uploading":
        return "uploading"
    if task.status == "converted":
        return "uploading"
    if task.status == "converting":
        return "converting"
    if task.status == "edited":
        return "converting"
    if task.status in ["editing", "pending"]:
        should_edit = not skip_edit and (
            (edit_params is not None and len(edit_params) > 0) or task_needs_trim_or_sync(task)
        )
        return "editing" if should_edit else "converting"
    return "pending"


def confirm_uuid(file_path: Path, auto_confirm: bool = False) -> str:
    """Get or generate UUID with user confirmation"""
    logger.phase_start("UUID Assignment")
    logger.info(f"Video file: {file_path.name}")
    
    generated_uuid = generate_uuid()
    logger.info(f"Generated UUID: {generated_uuid}")
    
    if auto_confirm:
        logger.info("Auto-confirm enabled, using generated UUID")
        return generated_uuid
    
    print(f"\n{logger.colors.BOLD}UUID Assignment:{logger.colors.RESET}")
    print(f"  File: {file_path.name}")
    print(f"  Generated UUID: {logger.colors.CYAN}{generated_uuid}{logger.colors.RESET}")
    
    while True:
        response = input(f"\nUse this UUID? [Y/n/custom]: ").strip().lower()
        
        if response in ['', 'y', 'yes']:
            logger.phase_end(f"Using UUID: {generated_uuid}")
            return generated_uuid
        elif response in ['n', 'no']:
            custom = input("Enter custom UUID: ").strip()
            if custom:
                logger.phase_end(f"Using custom UUID: {custom}")
                return custom
        else:
            # Treat as custom UUID
            logger.phase_end(f"Using custom UUID: {response}")
            return response


def load_csv_queue(csv_path: Path) -> List[VideoTask]:
    """Load video processing queue from CSV file"""
    if not csv_path.exists():
        logger.error(f"CSV file not found: {csv_path}")
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    
    logger.phase_start(f"Loading queue from: {csv_path}")
    
    tasks = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        # Validate headers
        required_headers = ['path', 'uuid', 'status']
        if not all(h in reader.fieldnames for h in required_headers):
            logger.error(f"CSV missing required headers: {required_headers}")
            raise ValueError(f"CSV must have headers: {', '.join(required_headers)}")
        
        for row_num, row in enumerate(reader, start=2):
            # Get path (required)
            path_str = row.get('path', '').strip() if row.get('path') else ''
            if not path_str:
                logger.warning(f"Row {row_num}: Missing path, skipping")
                continue
            
            path = Path(path_str)
            
            # Get UUID (generate if empty/null)
            uuid_str = row.get('uuid', '').strip() if row.get('uuid') else ''
            if not uuid_str:
                video_id = generate_uuid()
                logger.info(f"Row {row_num}: Generated UUID for {path.name}: {video_id}")
            else:
                video_id = uuid_str
            
            # Get status (default to 'pending')
            status_str = row.get('status', '').strip() if row.get('status') else ''
            status = status_str if status_str else 'pending'
            valid_statuses = {"pending", "editing", "edited", "converting", "converted", "uploading", "completed"}
            if status not in valid_statuses:
                logger.warning(f"Row {row_num}: Invalid status '{status}', defaulting to pending")
                status = "pending"
            
            # Get error message (default to empty)
            error_str = row.get('error', '').strip() if row.get('error') else ''
            error = error_str if error_str else ''
            
            # Get start_time (optional, in seconds)
            start_time = None
            start_time_str = row.get('start_time', '').strip() if row.get('start_time') else ''
            if start_time_str:
                try:
                    start_time = float(start_time_str)
                    if start_time < 0:
                        logger.warning(f"Row {row_num}: Invalid start_time (negative), ignoring")
                        start_time = None
                except ValueError:
                    logger.warning(f"Row {row_num}: Invalid start_time format '{start_time_str}', ignoring")
            
            # Get end_time (optional, in seconds)
            end_time = None
            end_time_str = row.get('end_time', '').strip() if row.get('end_time') else ''
            if end_time_str:
                try:
                    end_time = float(end_time_str)
                    if end_time < 0:
                        logger.warning(f"Row {row_num}: Invalid end_time (negative), ignoring")
                        end_time = None
                except ValueError:
                    logger.warning(f"Row {row_num}: Invalid end_time format '{end_time_str}', ignoring")
            
            # Get audio_delay (optional, in milliseconds)
            audio_delay = None
            audio_delay_str = row.get('audio_delay', '').strip() if row.get('audio_delay') else ''
            if audio_delay_str:
                try:
                    audio_delay = int(audio_delay_str)
                except ValueError:
                    logger.warning(f"Row {row_num}: Invalid audio_delay format '{audio_delay_str}', ignoring")
            
            # Get backup_path (optional)
            backup_path_str = row.get('backup_path', '').strip() if row.get('backup_path') else ''
            backup_path = backup_path_str if backup_path_str else ''
            
            # Get original_path (optional, for tracking original file location)
            original_path = None
            original_path_str = row.get('original_path', '').strip() if row.get('original_path') else ''
            if original_path_str:
                original_path = Path(original_path_str)
            else:
                # If not specified, use current path as original
                original_path = path
            
            task = VideoTask(
                file_path=path,
                video_id=video_id,
                status=status,
                error_message=error,
                start_time=start_time,
                end_time=end_time,
                audio_delay=audio_delay,
                backup_path=backup_path,
                original_path=original_path
            )
            tasks.append(task)
    
    logger.phase_end(f"Loaded {len(tasks)} tasks from queue")
    return tasks


def save_csv_queue(csv_path: Path, tasks: List[VideoTask]):
    """Save video processing queue to CSV file"""
    logger.info(f"Saving queue to: {csv_path}")
    
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        fieldnames = ['path', 'uuid', 'status', 'error', 'start_time', 'end_time', 'audio_delay', 'backup_path', 'original_path']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        writer.writeheader()
        for task in tasks:
            writer.writerow(task.to_dict())
    
    logger.info(f"✓ Queue saved ({len(tasks)} tasks)")


def load_and_display_presets(preset_path: Path, auto_confirm: bool = False) -> Tuple[List[Dict], Optional[List[str]]]:
    """Load presets and display for confirmation"""
    if not preset_path.exists():
        logger.error(f"Preset file not found: {preset_path}")
        raise FileNotFoundError(f"Preset file not found: {preset_path}")
    
    logger.phase_start(f"Loading presets from: {preset_path}")
    
    with open(preset_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Get profiles
    if isinstance(data, dict) and "profiles" in data:
        profiles = data["profiles"]
    elif isinstance(data, list):
        profiles = data
    else:
        logger.error("Invalid preset format")
        raise ValueError("Preset file must contain 'profiles' array or be an array")
    
    # Get edit params (optional)
    edit_params = None
    if isinstance(data, dict) and "edit_params" in data:
        edit_params = data["edit_params"]
    
    logger.phase_end(f"Loaded {len(profiles)} quality profiles")
    
    # Display presets
    print(f"\n{logger.colors.BOLD}{'=' * 70}{logger.colors.RESET}")
    print(f"{logger.colors.BOLD}PRESET CONFIGURATION{logger.colors.RESET}")
    print(f"{logger.colors.BOLD}{'=' * 70}{logger.colors.RESET}")
    
    print(f"\n{logger.colors.CYAN}Quality Profiles:{logger.colors.RESET}")
    for i, profile in enumerate(profiles, 1):
        name = profile.get('name', 'Unknown')
        resolution = f"{profile.get('width', 'N/A')} x { profile.get('height', 'N/A')}"
        bitrate = profile.get('bitrate', 'N/A')
        print(f"  {i}. {name}: {resolution} @ {bitrate}")
    
    if edit_params:
        print(f"\n{logger.colors.CYAN}Edit Parameters:{logger.colors.RESET}")
        i = 0
        while i < len(edit_params):
            param = edit_params[i]
            if i + 1 < len(edit_params) and not edit_params[i + 1].startswith("-"):
                print(f"  {param} {edit_params[i + 1]}")
                i += 2
            else:
                print(f"  {param}")
                i += 1
    else:
        print(f"\n{logger.colors.YELLOW}Edit Parameters: None (will use defaults){logger.colors.RESET}")
    
    print(f"\n{logger.colors.BOLD}{'=' * 70}{logger.colors.RESET}")
    
    if auto_confirm:
        logger.info("Auto-confirm enabled, using presets")
        return profiles, edit_params
    
    # Confirm with user
    while True:
        response = input(f"\nProceed with these presets? [Y/n]: ").strip().lower()
        if response in ['', 'y', 'yes']:
            logger.info("Presets confirmed by user")
            return profiles, edit_params
        elif response in ['n', 'no']:
            logger.warning("User cancelled processing")
            sys.exit(0)
        else:
            print("Please enter 'y' or 'n'")


def generate_sample_files(output_dir: Path = Path(".")) -> List[Path]:
    """Generate sample presets/input CSV files and field reference docs."""
    output_dir.mkdir(parents=True, exist_ok=True)

    sample_presets = {
        "edit_params": [
            "-vf",
            "hqdn3d=1.5:1.0:5.0:4.5,unsharp=5:5:0.8"
        ],
        "profiles": [
            {
                "name": "portrait_1080p_2mbps",
                "width": 1080,
                "height": 1920,
                "bitrate": "3000k",
                "max_bitrate": "5000k",
                "buffer_size": "1000k",
                "audio_bitrate": "192k",
                "auto_bitrate": True,
                "orientation": "portrait",
                "edit": {
                    "zoom_factor": 1.0,
                    "translate_x": 0,
                    "translate_y": 0,
                    "9:16_middle_cut": True
                }
            },
            {
                "name": "landscape_1080p_2mbps",
                "width": 1920,
                "height": 1080,
                "bitrate": "3000k",
                "max_bitrate": "5000k",
                "buffer_size": "2000k",
                "audio_bitrate": "192k",
                "auto_bitrate": True,
                "orientation": "landscape",
                "edit": {
                    "zoom_factor": 1.0,
                    "translate_x": 0,
                    "translate_y": 0,
                    "9:16_middle_cut": False
                }
            }
        ]
    }

    sample_csv_rows = [
        {
            "path": "/path/to/video1.mkv",
            "uuid": "11111111-1111-1111-1111-111111111111",
            "status": "pending",
            "error": "",
            "start_time": "5.0",
            "end_time": "3.5",
            "audio_delay": "333",
            "backup_path": "",
            "original_path": "/path/to/video1.mkv"
        },
        {
            "path": "./edited/22222222-2222-2222-2222-222222222222.mkv",
            "uuid": "22222222-2222-2222-2222-222222222222",
            "status": "edited",
            "error": "",
            "start_time": "",
            "end_time": "",
            "audio_delay": "",
            "backup_path": "",
            "original_path": "/path/to/original_video2.mkv"
        },
        {
            "path": "/path/to/video3.mkv",
            "uuid": "33333333-3333-3333-3333-333333333333",
            "status": "converting",
            "error": "Conversion failed: exit code 1",
            "start_time": "",
            "end_time": "",
            "audio_delay": "-100",
            "backup_path": "",
            "original_path": "/path/to/video3.mkv"
        }
    ]

    sample_doc = """# Sample Field Reference

## Input CSV fields

- path: Current file path used by pipeline. Must point to .mkv.
- uuid: Video ID. Leave empty to auto-generate.
- status: Task state.
    Available options: pending, editing, edited, converting, converted, uploading, completed.
- error: Error message from previous run. Usually empty when previous processing succeeded.
- start_time: Seconds to cut from the start (float >= 0, optional).
- end_time: Seconds to cut from the end (float >= 0, optional).
- audio_delay: Audio shift in milliseconds (integer, optional).
  Positive delays audio; negative advances audio.
- backup_path: Server backup location after upload (managed by pipeline).
- original_path: Original source file path (recommended, especially when path points to edited file).

## Presets JSON fields

- edit_params: Optional FFmpeg filter args passed to mkv_edit.py.
  Example format: ["-vf", "lut3d=/path/to/lut.cube,hqdn3d=...,unsharp=..."]
- profiles: Array of conversion profiles.

Each profile supports:
- name: Profile name label.
- width: Output width in pixels.
- height: Output height in pixels.
- bitrate: Target video bitrate string (example: 3000k).
- max_bitrate: Peak bitrate string.
- buffer_size: Rate-control buffer size string.
- audio_bitrate: Audio bitrate string (example: 128k, 192k).
- auto_bitrate: true/false.
- orientation: Available options: portrait, landscape.
- edit: Optional per-profile canvas composition options.
    - zoom_factor: Numeric zoom scale.
        1.0 = default, 0.8 = zoom out 20%, 1.1 = zoom in 10%.
    - translate_x: Horizontal shift in pixels on output canvas.
    - translate_y: Vertical shift in pixels on output canvas.
  - 9:16_middle_cut: true/false.

Canvas goal:
- Output always preserves target preset resolution (e.g., 1080x1920 or 1920x1080 canvas).

## manifest.json fields (./run.py manifest.json)

A manifest bundles a full run's configuration into one file instead of passing
flags on the command line. Fields:

- presets: Path to the presets JSON file (equivalent to --presets).
- input_csv: Path to a CSV queue (equivalent to --csv). Mutually exclusive with "input".
- input: Path to a single MKV file (equivalent to --input). Mutually exclusive with "input_csv".
- input_params: Only used with "input" (single-file mode). Optional object with:
    - start_time: Seconds to cut from the start.
    - end_time: Seconds to cut from the end.
    - audio_delay: Audio delay in milliseconds.
- edited_path: Directory for edited videos (overrides LOCAL_EDITED_PATH / ./edited default).
- output_path: Directory for converted HLS/DASH output (overrides LOCAL_OUTPUT_PATH / ./output default).
- log_path: Directory for log files (overrides LOCAL_LOG_PATH / ./log default).
- upload_target: One target string or a list, e.g. "r2" or ["ssh", "r2"].
- options: Optional object mirroring CLI flags:
    - yes, no_edit, retry_errors, reconvert, re_edit, verbose (all booleans).

Values in manifest.json take priority over the equivalent CLI flags.
"""

    sample_manifest = {
        "presets": "./presets.json",
        "input_csv": "./queue.csv",
        "edited_path": "./edited",
        "output_path": "./output",
        "log_path": "./log",
        "upload_target": ["ssh", "r2"],
        "options": {
            "yes": True,
            "no_edit": False,
            "retry_errors": False,
            "reconvert": False,
            "re_edit": False,
            "verbose": False
        }
    }

    presets_path = output_dir / "sample_presets.json"
    input_csv_path = output_dir / "sample_input.csv"
    doc_path = output_dir / "sample_fields.md"
    manifest_path = output_dir / "sample_manifest.json"

    with open(presets_path, 'w', encoding='utf-8') as f:
        json.dump(sample_presets, f, indent=4)

    fieldnames = ['path', 'uuid', 'status', 'error', 'start_time', 'end_time', 'audio_delay', 'backup_path', 'original_path']
    with open(input_csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sample_csv_rows:
            writer.writerow(row)

    with open(doc_path, 'w', encoding='utf-8') as f:
        f.write(sample_doc)

    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(sample_manifest, f, indent=4)

    return [presets_path, input_csv_path, doc_path, manifest_path]


def run_edit_video(task: VideoTask, preset_path: Path, auto_confirm: bool = False) -> bool:
    """Run mkv_edit.py to edit video"""
    logger.phase_start(f"Editing video: {task.video_id}")
    
    # Always use original_path as input for editing (to preserve original file reference)
    input_file = task.original_path if task.original_path else task.file_path
    logger.info(f"Input file: {input_file}")
    
    # Validate trim parameters before running mkv_edit
    if task.start_time is not None or task.end_time is not None:
        try:
            # Get video duration using ffprobe
            import json
            probe_cmd = [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(input_file)
            ]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
            
            if probe_result.returncode == 0:
                probe_data = json.loads(probe_result.stdout)
                duration = float(probe_data["format"]["duration"])
                
                start_cut = task.start_time if task.start_time else 0
                end_cut = task.end_time if task.end_time else 0
                final_duration = duration - start_cut - end_cut
                
                logger.info(f"Video duration: {duration:.2f}s")
                if task.start_time:
                    logger.info(f"Trim from start: {task.start_time}s")
                if task.end_time:
                    logger.info(f"Trim from end: {task.end_time}s")
                logger.info(f"Final duration after trim: {final_duration:.2f}s")
                
                if final_duration <= 0:
                    logger.error(f"Trim cuts exceed video duration! ({start_cut}s + {end_cut}s > {duration}s)")
                    task.error_message = f"Trim cuts exceed video duration: start={start_cut}s, end={end_cut}s, duration={duration:.2f}s"
                    return False
        except subprocess.TimeoutExpired:
            logger.warning("Failed to validate trim duration (ffprobe timeout)")
        except Exception as e:
            logger.warning(f"Failed to validate trim duration: {e}")
    
    # Create edited output path
    edited_dir = EDITED_PATH
    edited_file = edited_dir / f"{task.video_id}.mkv"
    
    # Build command
    cmd = [
        sys.executable, str(SOURCE_DIR / "mkv_edit.py"),
        "--input", str(input_file),
        "--output", str(edited_file),
        "--presets", str(preset_path)
    ]
    
    # Add trim parameters if specified
    if task.start_time is not None and task.start_time > 0:
        cmd.extend(["--start-time", str(task.start_time)])
    
    if task.end_time is not None and task.end_time > 0:
        cmd.extend(["--end-time", str(task.end_time)])
    
    # Add audio delay if specified
    if task.audio_delay is not None and task.audio_delay != 0:
        cmd.extend(["--audio-delay", str(task.audio_delay)])
    
    if auto_confirm:
        cmd.append("--verbose")
    
    logger.command(" ".join(cmd))
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=get_subprocess_env())
        
        if result.returncode == 0:
            logger.phase_end(f"✓ Video edited successfully: {edited_file}")
            # Update task to use edited file
            task.file_path = edited_file
            return True
        else:
            logger.error(f"Edit failed with exit code {result.returncode}")
            if result.stderr:
                # Check for trim-related errors
                stderr_text = result.stderr
                if "Trim cuts exceed video duration" in stderr_text:
                    task.error_message = "Trim cuts exceed video duration"
                    logger.error("Error: Trim parameters are invalid for this video")
                else:
                    for line in stderr_text.split('\n')[-10:]:
                        if line.strip():
                            logger.error(f"  {line}")
                    task.error_message = f"Edit failed: exit code {result.returncode}"
            else:
                task.error_message = f"Edit failed: exit code {result.returncode}"
            return False
            
    except Exception as e:
        logger.error(f"Error running mkv_edit.py: {e}")
        task.error_message = f"Edit error: {str(e)}"
        return False


def run_m3u8_converter(task: VideoTask, preset_path: Path) -> bool:
    """Run mkv_to_m3u8_converter.py to convert video"""
    logger.phase_start(f"Converting to HLS/DASH: {task.video_id}")
    logger.info(f"Input file: {task.file_path}")
    
    # Ensure we're using edited file if it exists
    edited_dir = EDITED_PATH
    edited_file = edited_dir / f"{task.video_id}.mkv"
    
    # Always prefer edited file if it exists
    if edited_file.exists() and task.file_path != edited_file:
        logger.info(f"Found edited file, using it instead of original")
        task.file_path = edited_file
    
    if task.file_path.parent == edited_dir:
        logger.info("✓ Converting EDITED video (as intended)")
    else:
        logger.warning("⚠️ Converting ORIGINAL video (edited file not found)")
    
    # Build command
    cmd = [
        sys.executable, str(SOURCE_DIR / "mkv_to_m3u8_converter.py"),
        "--input", str(task.file_path),
        "--output", str(OUTPUT_PATH),
        "--presets", str(preset_path),
        "--id", task.video_id
    ]
    
    logger.command(" ".join(cmd))
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=get_subprocess_env())
        
        if result.returncode == 0:
            logger.phase_end(f"✓ Video converted successfully")
            return True
        else:
            logger.error(f"Conversion failed with exit code {result.returncode}")
            if result.stderr:
                for line in result.stderr.split('\n')[-10:]:
                    if line.strip():
                        logger.error(f"  {line}")
            task.error_message = f"Conversion failed: exit code {result.returncode}"
            return False
            
    except Exception as e:
        logger.error(f"Error running mkv_to_m3u8_converter.py: {e}")
        task.error_message = f"Conversion error: {str(e)}"
        return False


def run_upload_server(task: VideoTask) -> bool:
    """Run upload_server.py to upload video folders"""
    logger.phase_start(f"Uploading to server: {task.video_id}")
    
    # Find output folder
    output_folder = OUTPUT_PATH / task.video_id
    if not output_folder.exists():
        logger.error(f"Output folder not found: {output_folder}")
        task.error_message = "Upload failed: output folder not found"
        return False
    
    # Build command
    cmd = [
        sys.executable, str(SOURCE_DIR / "upload_server.py"),
        "--folder", str(output_folder)
    ]
    
    logger.command(" ".join(cmd))
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=get_subprocess_env())
        
        if result.returncode == 0:
            logger.phase_end(f"✓ Video uploaded successfully")
            
            # Parse backup path from output if it exists
            for line in result.stdout.split('\n'):
                if line.startswith('BACKUP_PATH:'):
                    backup_path = line.replace('BACKUP_PATH:', '').strip()
                    task.backup_path = backup_path
                    logger.info(f"Backup path stored: {backup_path}")
                    break
            
            return True
        else:
            logger.error(f"Upload failed with exit code {result.returncode}")
            if result.stderr:
                for line in result.stderr.split('\n')[-10:]:
                    if line.strip():
                        logger.error(f"  {line}")
            task.error_message = f"Upload failed: exit code {result.returncode}"
            return False
            
    except Exception as e:
        logger.error(f"Error running upload_server.py: {e}")
        task.error_message = f"Upload error: {str(e)}"
        return False


def run_upload_r2(task: VideoTask) -> bool:
    """Run upload_r2.py to upload video folders to Cloudflare R2"""
    logger.phase_start(f"Uploading to R2: {task.video_id}")

    # Find output folder
    output_folder = OUTPUT_PATH / task.video_id
    if not output_folder.exists():
        logger.error(f"Output folder not found: {output_folder}")
        task.error_message = "Upload failed: output folder not found"
        return False

    # Build command
    cmd = [
        sys.executable, str(SOURCE_DIR / "upload_r2.py"),
        "--folder", str(output_folder)
    ]

    logger.command(" ".join(cmd))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=get_subprocess_env())

        if result.returncode == 0:
            logger.phase_end(f"✓ Video uploaded to R2 successfully")
            return True
        else:
            logger.error(f"R2 upload failed with exit code {result.returncode}")
            output = result.stderr or result.stdout
            if output:
                for line in output.split('\n')[-10:]:
                    if line.strip():
                        logger.error(f"  {line}")
            task.error_message = f"R2 upload failed: exit code {result.returncode}"
            return False

    except Exception as e:
        logger.error(f"Error running upload_r2.py: {e}")
        task.error_message = f"R2 upload error: {str(e)}"
        return False


def run_upload(task: VideoTask, upload_targets: List[str] = None) -> bool:
    """Run the configured upload target(s) for a processed video folder.

    Uploads to every target in upload_targets (e.g. ["ssh", "r2"]) and only
    reports success if all of them succeed. Failures from individual targets
    are combined into task.error_message.
    """
    targets = upload_targets if upload_targets else ["ssh"]

    failed_targets = []
    for target in targets:
        if target == "ssh":
            ok = run_upload_server(task)
        elif target == "r2":
            ok = run_upload_r2(task)
        else:
            logger.error(f"Unknown upload target: {target}")
            task.error_message = f"Upload failed: unknown upload target {target}"
            failed_targets.append(target)
            continue

        if not ok:
            failed_targets.append(target)

    if failed_targets:
        task.error_message = f"Upload failed for target(s): {', '.join(failed_targets)}"
        return False

    task.error_message = ""
    return True


def process_single_task(
    task: VideoTask,
    preset_path: Path,
    edit_params: Optional[List[str]] = None,
    skip_edit: bool = False,
    upload_targets: Optional[List[str]] = None,
    checkpoint_callback: Optional[Callable[[], None]] = None,
) -> bool:
    """Process a single video task through the complete pipeline"""
    logger.info(f"{'=' * 70}")
    logger.info(f"PROCESSING: {task.video_id}")
    logger.info(f"File: {task.file_path.name}")
    logger.info(f"{'=' * 70}\n")
    
    try:
        # Normalize status for checkpoint-based resume
        valid_statuses = {"pending", "editing", "edited", "converting", "converted", "uploading", "completed"}
        if task.status not in valid_statuses:
            logger.warning(f"Unknown status '{task.status}' for {task.video_id}, resetting to pending")
            task.status = "pending"
            if checkpoint_callback:
                checkpoint_callback()

        # If already fully completed, nothing to do
        if task.status == "completed":
            logger.info("Task already completed, skipping")
            return True

        # Step 4: Edit video checkpoint stage
        should_edit = not skip_edit and (
            (edit_params is not None and len(edit_params) > 0) or task_needs_trim_or_sync(task)
        )

        if task.status in ["pending", "editing"] and should_edit:
            task.status = "editing"
            if checkpoint_callback:
                checkpoint_callback()
            if not run_edit_video(task, preset_path, auto_confirm=True):
                # Keep status as 'editing' and write error details to error field
                if not task.error_message:
                    task.error_message = "Edit failed"
                if checkpoint_callback:
                    checkpoint_callback()
                return False
            # After editing, task.file_path now points to edited file
            task.status = "edited"
            task.error_message = ""
            if checkpoint_callback:
                checkpoint_callback()
            logger.info(f"Will convert edited file: {task.file_path}")
        elif task.status in ["pending", "editing"] and not should_edit:
            # Skip editing - check reasons
            if skip_edit:
                logger.info("⏭️  Skipping edit step: --no-edit flag enabled")
            else:
                logger.info("⏭️  Skipping edit step: No edit_params in presets and no trim/audio-sync params on task")
            
            # Check if edited file already exists from previous run
            edited_dir = EDITED_PATH
            edited_file = edited_dir / f"{task.video_id}.mkv"
            if edited_file.exists():
                logger.info(f"Found existing edited file: {edited_file}")
                logger.info("Using edited file for conversion")
                task.file_path = edited_file
            else:
                logger.info("No edited file found, using original file")
            task.status = "edited"
            task.error_message = ""
            if checkpoint_callback:
                checkpoint_callback()
        elif task.status == "edited":
            logger.info("⏭️  Edit step already completed (status=edited)")
        elif task.status in ["converting", "converted", "uploading"]:
            logger.info(f"⏭️  Resuming from checkpoint status: {task.status}")
        
        # Step 5: Convert to HLS/DASH checkpoint stage
        if task.status in ["edited", "converting"]:
            task.status = "converting"
            if checkpoint_callback:
                checkpoint_callback()
            if not run_m3u8_converter(task, preset_path):
                # Keep status as 'converting' and write error details to error field
                if not task.error_message:
                    task.error_message = "Conversion failed"
                if checkpoint_callback:
                    checkpoint_callback()
                return False
            task.status = "converted"
            task.error_message = ""
            if checkpoint_callback:
                checkpoint_callback()
        elif task.status == "converted":
            logger.info("⏭️  Conversion already completed (status=converted)")
        
        # Step 6: Upload checkpoint stage
        if task.status in ["converted", "uploading"]:
            task.status = "uploading"
            if checkpoint_callback:
                checkpoint_callback()
            if not run_upload(task, upload_targets=upload_targets):
                # Keep status as 'uploading' and write error details to error field
                if not task.error_message:
                    task.error_message = "Upload failed"
                if checkpoint_callback:
                    checkpoint_callback()
                return False
        
        # Step 7: Mark as completed
        task.status = "completed"
        task.error_message = ""
        if checkpoint_callback:
            checkpoint_callback()
        logger.info(f"\n{'=' * 70}")
        logger.phase_end(f"✓ COMPLETED: {task.video_id}")
        logger.info(f"{'=' * 70}\n")
        return True
        
    except KeyboardInterrupt:
        logger.warning("Processing interrupted by user")
        task.error_message = "Interrupted by user"
        if checkpoint_callback:
            checkpoint_callback()
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if not task.error_message:
            task.error_message = str(e)
        if checkpoint_callback:
            checkpoint_callback()
        return False


def load_manifest(manifest_path: Path) -> Dict[str, Any]:
    """Load a manifest.json describing a full run configuration"""
    if not manifest_path.exists():
        logger.error(f"Manifest file not found: {manifest_path}")
        raise FileNotFoundError(f"Manifest file not found: {manifest_path}")

    logger.phase_start(f"Loading manifest from: {manifest_path}")

    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    if not isinstance(manifest, dict):
        logger.error("Invalid manifest format")
        raise ValueError("Manifest file must contain a JSON object")

    if manifest.get("input_csv") and manifest.get("input"):
        logger.error("Manifest cannot specify both 'input_csv' and 'input'")
        raise ValueError("Manifest 'input_csv' and 'input' are mutually exclusive")

    logger.phase_end("Manifest loaded")
    return manifest


def apply_manifest_to_args(manifest: Dict[str, Any], args: argparse.Namespace) -> None:
    """Mutate parsed CLI args in place using manifest.json values (manifest wins)"""
    if manifest.get("presets"):
        args.presets = Path(manifest["presets"])

    if manifest.get("input_csv"):
        args.csv = Path(manifest["input_csv"])
        args.input = None
    elif manifest.get("input"):
        args.input = Path(manifest["input"])
        args.csv = None

    if manifest.get("upload_target"):
        targets = manifest["upload_target"]
        args.upload_target = [targets] if isinstance(targets, str) else list(targets)

    options = manifest.get("options") or {}
    for key in ["yes", "no_edit", "retry_errors", "reconvert", "re_edit", "verbose"]:
        if key in options:
            setattr(args, key, bool(options[key]))

    args.manifest_edited_path = manifest.get("edited_path")
    args.manifest_output_path = manifest.get("output_path")
    args.manifest_log_path = manifest.get("log_path")
    args.manifest_input_params = manifest.get("input_params") or {}


def main():
    """Main entry point"""
    global EDITED_PATH, OUTPUT_PATH, LOG_PATH
    parser = argparse.ArgumentParser(
        description="Batch video processor with CSV queue management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process single video file
  python batch_processor.py --input video.mkv --presets presets.json
  
  # Process CSV queue
  python batch_processor.py --csv queue.csv --presets presets.json
  
  # Auto-confirm mode (skip all prompts)
  python batch_processor.py --csv queue.csv -y
  
  # Skip video editing step (uses existing edited files if available)
  python batch_processor.py --csv queue.csv --no-edit
  
    # Retry all tasks that have an error message (keeps checkpoint status)
  python batch_processor.py --csv queue.csv --retry-errors -y
  
  # Re-run conversion only on completed tasks (no re-edit)
  python batch_processor.py --csv queue.csv --reconvert -y

    # Generate sample presets + input CSV + field documentation
    python batch_processor.py --generate-samples

CSV Format (queue.csv):
  path,uuid,status,error,start_time,end_time,audio_delay,backup_path,original_path
  /path/to/video1.mkv,uuid-1,pending,,5.0,3.5,333,,/path/to/video1.mkv
  /path/to/video2.mkv,uuid-2,completed,,,,,backup/uuid-2,
    /path/to/video3.mkv,uuid-3,converting,Conversion failed,10.0,,-100,,
  
  path: Current working file path (may change to edited file during processing)
  original_path: Original source file path (preserved for reference, optional)
  start_time: Seconds to cut from the beginning (optional, leave empty for no cut)
  end_time: Seconds to cut from the end (optional, leave empty for no cut)
  audio_delay: Audio delay in milliseconds (optional, positive = delay audio, negative = advance audio)
               Example: 333 = delay audio by 333ms (audio is faster than video)
                       -100 = advance audio by 100ms (audio is slower than video)
  
Processing Pipeline:
  1. Validate input file (MKV only)
  2. Assign/confirm UUID
  3. Load and confirm presets
  4. Edit video (ONLY if edit_params exist in presets)
     - Creates {edited_path}/{{uuid}}.mkv
     - Skipped automatically if no edit_params
  5. Convert to HLS/DASH (uses edited file if available, otherwise original)
  6. Upload to SSH server or Cloudflare R2
  7. Update CSV status
  
Note: Editing step is automatically skipped if:
      - No edit_params in presets.json, OR
      - --no-edit flag is used
      Converter will use existing edited file if found, otherwise original file.

Checkpoint status flow:
    pending -> editing -> edited -> converting -> converted -> uploading -> completed
    On failures, status is kept at the in-progress stage and details are written to the error field.
      
Paths (configured in .env):
  - Edited videos: {edited_path}
  - Output files: {output_path}
  - Log files: {log_path}
        """.format(
            edited_path=EDITED_PATH,
            output_path=OUTPUT_PATH,
            log_path=LOG_PATH
        )
    )
    
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=None,
        help="Path to a manifest.json bundling presets/input/paths/options for the run "
             "(e.g. ./run.py manifest.json). Values in the manifest override the equivalent "
             "CLI flags. Generate a sample with --generate-samples."
    )

    # Input options (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=False)
    input_group.add_argument(
        "--input", "-i",
        type=Path,
        help="Single MKV file to process"
    )
    input_group.add_argument(
        "--csv", "-c",
        type=Path,
        help="CSV file with processing queue"
    )
    
    parser.add_argument(
        "--presets", "-p",
        type=Path,
        default=Path("./presets.json"),
        help="Preset configuration file (default: presets.json)"
    )
    
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Auto-confirm all prompts (non-interactive mode)"
    )
    
    parser.add_argument(
        "--no-edit",
        action="store_true",
        help="Skip video editing step (will use existing edited file if found, otherwise original)"
    )
    
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="Retry tasks with non-empty error field (keeps checkpoint status and clears error message)"
    )
    
    parser.add_argument(
        "--reconvert",
        action="store_true",
        help="Re-run HLS/DASH conversion on completed tasks WITHOUT re-editing (uses existing edited file if present)"
    )

    parser.add_argument(
        "--re-edit",
        action="store_true",
        help="Re-edit tasks from original_path and reprocess full pipeline; tasks without original_path are skipped"
    )

    parser.add_argument(
        "--upload-target",
        nargs="+",
        choices=["ssh", "r2"],
        default=["ssh"],
        help="Upload target(s) after conversion: ssh, r2, or both (e.g. --upload-target ssh r2). Default: ssh"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    parser.add_argument(
        "--generate-samples",
        action="store_true",
        help="Generate sample_presets.json, sample_input.csv, and sample_fields.md then exit"
    )
    
    args = parser.parse_args()
    args.manifest_edited_path = None
    args.manifest_output_path = None
    args.manifest_log_path = None
    args.manifest_input_params = {}

    if args.generate_samples:
        generated_files = generate_sample_files(Path("."))
        logger.info("Generated sample files:")
        for generated_file in generated_files:
            logger.info(f"  - {generated_file}")
        logger.info("Use sample_fields.md for field descriptions and available options.")
        sys.exit(0)

    if args.manifest:
        manifest_data = load_manifest(args.manifest)
        apply_manifest_to_args(manifest_data, args)

    if not args.input and not args.csv:
        parser.error("one of the arguments --input/-i --csv/-c (or a manifest with 'input'/'input_csv') "
                      "is required unless --generate-samples is used")

    # Apply edited/output/log path overrides from manifest, if any
    if args.manifest_edited_path:
        EDITED_PATH = Path(args.manifest_edited_path)
        EDITED_PATH.mkdir(parents=True, exist_ok=True)
    if args.manifest_output_path:
        OUTPUT_PATH = Path(args.manifest_output_path)
        OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    if args.manifest_log_path:
        LOG_PATH = Path(args.manifest_log_path)
        LOG_PATH.mkdir(parents=True, exist_ok=True)
    refresh_subprocess_env()

    # Setup logging
    log_file = setup_file_logging()
    logger.info(f"Batch processing session started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    if args.verbose:
        import logging
        logger.setLevel(logging.DEBUG)
    
    try:
        logger.info("=" * 70)
        logger.info("BATCH VIDEO PROCESSOR")
        logger.info("=" * 70)
        if args.manifest:
            logger.metadata(f"Manifest: {args.manifest}")
        logger.metadata(f"Preset file: {args.presets}")
        logger.metadata(f"Edited path: {EDITED_PATH}")
        logger.metadata(f"Output path: {OUTPUT_PATH}")
        logger.metadata(f"Log path: {LOG_PATH}")
        logger.metadata(f"Auto-confirm: {args.yes}")
        logger.metadata(f"Skip editing: {args.no_edit}")
        logger.metadata(f"Retry errors: {args.retry_errors}")
        logger.metadata(f"Re-convert completed: {args.reconvert}")
        logger.metadata(f"Re-edit from original: {args.re_edit}")
        logger.metadata(f"Upload target(s): {', '.join(args.upload_target)}")
        logger.info("=" * 70 + "\n")
        
        # Step 1: Load tasks
        tasks = []
        csv_path = None
        
        if args.input:
            # Single file mode
            logger.phase_start("Single File Mode")
            
            # Step 1a.1: Validate input file
            if not validate_video_file(args.input):
                logger.error("File validation failed")
                sys.exit(1)
            
            # Step 1a.2: Get/generate UUID
            video_id = confirm_uuid(args.input, args.yes)
            
            input_params = args.manifest_input_params or {}
            task = VideoTask(
                file_path=args.input,
                video_id=video_id,
                status="pending",
                start_time=input_params.get("start_time"),
                end_time=input_params.get("end_time"),
                audio_delay=input_params.get("audio_delay"),
            )
            tasks.append(task)
            
            # Create CSV for tracking
            csv_path = Path(f"./queue_{video_id}.csv")
            logger.info(f"Creating queue file: {csv_path}")
            
        else:
            # CSV queue mode
            logger.phase_start("CSV Queue Mode")
            
            # Step 1b.1: Load CSV queue
            csv_path = args.csv
            tasks = load_csv_queue(csv_path)
            
            # Handle retry errors option
            if args.retry_errors:
                error_count = len([t for t in tasks if t.error_message])
                if error_count > 0:
                    logger.info(f"Retry mode enabled: clearing error field for {error_count} task(s)")
                    for task in tasks:
                        if task.error_message:
                            logger.info(f"  Retrying from checkpoint: {task.video_id} (status={task.status}, error={task.error_message})")
                            task.error_message = ""
                else:
                    logger.info("Retry mode enabled but no tasks with error message found")
            
            # Handle re-edit option — reset tasks to pending using original_path; skip if no original_path
            if args.re_edit:
                re_edit_count = 0
                skipped_count = 0
                for task in tasks:
                    if not task.original_path or not str(task.original_path).strip():
                        logger.warning(f"  Skipping (no original_path): {task.video_id}")
                        skipped_count += 1
                        continue
                    logger.info(f"  Re-editing from original: {task.video_id} ({task.original_path})")
                    task.file_path = task.original_path
                    task.status = "pending"
                    task.error_message = ""
                    re_edit_count += 1
                logger.info(f"Re-edit mode: {re_edit_count} task(s) reset, {skipped_count} skipped (no original_path)")

            # Handle reconvert option — reset completed tasks but never re-edit
            if args.reconvert:
                completed_count = len([t for t in tasks if t.status == "completed"])
                if completed_count > 0:
                    logger.info(f"Re-convert mode: resetting {completed_count} completed tasks (edit step will be skipped)")
                    for task in tasks:
                        if task.status == "completed":
                            logger.info(f"  Re-converting: {task.video_id}")
                            task.status = "pending"
                            task.error_message = ""
                else:
                    logger.info("Re-convert mode enabled but no completed tasks found")
            
            # Validate all files
            for task in tasks:
                if task.status in ["completed"]:
                    logger.info(f"Skipping completed task: {task.video_id}")
                    continue
                
                if not validate_video_file(task.file_path):
                    task.error_message = "File not found or invalid"
                    logger.error(f"Validation failed, writing error field: {task.video_id}")
                    # Keep current checkpoint status; do not use a dedicated error status
            
            logger.info(f"Found {len([t for t in tasks if t.status != 'completed'])} tasks to process")
        
        if not tasks:
            logger.error("No valid tasks to process")
            sys.exit(1)
        
        # Step 3: Load and confirm presets
        profiles, edit_params = load_and_display_presets(args.presets, args.yes)
        
        # Check if editing will be performed
        will_edit = edit_params is not None and len(edit_params) > 0 and not args.no_edit
        if not will_edit:
            if edit_params is None or len(edit_params) == 0:
                logger.info("ℹ️  No edit_params found in presets - will skip editing phase")
            elif args.no_edit:
                logger.info("ℹ️  --no-edit flag enabled - will skip editing phase")
            logger.info("")
        
        # Save initial queue state
        if csv_path:
            save_csv_queue(csv_path, tasks)
        
        # Process tasks
        logger.info("=" * 70)
        logger.phase_start(f"PROCESSING {len(tasks)} TASKS")
        logger.info("=" * 70 + "\n")
        
        for i, task in enumerate(tasks, 1):
            # Skip completed tasks
            if task.status in ["completed"]:
                logger.info(f"[{i}/{len(tasks)}] Skipping completed: {task.video_id}")
                continue
            
            # Process task with edit_params
            # --reconvert always skips editing regardless of other flags
            force_skip_edit = args.no_edit or args.reconvert
            logger.info(f"[{i}/{len(tasks)}] Next task stage: {get_next_task_stage(task, edit_params=edit_params, skip_edit=force_skip_edit)}")
            logger.info(f"[{i}/{len(tasks)}] {logger.colors.GREEN}Processing: {task.video_id}{logger.colors.RESET}")
            success = process_single_task(
                task,
                args.presets,
                edit_params=edit_params,
                skip_edit=force_skip_edit,
                upload_targets=args.upload_target,
                checkpoint_callback=(lambda: save_csv_queue(csv_path, tasks)) if csv_path else None,
            )
            
            # Save progress after each task
            if csv_path:
                save_csv_queue(csv_path, tasks)
            
            if not success:
                logger.error(f"Task failed: {task.video_id}")
                if task.error_message:
                    logger.error(f"Error: {task.error_message}")
        
        # Final summary
        completed = len([t for t in tasks if t.status == "completed"])
        errors = len([t for t in tasks if t.error_message])

        logger.info("=" * 70)
        logger.info("BATCH PROCESSING SUMMARY")
        logger.info("=" * 70)
        logger.metadata(f"Total tasks: {len(tasks)}")
        logger.metadata(f"Completed: {completed}")
        logger.metadata(f"Tasks with errors: {errors}")
        logger.info("=" * 70)
        logger.info(f"Queue file: {csv_path}")
        logger.info(f"Session log: {log_file}\n")
        
        sys.exit(0 if errors == 0 else 1)
        
    except KeyboardInterrupt:
        logger.warning("\n\nBatch processing cancelled by user")
        if csv_path and tasks:
            save_csv_queue(csv_path, tasks)
            logger.info(f"Queue saved: {csv_path}")
        logger.info(f"Session log: {log_file}\n")
        sys.exit(130)
    except Exception as e:
        logger.error(f"\nFatal error: {e}")
        if csv_path and tasks:
            save_csv_queue(csv_path, tasks)
            logger.info(f"Queue saved: {csv_path}")
        logger.info(f"Session log: {log_file}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
