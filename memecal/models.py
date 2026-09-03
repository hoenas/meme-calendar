"""SQLAlchemy-Modelle."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import DEFAULT_CATEGORY


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Selbstregistrierte User bleiben gesperrt, bis der Admin freischaltet.
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    #: Startdatum des persönlichen Kalenders, gesetzt beim ersten Login
    #: nach der Freischaltung.
    started_on: Mapped[date | None] = mapped_column(Date, default=None)
    #: Gewählte Kategorien, kommasepariert und sortiert (z.B. "hunde,memes").
    #: Bestimmt, aus welchem Vorrat gezogen wird.
    categories: Mapped[str] = mapped_column(String(255), default=DEFAULT_CATEGORY)
    #: Persönliches Enddatum, unter /einstellungen vom User selbst gesetzt.
    #: None => es gilt das globale Enddatum (Setting "end_date" bzw. Default).
    end_date: Mapped[date | None] = mapped_column(Date, default=None)

    doors: Mapped[list[UserDoor]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Wie der Kanal eingegeben wurde (@handle oder UC...), für die Anzeige.
    ref: Mapped[str] = mapped_column(String(255))
    #: Aufgelöste YouTube-channel_id, eindeutig.
    channel_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    #: Schlüssel aus config.CATEGORIES.
    category: Mapped[str] = mapped_column(
        String(32), default=DEFAULT_CATEGORY, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Video(Base):
    """Ein Kandidat im Pool. Wird lazy beim Öffnen eines Türchens befüllt."""

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    channel_id: Mapped[str] = mapped_column(String(64), index=True)
    #: Beim Einsortieren vom Kanal übernommen, damit das Filtern ohne Join
    #: geht und ein gelöschter Kanal den Pool nicht entwertet.
    category: Mapped[str] = mapped_column(
        String(32), default=DEFAULT_CATEGORY, index=True
    )
    title: Mapped[str] = mapped_column(String(500), default="")
    duration: Mapped[int | None] = mapped_column(Integer, default=None)
    thumbnail_url: Mapped[str] = mapped_column(String(500), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DoorAssignment(Base):
    """(Variante, Index) -> Video, gepinnt.

    Die Variante ist die Kategorie-Auswahl des Users ("hunde,memes"). User mit
    gleicher Auswahl teilen sich dieselbe Sequenz und sehen bei gleichem Index
    dasselbe Video, nur zeitversetzt. Wer eine andere Auswahl hat, bekommt eine
    eigene Sequenz (siehe AGENDS.md).
    """

    __tablename__ = "door_assignments"

    variant: Mapped[str] = mapped_column(String(255), primary_key=True)
    index: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_pk: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    video: Mapped[Video] = relationship()


class UserDoor(Base):
    """Merkt sich, welche Türchen ein User bereits geöffnet hat."""

    __tablename__ = "user_doors"
    __table_args__ = (UniqueConstraint("user_id", "index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    #: Die Auswahl, die beim Öffnen galt. Wird festgehalten, damit ein
    #: geöffnetes Türchen dasselbe Video behält, auch wenn der User seine
    #: Kategorien später ändert.
    variant: Mapped[str] = mapped_column(String(255), default=DEFAULT_CATEGORY)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    #: Per Herz-Button markiert, damit der User es leichter wiederfindet.
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="doors")


class Setting(Base):
    """Zur Laufzeit im Admin-UI änderbare Einstellungen (z.B. Enddatum)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
