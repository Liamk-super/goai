"""LaunchScope shared, body-free observability package boundary."""

from .redaction import REDACTED, payload_sha256, redact, safe_trace_attributes
from .semconv import ALLOWED_ATTRIBUTES, TRACE_LEVELS

__version__ = "0.1.0"

__all__ = [
    "ALLOWED_ATTRIBUTES",
    "REDACTED",
    "TRACE_LEVELS",
    "payload_sha256",
    "redact",
    "safe_trace_attributes",
]
