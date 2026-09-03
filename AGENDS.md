# Meme-Kalender

Diese App soll für jeden Tag bis zu einem bestimmten Datum ein deutsches Meme abspielen.

* Memes
    * Finde eine vernünftige Quelle für Memes
    * Memes sollten wiederholungsfrei sein, falls möglich
    * Memes sollten kurze Videos sein, die embedded abgespielt werden können
* Seite
    * Ist hinter einem Reverse-Proxy auf memecal.zitronas.de
    * Sollte per Login abgesichert sein, Freigabe über Admin Account
    * Sollte Kalender-Übersicht zeigen
    * Klick auf (freigeschaltete) Türchen spielt das Meme
    * Nur Türchen bis zum aktuellen Tag dürfen geöffnet werden
    * Geschlossene Türchen zeigen ein Symbol, offene ein Thumbnail
* Enddatum
    * Sollte einstellbar sein
* Entwicklung
    * Alle Tools, Umgebungen, ... sollten in nix-flake festgehalten werden
    * Deployment über docker / docker-compose, lokal auch möglich


Schreibe alle Änderungen an den Requirements in dieses File.

## Entscheidungen (2026-09-03)

* Meme-Quelle: **YouTube Shorts / Reels von deutschen Meme-Kanälen**
    * Kriterium ist "deutsche Memes"; kurze Meme-Reels sind ausdrücklich ok.
    * Zugriff **komplett ohne Credentials**, kein API-Key, kein OAuth:
        * Pro Kanal der offizielle Atom-Feed
          `youtube.com/feeds/videos.xml?channel_id=<UC...>` (liefert die 15
          neuesten Videos inkl. Shorts, ohne Auth).
        * Dauer und Einbettbarkeit stehen **nicht** im Feed, sondern im
          JSON-Blob der Watch-Page (`lengthSeconds`, `playableInEmbed`, beide
          etwa bei der Hälfte des ~1,4 MB großen Dokuments). Ein einziger
          gestreamter Request holt beides und bricht danach ab.
        * Thumbnail direkt über `i.ytimg.com/vi/<id>/hqdefault.jpg`.
    * **Kanäle sind konfigurierbar**, nicht hart kodiert:
        * Gepflegt werden sie im Admin-UI (anlegen, aktivieren/deaktivieren,
          löschen); sie liegen in der Datenbank.
        * Eine Default-Liste wird beim ersten Start in die DB übernommen und
          ist per Env `MEMECAL_DEFAULT_CHANNELS` überschreibbar (Einträge als
          `kanal` oder `kanal:kategorie`, kommasepariert). Sie steht in
          `memecal/config.py` als `DEFAULT_CHANNEL_SEED` und ist gefüllt.
        * Angegeben werden können sowohl Handles (`@name`) als auch fertige
          `channel_id`s (`UC...`); Handles werden **einmalig beim Anlegen** zur
          `channel_id` aufgelöst (die Kanalseite enthält die `rssUrl`).
          Im Betrieb wird nur noch RSS abgefragt.
    * **Kein Hintergrund-Daemon.** Gepollt wird lazy beim Öffnen eines
      Türchens (die App wird nur von ~2 Personen genutzt).
        * Ist dem Index bereits ein Video zugeordnet, wird es direkt
          ausgeliefert — kein Netzwerkzugriff.
        * Sonst werden die Feeds geholt, ein noch unbenutztes Video gewählt,
          die Zuordnung **dauerhaft in SQLite gepinnt** und ausgeliefert.
          Ein einmal geöffnetes Türchen zeigt damit für immer dasselbe Meme.
        * Auf dem Request-Pfad wird **nur das eine gebrauchte Video**
          beschafft; Kandidaten werden dabei schubweise parallel geprüft.
          Die Reserve füllt ein Background-Task **nach** der Antwort auf,
          damit niemand darauf wartet. Gemessen: kalter Pool ~4 s, danach
          0 s ohne Netzwerkzugriff.
          (Erste Fassung füllte die Reserve im Request-Pfad und seriell —
          das dauerte 41 s pro Klick.)
    * Das 15-Videos-pro-Kanal-Limit des Feeds ist dadurch unkritisch: pro
      Kalendertag wird nur ein einziges neues Video gebraucht, und der Feed
      liefert über die Zeit laufend Nachschub.
    * Da der Fetch im Request-Pfad liegt, zeigt das UI beim erstmaligen Öffnen
      einen Ladezustand (HTMX-Indicator).
    * Vollautomatisch, keine manuelle Freigabe durch den Admin.
    * Videos werden **nicht** heruntergeladen, sondern als YouTube-iframe
      eingebettet.
    * Längenfilter: aufgenommen werden nur Videos bis 90 Sekunden
      (`MEMECAL_MAX_VIDEO_SECONDS`).
    * EU-Consent-Wall: Requests brauchen einen `CONSENT`/`SOCS`-Cookie-Header,
      sonst kommt ein 302 auf `consent.youtube.com`.
    * **Drosselung:** zu viele Watch-Page-Abrufe in kurzer Zeit beantwortet
      YouTube mit HTTP 429 bzw. einem Redirect auf `google.com/sorry`
      (CAPTCHA). Das wird als eigener Fehler (`RateLimited`) behandelt und
      **nicht** als "Video hat keine Dauer" — sonst würde der Pool jeden
      Kandidaten stillschweigend verwerfen und "keine Memes gefunden" melden.
      Der Nachschub bricht dann ab, das Türchen bleibt unverbraucht.

  *Verworfen:* Reddit (öffentliches `.json` liefert **403**, nur noch mit
  OAuth-Credentials nutzbar) und Lemmy/feddit.org `ich_iel` (ohne Auth
  erreichbar, aber von 91 gesampelten Posts war genau **einer** ein Video —
  praktisch eine reine Bild-Community).
* Kalender
    * Darstellung als **Adventskalender-Raster**: Türchen mit Nummern 1..N,
      unregelmäßig im Raster verteilt. Datumsbezug nur intern.
    * **Startdatum pro User individuell**: der Kalender eines Users beginnt an
      dessen erstem Login. Verschiedene User sind entsprechend zeitversetzt.
    * **Enddatum ist ein absolutes Kalenderdatum und pro Account einstellbar**
      (Update): jeder User setzt sein eigenes unter `/einstellungen`
      (`User.end_date`, nullable). Leeres Feld = zurück zum globalen Default.
      Der Admin pflegt weiterhin ein globales Standard-Enddatum
      (`/admin/settings`) — das gilt für alle User, die kein eigenes gesetzt
      haben, und ist im Admin-UI pro User sichtbar (Spalte "Kalenderende").
      Der konfigurierte App-Default ist **08.03.2027** (`settings.end_date`).
      Wer später startet oder ein früheres Enddatum wählt, hat weniger
      Türchen.
    * Ein Türchen pro Kalendertag (Update, siehe unten) — nicht mehr nur
      Werktage.
    * "Nur Türchen bis zum aktuellen Tag" gilt relativ zum individuellen
      Startdatum des jeweiligen Users.
* **Inhalt ist pro User wählbar**
    * Kategorien: **Memes**, **Katzen**, **Hunde** (`config.CATEGORIES`).
      Jeder Kanal gehört zu genau einer, jeder User wählt eine oder mehrere
      unter `/einstellungen`. Default ist Memes.
    * Konsequenz für die Sequenz: sie kann nicht mehr global geteilt werden.
      Gepinnt wird jetzt pro **(Variante, Index)**, wobei die Variante die
      sortierte Auswahl ist (`"hunde,katzen"`). Wer dieselbe Auswahl hat,
      teilt weiter dieselbe Sequenz und ist nur zeitversetzt; andere Auswahl
      heißt eigene Sequenz.
    * Wiederholungsfreiheit gilt **innerhalb einer Variante**. Zwei User mit
      unterschiedlicher Auswahl dürfen dasselbe Video sehen.
    * Ein geöffnetes Türchen merkt sich die Variante von damals (`UserDoor.
      variant`). Stellt ein User seine Kategorien später um, ändert das
      rückwirkend **nichts** — nur neue Türchen ziehen aus der neuen Auswahl.
* Login
    * Selbstregistrierung durch den User; der Account bleibt gesperrt, bis der
      Admin ihn im Admin-UI freischaltet.
    * **Bootstrap:** der erste registrierte User (genauer: der erste, solange
      noch kein Admin existiert) wird automatisch Admin und sofort
      freigeschaltet — sonst gäbe es niemanden, der ihn freischalten könnte.
      Alle danach durchlaufen wieder die normale Freigabe. `MEMECAL_ADMIN_
      PASSWORD` bleibt als alternativer Weg bestehen, einen Admin vorab per
      Env zu setzen (dann greift der Bootstrap nicht mehr).
* Tech-Stack
    * Python + FastAPI + HTMX (server-rendered, wenig JS).
    * Persistenz: SQLite (Datei-Volume), reicht für die Nutzerzahl.
* Nix / Deployment
    * Flake liefert devShell (Tooling, Python-Umgebung) und ein baubares Paket
      für den lokalen Betrieb.
    * Ausgeliefert wird per Docker / docker-compose; das Image wird aus dem
      Flake gebaut (`nix build .#dockerImage`). Compose-Setup umfasst App +
      Volume für SQLite.
    * Das Image braucht `TZDIR` auf die tzdata, sonst läuft der Container auf
      UTC — das würde den Tageswechsel und damit das Freischalten der Türchen
      um zwei Stunden verschieben.

### Default-Kanalliste

Ausgewählt aus der YouTube-Kanalsuche, dann **einzeln durchgemessen**:
letzter Upload, Anteil Videos unter 90 Sekunden, Median-Laufzeit,
Einbettbarkeit. Aussortiert wurden inaktive Kanäle, Compilation-Kanäle mit
30-Minuten-Videos, KI-Slop sowie Streamer-, Gaming- und Fandom-Bezug.

* **Memes** (allgemeine deutsche Memes): Clipvoltic, Memes King, MemeAberReal,
  Baba Memes, KartoffelPuffer, Österreich-Deutsche Memes.
* **Katzen**: 2sies Catmemes, sinascolorcats (beide deutsch), Funny Cats Time,
  Naughty cats.
* **Hunde**: The Pet Collective, AGuyAndAGolden, RxCKSTxR — hier ist die
  Sprache egal, es sind Hundevideos.

Die Liste steht in `memecal/config.py` (`DEFAULT_CHANNEL_SEED`) und wird nur
beim allerersten Start übernommen; danach zählt die DB bzw. das Admin-UI.

### Hinweis zum Schema

Die Kategorien haben das DB-Schema geändert (`variant` in `door_assignments`
und `user_doors`, `category` in `channels`/`videos`). Es gibt keine
Migrationen — eine vorhandene Entwicklungs-Datenbank muss gelöscht werden.

## Entscheidungen (2026-09-03, Nachtrag)

* **Türchen jetzt pro Kalendertag, nicht mehr pro Werktag** (Update): jeder
  Tag zwischen Start- und Enddatum eines Users bekommt ein Türchen,
  Wochenenden und Feiertage zählen mit. Vorher wurden nur Mo–Fr abzüglich
  der gesetzlichen Feiertage in Baden-Württemberg gezählt.
    * `memecal/workdays.py` (Feiertagslogik über das `holidays`-Paket) ist
      entfallen, ersetzt durch `memecal/days.py` (reine Datumsarithmetik,
      keine externe Abhängigkeit mehr).
    * Damit entfällt auch `MEMECAL_HOLIDAY_SUBDIV`/`settings.holiday_subdiv`
      vollständig — es gibt keine Feiertagsberechnung mehr, die ein
      Bundesland bräuchte.
    * Darstellung bleibt wie zuvor: Türchen zeigen weiterhin nur ihre
      Nummer, kein Datum und kein Countdown auf dem Türchen selbst.
