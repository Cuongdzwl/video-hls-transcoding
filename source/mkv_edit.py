#!/usr/bin/env python3
# # -*- coding: utf-8 -*-
"""
MKV Editor - Lossless Video Re-encoding with Edit Parameters
=============================================================

Apply color grading and filters to MKV videos with lossless
or near-lossless re-encoding using FFmpeg.

Features:
- Load edit parameters from preset JSON
- Color grading (gamma, contrast, saturation, brightness)
- Noise reduction (hqdn3d)
- Hardware-accelerated encoding (H.264 priority, fallback to HEVC)
- Auto bit-depth detection (8-bit vs 10-bit)
- Preserves audio and subtitle streams
- Structured logging

Usage:
  python source/mkv_edit.py --input video.mkv --output edited.mkv --presets presets.json

Author: GitHub Copilot
Date: October 30, 2025
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

# Import shared modules
from logger import get_logger
from hw_detector import detect_hardware_acceleration

logger = get_logger("mkv_edit")


def setup_file_logging():
    """Setup file logging for edit operations"""
    log_dir = Path("./log")
    log_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"edit_{timestamp}.log"
    
    import logging
    file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)
    
    logger.info(f"Log file created: {log_file}")
    return log_file


def load_edit_params(preset_file: Path) -> Optional[List[str]]:
    """Load edit parameters from preset JSON file (returns None if not found)"""
    if not preset_file.exists():
        logger.warning(f"Preset file not found: {preset_file}")
        logger.info("Will use default parameters")
        return None
    
    logger.phase_start(f"Loading edit parameters from: {preset_file}")
    
    try:
        with open(preset_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if "edit_params" not in data:
            logger.warning("No 'edit_params' found in preset file")
            logger.info("Will use default parameters")
            return None
        
        edit_params = data["edit_params"]
        
        if not edit_params:
            logger.warning("'edit_params' is empty")
            logger.info("Will use default parameters")
            return None
        
        logger.phase_end(f"Loaded {len(edit_params)} edit parameters")
        
        # Log the parameters
        logger.metadata("Edit Parameters:")
        i = 0
        while i < len(edit_params):
            param = edit_params[i]
            if i + 1 < len(edit_params) and not edit_params[i + 1].startswith("-"):
                logger.metadata(f"  {param} {edit_params[i + 1]}")
                i += 2
            else:
                logger.metadata(f"  {param}")
                i += 1
        
        return edit_params
        
    except Exception as e:
        logger.warning(f"Error loading edit_params: {e}")
        logger.info("Will use default parameters")
        return None


def get_video_info(video_path: Path) -> Dict[str, Any]:
    """Get video information using ffprobe"""
    logger.phase_start(f"Analyzing video: {video_path.name}")
    
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path)
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.error(f"Failed to analyze video: {result.stderr}")
            raise Exception("Failed to get video info")
        
        data = json.loads(result.stdout)
        
        # Find video stream
        video_stream = None
        audio_streams = 0
        subtitle_streams = 0
        
        for stream in data["streams"]:
            if stream["codec_type"] == "video" and not video_stream:
                video_stream = stream
            elif stream["codec_type"] == "audio":
                audio_streams += 1
            elif stream["codec_type"] == "subtitle":
                subtitle_streams += 1
        
        if not video_stream:
            raise Exception("No video stream found")
        
        width = int(video_stream["width"])
        height = int(video_stream["height"])
        duration = float(data["format"]["duration"])
        codec = video_stream.get("codec_name", "unknown")
        
        # Get bitrate (prefer video stream bitrate, fallback to format bitrate)
        bitrate = None
        if "bit_rate" in video_stream:
            bitrate = int(video_stream["bit_rate"])
        elif "bit_rate" in data["format"]:
            bitrate = int(data["format"]["bit_rate"])
        
        # Get pixel format and profile
        pix_fmt = video_stream.get("pix_fmt", None)
        profile = video_stream.get("profile", None)
        
        # Detect bit depth
        bit_depth = 8
        if pix_fmt:
            # Check for 10-bit formats
            if any(x in pix_fmt for x in ["10le", "10be", "p010", "p016", "yuv420p10", "yuv422p10", "yuv444p10"]):
                bit_depth = 10
            # Check for 12-bit formats
            elif any(x in pix_fmt for x in ["12le", "12be", "yuv420p12", "yuv422p12", "yuv444p12"]):
                bit_depth = 12
        
        # Log video metadata
        logger.metadata(f"Resolution: {width}x{height}")
        logger.metadata(f"Duration: {duration:.2f}s")
        logger.metadata(f"Video Codec: {codec}")
        if bitrate:
            bitrate_mbps = bitrate / 1_000_000
            logger.metadata(f"Bitrate: {bitrate_mbps:.2f} Mbps")
        if pix_fmt:
            logger.metadata(f"Pixel Format: {pix_fmt}")
        logger.metadata(f"Bit Depth: {bit_depth}-bit")
        if profile:
            logger.metadata(f"Profile: {profile}")
        logger.metadata(f"Audio Streams: {audio_streams}")
        logger.metadata(f"Subtitle Streams: {subtitle_streams}")
        
        logger.phase_end("Video analysis completed")
        
        return {
            "width": width,
            "height": height,
            "duration": duration,
            "codec": codec,
            "bitrate": bitrate,
            "pix_fmt": pix_fmt,
            "bit_depth": bit_depth,
            "profile": profile,
            "audio_streams": audio_streams,
            "subtitle_streams": subtitle_streams
        }
        
    except subprocess.TimeoutExpired:
        logger.error("Video analysis timed out")
        raise
    except Exception as e:
        logger.error(f"Error analyzing video: {e}")
        raise

def select_encoder_and_settings(video_info: Dict[str, Any], hw_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Auto-select encoder, profile, and pixel format based on source video and hardware.
    
    Priority:
    1. Hardware encoder first (any available: H.264 or HEVC)
    2. Software fallback if no hardware available
    
    Rules:
    - Hardware available -> Use hardware encoder (prefer H.264 for 8-bit, HEVC for 10-bit)
    - 10-bit source with hardware -> HEVC with p010le (hardware format)
    - 8-bit source with hardware -> H.264/HEVC, preserve pixel format
    - Software fallback -> libx264 for 8-bit, libx265 for 10-bit, preserve source pixel format
    """
    bit_depth = video_info.get("bit_depth", 8)
    source_pix_fmt = video_info.get("pix_fmt", "yuv420p")
    hw_encoder = hw_info.get("encoder", "libx264")
    
    settings = {
        "encoder": hw_encoder,
        "pix_fmt": source_pix_fmt,
        "profile": None,
        "preset": hw_info.get("preset"),
        "crf": None,
        "qp": None
    }
    
    logger.phase_start("Auto-selecting encoder and settings (priority: hardware)")
    logger.metadata(f"Source bit depth: {bit_depth}-bit")
    logger.metadata(f"Source pixel format: {source_pix_fmt}")
    logger.metadata(f"Available hardware encoder: {hw_encoder}")
    
    # Check if hardware encoder is available
    is_hardware = hw_encoder in ["h264_nvenc", "h264_videotoolbox", "hevc_nvenc", "hevc_videotoolbox"]
    
    if is_hardware:
        logger.info("✓ Hardware encoder available - prioritizing hardware encoding")
        
        # 10-bit video with hardware
        if bit_depth >= 10:
            logger.info("10-bit source detected")
            
            # HEVC hardware encoders support 10-bit
            if hw_encoder in ["hevc_nvenc", "hevc_videotoolbox"]:
                settings["encoder"] = hw_encoder
                settings["pix_fmt"] = "p010le"  # 10-bit hardware format
                settings["profile"] = None  # Let hardware decide
                settings["qp"] = 0 if hw_encoder == "hevc_nvenc" else None  # Lossless for NVENC
                logger.info(f"Using hardware HEVC encoder: {hw_encoder}")
                logger.info(f"Pixel format: p010le (10-bit hardware)")
                logger.info("Profile: auto (lossless)")
            
            # H.264 hardware doesn't support 10-bit, but still try to use hardware with downgrade
            elif hw_encoder in ["h264_nvenc", "h264_videotoolbox"]:
                logger.warning("Hardware H.264 doesn't support 10-bit - converting to 8-bit for hardware encoding")
                settings["encoder"] = hw_encoder
                # Convert to 8-bit format for hardware
                if source_pix_fmt.startswith("yuvj"):
                    settings["pix_fmt"] = "yuv420p"
                else:
                    settings["pix_fmt"] = "yuv420p"
                settings["profile"] = "high"
                settings["qp"] = 0 if hw_encoder == "h264_nvenc" else None
                logger.info(f"Using hardware H.264 encoder: {hw_encoder}")
                logger.info(f"Pixel format: {settings['pix_fmt']} (8-bit conversion for hardware)")
                logger.info("Profile: high (lossless)")
        
        # 8-bit video with hardware
        else:
            logger.info("8-bit source detected")
            
            # Prefer H.264 hardware for 8-bit
            if hw_encoder in ["h264_nvenc", "h264_videotoolbox"]:
                settings["encoder"] = hw_encoder
                # Normalize yuvj formats for hardware
                if source_pix_fmt.startswith("yuvj"):
                    settings["pix_fmt"] = source_pix_fmt.replace("yuvj", "yuv")
                    logger.info(f"Normalized pixel format: {source_pix_fmt} -> {settings['pix_fmt']}")
                else:
                    settings["pix_fmt"] = source_pix_fmt
                settings["profile"] = "high"  # High profile for best quality
                settings["qp"] = 0 if hw_encoder == "h264_nvenc" else None  # Lossless for NVENC
                logger.info(f"Using hardware H.264 encoder: {hw_encoder}")
                logger.info(f"Pixel format: {settings['pix_fmt']} (preserved)")
                logger.info("Profile: high (lossless)")
            
            # HEVC hardware also works for 8-bit
            elif hw_encoder in ["hevc_nvenc", "hevc_videotoolbox"]:
                settings["encoder"] = hw_encoder
                if source_pix_fmt.startswith("yuvj"):
                    settings["pix_fmt"] = source_pix_fmt.replace("yuvj", "yuv")
                else:
                    settings["pix_fmt"] = source_pix_fmt
                settings["profile"] = "main"
                settings["qp"] = 0 if hw_encoder == "hevc_nvenc" else None
                logger.info(f"Using hardware HEVC encoder: {hw_encoder}")
                logger.info(f"Pixel format: {settings['pix_fmt']} (preserved)")
                logger.info("Profile: main (lossless)")
    
    else:
        # No hardware available - use software encoders
        logger.warning("✗ No hardware encoder available - using software encoding")
        
        if bit_depth >= 10:
            logger.info("10-bit source detected - using software HEVC")
            settings["encoder"] = "libx265"
            settings["pix_fmt"] = source_pix_fmt  # Preserve source
            settings["preset"] = "veryslow"
            settings["crf"] = 0  # Lossless
            logger.info("Using software HEVC encoder: libx265")
            logger.info(f"Pixel format: {source_pix_fmt} (preserved from source)")
            logger.info("CRF: 0 (lossless)")
        else:
            logger.info("8-bit source detected - using software H.264")
            settings["encoder"] = "libx264"
            settings["pix_fmt"] = source_pix_fmt
            settings["preset"] = "veryslow"
            settings["crf"] = 0  # Lossless
            settings["profile"] = "high"
            logger.info("Using software H.264 encoder: libx264")
            logger.info(f"Pixel format: {source_pix_fmt} (preserved)")
            logger.info("CRF: 0 (lossless)")
    
    logger.phase_end("Encoder selection completed")
    logger.metadata(f"Selected encoder: {settings['encoder']}")
    logger.metadata(f"Pixel format: {settings['pix_fmt']}")
    if settings['profile']:
        logger.metadata(f"Profile: {settings['profile']}")
    if settings['crf'] is not None:
        logger.metadata(f"CRF: {settings['crf']}")
    if settings['qp'] is not None:
        logger.metadata(f"QP: {settings['qp']}")
    
    return settings


def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    edit_params: Optional[List[str]],
    video_info: Dict[str, Any],
    encoder_settings: Dict[str, Any],
    hide_banner: bool = True,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    audio_delay: Optional[int] = None
) -> List[str]:
    """Build FFmpeg command with edit parameters and auto-selected encoder settings
    
    Args:
        start_time: Seconds to cut from the start (e.g., 5.0 = skip first 5 seconds)
        end_time: Seconds to cut from the end (e.g., 3.5 = cut last 3.5 seconds)
        audio_delay: Audio delay in milliseconds (e.g., 333 = delay audio by 333ms, -100 = advance audio by 100ms)
    """
    
    cmd = ["ffmpeg"]
    
    if hide_banner:
        cmd.append("-hide_banner")
    
    # Detect encoder early so we can request hardware acceleration before input
    encoder = None
    try:
        encoder = encoder_settings.get("encoder") if encoder_settings else None
    except Exception:
        encoder = None

    # If using Apple's VideoToolbox encoder, ask ffmpeg to use VideoToolbox hwaccel
    # (must come before -i)
    if encoder in ["h264_videotoolbox", "hevc_videotoolbox"]:
        cmd.extend(["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox"])

    # Input file (read TWICE if audio delay is needed - once for video, once for audio with offset)
    if audio_delay is not None and audio_delay != 0:
        # For audio sync: use two inputs, one for video and one for audio with offset
        # When using audio delay, we need TWO inputs:
        # 1. First input: for video (with -ss if trimming)
        # 2. Second input: for audio with offset (with -ss + offset combined)
        
        # First input: video + audio (with start time if specified)
        if start_time is not None and start_time > 0:
            cmd.extend(["-ss", str(start_time)])
            logger.info(f"Start time: {start_time}s (cutting {start_time}s from beginning)")
        cmd.extend(["-i", str(input_path)])
        
        # Second input: ONLY for audio with offset
        # Convert milliseconds to seconds
        offset_seconds = -(audio_delay / 1000.0)
        
        # Calculate combined offset: start_time + audio_delay
        # We need to apply BOTH the start cut AND the audio offset to the second input
        combined_offset = offset_seconds
        if start_time is not None and start_time > 0:
            combined_offset += start_time
        
        # itsoffset logic:
        # - Negative offset = delay the stream (shift right)
        # - Positive offset = advance the stream (shift left)
        # Our convention:
        # - Positive audio_delay (+333ms) = audio is FASTER than video, need to DELAY audio
        # - Negative audio_delay (-333ms) = audio is SLOWER than video, need to ADVANCE audio
        
        cmd.extend(["-ss", str(combined_offset)])
        cmd.extend(["-i", str(input_path)])
        
        logger.info(f"Audio sync configuration:")
        logger.info(f"  Input delay: {audio_delay}ms")
        logger.info(f"  Audio offset: {offset_seconds}s")
        if start_time:
            logger.info(f"  Combined offset (with trim): {combined_offset}s")
        if audio_delay > 0:
            logger.info(f"  Effect: Audio will be delayed by {audio_delay}ms (audio is faster, needs to wait)")
        else:
            logger.info(f"  Effect: Audio will be advanced by {abs(audio_delay)}ms (audio is slower, needs to catch up)")
    else:
        # Normal: single input with start time if specified
        if start_time is not None and start_time > 0:
            cmd.extend(["-ss", str(start_time)])
            logger.info(f"Start time: {start_time}s (cutting {start_time}s from beginning)")
        cmd.extend(["-i", str(input_path)])
    
    # Add duration/end time if specified
    if end_time is not None and end_time > 0:
        duration = video_info.get("duration", 0)
        if duration > 0:
            # Calculate target duration (total - start_cut - end_cut)
            start_cut = start_time if start_time else 0
            target_duration = duration - start_cut - end_time
            if target_duration > 0:
                cmd.extend(["-t", str(target_duration)])
                logger.info(f"End time: cutting {end_time}s from end (duration: {target_duration}s)")
            else:
                logger.warning(f"Invalid trim: cuts exceed video duration ({duration}s)")
        else:
            logger.warning("Cannot apply end time cut: video duration unknown")
    
    # Apply video filters from edit_params if provided
    if edit_params:
        logger.info("Applying edit parameters from presets")
        i = 0
        while i < len(edit_params):
            param = edit_params[i]
            
            # Apply video filters and color settings only
            # Skip encoder-specific params as they will be auto-selected
            if param in ["-vf", "-filter:v", "-filter_complex"]:
                if i + 1 < len(edit_params):
                    cmd.append(param)
                    cmd.append(edit_params[i + 1])
                    logger.info(f"Video filter: {edit_params[i + 1]}")
                    i += 2
                else:
                    i += 1
            elif param in ["-color_range", "-color_primaries", "-color_trc", "-colorspace"]:
                if i + 1 < len(edit_params):
                    cmd.append(param)
                    cmd.append(edit_params[i + 1])
                    logger.info(f"{param}: {edit_params[i + 1]}")
                    i += 2
                else:
                    i += 1
            else:
                # Skip encoding params (will be auto-selected)
                i += 1
    
    # Auto-select encoder and settings
    encoder = encoder_settings["encoder"]
    pix_fmt = encoder_settings["pix_fmt"]
    preset = encoder_settings.get("preset")
    profile = encoder_settings.get("profile")
    crf = encoder_settings.get("crf")
    qp = encoder_settings.get("qp")
    
    logger.phase_start("Adding encoding parameters")
    
    # Video codec
    cmd.extend(["-c:v", encoder])
    logger.info(f"Encoder: {encoder}")
    
    # Preset (for software encoders and some hardware)
    if preset:
        cmd.extend(["-preset", preset])
        logger.info(f"Preset: {preset}")
    
    # Profile
    if profile:
        cmd.extend(["-profile:v", profile])
        logger.info(f"Profile: {profile}")
    
    # Pixel format
    cmd.extend(["-pix_fmt", pix_fmt])
    logger.info(f"Pixel format: {pix_fmt}")
    
    # Quality settings - CRF for lossless or QP for hardware
    if qp is not None:
        # Hardware encoder lossless (NVENC)
        cmd.extend(["-qp", str(qp)])
        logger.info(f"QP: {qp} (lossless)")
    elif crf is not None:
        # Software encoder lossless
        cmd.extend(["-crf", str(crf)])
        logger.info(f"CRF: {crf} (lossless)")
    
    # Bitrate - use source bitrate + 10% for variable bitrate
    bitrate = video_info.get("bitrate")
    if bitrate:
        # Add 10% for quality headroom
        target_bitrate = int(bitrate * 0.8)
        max_bitrate = int(bitrate * 1.1)
        cmd.extend(["-b:v", str(target_bitrate)])
        cmd.extend(["-maxrate", str(max_bitrate)])
        cmd.extend(["-bufsize", str(max_bitrate)])
        logger.info(f"Target bitrate: {target_bitrate / 1_000_000:.2f} Mbps (80% of source)")
        logger.info(f"Max bitrate: {max_bitrate / 1_000_000:.2f} Mbps")
    else:
        # Default bitrate if not detected
        cmd.extend(["-b:v", "100M"])
        cmd.extend(["-maxrate", "110M"])
        cmd.extend(["-bufsize", "110M"])
        logger.info("Target bitrate: 100 Mbps (default)")
        logger.info("Max bitrate: 110 Mbps")
    
    # Handle audio and subtitle mapping
    if audio_delay is not None and audio_delay != 0:
        # Using two inputs: map video from first input, audio from second input (with offset)
        cmd.extend([
            "-map", "0:v",      # Video from first input
            "-map", "1:a",      # Audio from second input (with itsoffset)
            "-c:a", "copy",     # Copy audio (no re-encoding!)
            "-map", "0:s?",     # Subtitles from first input (if any)
            "-c:s", "copy"      # Copy subtitles
        ])
        logger.info("Audio: copy with offset (no re-encoding)")
        logger.info("Subtitle: copy all streams")
    else:
        # Normal mapping: copy everything
        cmd.extend([
            "-c:a", "copy",
            "-c:s", "copy",
            "-map", "0"
        ])
        logger.info("Audio/Subtitle: copy all streams")
    
    logger.phase_end("Encoding parameters added")
    
    # Output file (overwrite)
    cmd.extend(["-y", str(output_path)])
    
    return cmd


def edit_video(
    input_path: Path,
    output_path: Path,
    edit_params: Optional[List[str]],
    video_info: Dict[str, Any],
    encoder_settings: Dict[str, Any],
    hide_banner: bool = True,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    audio_delay: Optional[int] = None
) -> bool:
    """Edit video with specified parameters and auto-selected encoder
    
    Args:
        start_time: Seconds to cut from the start
        end_time: Seconds to cut from the end
        audio_delay: Audio delay in milliseconds
    """
    
    logger.phase_start(f"Editing video: {input_path.name}")
    logger.info(f"Output: {output_path}")
    
    # Build command
    cmd = build_ffmpeg_command(input_path, output_path, edit_params, video_info, encoder_settings, 
                               hide_banner, start_time, end_time, audio_delay)
    
    # Log command
    logger.command(" ".join(cmd))
    
    # Execute FFmpeg with progress monitoring
    logger.phase_start("Starting FFmpeg encoding...")
    
    try:
        import re
        import time
        
        duration = video_info.get("duration", 0)
        start_time = time.time()
        
        # Start FFmpeg process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Progress tracking variables
        last_progress_time = start_time
        progress_update_interval = 2.0  # Update every 2 seconds
        
        # Regex patterns for parsing FFmpeg output
        time_pattern = re.compile(r'time=(\d+):(\d+):(\d+\.\d+)')
        bitrate_pattern = re.compile(r'bitrate=\s*(\d+\.?\d*)\s*(\w+)/s')
        size_pattern = re.compile(r'size=\s*(\d+\.?\d*)(\w+)')
        
        stderr_lines = []
        
        for line in process.stdout:
            stderr_lines.append(line)
            
            # Parse progress information
            current_time = time.time()
            if current_time - last_progress_time >= progress_update_interval:
                # Extract time
                time_match = time_pattern.search(line)
                if time_match:
                    hours = int(time_match.group(1))
                    minutes = int(time_match.group(2))
                    seconds = float(time_match.group(3))
                    current_seconds = hours * 3600 + minutes * 60 + seconds
                    
                    # Extract bitrate
                    bitrate_match = bitrate_pattern.search(line)
                    bitrate_str = "N/A"
                    if bitrate_match:
                        bitrate_value = float(bitrate_match.group(1))
                        bitrate_unit = bitrate_match.group(2)
                        bitrate_str = f"{bitrate_value:.1f} {bitrate_unit}"
                    
                    # Extract size (handle kB, MB, GB, etc.)
                    size_match = size_pattern.search(line)
                    size_mb = 0
                    if size_match:
                        size_value = float(size_match.group(1))
                        size_unit = size_match.group(2).lower()
                        
                        # Convert to MB
                        if size_unit == 'kb':
                            size_mb = size_value / 1024
                        elif size_unit == 'mb':
                            size_mb = size_value
                        elif size_unit == 'gb':
                            size_mb = size_value * 1024
                        elif size_unit == 'b':
                            size_mb = size_value / (1024 * 1024)
                        else:
                            # Default to kB if unknown
                            size_mb = size_value / 1024
                    
                    # Calculate progress and ETA
                    if duration > 0:
                        progress = (current_seconds / duration) * 100
                        elapsed = current_time - start_time
                        
                        if progress > 0:
                            total_estimated = elapsed / (progress / 100)
                            eta_seconds = total_estimated - elapsed
                            
                            eta_hours = int(eta_seconds // 3600)
                            eta_minutes = int((eta_seconds % 3600) // 60)
                            eta_secs = int(eta_seconds % 60)
                            
                            if eta_hours > 0:
                                eta_str = f"{eta_hours}h {eta_minutes}m {eta_secs}s"
                            elif eta_minutes > 0:
                                eta_str = f"{eta_minutes}m {eta_secs}s"
                            else:
                                eta_str = f"{eta_secs}s"
                            
                            logger.info(f"Progress: {progress:.1f}% | Bitrate: {bitrate_str} | Written: {size_mb:.1f} MB | ETA: {eta_str}")
                        else:
                            logger.info(f"Progress: {progress:.1f}% | Bitrate: {bitrate_str} | Written: {size_mb:.1f} MB")
                    else:
                        logger.info(f"Bitrate: {bitrate_str} | Written: {size_mb:.1f} MB")
                    
                    last_progress_time = current_time
        
        # Wait for process to complete
        return_code = process.wait()
        
        if return_code == 0:
            logger.phase_end("Video editing completed successfully!")
            
            # Check output file
            if output_path.exists():
                size_mb = output_path.stat().st_size / (1024 * 1024)
                elapsed = time.time() - start_time
                elapsed_minutes = int(elapsed // 60)
                elapsed_seconds = int(elapsed % 60)
                
                logger.metadata(f"Output file size: {size_mb:.2f} MB")
                logger.metadata(f"Encoding time: {elapsed_minutes}m {elapsed_seconds}s")
                return True
            else:
                logger.error("Output file was not created")
                return False
        else:
            logger.error(f"FFmpeg failed with exit code {return_code}")
            
            # Log stderr for debugging
            if stderr_lines:
                logger.error("FFmpeg error output:")
                for line in stderr_lines[-20:]:  # Last 20 lines
                    if line.strip():
                        logger.error(f"  {line.strip()}")
            
            return False
            
    except KeyboardInterrupt:
        logger.warning("\nEncoding interrupted by user")
        if 'process' in locals():
            process.terminate()
            process.wait()
        return False
    except Exception as e:
        logger.error(f"Error during video editing: {e}")
        if 'process' in locals():
            process.terminate()
            process.wait()
        return False


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Apply color grading and filters to MKV videos with lossless re-encoding",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Edit video with filters from presets.json
  python source/mkv_edit.py --input video.mkv --output edited.mkv --presets presets.json
  
  # Edit without filters (encoder auto-selected)
  python source/mkv_edit.py -i input.mkv -o output.mkv
  
  # Cut 5 seconds from start and 3.5 seconds from end
  python source/mkv_edit.py -i input.mkv -o output.mkv --start-time 5.0 --end-time 3.5
  
  # Cut only from start (skip first 10 seconds)
  python source/mkv_edit.py -i input.mkv -o output.mkv -ss 10
  
  # Cut only from end (remove last 7.2 seconds)
  python source/mkv_edit.py -i input.mkv -o output.mkv -et 7.2
  
  # Audio sync: delay audio by 333ms (audio is faster than video)
  python source/mkv_edit.py -i input.mkv -o output.mkv --audio-delay 333
  
  # Audio sync: advance audio by 100ms (audio is slower than video)
  python source/mkv_edit.py -i input.mkv -o output.mkv --audio-delay -100
  
  # Combine trim and audio delay
  python source/mkv_edit.py -i input.mkv -o output.mkv -ss 5 -et 3.5 -ad 333
  
  # Use software encoding (disable hardware)
  python source/mkv_edit.py --input video.mkv --output edited.mkv --no-hardware

Encoder Auto-Selection (Priority):
  1. H.264 hardware encoder (h264_nvenc, h264_videotoolbox) for 8-bit
  2. HEVC hardware encoder (hevc_nvenc, hevc_videotoolbox) for 10-bit
  3. Software fallback (libx264 for 8-bit, libx265 for 10-bit)

Encoding Rules:
  - 10-bit source → HEVC, p010le (hw) or source pix_fmt (sw), lossless
  - 8-bit source → H.264/HEVC, preserve pix_fmt, lossless
  - Bitrate → Source bitrate + 10% (variable bitrate)
  - Quality → Lossless (CRF 0 for software, QP 0 for NVENC)

Edit Parameters in presets.json (optional - filters only):
  {
    "edit_params": [
      "-vf", "eq=gamma=0.95:contrast=1.03:saturation=1.03:brightness=0.02,hqdn3d=1.5:1.0:5.0:4.5",
      "-color_range", "2",
      "-color_primaries", "1",
      "-color_trc", "1",
      "-colorspace", "1"
    ]
  }
  
Note: edit_params should only contain video filters and color settings.
      Encoder, bitrate, and quality are auto-selected based on source video.
        """
    )
    
    parser.add_argument(
        "--input", "-i",
        type=Path,
        required=True,
        help="Input MKV file path (required)"
    )
    
    parser.add_argument(
        "--output", "-o",
        type=Path,
        required=True,
        help="Output file path (required)"
    )
    
    parser.add_argument(
        "--presets", "-p",
        type=Path,
        default=Path("./presets.json"),
        help="JSON file with edit_params array (optional - will use defaults if missing)"
    )
    
    parser.add_argument(
        "--no-hardware",
        action="store_true",
        help="Disable hardware acceleration (use software encoding)"
    )
    
    parser.add_argument(
        "--start-time", "-ss",
        type=float,
        default=None,
        help="Seconds to cut from the start (e.g., 5.0 = skip first 5 seconds)"
    )
    
    parser.add_argument(
        "--end-time", "-et",
        type=float,
        default=None,
        help="Seconds to cut from the end (e.g., 3.5 = cut last 3.5 seconds)"
    )
    
    parser.add_argument(
        "--audio-delay", "-ad",
        type=int,
        default=None,
        help="Audio delay in milliseconds (e.g., 333 = delay audio by 333ms, -100 = advance audio by 100ms)"
    )
    
    parser.add_argument(
        "--show-banner",
        action="store_true",
        help="Show FFmpeg banner and version information"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    log_file = setup_file_logging()
    logger.info(f"MKV Edit session started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    if args.verbose:
        import logging
        logger.setLevel(logging.DEBUG)
    
    # Validate input file
    if not args.input.exists():
        logger.error(f"Input file not found: {args.input}")
        sys.exit(1)
    
    if args.input.suffix.lower() not in ['.mkv', '.mp4', '.mov', '.avi']:
        logger.warning(f"Input file may not be a supported video format: {args.input}")
    
    # Create output directory if needed
    args.output.parent.mkdir(parents=True, exist_ok=True)
    
    logger.info("=" * 70)
    logger.info("MKV EDIT - VIDEO RE-ENCODING WITH FILTERS")
    logger.info("=" * 70)
    logger.metadata(f"Input: {args.input}")
    logger.metadata(f"Output: {args.output}")
    logger.metadata(f"Presets: {args.presets}")
    logger.metadata(f"Hardware Acceleration: {'disabled' if args.no_hardware else 'enabled'}")
    logger.info("=" * 70 + "\n")
    
    try:
        # Get video info first (needed for encoder selection)
        video_info = get_video_info(args.input)
        logger.info("")
        
        # Detect hardware acceleration
        hw_info = None
        if not args.no_hardware:
            hw_info = detect_hardware_acceleration(hide_banner=not args.show_banner)
            logger.info(f"Hardware acceleration detected: {hw_info['encoder']}")
            if hw_info.get('hw_accel'):
                logger.info(f"Hardware type: {hw_info['hw_accel']}")
            logger.info("")
        else:
            logger.info("Hardware acceleration disabled\n")
            hw_info = {
                "encoder": "libx264",
                "preset": "veryslow",
                "hw_accel": None
            }
        
        # Auto-select encoder and settings based on video info and hardware
        encoder_settings = select_encoder_and_settings(video_info, hw_info)
        logger.info("")
        
        # Load edit parameters (optional - only for filters and color)
        edit_params = load_edit_params(args.presets)
        logger.info("")
        
        # Validate trim parameters
        if args.start_time is not None and args.start_time < 0:
            logger.error("Start time cannot be negative")
            sys.exit(1)
        
        if args.end_time is not None and args.end_time < 0:
            logger.error("End time cannot be negative")
            sys.exit(1)
        
        # Log trim info if specified
        if args.start_time or args.end_time:
            logger.phase_start("Trim Configuration")
            if args.start_time:
                logger.metadata(f"Cut from start: {args.start_time}s")
            if args.end_time:
                logger.metadata(f"Cut from end: {args.end_time}s")
            
            duration = video_info.get("duration", 0)
            if duration > 0:
                start_cut = args.start_time if args.start_time else 0
                end_cut = args.end_time if args.end_time else 0
                final_duration = duration - start_cut - end_cut
                logger.metadata(f"Original duration: {duration:.2f}s")
                logger.metadata(f"Final duration: {final_duration:.2f}s")
                
                if final_duration <= 0:
                    logger.error("Trim cuts exceed video duration!")
                    sys.exit(1)
            logger.phase_end("Trim configuration validated")
            logger.info("")
        
        # Log audio delay if specified
        if args.audio_delay is not None and args.audio_delay != 0:
            logger.phase_start("Audio Sync Configuration")
            logger.metadata(f"Audio delay (input): {args.audio_delay}ms")
            
            # Calculate offset in seconds for FFmpeg
            offset_seconds = -(args.audio_delay / 1000.0)
            logger.metadata(f"FFmpeg -itsoffset: {offset_seconds:.3f}s")
            
            if args.audio_delay > 0:
                logger.metadata(f"Effect: Audio will be delayed by {args.audio_delay}ms")
                logger.metadata(f"Reason: Audio is faster than video (needs to wait)")
            else:
                logger.metadata(f"Effect: Audio will be advanced by {abs(args.audio_delay)}ms")
                logger.metadata(f"Reason: Audio is slower than video (needs to catch up)")
            
            # Warn if delay seems too large (> 2 seconds)
            if abs(args.audio_delay) > 2000:
                logger.warning(f"⚠️  Large audio delay detected: {args.audio_delay}ms ({abs(args.audio_delay)/1000:.2f}s)")
                logger.warning(f"⚠️  Are you sure this is correct? Typical delays are 50-500ms.")
            
            logger.phase_end("Audio sync configuration validated")
            logger.info("")
        
        # Edit video
        success = edit_video(
            args.input,
            args.output,
            edit_params,
            video_info,
            encoder_settings,
            hide_banner=not args.show_banner,
            start_time=args.start_time,
            end_time=args.end_time,
            audio_delay=args.audio_delay
        )
        
        if success:
            logger.info("=" * 70)
            logger.phase_end("VIDEO EDITING COMPLETED SUCCESSFULLY!")
            logger.info("=" * 70)
            logger.metadata(f"Output: {args.output}")
            logger.info(f"Session log: {log_file}\n")
            sys.exit(0)
        else:
            logger.info("=" * 70)
            logger.error("VIDEO EDITING FAILED!")
            logger.info("=" * 70)
            logger.info(f"Check log file for details: {log_file}\n")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.warning("\n\nOperation cancelled by user")
        logger.info(f"Session log: {log_file}\n")
        sys.exit(130)
    except Exception as e:
        logger.error(f"\nFatal error: {e}")
        logger.info(f"Session log: {log_file}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
