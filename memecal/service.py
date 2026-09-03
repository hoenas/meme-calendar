"""Meme-Beschaffung und Türchen-Zuordnung.

Kein Hintergrund-Daemon: gepollt wird lazy beim Öffnen eines Türchens.
Ist einem Index einmal ein Video zugeordnet, bleibt das dauerhaft so - alle
User sehen bei gleichem Index dasselbe Meme, nur zeitversetzt.
"""

from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import youtube
from .config import CATEGORIES, DEFAULT_CATEGORY, settings
from .models import Channel, DoorAssignment, Setting, Video

log = logging.getLogger(__name__)

#: Wie viele Watch-Pages gleichzeitig geprüft werden.
_MAX_PARALLEL_CHECKS = 8


class NoMemeAvailable(RuntimeError):
    """Pool leer und Nachschub nicht beschaffbar."""


@dataclass(frozen=True)
class Meme:
    video_id: str
    title: str
    thumbnail_url: str
    embed_url: str
    watch_url: str
    duration: int | None


def _to_meme(video: Video) -> Meme:
    return Meme(
        video_id=video.video_id,
        title=video.title,
        thumbnail_url=video.thumbnail_url or youtube.thumbnail_for(video.video_id),
        embed_url=youtube.embed_url(video.video_id),
        watch_url=youtube.watch_url(video.video_id),
        duration=video.duration,
    )


def normalize_categories(raw: str | list[str] | None) -> list[str]:
    """Bereinigt eine Kategorie-Auswahl: bekannt, eindeutig, sortiert."""
    if raw is None:
        items: list[str] = []
    elif isinstance(raw, str):
        items = [p.strip() for p in raw.split(",")]
    else:
        items = [str(p).strip() for p in raw]
    chosen = sorted({i for i in items if i in CATEGORIES})
    return chosen or [DEFAULT_CATEGORY]


def variant_of(categories: str | list[str] | None) -> str:
    """Der Schlüssel, unter dem eine Auswahl ihre Sequenz pinnt."""
    return ",".join(normalize_categories(categories))


def enabled_channels(
    session: Session, categories: list[str] | None = None
) -> list[Channel]:
    stmt = select(Channel).where(Channel.enabled.is_(True))
    if categories:
        stmt = stmt.where(Channel.category.in_(categories))
    return list(session.scalars(stmt).all())


def add_channel(
    session: Session, ref: str, category: str = DEFAULT_CATEGORY
) -> Channel:
    """Legt einen Kanal an und löst dabei @handle -> channel_id auf."""
    if category not in CATEGORIES:
        raise ValueError(f"Unbekannte Kategorie '{category}'.")
    channel_id, title = youtube.resolve_channel_id(ref)
    existing = session.scalar(
        select(Channel).where(Channel.channel_id == channel_id)
    )
    if existing is not None:
        raise ValueError(f"Kanal '{title}' ist bereits eingetragen.")
    channel = Channel(
        ref=ref, channel_id=channel_id, title=title, category=category
    )
    session.add(channel)
    session.commit()
    return channel


def unused_video_count(session: Session, variant: str | None = None) -> int:
    """Ungenutzte Videos - bezogen auf eine Variante, sonst insgesamt."""
    stmt = select(func.count()).select_from(Video)
    if variant is not None:
        categories = normalize_categories(variant)
        assigned = select(DoorAssignment.video_pk).where(
            DoorAssignment.variant == variant
        )
        stmt = stmt.where(Video.category.in_(categories), Video.id.not_in(assigned))
    return session.scalar(stmt) or 0


def pool_by_category(session: Session) -> dict[str, int]:
    """Wie viele Videos pro Kategorie im Pool liegen - für das Admin-UI."""
    rows = session.execute(
        select(Video.category, func.count()).group_by(Video.category)
    ).all()
    counts = dict.fromkeys(CATEGORIES, 0)
    for category, count in rows:
        if category in counts:
            counts[category] = count
    return counts


def refill_pool(
    session: Session,
    wanted: int | None = None,
    categories: list[str] | None = None,
) -> int:
    """Holt die Feeds und legt neue, brauchbare Videos in den Pool.

    Gibt die Anzahl neu aufgenommener Videos zurück. Netzwerkfehler werden
    geschluckt - im Zweifel bleibt der Pool eben so klein wie er ist. Eine
    Drosselung durch YouTube wird dagegen durchgereicht, sonst sähe sie aus
    wie "alle Kandidaten unbrauchbar".
    """
    wanted = settings.reserve_target if wanted is None else wanted
    channels = enabled_channels(session, categories)
    if not channels:
        return 0

    known = set(session.scalars(select(Video.video_id)).all())
    candidates: list[tuple[youtube.FeedEntry, str]] = []

    with httpx.Client(
        headers=youtube.HEADERS,
        timeout=youtube.DEFAULT_TIMEOUT,
        follow_redirects=True,
    ) as client:
        for channel in channels:
            for entry in youtube.fetch_feed(channel.channel_id, client=client):
                if entry.video_id not in known:
                    candidates.append((entry, channel.category))

        # Mischen, damit nicht immer derselbe Kanal zuerst drankommt.
        random.shuffle(candidates)

        added = 0
        # Die Metadaten kosten je einen Request auf die Watch-Page. Seriell
        # dauert das bei einem kalten Pool zu lange, deshalb schubweise
        # parallel - und abbrechen, sobald genug beisammen ist.
        with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_CHECKS) as pool:
            for start in range(0, len(candidates), _MAX_PARALLEL_CHECKS):
                if added >= wanted:
                    break
                batch = candidates[start : start + _MAX_PARALLEL_CHECKS]
                try:
                    metas = list(
                        pool.map(
                            lambda pair: (
                                *pair,
                                youtube.fetch_video_meta(
                                    pair[0].video_id, client=client
                                ),
                            ),
                            batch,
                        )
                    )
                except youtube.RateLimited:
                    # Weitermachen würde die Drosselung nur verlängern und
                    # jeden Kandidaten fälschlich als unbrauchbar verwerfen.
                    log.warning("YouTube drosselt - Nachschub abgebrochen")
                    if added == 0:
                        raise
                    break
                for entry, category, meta in metas:
                    if added >= wanted:
                        break
                    if meta is None or not meta.embeddable:
                        continue
                    if meta.duration > settings.max_video_seconds:
                        continue
                    video = Video(
                        video_id=entry.video_id,
                        channel_id=entry.channel_id,
                        category=category,
                        title=entry.title,
                        duration=meta.duration,
                        thumbnail_url=entry.thumbnail_url,
                        published_at=entry.published.replace(tzinfo=None)
                        if entry.published
                        else None,
                    )
                    session.add(video)
                    try:
                        session.commit()
                    except IntegrityError:
                        # Paralleler Request war schneller.
                        session.rollback()
                        continue
                    known.add(entry.video_id)
                    added += 1

    return added


def top_up_reserve(variant: str) -> None:
    """Füllt die Reserve für eine Variante auf.

    Läuft als Background-Task nach dem Response - das Öffnen eines Türchens
    soll nicht darauf warten, dass zehn weitere Videos geprüft werden.
    """
    from .db import session_scope

    try:
        with session_scope() as session:
            if unused_video_count(session, variant) < settings.reserve_target:
                added = refill_pool(
                    session, categories=normalize_categories(variant)
                )
                log.info(
                    "Reserve '%s' aufgefüllt: %s neue Videos", variant, added
                )
    except Exception:  # pragma: no cover - Nachschub ist best effort
        log.warning("Nachfüllen des Pools fehlgeschlagen", exc_info=True)


def assignment_for(session: Session, variant: str, index: int) -> Meme | None:
    """Bereits gepinntes Video für diese Variante, ohne Netzwerkzugriff."""
    assignment = session.get(DoorAssignment, (variant, index))
    return _to_meme(assignment.video) if assignment else None


def get_or_assign(session: Session, variant: str, index: int) -> Meme:
    """Liefert das Video für einen Index, beschafft bei Bedarf Nachschub."""
    existing = assignment_for(session, variant, index)
    if existing is not None:
        return existing

    categories = normalize_categories(variant)
    video = _take_unused(session, variant, categories)
    if video is None:
        # Auf dem Request-Pfad nur das eine Video beschaffen, das jetzt
        # gebraucht wird. Die Reserve füllt der Background-Task.
        try:
            refill_pool(session, wanted=1, categories=categories)
        except youtube.RateLimited as exc:
            raise NoMemeAvailable(
                "YouTube drosselt gerade die Abrufe. Das Türchen bleibt "
                "unverbraucht - bitte in ein paar Minuten nochmal probieren."
            ) from exc
        video = _take_unused(session, variant, categories)
    if video is None:
        raise NoMemeAvailable(
            "Für deine Auswahl wurde nichts gefunden. Sind für diese "
            "Kategorien Kanäle konfiguriert und ist YouTube erreichbar?"
        )

    session.add(DoorAssignment(variant=variant, index=index, video_pk=video.id))
    try:
        session.commit()
    except IntegrityError:
        # Anderer Request war schneller - dessen Zuordnung gilt.
        session.rollback()
        pinned = assignment_for(session, variant, index)
        if pinned is None:
            raise
        return pinned

    return _to_meme(video)


def _take_unused(
    session: Session, variant: str, categories: list[str]
) -> Video | None:
    """Ältestes Video, das diese Variante noch nicht verbraucht hat.

    Wiederholungsfreiheit gilt innerhalb einer Variante - zwei User mit
    unterschiedlicher Auswahl dürfen dasselbe Video sehen.
    """
    assigned = select(DoorAssignment.video_pk).where(
        DoorAssignment.variant == variant
    )
    return session.scalar(
        select(Video)
        .where(Video.category.in_(categories), Video.id.not_in(assigned))
        .order_by(Video.discovered_at, Video.id)
        .limit(1)
    )


def get_setting(session: Session, key: str) -> str | None:
    row = session.get(Setting, key)
    return row.value if row else None


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    session.commit()
