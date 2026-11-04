"""Preisquellen. Jede Quelle liefert für einen Alarm eine Liste von Offers."""

from __future__ import annotations

from typing import Protocol

from ..models import Alert, Offer


class SourceError(RuntimeError):
    """Quelle nicht verfügbar (fehlender Token, Netzwerk, API-Fehler)."""


class Source(Protocol):
    name: str
    #: True, wenn die Preise in die Preis-Historie (Referenzpreis) einfließen
    records_history: bool

    def fetch(self, alert: Alert) -> list[Offer]: ...
