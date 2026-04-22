"""Event ingestion: WebSocket (primary) + HTTP (fallback) + OTLP (GenAI spans)."""

from .normalizer import normalize_event, NormalizationError

__all__ = ["normalize_event", "NormalizationError"]
