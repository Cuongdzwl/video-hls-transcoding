#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video to M3U8 Converter with Edit Features
==========================================

Convert video files (MKV, MP4, MOV, etc.) to adaptive M3U8 streaming format with:
- Video editing capabilities (zoom, translate, aspect ratio cropping)
- Multiple quality presets
- Portrait/Landscape orientation support
- HLS and DASH output generation
- Automated file cleanup and formatting
- Structured color-coded logging for easy monitoring

Logging System:
---------------
[Info]     - General information (cyan)
[Metadata] - File metadata and technical specs (bright blue)
[Command]  - Commands being executed (magenta)
[Phase]    - Phase/task starting (bright yellow/orange)
[Complete] - Phase/task completed successfully (bright green)
[Progress] - Encoding progress updates (cyan)
[Warning]  - Warnings (yellow)
[Error]    - Errors (red)
[Debug]    - Debug information (gray)

Author: GitHub Copilot
Date: August 29, 2025
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import platform
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import time

# Import shared modules
from logger import get_logger
from hw_detector import detect_hardware_acceleration
from packager_detector import detect_packager_executable, detect_mpd_generator_executable
from config import get_output_path, get_log_path

# Load paths from config
OUTPUT_PATH = get_output_path()
LOG_PATH = get_log_path()

# Try to import psutil for enhanced system info, fall back gracefully if not available
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════════════════════════════

logger = get_logger("video_to_m3u8")

# ═══════════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════════

@dataclass
class EditSettings:
    """Video editing settings"""
    zoom_factor: float = 1.0  # Canvas zoom scale: 1.0 = default, 0.8 = zoom out 20%, 1.1 = zoom in 10%
    translate_x: int = 0
    translate_y: int = 0
    middle_cut_9_16: bool = False

@dataclass
class QualityPreset:
    """Quality preset for video conversion"""
    name: str
    width: int
    height: int
    bitrate: str
    max_bitrate: str
    buffer_size: str
    audio_bitrate: str
    auto_bitrate: bool = True
    orientation: str = "portrait"
    edit: Optional[EditSettings] = None

@dataclass
class VideoInfo:
    """Video file information"""
    width: int
    height: int
    duration: float
    fps: float
    bitrate: int
    is_4k: bool = False

# ═══════════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════════

def setup_logging(video_id: str):
    """Setup file logging"""
    log_dir = LOG_PATH
    log_file = log_dir / f"conversion_{video_id}.log"
    file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
    # File handler uses plain format without colors
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)
    logger.info(f"Log file created: {log_file}")

def run_command(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 3600, hide_banner: bool = False) -> Tuple[bool, str]:
    """Run shell command with logging"""
    # Add hide_banner option for FFmpeg commands
    if hide_banner and len(cmd) > 0 and cmd[0] in ['ffmpeg', 'ffprobe']:
        if '-hide_banner' not in cmd:
            cmd = cmd[:1] + ['-hide_banner'] + cmd[1:]
    
    logger.command(f"{' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding='utf-8',
            errors='replace'
        )
        
        if result.returncode == 0:
            logger.phase_end(f"Command completed successfully")
            return True, result.stdout
        else:
            logger.error(f"Command failed with code {result.returncode}")
            logger.error(f"Error output: {result.stderr}")
            return False, result.stderr
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after {timeout} seconds")
        return False, "Command timed out"
    except Exception as e:
        logger.error(f"Exception running command: {e}")
        return False, str(e)

def run_ffmpeg_with_progress(cmd: List[str], duration: float, cwd: Optional[Path] = None, timeout: int = 3600, hide_banner: bool = False) -> Tuple[bool, str]:
    """Run FFmpeg command with real-time progress monitoring"""
    # Add hide_banner option for FFmpeg commands
    if hide_banner and len(cmd) > 0 and cmd[0] in ['ffmpeg', 'ffprobe']:
        if '-hide_banner' not in cmd:
            cmd = cmd[:1] + ['-hide_banner'] + cmd[1:]
    
    # Add progress reporting to FFmpeg
    if cmd[0] == 'ffmpeg':
        # Insert progress options after ffmpeg command
        progress_cmd = cmd[:1] + ['-progress', 'pipe:2'] + cmd[1:]
    else:
        progress_cmd = cmd
    
    logger.command(f"{' '.join(cmd)}")
    logger.phase_start("Starting FFmpeg encoding with progress monitoring")
    
    try:
        process = subprocess.Popen(
            progress_cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        stdout_lines = []
        stderr_lines = []
        last_progress = 0
        start_time = time.time()
        
        def read_stdout():
            for line in iter(process.stdout.readline, ''):
                if line:
                    stdout_lines.append(line.strip())
        
        def read_stderr():
            nonlocal last_progress
            current_bitrate = 0
            total_size = 0
            
            for line in iter(process.stderr.readline, ''):
                if line:
                    stderr_lines.append(line.strip())
                    
                    # Parse FFmpeg progress output
                    if cmd[0] == 'ffmpeg' and '=' in line:
                        try:
                            # Track bitrate
                            if line.startswith('bitrate='):
                                bitrate_str = line.split('=')[1].strip()
                                # Parse bitrate (e.g., "1234.5kbits/s" or "N/A")
                                if 'kbits/s' in bitrate_str:
                                    current_bitrate = float(bitrate_str.replace('kbits/s', '').strip())
                                elif 'Mbits/s' in bitrate_str:
                                    current_bitrate = float(bitrate_str.replace('Mbits/s', '').strip()) * 1000
                            
                            # Track total output size
                            elif line.startswith('total_size='):
                                total_size = int(line.split('=')[1])
                            
                            elif line.startswith('out_time_ms='):
                                time_ms = int(line.split('=')[1])
                                current_time = time_ms / 1000000.0  # Convert microseconds to seconds
                                
                                if duration > 0:
                                    progress = min((current_time / duration) * 100, 100)
                                    
                                    # Only log progress every 5% or every 10 seconds
                                    now = time.time()
                                    if progress - last_progress >= 5 or now - start_time >= 10:
                                        elapsed = now - start_time
                                        if progress > 0 and elapsed > 0:
                                            speed = current_time / elapsed  # encoding speed ratio
                                            eta = (duration - current_time) / speed if speed > 0 else 0
                                            fps = current_time * 30 / elapsed if elapsed > 0 else 0  # Approximate FPS
                                            
                                            # Include bitrate in progress
                                            if current_bitrate > 0:
                                                bitrate_mbps = current_bitrate / 1000
                                                size_mb = total_size / (1024 * 1024)
                                                logger.progress(f"{progress:.1f}% | Speed: {speed:.2f}x | FPS: {fps:.1f} | Bitrate: {bitrate_mbps:.2f}Mbps | Size: {size_mb:.1f}MB | Elapsed: {elapsed:.1f}s | ETA: {eta:.1f}s")
                                            else:
                                                logger.progress(f"{progress:.1f}% | Speed: {speed:.2f}x | FPS: {fps:.1f} | Elapsed: {elapsed:.1f}s | ETA: {eta:.1f}s")
                                        else:
                                            logger.progress(f"{progress:.1f}% | Elapsed: {elapsed:.1f}s")
                                        last_progress = progress
                                        
                            elif line.startswith('progress=end'):
                                elapsed = time.time() - start_time
                                avg_speed = duration / elapsed if elapsed > 0 else 0
                                logger.phase_end(f"FFmpeg encoding completed in {elapsed:.1f}s | Average speed: {avg_speed:.2f}x")
                                
                        except (ValueError, IndexError):
                            continue
        
        # Start reading threads
        stdout_thread = threading.Thread(target=read_stdout)
        stderr_thread = threading.Thread(target=read_stderr)
        
        stdout_thread.daemon = True
        stderr_thread.daemon = True
        
        stdout_thread.start()
        stderr_thread.start()
        
        # Wait for process to complete
        return_code = process.wait(timeout=timeout)
        
        # Wait for threads to finish reading
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        
        stdout_output = '\n'.join(stdout_lines)
        stderr_output = '\n'.join(stderr_lines)
        
        if return_code == 0:
            logger.phase_end("FFmpeg command completed successfully")
            return True, stdout_output
        else:
            logger.error(f"FFmpeg failed with code {return_code}")
            logger.error(f"Error output: {stderr_output}")
            return False, stderr_output
            
    except subprocess.TimeoutExpired:
        logger.error(f"FFmpeg timed out after {timeout} seconds")
        if process:
            process.kill()
        return False, "FFmpeg timed out"
    except Exception as e:
        logger.error(f"Exception running FFmpeg: {e}")
        return False, str(e)

def get_video_info(video_path: Path, hide_banner: bool = False) -> VideoInfo:
    """Get video file information using ffprobe"""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path)
    ]
    
    success, output = run_command(cmd, hide_banner=hide_banner)
    if not success:
        raise Exception(f"Failed to get video info: {output}")
    
    data = json.loads(output)
    video_stream = None
    
    for stream in data["streams"]:
        if stream["codec_type"] == "video":
            video_stream = stream
            break
    
    if not video_stream:
        raise Exception("No video stream found")
    
    width = int(video_stream["width"])
    height = int(video_stream["height"])
    duration = float(data["format"]["duration"])
    fps = eval(video_stream["r_frame_rate"]) if "r_frame_rate" in video_stream else 30.0
    bitrate = int(data["format"]["bit_rate"]) if "bit_rate" in data["format"] else 0
    
    is_4k = width >= 3840 or height >= 2160
    
    # Log video metadata
    logger.metadata(f"Resolution: {width}x{height} {'(4K)' if is_4k else ''}")
    logger.metadata(f"Duration: {duration:.2f}s | FPS: {fps:.2f}")
    logger.metadata(f"Bitrate: {bitrate / 1000000:.2f} Mbps")
    logger.metadata(f"Pixel Format: {video_stream.get('pix_fmt', 'unknown')}")
    logger.metadata(f"Codec: {video_stream.get('codec_name', 'unknown')}")
    
    return VideoInfo(
        width=width,
        height=height,
        duration=duration,
        fps=fps,
        bitrate=bitrate,
        is_4k=is_4k
    )

def load_presets(preset_file: Path) -> List[QualityPreset]:
    """Load quality presets from JSON file"""
    if not preset_file.exists():
        raise FileNotFoundError(f"Preset file not found: {preset_file}")
    
    with open(preset_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Support both direct array format and profiles format
    if isinstance(data, list):
        # Old format: direct array of presets
        preset_list = data
    elif isinstance(data, dict) and "profiles" in data:
        # New format: object with profiles array
        preset_list = data["profiles"]
    else:
        raise ValueError("Invalid preset file format. Expected array or object with 'profiles' key")
    
    presets = []
    for preset in preset_list:
        edit_settings = None
        if "edit" in preset and preset["edit"]:
            edit_data = preset["edit"]

            # New zoom_factor format: numeric only
            zoom_factor_value = edit_data.get("zoom_factor", 1.0)
            if isinstance(zoom_factor_value, dict):
                logger.warning(
                    "Directional zoom_factor object is deprecated. "
                    "Use numeric zoom_factor (e.g. 0.8, 1.0, 1.1). Falling back to 1.0"
                )
                zoom_factor_value = 1.0

            try:
                zoom_factor_value = float(zoom_factor_value)
            except (TypeError, ValueError):
                logger.warning(f"Invalid zoom_factor '{zoom_factor_value}', falling back to 1.0")
                zoom_factor_value = 1.0

            if zoom_factor_value <= 0:
                logger.warning(f"zoom_factor must be > 0, got {zoom_factor_value}. Falling back to 1.0")
                zoom_factor_value = 1.0
            
            edit_settings = EditSettings(
                zoom_factor=zoom_factor_value,
                translate_x=edit_data.get("translate_x", 0),
                translate_y=edit_data.get("translate_y", 0),
                middle_cut_9_16=edit_data.get("9:16_middle_cut", False)
            )
        
        quality_preset = QualityPreset(
            name=preset["name"],
            width=preset["width"],
            height=preset["height"],
            bitrate=preset["bitrate"],
            max_bitrate=preset["max_bitrate"],
            buffer_size=preset["buffer_size"],
            audio_bitrate=preset["audio_bitrate"],
            auto_bitrate=preset.get("auto_bitrate", True),
            orientation=preset.get("orientation", "portrait"),
            edit=edit_settings
        )
        presets.append(quality_preset)
    
    return presets

def get_system_specs() -> Dict[str, Any]:
    """Get comprehensive system specifications"""
    specs = {
        "os": platform.system(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "memory": {},
        "gpu": [],
        "ffmpeg_version": "Unknown"
    }
    
    # Get memory info
    if PSUTIL_AVAILABLE:
        try:
            memory = psutil.virtual_memory()
            specs["memory"] = {
                "total": f"{memory.total / (1024**3):.1f} GB",
                "available": f"{memory.available / (1024**3):.1f} GB",
                "used": f"{memory.used / (1024**3):.1f} GB",
                "percent": f"{memory.percent:.1f}%"
            }
        except Exception as e:
            specs["memory"] = {"error": str(e)}
    else:
        specs["memory"] = {"note": "Install psutil for detailed memory info"}
    
    # Get GPU info
    try:
        # Try NVIDIA-SMI first
        if platform.system().lower() == "windows":
            nvidia_cmd = ["nvidia-smi", "--query-gpu=gpu_name,memory.total,driver_version", "--format=csv,noheader,nounits"]
        else:
            nvidia_cmd = ["nvidia-smi", "--query-gpu=gpu_name,memory.total,driver_version", "--format=csv,noheader,nounits"]
        
        try:
            result = subprocess.run(nvidia_cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        parts = [p.strip() for p in line.split(',')]
                        if len(parts) >= 3:
                            specs["gpu"].append({
                                "type": "NVIDIA",
                                "name": parts[0],
                                "memory": f"{parts[1]} MB",
                                "driver": parts[2]
                            })
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        # Try AMD GPU detection
        try:
            # On Windows, try wmic
            if platform.system().lower() == "windows":
                wmic_cmd = ["wmic", "path", "win32_VideoController", "get", "name,AdapterRAM", "/format:csv"]
                result = subprocess.run(wmic_cmd, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')
                    for line in lines[1:]:  # Skip header
                        if line.strip() and ',' in line:
                            parts = line.split(',')
                            if len(parts) >= 3 and parts[1].strip() and parts[2].strip():
                                memory_bytes = parts[1].strip()
                                name = parts[2].strip()
                                if memory_bytes.isdigit() and int(memory_bytes) > 0:
                                    memory_gb = int(memory_bytes) / (1024**3)
                                    specs["gpu"].append({
                                        "type": "Integrated/Other",
                                        "name": name,
                                        "memory": f"{memory_gb:.1f} GB",
                                        "driver": "Windows"
                                    })
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
            
    except Exception as e:
        specs["gpu"] = [{"error": str(e)}]
    
    # Get FFmpeg version and available encoders
    try:
        ffmpeg_cmd = ["ffmpeg", "-version"]
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            first_line = result.stdout.split('\n')[0]
            if 'ffmpeg version' in first_line:
                specs["ffmpeg_version"] = first_line.split('ffmpeg version')[1].split()[0]
        
        # Get available encoders
        encoders_cmd = ["ffmpeg", "-encoders"]
        result = subprocess.run(encoders_cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            encoders = []
            for line in result.stdout.split('\n'):
                if 'h264' in line.lower():
                    if 'nvenc' in line.lower():
                        encoders.append("h264_nvenc (NVIDIA)")
                    elif 'videotoolbox' in line.lower():
                        encoders.append("h264_videotoolbox (Apple)")
                    elif 'qsv' in line.lower():
                        encoders.append("h264_qsv (Intel)")
                    elif 'amf' in line.lower():
                        encoders.append("h264_amf (AMD)")
            specs["available_encoders"] = encoders if encoders else ["libx264 (Software)"]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    
    return specs

def display_system_specs():
    """Display system specifications in a formatted way"""
    logger.info("=" * 70)
    logger.info("SYSTEM SPECIFICATIONS")
    logger.info("=" * 70)
    
    specs = get_system_specs()
    
    # Basic system info
    logger.metadata(f"OS: {specs['os']} {specs['os_version']}")
    logger.metadata(f"Architecture: {specs['architecture']}")
    logger.metadata(f"Processor: {specs['processor']}")
    logger.metadata(f"CPU Cores: {specs['cpu_count']}")
    logger.metadata(f"Python: {specs['python_version']}")
    logger.metadata(f"FFmpeg: {specs['ffmpeg_version']}")
    
    # Memory info
    if isinstance(specs["memory"], dict) and "total" in specs["memory"]:
        logger.metadata(f"Memory: {specs['memory']['used']} / {specs['memory']['total']} ({specs['memory']['percent']} used)")
    else:
        logger.metadata(f"Memory: {specs['memory']}")
    
    # GPU info
    if specs["gpu"]:
        logger.metadata("GPU(s):")
        for i, gpu in enumerate(specs["gpu"], 1):
            if "error" in gpu:
                logger.metadata(f"  {i}. Error: {gpu['error']}")
            else:
                logger.metadata(f"  {i}. {gpu['name']} ({gpu['type']}) - {gpu['memory']} VRAM")
                if "driver" in gpu:
                    logger.metadata(f"     Driver: {gpu['driver']}")
    else:
        logger.metadata("GPU: No GPU detected or information unavailable")
    
    # Available encoders
    if "available_encoders" in specs:
        logger.metadata("Available H.264 Encoders:")
        for encoder in specs["available_encoders"]:
            logger.metadata(f"  - {encoder}")
    
    logger.info("=" * 70)

def parse_crop_value(value: Any, dimension: int) -> int:
    """Parse crop value - supports percentage (float) or pixels (string with 'px')
    
    Args:
        value: Crop value - can be float (0.0-1.0 for percentage) or string ("100px" for pixels)
        dimension: Width or height of the video in pixels
    
    Returns:
        Number of pixels to crop
    """
    if value is None:
        return 0
    
    if isinstance(value, str):
        # Handle pixel format: "100px" or "100"
        value_str = value.strip().lower()
        if value_str.endswith('px'):
            try:
                return int(value_str[:-2])
            except ValueError:
                logger.warning(f"Invalid pixel value: {value}, treating as 0")
                return 0
        else:
            try:
                return int(value_str)
            except ValueError:
                logger.warning(f"Invalid pixel value: {value}, treating as 0")
                return 0
    elif isinstance(value, (int, float)):
        # Handle percentage format: 0.0-1.0
        if value > 1.0:
            # Assume it's pixels if greater than 1
            return int(value)
        else:
            # It's a percentage
            return int(dimension * value)
    else:
        logger.warning(f"Unsupported crop value type: {type(value)}, treating as 0")
        return 0

def build_ffmpeg_filter(video_info: VideoInfo, preset: QualityPreset) -> str:
    """Build FFmpeg filter chain for video processing"""
    filters = []
    
    # Track current dimensions for filter chain
    current_width = video_info.width
    current_height = video_info.height
    
    # Apply editing if specified
    if preset.edit:
        edit = preset.edit

        if edit.zoom_factor <= 0:
            logger.warning(f"Invalid zoom_factor={edit.zoom_factor}, using 1.0")
            edit.zoom_factor = 1.0
        
        # Check if 9:16 middle cut is valid (only for 4K input)
        if edit.middle_cut_9_16 and video_info.is_4k:
            # Calculate crop parameters for target aspect ratio from center based on preset dimensions
            target_ratio = preset.width / preset.height
            input_ratio = current_width / current_height
            
            if input_ratio > target_ratio:
                # Input is wider, crop width
                crop_width = int(current_height * target_ratio)
                crop_height = current_height
                crop_x = (current_width - crop_width) // 2
                crop_y = 0
            else:
                # Input is taller, crop height
                crop_width = current_width
                crop_height = int(current_width / target_ratio)
                crop_x = 0
                crop_y = (current_height - crop_height) // 2
                
            filters.append(f"crop={crop_width}:{crop_height}:{crop_x}:{crop_y}")
            logger.metadata(f"Aspect ratio crop: {crop_width}x{crop_height} for {target_ratio:.2f} ratio")
            
            # Update current dimensions
            current_width = crop_width
            current_height = crop_height
        
        # Apply translation (crop to shift the frame)
        if edit.translate_x != 0 or edit.translate_y != 0:
            # Translation by cropping - positive values shift content left/up
            crop_x = max(0, edit.translate_x)
            crop_y = max(0, edit.translate_y)
            crop_width = current_width - abs(edit.translate_x)
            crop_height = current_height - abs(edit.translate_y)
            
            filters.append(f"crop={crop_width}:{crop_height}:{crop_x}:{crop_y}")
            logger.metadata(f"Translation crop: {crop_width}x{crop_height} at ({crop_x}, {crop_y})")
            
            # Update current dimensions
            current_width = crop_width
            current_height = crop_height
    
    # Final composition on fixed canvas to preserve output resolution exactly as preset.
    # zoom_factor: 1.0 = default, <1 zoom out, >1 zoom in.
    # translate_x/y: shift composition on canvas for framing.
    zoom = preset.edit.zoom_factor if preset.edit else 1.0
    translate_x = preset.edit.translate_x if preset.edit else 0
    translate_y = preset.edit.translate_y if preset.edit else 0

    # Keep dimensions even for encoder compatibility
    filters.append(f"scale=trunc(iw*{zoom}/2)*2:trunc(ih*{zoom}/2)*2")
    # Pad to at least target canvas while applying translation
    filters.append(
        f"pad=max(iw\\,{preset.width}):max(ih\\,{preset.height}):"
        f"(ow-iw)/2+{translate_x}:(oh-ih)/2+{translate_y}:color=black"
    )
    # Crop back to exact canvas size; clamp to valid crop window
    filters.append(
        f"crop={preset.width}:{preset.height}:"
        f"max(0\\,min(in_w-out_w\\,(in_w-out_w)/2-{translate_x})):"
        f"max(0\\,min(in_h-out_h\\,(in_h-out_h)/2-{translate_y}))"
    )

    logger.metadata(
        f"Canvas compose: {preset.width}x{preset.height} | "
        f"zoom_factor={zoom} | translate=({translate_x},{translate_y})"
    )

    return ",".join(filters) if filters else f"scale={preset.width}:{preset.height}"

def convert_video_to_mp4(input_path: Path, output_path: Path, preset: QualityPreset, video_info: VideoInfo, hw_options: Dict[str, Any], hide_banner: bool = False, show_progress: bool = True) -> bool:
    """Convert video using FFmpeg with specified preset and hardware acceleration"""
    logger.phase_start(f"Converting to {preset.name}: {output_path.name}")
    logger.info(f"Encoder: {hw_options['encoder']} | Preset: {hw_options.get('preset', 'N/A')}")
    logger.info(f"Target Video Bitrate: {preset.bitrate} | Max: {preset.max_bitrate} | Audio: {preset.audio_bitrate}")
    logger.info(f"Expected duration: {video_info.duration:.1f}s | Progress monitoring: {show_progress}")
    
    # Build filter chain
    video_filter = build_ffmpeg_filter(video_info, preset)
    
    # Build FFmpeg command
    cmd = ["ffmpeg", "-y"]  # Overwrite output files
    
    # Add input (no hardware acceleration for input to avoid pixel format issues)
    cmd.extend(["-i", str(input_path)])
    
    # Video encoding settings
    cmd.extend(["-c:v", hw_options["encoder"]])
    
    # Add preset if available
    if hw_options.get("preset"):
        cmd.extend(["-preset", hw_options["preset"]])
    
    # Add encoder-specific quality settings
    if hw_options["encoder"] == "libx264":
        cmd.extend([
            "-profile:v", "high",
            "-level", "4.1",
            "-pix_fmt", "yuv420p"
        ])
        # Variable bitrate with CRF + bitrate constraints
        if preset.auto_bitrate:
            # CRF 16 gives very high visual quality without extreme bitrate spikes.
            cmd.extend(["-crf", "16"])
        cmd.extend([
            "-b:v", preset.bitrate,
            "-maxrate", preset.max_bitrate,
            "-bufsize", preset.buffer_size
        ])
    
    elif hw_options["encoder"] == "h264_nvenc":
        cmd.extend([
            "-profile:v", "high",
            "-level", "4.1",
            # Use 4:2:0 for best HLS player compatibility.
            "-pix_fmt", "yuv420p",
            "-rc", "vbr_hq",
            "-cq", "17",
            "-b:v", preset.bitrate,
            "-maxrate", preset.max_bitrate,
            "-bufsize", preset.buffer_size,
            "-spatial_aq", "1",
            "-temporal_aq", "1",
            "-aq-strength", "8",
            "-rc-lookahead", "32"
        ])
    
    elif hw_options["encoder"] == "h264_videotoolbox":
        cmd.extend([
            "-profile:v", "high",
            "-level", "4.1",
            "-pix_fmt", "yuv420p",
            "-preset", "main",  # Use quality preset
            "-b:v", preset.bitrate,
            "-maxrate", preset.max_bitrate,
            "-bufsize", preset.buffer_size,
            "-allow_sw", "1"        # Allow software fallback if needed
        ])
    
    # Add video filter
    cmd.extend(["-vf", video_filter])
    
    # Audio settings
    cmd.extend([
        "-c:a", "aac",
        "-b:a", preset.audio_bitrate,
        "-ac", "2",
        "-ar", "48000"
    ])
    
    # Output options: generate fragmented MP4 for pending assets (better for streaming workflows)
    cmd.extend([
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
        str(output_path)
    ])
    
    # Use appropriate function based on progress setting
    if show_progress:
        success, output = run_ffmpeg_with_progress(cmd, video_info.duration, hide_banner=hide_banner)
    else:
        success, output = run_command(cmd, hide_banner=hide_banner)
        
    if success:
        logger.phase_end(f"Successfully converted: {output_path.name}")
    else:
        logger.error(f"Failed to convert: {output_path.name}")
        # Log the actual command for debugging
        logger.debug(f"Command: {' '.join(cmd)}")
        # Log FFmpeg output (last 1000 chars) to help debugging
        try:
            if output:
                # Show last ~200 lines or 2000 chars
                snippet = output if len(output) < 2000 else output[-2000:]
                logger.error("FFmpeg output (truncated):")
                for line in snippet.splitlines()[-200:]:
                    logger.error(f"  {line}")
        except Exception:
            logger.debug("Could not log FFmpeg output")
    
    return success

def package_to_hls(input_dir: Path, output_dir: Path, video_id: str, orientation: str, hide_banner: bool = False) -> bool:
    """Package MP4 files to HLS using Shaka Packager"""
    logger.phase_start(f"Packaging HLS for {video_id} ({orientation})")
    
    # Find all MP4 files in input directory, excluding macOS metadata files (._*)
    all_mp4_files = list(input_dir.glob("*.mp4"))
    mp4_files = [f for f in all_mp4_files if not f.name.startswith("._")]
    
    if not mp4_files:
        logger.error("No MP4 files found for packaging")
        return False
    
    # Log filtered files if any were excluded
    filtered_count = len(all_mp4_files) - len(mp4_files)
    if filtered_count > 0:
        logger.info(f"Filtered out {filtered_count} macOS metadata file(s) (._*)")
    
    # Detect appropriate packager executable
    try:
        packager_exe = detect_packager_executable()
    except FileNotFoundError as e:
        logger.error(f"❌ {e}")
        return False
    
    # Build packager command
    cmd = [packager_exe]
    
    # Add input streams with new naming convention
    for mp4_file in mp4_files:
        preset_name = mp4_file.stem  # e.g., "1080p_2mbps"
        video_output = output_dir / f"{video_id}_video_{preset_name}.cmfv"
        variant_playlist = output_dir / f"{video_id}_video_{preset_name}.m3u8"
        
        cmd.extend([
            f"in={mp4_file},stream=video,output={video_output},playlist_name={variant_playlist.name}"
        ])
    
    # Add audio from first file
    first_mp4 = mp4_files[0]
    audio_output = output_dir / f"{video_id}_a.cmfa"
    audio_playlist = output_dir / f"{video_id}_a.m3u8"
    cmd.extend([
        f"in={first_mp4},stream=audio,output={audio_output},playlist_name={audio_playlist.name}"
    ])
    
    # Check for subtitle/caption streams and add them
    # This will create {id}_captions_{lang_code}_000000000.cmft and {id}_captions_{lang_code}.m3u8
    try:
        # Probe for subtitle streams
        probe_cmd = [
            "ffprobe", "-v", "quiet", "-select_streams", "s", 
            "-show_entries", "stream=index:stream_tags=language", 
            "-of", "csv=p=0", str(first_mp4)
        ]
        success, probe_output = run_command(probe_cmd, hide_banner=hide_banner)
        
        if success and probe_output.strip():
            # Add subtitle streams
            for line in probe_output.strip().split('\n'):
                parts = line.split(',')
                if len(parts) >= 2:
                    stream_index = parts[0]
                    lang_code = parts[1] if parts[1] else "und"  # undefined language
                    
                    caption_output = output_dir / f"{video_id}_captions_{lang_code}_000000000.cmft"
                    caption_playlist = output_dir / f"{video_id}_captions_{lang_code}.m3u8"
                    
                    cmd.extend([
                        f"in={first_mp4},stream=subtitle:{stream_index},output={caption_output},playlist_name={caption_playlist.name}"
                    ])
                    logger.info(f"Added subtitle stream: {lang_code}")
    except Exception as e:
        logger.warning(f"Could not probe for subtitles: {e}")
    
    # Add segment duration for TARGET-DURATION = 12
    cmd.extend([
        "--segment_duration", "12"
    ])
    
    # Add HLS master playlist
    master_playlist = output_dir / f"{video_id}.m3u8"
    cmd.extend([
        "--hls_master_playlist_output", str(master_playlist)
    ])
    
    # Add DASH manifest
    dash_manifest = output_dir / f"{video_id}.mpd"
    cmd.extend([
        "--mpd_output", str(dash_manifest)
    ])
    
    success, output = run_command(cmd, cwd=Path("."))
    if success:
        logger.phase_end(f"Successfully packaged HLS: {master_playlist.name}")
    else:
        logger.error(f"Failed to package HLS: {output}")
    
    return success

def clean_m3u8_files(output_dir: Path):
    """Remove Shaka Packager generated comments from M3U8 files"""
    logger.phase_start("Cleaning M3U8 files")
    
    all_m3u8 = list(output_dir.glob("*.m3u8"))
    m3u8_files = [f for f in all_m3u8 if not f.name.startswith("._")]
    filtered_count = len(all_m3u8) - len(m3u8_files)
    if filtered_count > 0:
        logger.info(f"Filtered out {filtered_count} macOS metadata m3u8 file(s) (._*)")

    for m3u8_file in m3u8_files:
        try:
            with m3u8_file.open('r', encoding='utf-8') as f:
                lines = f.readlines()

            cleaned_lines = []
            for line in lines:
                # Remove Shaka Packager comment lines
                if "Generated with https://github.com/shaka-project/shaka-packager" in line:
                    continue
                # Also remove any packager-related comment lines (case-insensitive)
                if line.strip().startswith("#") and "shaka-packager" in line.lower():
                    continue
                cleaned_lines.append(line)

            with m3u8_file.open('w', encoding='utf-8') as f:
                f.writelines(cleaned_lines)

            logger.info(f"Cleaned: {m3u8_file.name}")

        except Exception as e:
            logger.error(f"Failed to clean {m3u8_file.name}: {e}")
    
    logger.phase_end("M3U8 files cleaned")

# ═══════════════════════════════════════════════════════════════════════════════════
# MAIN CONVERTER CLASS
# ═══════════════════════════════════════════════════════════════════════════════════

class VideoToM3U8Converter:
    """Main converter class for video files (MKV, MP4, MOV, etc.) to M3U8 streaming format"""
    
    def __init__(self, base_output_dir: Optional[Path] = None):
        self.base_output_dir = base_output_dir or OUTPUT_PATH
        self.base_output_dir.mkdir(exist_ok=True)
    
    def convert(self, input_file: Path, video_id: Optional[str] = None, preset_file: Path = Path("presets-portrait.json"), hide_banner: bool = False, show_progress: bool = True, show_specs: bool = False) -> bool:
        """Convert video file (MKV, MP4, MOV, etc.) to M3U8 streaming format"""
        
        # Determine video ID
        if video_id:
            actual_video_id = video_id
        else:
            actual_video_id = input_file.stem
        
        logger.info("=" * 70)
        logger.phase_start(f"Starting conversion for: {input_file.name}")
        logger.info(f"Video ID: {actual_video_id}")
        logger.info(f"Hide FFmpeg banner: {hide_banner}")
        logger.info(f"Show progress monitoring: {show_progress}")
        logger.info("=" * 70)
        
        # Check if input file exists
        if not input_file.exists():
            logger.error(f"Input file not found: {input_file}")
            return False
        
        # If video_id is provided and different from filename, create a symlink or copy
        working_file = input_file
        if video_id and video_id != input_file.stem:
            # Use the same extension as the input file
            working_file = Path(f"{video_id}{input_file.suffix}")
            if not working_file.exists():
                logger.info(f"Creating working copy: {working_file}")
                shutil.copy2(input_file, working_file)
        
        # Load presets
        try:
            logger.phase_start("Loading quality presets")
            presets = load_presets(preset_file)
            logger.phase_end(f"Loaded {len(presets)} quality presets")
        except Exception as e:
            logger.error(f"Failed to load presets: {e}")
            return False
        
        # Get video information
        try:
            logger.phase_start("Analyzing video file")
            video_info = get_video_info(working_file, hide_banner=hide_banner)
            logger.phase_end("Video analysis completed")
        except Exception as e:
            logger.error(f"Failed to get video info: {e}")
            return False
        
        # Setup logging
        setup_logging(actual_video_id)
        
        # Show system specs if requested
        if show_specs:
            display_system_specs()
        
        # Detect hardware acceleration
        hw_options = detect_hardware_acceleration(hide_banner=hide_banner)
        
        # Group presets by orientation
        from collections import defaultdict
        presets_by_orientation = defaultdict(list)
        for preset in presets:
            presets_by_orientation[preset.orientation].append(preset)
        
        logger.info(f"📋 Found {len(presets_by_orientation)} orientation(s): {', '.join(presets_by_orientation.keys())}")
        
        # Process each orientation separately
        overall_success = True
        
        for orientation, oriented_presets in presets_by_orientation.items():
            logger.info("=" * 70)
            logger.phase_start(f"Processing {orientation.upper()} orientation ({len(oriented_presets)} presets)")
            logger.info("=" * 70)
            
            # Setup directories for this orientation
            pending_dir = self.base_output_dir / "pending" / actual_video_id / orientation
            pending_dir.mkdir(parents=True, exist_ok=True)
            
            final_output_dir = self.base_output_dir / actual_video_id / orientation
            final_output_dir.mkdir(parents=True, exist_ok=True)
            
            # Convert to MP4 files
            logger.info(f"🎬 Starting MP4 conversion for {orientation}")
            conversion_success = True
            
            for preset in oriented_presets:
                output_file = pending_dir / f"{preset.name}.mp4"
                success = convert_video_to_mp4(working_file, output_file, preset, video_info, hw_options, hide_banner=hide_banner, show_progress=show_progress)
                if not success:
                    conversion_success = False
                    logger.error(f"❌ Failed to convert preset: {preset.name}")
            
            if not conversion_success:
                logger.error(f"❌ Some {orientation} conversions failed, skipping packaging for this orientation")
                overall_success = False
                continue
            
            # Package to HLS
            logger.info(f"📦 Starting HLS packaging for {orientation}")
            packaging_success = package_to_hls(pending_dir, final_output_dir, actual_video_id, orientation, hide_banner=hide_banner)
            
            if not packaging_success:
                logger.error(f"❌ HLS packaging failed for {orientation}")
                overall_success = False
                continue
            
            # Clean M3U8 files
            clean_m3u8_files(final_output_dir)
            
            # Cleanup pending directory for this orientation
            try:
                shutil.rmtree(pending_dir)
                logger.info(f"🗑️ Cleaned up pending directory: {pending_dir}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to cleanup pending directory: {e}")
            
            logger.phase_end(f"✅ {orientation.upper()} orientation completed successfully!")
            logger.info(f"📁 Output directory: {final_output_dir}")
            logger.info(f"🎬 Master playlist: {final_output_dir / f'{actual_video_id}.m3u8'}")
            logger.info(f"📺 DASH manifest: {final_output_dir / f'{actual_video_id}.mpd'}")
        
        # Cleanup working file if it was a copy
        if working_file != input_file and working_file.exists():
            try:
                working_file.unlink()
                logger.info(f"🗑️ Cleaned up working file: {working_file}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to cleanup working file: {e}")
        
        if overall_success:
            logger.info("=" * 70)
            logger.info(f"🎉 All conversions completed successfully for {actual_video_id}")
            logger.info(f"📁 Base output directory: {self.base_output_dir / actual_video_id}")
            logger.info("=" * 70)
        else:
            logger.warning("=" * 70)
            logger.warning(f"⚠️ Some conversions failed for {actual_video_id}")
            logger.info(f"� Base output directory: {self.base_output_dir / actual_video_id}")
            logger.warning("=" * 70)
        
        return overall_success

# ═══════════════════════════════════════════════════════════════════════════════════
# COMMAND LINE INTERFACE
# ═══════════════════════════════════════════════════════════════════════════════════

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Convert video files (MKV, MP4, MOV, etc.) to adaptive M3U8 streaming format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python source/mkv_to_m3u8_converter.py --input video.mp4 --presets presets-portrait.json
  python source/mkv_to_m3u8_converter.py --input sample.mov --id my_video --presets custom_presets.json
  python source/mkv_to_m3u8_converter.py --input demo.mkv --output ./streaming_output
        """
    )
    
    parser.add_argument(
        "--input", 
        type=Path,
        required=True,
        help="Input video file path - supports MKV, MP4, MOV, and other formats (required)"
    )
    
    parser.add_argument(
        "--id",
        help="Custom video ID (optional, defaults to input filename without extension). If provided, output will use this ID."
    )
    
    parser.add_argument(
        "--presets",
        type=Path,
        default=Path("./presets.json"),
        help="JSON file containing quality presets (default: presets.json)"
    )
    
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("./output"),
        help="Base output directory (default: output)"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    parser.add_argument(
        "--hide-banner",
        action="store_true",
        help="Hide FFmpeg banner and version information during conversion (default: False)"
    )
    
    parser.add_argument(
        "--show-progress",
        action="store_true",
        help="Show FFmpeg conversion progress with performance monitoring (default: True)",
        default=True
    )
    
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable FFmpeg progress monitoring for cleaner logs"
    )
    
    parser.add_argument(
        "--show-specs",
        action="store_true",
        help="Display detailed system specifications including GPU info"
    )
    
    parser.add_argument(
        "--specs-only",
        action="store_true", 
        help="Only display system specifications and exit (no conversion)"
    )
    
    args = parser.parse_args()
    
    # Handle specs-only option
    if args.specs_only:
        display_system_specs()
        sys.exit(0)
    
    # Handle progress monitoring options
    show_progress = args.show_progress and not args.no_progress
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    # Validate input file
    if not args.input.exists():
        logger.error(f"Input file not found: {args.input}")
        sys.exit(1)
    
    # Check if file has a video extension
    supported_formats = ['.mkv', '.mp4', '.mov', '.avi', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', '.3gp', '.ts', '.mts', '.m2ts']
    if args.input.suffix.lower() not in supported_formats:
        logger.error(f"Unsupported video format: {args.input.suffix}")
        logger.info(f"Supported formats: {', '.join(supported_formats)}")
        sys.exit(1)
    
    # Initialize converter
    converter = VideoToM3U8Converter(args.output)
    
    # Run conversion
    success = converter.convert(args.input, args.id, args.presets, hide_banner=args.hide_banner, show_progress=show_progress, show_specs=args.show_specs)
    
    if success:
        video_id = args.id if args.id else args.input.stem
        logger.info("=" * 70)
        logger.phase_end("CONVERSION COMPLETED SUCCESSFULLY!")
        logger.info(f"Output directory: {args.output / video_id}")
        logger.info("=" * 70)
        sys.exit(0)
    else:
        logger.info("=" * 70)
        logger.error("CONVERSION FAILED!")
        logger.info("=" * 70)
        sys.exit(1)

if __name__ == "__main__":
    main()
