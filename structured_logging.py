"""
Structured Logging Utilities for THO AI Agent
Provides JSON-formatted logging with request context
"""

import logging
import json
import sys
from datetime import datetime
from typing import Any, Dict, Optional

class StructuredLogger:
    """JSON-structured logger for Cloud Run"""
    
    def __init__(self, name: str = "tho-agent"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        
        # Remove existing handlers
        self.logger.handlers = []
        
        # Add JSON handler for stdout (Cloud Run logs)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        self.logger.addHandler(handler)
    
    def _log(self, level: str, message: str, **kwargs):
        """Internal log method with structured data"""
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "severity": level.upper(),
            "message": message,
            **kwargs
        }
        
        if level == "info":
            self.logger.info(json.dumps(log_data))
        elif level == "warning":
            self.logger.warning(json.dumps(log_data))
        elif level == "error":
            self.logger.error(json.dumps(log_data))
        elif level == "debug":
            self.logger.debug(json.dumps(log_data))
    
    def info(self, message: str, **kwargs):
        """Log info message"""
        self._log("info", message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """Log warning message"""
        self._log("warning", message, **kwargs)
    
    def error(self, message: str, **kwargs):
        """Log error message"""
        self._log("error", message, **kwargs)
    
    def debug(self, message: str, **kwargs):
        """Log debug message"""
        self._log("debug", message, **kwargs)
    
    def request(self, request_id: str, user_id: str, session_id: str, message: str):
        """Log incoming request"""
        self.info(
            "Incoming request",
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
            message_preview=message[:50] + "..." if len(message) > 50 else message
        )
    
    def response(self, request_id: str, response_length: int, duration_ms: float):
        """Log response"""
        self.info(
            "Response sent",
            request_id=request_id,
            response_length=response_length,
            duration_ms=round(duration_ms, 2)
        )
    
    def tool_call(self, request_id: str, tool_name: str, status: str, duration_ms: Optional[float] = None):
        """Log tool invocation"""
        log_kwargs = {
            "request_id": request_id,
            "tool_name": tool_name,
            "status": status
        }
        if duration_ms is not None:
            log_kwargs["duration_ms"] = round(duration_ms, 2)
        
        self.info("Tool call", **log_kwargs)

    def log_analytics_event(self, event_type: str, session_id: str = "unknown", data: Dict[str, Any] = None):
        """
        Log a structured analytics event for business intelligence.
        
        Args:
            event_type: Name of event (e.g., SEARCH_PERFORMED, LEAD_CAPTURED)
            session_id: User session ID
            data: Additional context (e.g., search params, lead info)
        """
        analytics_payload = {
            "event_type": event_type,
            "session_id": session_id,
            "event_data": data or {},
            "analytics_version": "1.0"
        }
        
        # Log to stdout with specific 'ANALYTICS' marker for easy filtering
        # In production, this could be routed to BigQuery
        self._log("info", f"ANALYTICS_EVENT: {event_type}", analytics_payload=analytics_payload)


class JsonFormatter(logging.Formatter):
    """Custom JSON formatter for structured logs"""
    
    def format(self, record: logging.LogRecord) -> str:
        # The message is already JSON from _log method
        return record.getMessage()


# Global logger instance
logger = StructuredLogger()
