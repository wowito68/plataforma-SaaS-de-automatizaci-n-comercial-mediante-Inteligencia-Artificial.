"""Structured telecom extraction bounded capability."""

from saas_platform.modules.telecom_extraction.application import ExtractTelecomConversation
from saas_platform.modules.telecom_extraction.domain import (
    TelecomExtractionCommand,
    TelecomExtractionResult,
)

__all__ = [
    "ExtractTelecomConversation",
    "TelecomExtractionCommand",
    "TelecomExtractionResult",
]
