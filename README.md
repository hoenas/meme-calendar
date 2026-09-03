# Meme-Kalender

Ein Adventskalender: pro Tag ein deutsches Meme-Short.
Die Anforderungen und alle getroffenen Entscheidungen stehen in
[AGENDS.md](AGENDS.md).

## Schnellstart (lokal)

```sh
nix run          # http://127.0.0.1:8000
```

oder in der Entwicklungsumgebung:

```sh
nix develop
python -m memecal --reload
```

Beim ersten Start übernimmt die App die kuratierte Kanalliste. Auf `/register`
registrieren — der allererste Account wird automatisch Admin und ist sofort
freigeschaltet, keine weitere Einrichtung nötig.

`MEMECAL_ADMIN_PASSWORD` ist optional und legt stattdessen schon beim Start
einen festen Admin an (praktisch für automatisiertes Deployment); ist die
Variable gesetzt, entfällt der Auto-Admin bei der Registrierung.

## Deployment (Docker)

Zwei Wege zum selben Image-Tag `meme-calendar:latest`:

```sh
cp .env.example .env

# Mit Nix (gepinnt, reproduzierbar):
nix build .#dockerImage
docker load < result
docker compose up -d

# Ohne Nix, nur mit Docker (z.B. auf einem Server ohne Nix-Installation):
docker compose up -d --build
```

`MEMECAL_ADMIN_PASSWORD` in der `.env` ist optional (siehe oben) — ohne sie
wird einfach der erste, der sich registriert, zum Admin.

Der Container lauscht auf `127.0.0.1:8000`; TLS und die Domain
`memecal.zitronas.de` macht der Reverse-Proxy davor (siehe `caddy-config`).
SQLite liegt im Volume `memecal-data` unter `/data`.

## Wie es funktioniert

**Quelle.** YouTube, komplett ohne Credentials. Pro Kanal wird der offizielle
Atom-Feed (`feeds/videos.xml?channel_id=…`) gelesen. Dauer und
Einbettbarkeit stehen nicht im Feed, sondern nur im JSON-Blob der Watch-Page
(`lengthSeconds`, `playableInEmbed`) — die wird gestreamt und abgebrochen,
sobald beide Werte da sind. Aus der EU braucht jeder Request einen
Consent-Cookie, sonst kommt ein 302 auf `consent.youtube.com`.

Zu viele Watch-Page-Abrufe in kurzer Zeit quittiert YouTube mit HTTP 429 bzw.
einem CAPTCHA-Redirect. Das wird als eigener Fehler behandelt, nicht als
"Video unbrauchbar" — sonst würde der Pool stillschweigend alles verwerfen.

**Kanäle** werden im Admin-UI gepflegt, jeweils mit Kategorie. Handles
(`@name`) werden einmalig beim Anlegen zur `channel_id` aufgelöst; danach
läuft alles über den Feed. `MEMECAL_DEFAULT_CHANNELS` überschreibt die
kuratierte Startliste aus `memecal/config.py`.

**Kein Daemon.** Gepollt wird ausschließlich beim Öffnen eines Türchens, und
dabei auch nur das eine gebrauchte Video - keine Reserve, kein Vorrat auf
Vorrat. Kandidaten werden in kleinen Batches (max. 3 gleichzeitig, siehe
`service._MAX_PARALLEL_CHECKS`) mit Pause dazwischen geprüft, bis einer passt.
Das hält Türchen-Öffnen bewusst langsamer, dafür bleibt YouTube gegenüber
zurückhaltend: Kalter Pool bis zu ein paar Sekunden, danach sofort, ohne
Netzwerk. Wird YouTube trotzdem einmal knapp (HTTP 429), pausiert die App für
10 Minuten jeden weiteren Versuch, statt die Drosselung durch Nachfragen zu
verlängern (`service._RATE_LIMIT_COOLDOWN_SECONDS`). Ein Admin kann den Pool
manuell auffüllen (`/admin`, Pool-Button, `POST /admin/pool/refill`).

**Kategorien.** Jeder Kanal gehört zu einer Kategorie (Memes, Katzen, Hunde),
jeder User wählt unter `/einstellungen` eine oder mehrere aus.

**Sequenz.** Gepinnt wird pro (Variante, Index), wobei die Variante die
sortierte Kategorie-Auswahl ist. Wer dieselbe Auswahl hat, teilt dieselbe
Sequenz und ist nur zeitversetzt; andere Auswahl heißt eigene Sequenz.
Wiederholungsfreiheit gilt innerhalb einer Variante. Ein geöffnetes Türchen
merkt sich seine Variante und ändert sich auch dann nicht mehr, wenn der User
später umstellt.

**Favoriten.** Ein Herz-Button im geöffneten Türchen markiert es als Favorit
(`UserDoor.is_favorite`); `/favoriten` listet sie als Karten-Raster zum
Wiederansehen.

## Konfiguration

Alles über Env mit Prefix `MEMECAL_`, siehe `.env.example` und
`memecal/config.py`. Die wichtigsten:

| Variable | Default | Bedeutung |
|---|---|---|
| `MEMECAL_ADMIN_PASSWORD` | — | legt beim Start den Admin an (nur falls nicht vorhanden) |
| `MEMECAL_END_DATE` | `2027-03-08` | Standard-Enddatum für User ohne eigenes; im Admin-UI überschreibbar, jeder User kann unter `/einstellungen` sein eigenes setzen |
| `MEMECAL_DEFAULT_CHANNELS` | kuratierte Liste | Startliste, kommasepariert, Einträge `kanal` oder `kanal:kategorie` |
| `MEMECAL_MAX_VIDEO_SECONDS` | `90` | längere Videos landen nicht im Pool |
| `MEMECAL_DATA_DIR` | `./data` | SQLite und Session-Key |

## Tests

```sh
nix develop --command pytest              # alles
nix develop --command pytest -m "not network"
```

`tests/test_youtube_live.py` prüft die echten YouTube-Endpunkte und braucht
Netz — im Nix-Build ist die Datei deshalb ausgeschlossen.
