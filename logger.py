import base64
import re
import logging
from logging.handlers import RotatingFileHandler
import os
from pythonjsonlogger import jsonlogger
from fastapi import Request
import time
from datetime import datetime, timezone

# Constants
LOG_FOLDER = os.path.join(os.getcwd(), os.getenv("LOGS_DIR", "logs"))
ACCESS_LOG_FILE = os.path.join(LOG_FOLDER, 'access.log')
DEBUG_LOG_FILE = os.path.join(LOG_FOLDER, 'debug.log')
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
LOG_BACKUP_COUNT = 3

# Environment variables
LOG_FORMAT = os.getenv("LOG_FORMAT", "human").lower()
LOG_TO_STDOUT = os.getenv("LOG_TO_STDOUT", "false").lower() == "true"
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
SERVICE_NAME = os.getenv("SERVICE_NAME", "dvr-fleet-assistant")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "unknown")

# Regex patterns
authSchemeRegex = re.compile(r"^[^ ]*")
basicCredentialsRegex = re.compile(r"^basic *([^ ]*)", re.IGNORECASE)

# Sensitive data patterns for redaction
SENSITIVE_PATTERNS = [
    (re.compile(r'"password"\s*:\s*"[^"]*"', re.IGNORECASE), '"password":"***REDACTED***"'),
    (re.compile(r'"api_key"\s*:\s*"[^"]*"', re.IGNORECASE), '"api_key":"***REDACTED***"'),
    (re.compile(r'"token"\s*:\s*"[^"]*"', re.IGNORECASE), '"token":"***REDACTED***"'),
    (re.compile(r'Bearer [A-Za-z0-9\-._~+/]+=*', re.IGNORECASE), 'Bearer ***REDACTED***'),
]

# Paths to skip in access logging (reduce noise)
SKIP_DEBUG_LOG_PATHS = {'/health-check', '/v1/dvr/health-check'}

# Ensure log folder exists
os.makedirs(LOG_FOLDER, exist_ok=True)

# Memoization caches
_logger_cache = {}
_formatter_cache = {}

# Third-party library loggers to configure
THIRD_PARTY_LOGGERS = {
    'httpx': logging.INFO,              # Used by AuthManager for OAuth2 token requests
    'httpcore': logging.WARNING,        # HTTP core library
    'urllib3': logging.WARNING,         # requests library HTTP client
    'uvicorn': logging.INFO,            # FastAPI server
    'uvicorn.access': logging.WARNING,  # Uvicorn access logs (we handle our own)
    'fastapi': logging.WARNING,         # FastAPI framework
    'openai': logging.WARNING,          # OpenAI SDK
    'langchain': logging.WARNING,       # LangChain
    'langgraph': logging.WARNING,       # LangGraph
}


def get_env_var(key, default=None):
    """Helper to fetch environment variables with a default value."""
    return os.getenv(key, default)


def configure_third_party_loggers():
    """
    Configure third-party library loggers to reduce noise.
    Sets appropriate log levels and prevents propagation to root logger.
    """
    for logger_name, level in THIRD_PARTY_LOGGERS.items():
        lib_logger = logging.getLogger(logger_name)
        lib_logger.setLevel(level)
        lib_logger.propagate = False

        if not lib_logger.handlers:
            handler = get_log_handler(DEBUG_LOG_FILE, level, get_log_formatter())
            lib_logger.addHandler(handler)


def redact_sensitive_data(message):
    """Redact sensitive data from log messages."""
    if not isinstance(message, str):
        return message

    for pattern, replacement in SENSITIVE_PATTERNS:
        message = pattern.sub(replacement, message)

    return message


# Custom JSON formatter with auto-injected fields
class JSONRequestIDFormatter(jsonlogger.JsonFormatter):
    """JSON formatter with auto-injected context fields."""

    def add_fields(self, log_record, record, message_dict):
        try:
            if not hasattr(record, 'requestId'):
                record.requestId = getattr(record, '_request_id', 'N/A')

            super().add_fields(log_record, record, message_dict)

            log_record['timestamp'] = datetime.fromtimestamp(record.created, timezone.utc).isoformat()
            log_record['logger'] = record.name
            log_record['environment'] = ENVIRONMENT
            log_record['service'] = SERVICE_NAME
            if SERVICE_VERSION != "unknown":
                log_record['version'] = SERVICE_VERSION

            if 'message' in log_record:
                log_record['message'] = redact_sensitive_data(log_record['message'])

        except Exception as e:
            log_record['formatter_error'] = str(e)
            super().add_fields(log_record, record, message_dict)


# Custom human-readable formatter with auto-injected RequestID
class RequestIDFormatter(logging.Formatter):
    """Human-readable formatter with auto-injected RequestID."""

    def format(self, record):
        try:
            if not hasattr(record, 'requestId'):
                record.requestId = getattr(record, '_request_id', 'N/A')

            formatted = super().format(record)
            return redact_sensitive_data(formatted)
        except Exception as e:
            return f"{record.levelname}: {record.getMessage()} [formatter_error: {e}]"


def get_log_formatter(format_type="default"):
    """
    Returns the appropriate log formatter (cached for performance).

    Supports hybrid logging:
    - JSON format for production (set LOG_FORMAT=json)
    - Human-readable format for development (set LOG_FORMAT=human or leave unset)
    """
    cache_key = f"{format_type}_{LOG_FORMAT}"
    if cache_key in _formatter_cache:
        return _formatter_cache[cache_key]

    # Access logs are always JSON for log aggregation systems
    if format_type == "access":
        formatter = JSONRequestIDFormatter(
            '%(name)s %(levelname)s %(requestId)s %(reqTimestamp)s %(httpVersion)s %(path)s %(route)s %(queryString)s '
            '%(method)s %(authScheme)s %(forwardedFor)s %(userAgent)s %(reqContentType)s %(referer)s %(origin)s %(host)s '
            '%(visitId)s %(accessorId)s %(accountType)s %(clientId)s '
            '%(resTimestamp)s %(responseTimeMs)s %(statusCode)s %(resContentLength)s %(resContentType)s '
            '%(errMessage)s %(errStack)s'
        )
    elif LOG_FORMAT == "json":
        # Structured JSON for production (CloudWatch, Datadog, etc.)
        formatter = JSONRequestIDFormatter(
            '%(timestamp)s %(logger)s %(levelname)s %(requestId)s %(message)s'
        )
    else:
        # Human-readable for local development
        formatter = RequestIDFormatter(
            '%(asctime)s | %(name)s | %(levelname)s | RequestID: %(requestId)s | %(message)s'
        )

    _formatter_cache[cache_key] = formatter
    return formatter


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
    logger.propagate = False  # Prevent propagation to root logger

    for handler in handlers:
        logger.addHandler(handler)

    # Add stream handler if LOG_TO_STDOUT is enabled
    if LOG_TO_STDOUT:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(get_log_formatter())
        logger.addHandler(stream_handler)

    return logger


def access_logger():
    """Returns a logger for access logs."""
    if 'access_logger' in _logger_cache:
        return _logger_cache['access_logger']

    access_handler = get_log_handler(
        ACCESS_LOG_FILE, logging.INFO, get_log_formatter("access")
    )
    logger = configure_logger('access_logger', [access_handler])
    _logger_cache['access_logger'] = logger
    return logger


def debug_logger():
    """Returns a logger for debug logs."""
    if 'debug_logger' in _logger_cache:
        return _logger_cache['debug_logger']

    debug_handler = get_log_handler(
        DEBUG_LOG_FILE, logging.DEBUG, get_log_formatter()
    )
    logger = configure_logger('debug_logger', [debug_handler])
    _logger_cache['debug_logger'] = logger
    return logger


async def log_request(request: Request, response, logger, start_time: float, total_bytes=0, ttfb_ms=None):
    """
    Logs request and response details for FastAPI.
    Call this from HTTP middleware after call_next(request) returns.
    Skips health check paths and OPTIONS/ELB requests to reduce noise.
    """
    try:
        # Skip health check paths and OPTIONS requests
        if request.url.path in SKIP_DEBUG_LOG_PATHS or request.method == 'OPTIONS':
            return

        # Skip ELB health checker
        user_agent = request.headers.get('user-agent', '')
        if user_agent.startswith('ELB-HealthChecker'):
            return

        status_code = getattr(response, 'status_code', 200)
        authHeader = request.headers.get('authorization')
        authScheme = authHeader.split()[0] if authHeader else "none"

        res_content_length = response.headers.get("content-length", total_bytes)
        res_content_type = response.headers.get("content-type", "unknown")
        response_time = round((time.time() - start_time) * 1000, 2)

        # Extract clientId
        clientId = (
            request.headers.get('x-lm-client-id') or
            (
                authScheme.lower() == 'basic' and
                authHeader and basicCredentialsRegex.match(authHeader) and
                base64.b64decode(basicCredentialsRegex.match(authHeader).group(1)).decode('utf-8').split(':', 1)[0]
            )
        ) or "Unknown"

        log_data = {
            "visitId": request.headers.get('x-visit-id', 'Unknown'),
            "accessorId": request.headers.get('x-lm-accessor-id', 'Unknown'),
            "accountType": request.headers.get('x-lm-account-type', 'Unknown'),
            "clientId": clientId,
            "requestId": request.headers.get("x-request-id", "N/A"),
            "reqTimestamp": datetime.fromtimestamp(start_time, timezone.utc).isoformat(),
            "httpVersion": request.scope.get("http_version", "1.1"),
            "method": request.method,
            "path": request.url.path,
            "route": request.scope.get("route").path if request.scope.get("route") else "unknown",
            "queryString": str(request.url.query),
            "authScheme": authScheme,
            "forwardedFor": request.headers.get("x-forwarded-for", request.client.host if request.client else "Unknown"),
            "userAgent": request.headers.get("user-agent", "Unknown"),
            "referer": request.headers.get("referer", "Unknown"),
            "origin": request.headers.get("origin", "Unknown"),
            "host": request.headers.get("host", "Unknown"),
            "reqContentType": request.headers.get("content-type", "Unknown"),
            "resTimestamp": datetime.now(timezone.utc).isoformat(),
            "responseTimeMs": response_time,
            "ttfbMs": ttfb_ms if ttfb_ms is not None else response_time,
            "statusCode": status_code,
            "resContentLength": res_content_length,
            "resContentType": res_content_type,
            "errMessage": getattr(response, "message", None),
            "errStack": getattr(response, "stack", None),
        }

        logger.info(
            f"Request completed with status code {status_code} | Path: {request.url.path}",
            extra=log_data
        )

    except Exception as e:
        logger.error(f"Failed to log request: {e}", exc_info=True)


# Initialize third-party loggers on module import
configure_third_party_loggers()