"""Kalendertage: ein Türchen pro Tag zwischen Start- und Enddatum."""

from __future__ import annotations

from datetime import date, timedelta


def days_between(start: date, end: date) -> list[date]:
    """Alle Tage von start bis end, beide inklusive."""
    if end < start:
        return []
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def total_doors(start: date, end: date) -> int:
    """Anzahl Türchen für einen User, der am `start` anfängt."""
    return len(days_between(start, end))


def unlocked_count(start: date, today: date, end: date) -> int:
    """Wie viele Türchen darf dieser User heute geöffnet haben?

    Entspricht der Anzahl Kalendertage von seinem Startdatum bis heute,
    gedeckelt auf die Gesamtzahl der Türchen.
    """
    if today < start:
        return 0
    horizon = min(today, end)
    return min(
        len(days_between(start, horizon)),
        total_doors(start, end),
    )


def door_date(start: date, index: int, end: date) -> date | None:
    """Datum, an dem Türchen `index` (1-basiert) aufgeht."""
    days = days_between(start, end)
    if 1 <= index <= len(days):
        return days[index - 1]
    return None
