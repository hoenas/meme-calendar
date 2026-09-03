"""Prüft die YouTube-Endpunkte gegen die echte API.

Braucht Netz und läuft deshalb nicht im Nix-Build mit:

    pytest tests/test_youtube_live.py
"""

from __future__ import annotations

import pytest

from memecal import youtube

pytestmark = pytest.mark.network

# Stabiler, alter Kanal als Kontrolle.
CONTROL_CHANNEL = "UCXuqSBlHAE6Xw-yeJA0Tunw"
CONTROL_VIDEO = "dQw4w9WgXcQ"


def test_feed_liefert_eintraege():
    entries = youtube.fetch_feed(CONTROL_CHANNEL)
    assert entries, "Atom-Feed ohne Auth sollte erreichbar sein"
    assert all(e.video_id for e in entries)


def test_handle_aufloesen():
    channel_id, title = youtube.resolve_channel_id("@LinusTechTips")
    assert channel_id == CONTROL_CHANNEL
    assert title


def test_channel_id_wird_durchgereicht():
    channel_id, _ = youtube.resolve_channel_id(CONTROL_CHANNEL)
    assert channel_id == CONTROL_CHANNEL


def test_metadaten_aus_der_watch_page():
    # Weder Dauer noch Einbettbarkeit stehen im Feed - beides kommt aus
    # einem einzigen gestreamten Request auf die Watch-Page.
    meta = youtube.fetch_video_meta(CONTROL_VIDEO)
    assert meta is not None
    assert meta.duration == 213
    assert meta.embeddable is True


def test_metadaten_fuer_unbekanntes_video():
    assert youtube.fetch_video_meta("xxxxxxxxxxx") is None
