"""Arbeitstage: Mo-Fr abzüglich gesetzlicher Feiertage (Default Baden-Württemberg)."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import holidays


@lru_cache(maxsize=8)
def _holiday_calendar(subdiv: str, first_year: int, last_year: int):
    years = range(first_year, last_year + 1)
    return holidays.Germany(subdiv=subdiv, years=years)


def is_workday(day: date, subdiv: str = "BW") -> bool:
    if day.weekday() >= 5:  # Samstag/Sonntag
        return False
    cal = _holiday_calendar(subdiv, day.year, day.year)
    return day not in cal


def workdays_between(start: date, end: date, subdiv: str = "BW") -> list[date]:
    """Alle Arbeitstage von start bis end, beide inklusive."""
    if end < start:
        return []
    cal = _holiday_calendar(subdiv, start.year, end.year)
    out: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5 and day not in cal:
            out.append(day)
        day += timedelta(days=1)
    return out


def total_doors(start: date, end: date, subdiv: str = "BW") -> int:
    """Anzahl Türchen für einen User, der am `start` anfängt."""
    return len(workdays_between(start, end, subdiv))


def unlocked_count(start: date, today: date, end: date, subdiv: str = "BW") -> int:
    """Wie viele Türchen darf dieser User heute geöffnet haben?

    Entspricht der Anzahl Arbeitstage von seinem Startdatum bis heute,
    gedeckelt auf die Gesamtzahl der Türchen.
    """
    if today < start:
        return 0
    horizon = min(today, end)
    return min(
        len(workdays_between(start, horizon, subdiv)),
        total_doors(start, end, subdiv),
    )


def door_date(start: date, index: int, end: date, subdiv: str = "BW") -> date | None:
    """Datum, an dem Türchen `index` (1-basiert) aufgeht."""
    days = workdays_between(start, end, subdiv)
    if 1 <= index <= len(days):
        return days[index - 1]
    return None
