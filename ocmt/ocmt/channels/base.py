"""Abstract base class for channel adapters.

Each channel adapter handles inbound messages from a chat platform
(Telegram, Discord, Slack, etc.), routes them to the agent runner,
and sends the response back.

Mirrors OpenClaw's ChannelPlugin interface from
src/channels/plugins/types.ts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class InboundMessage:
    """An inbound message from a chat platform."""

    text: str
    user_id: str
    channel_id: str
    platform: str  # "telegram", "discord", "slack"
    tenant_id: str | None = None  # Resolved from channel config
    group_id: str | None = None


class ChannelAdapter(ABC):
    """Abstract base for chat platform integrations."""

    @abstractmethod
    async def start(self) -> None:
        """Start listening for messages."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop and clean up."""

    @abstractmethod
    async def send(self, channel_id: str, text: str) -> None:
        """Send an outbound message to a channel."""

    @abstractmethod
    async def on_message(self, message: InboundMessage) -> str | None:
        """Handle an inbound message.

        Routes to the agent runner and returns the response text,
        or None if no response should be sent.
        """
