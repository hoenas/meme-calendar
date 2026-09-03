"""Konfiguration. Alles über Env-Variablen mit Prefix MEMECAL_."""

from __future__ import annotations

import secrets
from datetime import date
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: Was ein User sich in den Kalender legen kann. Jeder Kanal gehört zu genau
#: einer Kategorie, jeder User wählt eine oder mehrere aus.
CATEGORIES: dict[str, str] = {
    "memes": "Memes",
    "katzen": "Katzen",
    "hunde": "Hunde",
}

DEFAULT_CATEGORY = "memes"

#: Kuratierte Startbelegung, als "channel_id:kategorie".
#: channel_ids statt Handles, weil die Auflösung entfällt und Umlaute in
#: Handles (@ÖsterreichDeutscheMeme) keine Rolle spielen.
#:
#: Ausgewählt aus der YouTube-Kanalsuche und einzeln durchgemessen:
#: aktiv (letzter Upload <= 3 Tage) und alle bzw. fast alle Videos der
#: letzten 12 unter 90 Sekunden. Kanäle mit Streamer-, Gaming- oder
#: Fandom-Bezug wurden aussortiert, ebenso inaktive und KI-generierte.
DEFAULT_CHANNEL_SEED: list[str] = [
    # Memes - allgemeine deutsche Memes, kein Nischenkram
    "UC0mpsqUUVEY6-lJsGHu5huA:memes",  # Deutsche Memes | Clipvoltic (24k)
    "UCj2n9vnWVsV8W6JtNWehkyQ:memes",  # Memes King (66k)
    "UCDpGaAG9wG1DKwGiM67kZHw:memes",  # MemeAberReal (11k)
    "UCEBJ04RAkkUVEjk2QQMBMRg:memes",  # Baba Memes (156k)
    "UC6kAJyGJhKNOkFi8XjwondQ:memes",  # KartoffelPuffer (233k)
    "UCBLJ_4Nq49Ft7-Do2uT8dAQ:memes",  # Österreich-Deutsche Memes (5.6k)
    # Katzen
    "UCWHTM74xJdXmqiqU_4h_1UQ:katzen",  # 2sies Catmemes (187k, deutsch)
    "UCbxKmdDgEV5IPEkT8cvB_rA:katzen",  # sinascolorcats (176k, deutsch)
    "UCFWN4K4K9X0doYjtcyUzYdg:katzen",  # Funny Cats Time (767k)
    "UCvlYzUC4cqL8YcC0ecQTC_Q:katzen",  # Naughty cats (448k)
    # Hunde - hier ist die Sprache egal, es sind Hundevideos
    "UCPIvT-zcQl2H0vabdXJGcpg:hunde",  # The Pet Collective (9.6M)
    "UCQiTesViiVyhqaObE9Lzk2w:hunde",  # AGuyAndAGolden (5.4M)
    "UCHnE9NodOn_dguyg5_yZXwQ:hunde",  # RxCKSTxR (1.7M)
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEMECAL_", env_file=".env")

    #: Verzeichnis für SQLite-Datei und generierten Secret Key.
    data_dir: Path = Path("./data")

    #: Letzter Tag des Kalenders, falls weder User noch Admin etwas anderes
    #: eingestellt haben (absolutes Datum, siehe AGENDS.md).
    end_date: date = date(2027, 3, 8)

    #: Videos länger als das werden nicht in den Pool aufgenommen.
    max_video_seconds: int = 90

    #: So viele ungenutzte Videos versucht die App vorrätig zu halten.
    reserve_target: int = 10

    #: Startliste, wird beim ersten Start in die DB übernommen. Einträge sind
    #: "kanal" oder "kanal:kategorie"; Handles (@name) und channel_ids (UC...)
    #: sind beide erlaubt. Leert man die Variable explizit, startet die App
    #: ohne Kanäle.
    #: NoDecode, weil pydantic-settings den Env-Wert sonst als JSON lesen will
    #: und an einer simplen kommaseparierten Liste scheitert.
    default_channels: Annotated[list[str], NoDecode] = DEFAULT_CHANNEL_SEED

    #: Erster Admin-Account, wird beim Start angelegt falls nicht vorhanden.
    admin_username: str = "admin"
    admin_password: str = ""

    #: Signiert die Session-Cookies. Leer => wird in data_dir generiert.
    secret_key: str = ""

    #: Layout des Adventskalender-Rasters.
    grid_columns: int = 6
    #: Fixer Seed, damit die Türchen-Verteilung stabil bleibt.
    grid_seed: int = 1312

    @field_validator("default_channels", mode="before")
    @classmethod
    def _split_channels(cls, v: object) -> object:
        if isinstance(v, str):
            return [part.strip() for part in v.split(",") if part.strip()]
        return v

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'memecal.sqlite3'}"

    def resolve_secret_key(self) -> str:
        """Nimmt den konfigurierten Key, sonst einen persistenten aus data_dir."""
        if self.secret_key:
            return self.secret_key
        key_file = self.data_dir / "secret_key"
        if not key_file.exists():
            self.data_dir.mkdir(parents=True, exist_ok=True)
            key_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
            key_file.chmod(0o600)
        return key_file.read_text(encoding="utf-8").strip()


settings = Settings()
