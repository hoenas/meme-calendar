"""Aufbau der Kalender-Ansicht für einen User."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .days import days_between, door_date, total_doors, unlocked_count
from .models import User, UserDoor
from .service import get_setting

if TYPE_CHECKING:
    from .service import Meme


@dataclass(frozen=True)
class Door:
    index: int
    day: date
    #: Datum erreicht - darf geöffnet werden.
    unlocked: bool
    #: User hat es bereits geöffnet - zeigt Thumbnail statt Symbol.
    opened: bool
    thumbnail_url: str = ""
    #: Vom User mit dem Herz-Button markiert.
    favorite: bool = False


@dataclass(frozen=True)
class FavoriteDoor:
    index: int
    day: date | None
    meme: Meme


def effective_end_date(session: Session) -> date:
    """Globales Enddatum: aus dem Admin-UI, sonst der konfigurierte Default.

    Dient als Vorgabe für User, die kein eigenes Enddatum gesetzt haben.
    """
    override = get_setting(session, "end_date")
    if override:
        try:
            return date.fromisoformat(override)
        except ValueError:
            pass
    return settings.end_date


def effective_end_date_for(session: Session, user: User) -> date:
    """Enddatum dieses Users: eigene Einstellung, sonst das globale."""
    return user.end_date or effective_end_date(session)


def scatter(count: int, seed: int) -> list[int]:
    """Türchen-Nummern unregelmäßig verteilen, aber stabil über Requests."""
    order = list(range(1, count + 1))
    random.Random(seed).shuffle(order)
    return order


def build_doors(
    session: Session, user: User, today: date, end: date
) -> list[Door]:
    if user.started_on is None:
        return []

    days = days_between(user.started_on, end)
    if not days:
        return []

    unlocked = unlocked_count(user.started_on, today, end)
    opened_rows = session.scalars(
        select(UserDoor).where(UserDoor.user_id == user.id)
    ).all()
    # Variante von damals, nicht die aktuelle: ein geöffnetes Türchen behält
    # sein Video, auch wenn der User seine Kategorien inzwischen umgestellt hat.
    opened = {row.index: row.variant for row in opened_rows}
    favorites = {row.index for row in opened_rows if row.is_favorite}

    from .service import assignment_for  # zirkulärer Import vermieden

    doors: list[Door] = []
    for index in scatter(len(days), settings.grid_seed):
        thumb = ""
        if index in opened:
            meme = assignment_for(session, opened[index], index)
            thumb = meme.thumbnail_url if meme else ""
        doors.append(
            Door(
                index=index,
                day=days[index - 1],
                unlocked=index <= unlocked,
                opened=index in opened,
                thumbnail_url=thumb,
                favorite=index in favorites,
            )
        )
    return doors


def door_count(user: User, end: date) -> int:
    if user.started_on is None:
        return 0
    return total_doors(user.started_on, end)


def build_favorites(session: Session, user: User, end: date) -> list[FavoriteDoor]:
    """Alle vom User als Favorit markierten Türchen, neueste zuerst."""
    if user.started_on is None:
        return []

    from .service import assignment_for  # zirkulärer Import vermieden

    rows = session.scalars(
        select(UserDoor)
        .where(UserDoor.user_id == user.id, UserDoor.is_favorite.is_(True))
        .order_by(UserDoor.opened_at.desc())
    ).all()

    favorites: list[FavoriteDoor] = []
    for row in rows:
        meme = assignment_for(session, row.variant, row.index)
        if meme is None:
            continue
        favorites.append(
            FavoriteDoor(
                index=row.index,
                day=door_date(user.started_on, row.index, end),
                meme=meme,
            )
        )
    return favorites
