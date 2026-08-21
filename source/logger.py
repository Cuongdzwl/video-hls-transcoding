#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Structured Logger Module
========================

Provides color-coded structured logging for command-line tools.

Log Types:
----------
[Info]     - General information (cyan)
[Metadata] - File metadata and technical specs (bright blue)
[Command]  - Commands being executed (magenta)
[Phase]    - Phase/task starting (bright yellow/orange)
[Complete] - Phase/task completed successfully (bright green)
[Progress] - Progress updates (cyan)
[Warning]  - Warnings (yellow)
[Error]    - Errors (red)
[Debug]    - Debug information (gray)

Author: GitHub Copilot
Date: October 29, 2025
"""

import logging
import sys


class Colors:
    """ANSI color codes for terminal output"""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    # Regular colors
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    
    # Bright colors
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'
    
    # Background colors
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    BG_ORANGE = '\033[48;5;208m'


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for different log levels"""
    
    FORMATS = {
        'INFO': f"{Colors.CYAN}[Info]{Colors.RESET}     %(message)s",
        'WARNING': f"{Colors.YELLOW}[Warning]{Colors.RESET}  %(message)s",
        'ERROR': f"{Colors.RED}[Error]{Colors.RESET}    %(message)s",
        'DEBUG': f"{Colors.BRIGHT_BLACK}[Debug]{Colors.RESET}    %(message)s",
        'CRITICAL': f"{Colors.BG_RED}{Colors.WHITE}[Critical]{Colors.RESET} %(message)s",
        'COMMAND': f"{Colors.MAGENTA}[Command]{Colors.RESET}  %(message)s",
        'PHASE_START': f"{Colors.BRIGHT_YELLOW}[Phase]{Colors.RESET}    %(message)s",
        'PHASE_END': f"{Colors.BRIGHT_GREEN}[Complete]{Colors.RESET} %(message)s",
        'METADATA': f"{Colors.BRIGHT_BLUE}[Metadata]{Colors.RESET} %(message)s",
        'PROGRESS': f"{Colors.CYAN}[Progress]{Colors.RESET} %(message)s",
    }
    
    def format(self, record):
        # Get the format based on log level or custom attribute
        log_format = self.FORMATS.get(record.levelname, self.FORMATS['INFO'])
        
        # Check for custom log type
        if hasattr(record, 'log_type'):
            log_format = self.FORMATS.get(record.log_type, log_format)
        
        formatter = logging.Formatter(log_format)
        return formatter.format(record)


class StructuredLogger:
    """Structured logger with custom log types"""
    
    # Make Colors accessible as a class attribute
    colors = Colors
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        # Also make colors accessible as instance attribute
        self.colors = Colors
        
        if not self.logger.handlers:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(ColoredFormatter())
            self.logger.addHandler(console_handler)
    
    def info(self, message: str):
        """Standard information message"""
        self.logger.info(message)
    
    def warning(self, message: str):
        """Warning message"""
        self.logger.warning(message)
    
    def error(self, message: str):
        """Error message"""
        self.logger.error(message)
    
    def debug(self, message: str):
        """Debug message"""
        self.logger.debug(message)
    
    def critical(self, message: str):
        """Critical error message"""
        self.logger.critical(message)
    
    def command(self, message: str):
        """Command execution message"""
        record = self.logger.makeRecord(
            self.logger.name, logging.INFO, "", 0, message, (), None
        )
        record.log_type = 'COMMAND'
        self.logger.handle(record)
    
    def phase_start(self, message: str):
        """Phase start message (orange/yellow)"""
        record = self.logger.makeRecord(
            self.logger.name, logging.INFO, "", 0, message, (), None
        )
        record.log_type = 'PHASE_START'
        self.logger.handle(record)
    
    def phase_end(self, message: str):
        """Phase completion message (green)"""
        record = self.logger.makeRecord(
            self.logger.name, logging.INFO, "", 0, message, (), None
        )
        record.log_type = 'PHASE_END'
        self.logger.handle(record)
    
    def metadata(self, message: str):
        """Metadata information message"""
        record = self.logger.makeRecord(
            self.logger.name, logging.INFO, "", 0, message, (), None
        )
        record.log_type = 'METADATA'
        self.logger.handle(record)
    
    def progress(self, message: str):
        """Progress update message"""
        record = self.logger.makeRecord(
            self.logger.name, logging.INFO, "", 0, message, (), None
        )
        record.log_type = 'PROGRESS'
        self.logger.handle(record)
    
    def addHandler(self, handler):
        """Add handler to logger"""
        self.logger.addHandler(handler)
    
    def setLevel(self, level):
        """Set logging level"""
        self.logger.setLevel(level)


def get_logger(name: str) -> StructuredLogger:
    """Get a structured logger instance"""
    return StructuredLogger(name)
