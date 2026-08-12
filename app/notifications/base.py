"""Notification abstraction.

Kept deliberately dumb and provider-agnostic. The strategy engine never
imports a provider -- it calls `Notifier.send`, and which provider that is
resolves at construction. Section 19: "Do not hard-code Telegram/Discord/etc.
into the strategy engine."
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)

#: Events worth waking someone for, per the brief.
ALERT_EVENTS = (
    "SETUP_DETECTED", "SETUP_REJECTED", "TRADE_OPENED", "TRADE_STOPPED",
    "TRADE_TARGET", "DAILY_RISK_LIMIT", "WS_STALE", "DATA_GAP",
    "SYSTEM_RESTART", "RECONCILIATION_FAILED",
)


class Notifier(ABC):
    @abstractmethod
    async def send(self, title: str, body: str = "", *, severity: str = "INFO") -> None:
        ...


class NullNotifier(Notifier):
    """Default. Records nothing, sends nothing, never fails."""

    async def send(self, title, body="", *, severity="INFO") -> None:
        return None


class LogNotifier(Notifier):
    """Writes to the structured log. Adequate for a single-operator forward test."""

    async def send(self, title, body="", *, severity="INFO") -> None:
        log.log(getattr(logging, severity, logging.INFO), "alert: %s -- %s",
                title, body)


class CollectingNotifier(Notifier):
    """For tests and the dashboard's recent-alerts panel."""

    def __init__(self, limit: int = 200) -> None:
        self.messages: list[tuple[str, str, str]] = []
        self.limit = limit

    async def send(self, title, body="", *, severity="INFO") -> None:
        self.messages.append((severity, title, body))
        del self.messages[:-self.limit]


class WebhookNotifier(Notifier):
    """Generic outbound webhook.

    Not implemented in V1: it would be the only outbound write path in a
    process whose entire safety argument is that it has none. If a webhook is
    wanted later it belongs behind an explicit allow-list of destination hosts,
    added as its own reviewed change rather than smuggled in with the bot.
    """

    def __init__(self, url: str) -> None:
        self.url = url

    async def send(self, title, body="", *, severity="INFO") -> None:
        raise NotImplementedError(
            "outbound webhooks are deliberately not implemented in V1; "
            "use LogNotifier and read the structured log")
