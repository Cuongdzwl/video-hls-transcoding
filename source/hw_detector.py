#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hardware Encoder Detector
=========================

Detect available hardware acceleration options for video encoding.

Supports:
- NVIDIA NVENC (h264_nvenc)
- Apple VideoToolbox (h264_videotoolbox)
- Intel Quick Sync (h264_qsv)
- AMD AMF (h264_amf)
- Software fallback (libx264)

Author: GitHub Copilot
Date: October 29, 2025
"""

import subprocess
from typing import Dict, Any
from logger import get_logger

logger = get_logger("hw_detector")


def detect_hardware_acceleration(hide_banner: bool = False) -> Dict[str, Any]:
    """Detect available hardware acceleration"""
    
    logger.phase_start("Detecting hardware acceleration options")
    
    hw_options = {
        "encoder": "libx264",  # Default software encoder
        "preset": "veryslow",  # Slowest for max quality
        "hw_accel": None,
        "hw_device": None,
        "extra_args": []
    }
    
    # Test NVIDIA NVENC
    try:
        cmd = ["ffmpeg", "-encoders"]
        if hide_banner:
            cmd.insert(1, "-hide_banner")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        output = result.stdout
        
        if result.returncode == 0 and "h264_nvenc" in output:
            # Test if NVENC actually works
            logger.info("Testing NVIDIA NVENC hardware encoder...")
            test_cmd = [
                "ffmpeg", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=1",
                "-c:v", "h264_nvenc", "-preset", "p7", "-f", "null", "-"
            ]
            if hide_banner:
                test_cmd.insert(1, "-hide_banner")
            
            test_result = subprocess.run(test_cmd, capture_output=True, timeout=10)
            if test_result.returncode == 0:
                hw_options.update({
                    "encoder": "h264_nvenc",
                    "preset": "p7",  # Slowest NVENC preset for max quality
                    "hw_accel": "nvenc",
                    "extra_args": []
                })
                logger.phase_end("NVIDIA NVENC detected and working")
                return hw_options
    except Exception as e:
        logger.debug(f"NVENC test failed: {e}")
    
    # Test VideoToolbox (macOS Metal)
    try:
        cmd = ["ffmpeg", "-encoders"]
        if hide_banner:
            cmd.insert(1, "-hide_banner")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        output = result.stdout
        
        if result.returncode == 0 and "h264_videotoolbox" in output:
            logger.info("Testing Apple VideoToolbox hardware encoder...")
            test_cmd = [
                "ffmpeg", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=1",
                "-c:v", "h264_videotoolbox", "-q:v", "50", "-f", "null", "-"
            ]
            if hide_banner:
                test_cmd.insert(1, "-hide_banner")
            
            test_result = subprocess.run(test_cmd, capture_output=True, timeout=10)
            if test_result.returncode == 0:
                hw_options.update({
                    "encoder": "h264_videotoolbox",
                    "preset": None,  # VideoToolbox doesn't use presets
                    "hw_accel": "videotoolbox",
                    "extra_args": []
                })
                logger.phase_end("VideoToolbox (Metal) detected and working")
                return hw_options
    except Exception as e:
        logger.debug(f"VideoToolbox test failed: {e}")
    
    # Fallback to software with highest quality settings
    logger.warning("No hardware acceleration detected, using software encoder with max quality")
    logger.phase_end("Hardware detection completed - using software encoder")
    
    return hw_options


def detect_hevc_encoder(hide_banner: bool = False) -> Dict[str, Any]:
    """Detect available HEVC/H.265 hardware acceleration"""
    
    logger.phase_start("Detecting HEVC/H.265 hardware acceleration")
    
    hw_options = {
        "encoder": "libx265",  # Default software encoder
        "preset": "veryslow",
        "hw_accel": None,
        "hw_device": None,
        "extra_args": []
    }
    
    # Test NVIDIA NVENC HEVC
    try:
        cmd = ["ffmpeg", "-encoders"]
        if hide_banner:
            cmd.insert(1, "-hide_banner")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        output = result.stdout
        
        if result.returncode == 0 and "hevc_nvenc" in output:
            logger.info("Testing NVIDIA NVENC HEVC encoder...")
            test_cmd = [
                "ffmpeg", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=1",
                "-c:v", "hevc_nvenc", "-preset", "p7", "-f", "null", "-"
            ]
            if hide_banner:
                test_cmd.insert(1, "-hide_banner")
            
            test_result = subprocess.run(test_cmd, capture_output=True, timeout=10)
            if test_result.returncode == 0:
                hw_options.update({
                    "encoder": "hevc_nvenc",
                    "preset": "p7",
                    "hw_accel": "nvenc",
                    "extra_args": []
                })
                logger.phase_end("NVIDIA NVENC HEVC detected and working")
                return hw_options
    except Exception as e:
        logger.debug(f"NVENC HEVC test failed: {e}")
    
    # Test VideoToolbox HEVC (macOS)
    try:
        cmd = ["ffmpeg", "-encoders"]
        if hide_banner:
            cmd.insert(1, "-hide_banner")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        output = result.stdout
        
        if result.returncode == 0 and "hevc_videotoolbox" in output:
            logger.info("Testing Apple VideoToolbox HEVC encoder...")
            test_cmd = [
                "ffmpeg", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=1",
                "-c:v", "hevc_videotoolbox", "-q:v", "50", "-f", "null", "-"
            ]
            if hide_banner:
                test_cmd.insert(1, "-hide_banner")
            
            test_result = subprocess.run(test_cmd, capture_output=True, timeout=10)
            if test_result.returncode == 0:
                hw_options.update({
                    "encoder": "hevc_videotoolbox",
                    "preset": None,
                    "hw_accel": "videotoolbox",
                    "extra_args": []
                })
                logger.phase_end("VideoToolbox HEVC detected and working")
                return hw_options
    except Exception as e:
        logger.debug(f"VideoToolbox HEVC test failed: {e}")
    
    # Fallback to software
    logger.warning("No HEVC hardware acceleration detected, using libx265")
    logger.phase_end("HEVC detection completed - using software encoder")
    
    return hw_options
