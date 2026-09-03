"""YouTube-Zugriff ohne jegliche Credentials.

Verwendet ausschließlich öffentlich erreichbare Endpunkte:

* ``feeds/videos.xml?channel_id=`` - offizieller Atom-Feed, 15 neueste Videos
* die Watch-Page - für ``lengthSeconds`` und ``playableInEmbed``, die beide
  nicht im Feed stehen; wird gestreamt und früh abgebrochen
* ``i.ytimg.com`` - Thumbnails, ohne Request aus der App heraus

Requests aus der EU laufen sonst in die Consent-Wall (302 auf
consent.youtube.com), deshalb wird durchgehend ein Consent-Cookie gesetzt.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

import httpx

log = logging.getLogger(__name__)

FEED_URL = "https://www.youtube.com/feeds/videos.xml"
WATCH_URL = "https://www.youtube.com/watch"

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
#: Ohne das antwortet YouTube aus der EU mit 302 auf consent.youtube.com.
CONSENT_COOKIE = "CONSENT=YES+cb; SOCS=CAI"

HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept-Language": "de-DE,de;q=0.9",
    "Cookie": CONSENT_COOKIE,
}

ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_RSS_URL_RE = re.compile(r"rssUrl\":\"https://www\.youtube\.com/feeds/videos\.xml\?channel_id=(UC[A-Za-z0-9_-]{22})")
_CANONICAL_RE = re.compile(r"youtube\.com/channel/(UC[A-Za-z0-9_-]{22})")
_LENGTH_BYTES_RE = re.compile(rb'"lengthSeconds":"(\d+)"')
_EMBEDDABLE_BYTES_RE = re.compile(rb'"playableInEmbed":(true|false)')

#: Beide Felder liegen etwa in der Mitte der Watch-Page - streamen und
#: abbrechen, statt 1,4 MB komplett zu laden.
_STREAM_CHUNK = 64 * 1024
_STREAM_CAP = 3 * 1024 * 1024

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


class YouTubeError(RuntimeError):
    pass


class RateLimited(YouTubeError):
    """YouTube drosselt uns.

    Tritt auf, wenn in kurzer Zeit zu viele Watch-Pages abgerufen werden:
    HTTP 429, oder ein Redirect auf die CAPTCHA-Seite google.com/sorry.
    Muss von einem "Video hat keine Dauer" unterschieden werden - sonst
    verwirft der Pool stillschweigend jeden Kandidaten.
    """


@dataclass(frozen=True)
class VideoMeta:
    duration: int
    embeddable: bool


@dataclass(frozen=True)
class FeedEntry:
    video_id: str
    channel_id: str
    title: str
    published: datetime | None
    thumbnail_url: str


def _is_rate_limited(resp: httpx.Response) -> bool:
    return resp.status_code == 429 or "/sorry/" in str(resp.url)


#: Textmarker, die verraten, warum eine Watch-Page ohne ``lengthSeconds``
#: durchkam - meist eine Bot-Check-Seite statt der echten Player-Daten.
#: Reihenfolge ist Priorität: der erste Treffer gewinnt.
_MISSING_META_MARKERS: list[tuple[bytes, str]] = [
    (b"Sign in to confirm", 'Bot-Check-Seite ("Sign in to confirm ...")'),
    (b"unusual traffic", 'Bot-Check-Seite ("unusual traffic")'),
    (b"recaptcha", "reCAPTCHA im Inhalt"),
    (b"Video unavailable", "Video als nicht verfügbar markiert"),
    (b"confirm your age", "Altersbestätigung verlangt"),
]


def _diagnose_missing_length(buf: bytes) -> str:
    """Grobe Heuristik, warum eine 200er-Watch-Page kein lengthSeconds hatte."""
    for needle, label in _MISSING_META_MARKERS:
        if needle in buf:
            return label
    if b"ytInitialPlayerResponse" not in buf:
        return "kein ytInitialPlayerResponse im HTML - untypische Seite"
    return "ytInitialPlayerResponse vorhanden, aber ohne lengthSeconds"


def _client() -> httpx.Client:
    return httpx.Client(
        headers=HEADERS, timeout=DEFAULT_TIMEOUT, follow_redirects=True
    )


def resolve_channel_id(ref: str, client: httpx.Client | None = None) -> tuple[str, str]:
    """Löst @handle oder Kanal-URL zu (channel_id, title) auf.

    Wird nur einmal beim Anlegen eines Kanals gebraucht - im Betrieb läuft
    alles über die channel_id.
    """
    ref = ref.strip()
    if CHANNEL_ID_RE.match(ref):
        return ref, _channel_title(ref, client) or ref

    if ref.startswith(("http://", "https://")):
        url = ref
        direct = _CANONICAL_RE.search(ref)
        if direct:
            cid = direct.group(1)
            return cid, _channel_title(cid, client) or ref
    else:
        handle = ref if ref.startswith("@") else f"@{ref}"
        url = f"https://www.youtube.com/{handle}"

    owns_client = client is None
    client = client or _client()
    try:
        resp = client.get(url)
        if resp.status_code != 200:
            raise YouTubeError(
                f"Kanal '{ref}' nicht erreichbar (HTTP {resp.status_code})"
            )
        body = resp.text
        match = _RSS_URL_RE.search(body) or _CANONICAL_RE.search(body)
        if not match:
            raise YouTubeError(f"Konnte keine channel_id für '{ref}' finden")
        channel_id = match.group(1)
        title_match = re.search(r"<title>([^<]*)</title>", body)
        title = (
            title_match.group(1).replace(" - YouTube", "").strip()
            if title_match
            else ref
        )
        return channel_id, title or ref
    finally:
        if owns_client:
            client.close()


def _channel_title(channel_id: str, client: httpx.Client | None = None) -> str:
    """Kanaltitel aus dem Feed - billiger als die Kanalseite."""
    owns_client = client is None
    client = client or _client()
    try:
        resp = client.get(FEED_URL, params={"channel_id": channel_id})
        if resp.status_code != 200:
            return ""
        root = ET.fromstring(resp.content)
        node = root.find("a:title", ATOM_NS)
        return (node.text or "").strip() if node is not None else ""
    except (httpx.HTTPError, ET.ParseError):
        return ""
    finally:
        if owns_client:
            client.close()


def fetch_feed(channel_id: str, client: httpx.Client | None = None) -> list[FeedEntry]:
    """Die 15 neuesten Videos eines Kanals. Fehler => leere Liste."""
    owns_client = client is None
    client = client or _client()
    try:
        resp = client.get(FEED_URL, params={"channel_id": channel_id})
        if resp.status_code != 200:
            log.warning("Feed %s: HTTP %s", channel_id, resp.status_code)
            return []
        root = ET.fromstring(resp.content)
    except (httpx.HTTPError, ET.ParseError) as exc:
        log.warning("Feed %s fehlgeschlagen: %s", channel_id, exc)
        return []
    finally:
        if owns_client:
            client.close()

    entries: list[FeedEntry] = []
    for node in root.findall("a:entry", ATOM_NS):
        vid_node = node.find("yt:videoId", ATOM_NS)
        if vid_node is None or not vid_node.text:
            continue
        video_id = vid_node.text.strip()
        title_node = node.find("a:title", ATOM_NS)
        pub_node = node.find("a:published", ATOM_NS)
        published = None
        if pub_node is not None and pub_node.text:
            try:
                published = datetime.fromisoformat(pub_node.text)
            except ValueError:
                published = None
        entries.append(
            FeedEntry(
                video_id=video_id,
                channel_id=channel_id,
                title=(title_node.text or "").strip() if title_node is not None else "",
                published=published,
                thumbnail_url=thumbnail_for(video_id),
            )
        )
    return entries


def thumbnail_for(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def embed_url(video_id: str) -> str:
    # nocookie-Variante, damit hinter dem Reverse-Proxy weniger Tracking anfällt.
    return f"https://www.youtube-nocookie.com/embed/{video_id}?rel=0"


def fetch_video_meta(
    video_id: str, client: httpx.Client | None = None
) -> VideoMeta | None:
    """Dauer und Einbettbarkeit in einem einzigen Request.

    Beides steht im JSON-Blob der Watch-Page (``lengthSeconds`` und
    ``playableInEmbed``), rund bei der Hälfte des Dokuments. Die Seite ist
    ~1,4 MB groß, deshalb wird gestreamt und abgebrochen, sobald beide Werte
    gefunden sind - das spart knapp die Hälfte der Übertragung pro Video.

    Der Atom-Feed enthält keine Dauer; das hier ist der einzige
    Scraping-Anteil. Schlägt er fehl, kommt None zurück und der Aufrufer
    entscheidet.
    """
    owns_client = client is None
    client = client or _client()
    try:
        with client.stream("GET", WATCH_URL, params={"v": video_id}) as resp:
            if _is_rate_limited(resp):
                raise RateLimited(
                    "YouTube drosselt die Abrufe (HTTP "
                    f"{resp.status_code}). Später erneut versuchen."
                )
            if "consent.youtube.com" in str(resp.url):
                # Das Consent-Cookie greift nicht (z.B. andere Region/Consent-
                # Variante ab dieser IP) - ohne Log sieht das aus wie "jedes
                # Video unbrauchbar", obwohl gar keins wirklich geprüft wurde.
                log.warning(
                    "Watch-Page für %s landete auf der Consent-Wall (%s)",
                    video_id,
                    resp.url,
                )
                return None
            if resp.status_code != 200:
                log.warning("Watch-Page für %s: HTTP %s", video_id, resp.status_code)
                return None
            buf = bytearray()
            duration: int | None = None
            embeddable: bool | None = None
            for chunk in resp.iter_bytes(_STREAM_CHUNK):
                buf += chunk
                if duration is None:
                    match = _LENGTH_BYTES_RE.search(buf)
                    if match:
                        duration = int(match.group(1))
                if embeddable is None:
                    match = _EMBEDDABLE_BYTES_RE.search(buf)
                    if match:
                        embeddable = match.group(1) == b"true"
                if duration is not None and embeddable is not None:
                    break
                if len(buf) > _STREAM_CAP:
                    break
        if duration is None:
            # Seite kam durch (200, keine Consent-Wall), aber das erwartete
            # JSON-Feld stand nicht drin - z.B. weil YouTube die Watch-Page
            # für diese Anfrage anders aufgebaut hat. Ohne Log nicht von einem
            # echten "Video unbrauchbar" zu unterscheiden.
            log.warning(
                "Watch-Page für %s gelesen (%d Bytes), aber lengthSeconds nicht "
                "gefunden - %s",
                video_id,
                len(buf),
                _diagnose_missing_length(bytes(buf)),
            )
            return None
        # Fehlt das Flag, gehen wir von einbettbar aus - der iframe zeigt im
        # Zweifel selbst eine Fehlermeldung.
        return VideoMeta(duration=duration, embeddable=embeddable is not False)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Metadaten für %s nicht ermittelbar: %s", video_id, exc)
        return None
    finally:
        if owns_client:
            client.close()


def fetch_duration(video_id: str, client: httpx.Client | None = None) -> int | None:
    meta = fetch_video_meta(video_id, client=client)
    return meta.duration if meta else None
