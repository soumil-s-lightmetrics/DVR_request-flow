import re
import logging
from logging.handlers import RotatingFileHandler
import os
from pythonjsonlogger import jsonlogger
from datetime import datetime, timezone

# Constants
LOG_FOLDER = os.path.join(os.getcwd(), os.getenv("LOGS_DIR", "logs"))
ACCESS_LOG_FILE = os.path.join(LOG_FOLDER, 'access.log')
DEBUG_LOG_FILE = os.path.join(LOG_FOLDER, 'debug.log')
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
LOG_BACKUP_COUNT = 3

authSchemeRegex = re.compile(r"^[^ ]*")
basicCredentialsRegex = re.compile(r"^basic *([^ ]*)", re.IGNORECASE)

# Ensure log folder exists
os.makedirs(LOG_FOLDER, exist_ok=True)

# Memoization cache for loggers
_logger_cache = {}

def get_env_var(key, default=None):
    """Helper to fetch environment variables with a default value."""
    return os.getenv(key, default)

def get_log_formatter():
    """Returns the appropriate log formatter."""
    return logging.Formatter(
        '%(asctime)s | %(name)s | %(levelname)s | %(message)s',
        defaults={"requestId": "N/A"}
    )

def get_log_handler(filename, level, formatter):
    """Creates and returns a RotatingFileHandler."""
    handler = RotatingFileHandler(
        filename, maxBytes=LOG_FILE_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler

def configure_logger(logger_name, handlers, level=logging.DEBUG):
    """Configures and returns a logger with the specified handlers."""
    logger = logging.getLogger(logger_name)
    if logger.hasHandlers():
        return logger  # Avoid adding duplicate handlers

    logger.setLevel(level)
    for handler in handlers:
        logger.addHandler(handler)

    # Add stream handler if LOG_TO_STDOUT is enabled
    if get_env_var("LOG_TO_STDOUT", "false").lower() == "true":
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(get_log_formatter())
        logger.addHandler(stream_handler)

    return logger