"""End-to-End über den TestClient, mit gefälschter YouTube-Quelle."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from memecal import service, youtube
from memecal.auth import hash_password
from memecal.db import SessionLocal, engine
from memecal.main import app
from memecal.models import Base, Channel, User, Video


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def fake_youtube(monkeypatch):
    """Ersetzt alle Netzwerkzugriffe durch einen kleinen Vorrat."""

    def entries_for(cid, client=None):
        # Je Kanal eigene Video-IDs, sonst kollidieren mehrere Kategorien.
        tag = cid[-2:]
        return [
            youtube.FeedEntry(
                video_id=f"vid{tag}{i:06d}",
                channel_id=cid,
                title=f"Testvideo {tag}-{i}",
                published=datetime(2026, 9, 1, 12, 0),
                thumbnail_url=youtube.thumbnail_for(f"vid{tag}{i:06d}"),
            )
            for i in range(30)
        ]

    entries = entries_for("UCtesttesttesttesttes01")
    monkeypatch.setattr(service.youtube, "fetch_feed", entries_for)
    monkeypatch.setattr(
        service.youtube,
        "fetch_video_meta",
        lambda vid, client=None: youtube.VideoMeta(duration=30, embeddable=True),
    )
    return entries


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_user(username: str, *, admin: bool = False, approved: bool = True,
               started_on: date | None = None, categories: str = "memes") -> int:
    with SessionLocal() as session:
        user = User(
            username=username,
            password_hash=hash_password("passwort123"),
            is_admin=admin,
            is_approved=approved,
            started_on=started_on,
            categories=categories,
        )
        session.add(user)
        session.commit()
        return user.id


def _add_channel(category: str = "memes", suffix: str = "01") -> None:
    with SessionLocal() as session:
        session.add(
            Channel(
                ref=f"@test{suffix}",
                channel_id=f"UCtesttesttesttesttes{suffix}",
                title=f"Testkanal {category}",
                category=category,
            )
        )
        session.commit()


def _login(client: TestClient, username: str) -> None:
    resp = client.post(
        "/login",
        data={"username": username, "password": "passwort123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


# --------------------------------------------------------------------------


def test_erster_registrierter_user_wird_admin(client):
    """Ohne Admin könnte sonst niemand einen wartenden Account freischalten."""
    resp = client.post(
        "/register",
        data={"username": "jonas", "password": "passwort123", "confirm": "passwort123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    with SessionLocal() as session:
        user = session.query(User).filter_by(username="jonas").one()
        assert user.is_admin
        assert user.is_approved
        assert user.started_on == date.today()


def test_zweiter_registrierter_user_landet_in_wartestellung(client):
    _make_user("chef", admin=True)
    resp = client.post(
        "/register",
        data={"username": "jonas", "password": "passwort123", "confirm": "passwort123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/pending"

    with SessionLocal() as session:
        user = session.query(User).filter_by(username="jonas").one()
        assert not user.is_admin
        assert not user.is_approved
        assert user.started_on is None


def test_registrierungsseite_kuendigt_admin_bootstrap_an(client):
    resp = client.get("/register")
    assert "wird automatisch der Admin" in resp.text

    _make_user("chef", admin=True)
    resp = client.get("/register")
    assert "muss danach noch vom Admin freigeschaltet werden" in resp.text


def test_nicht_freigeschalteter_user_sieht_keinen_kalender(client):
    _make_user("wartend", approved=False)
    _login(client, "wartend")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/pending"


def test_registrierung_lehnt_doppelten_namen_ab(client):
    _make_user("jonas")
    resp = client.post(
        "/register",
        data={"username": "jonas", "password": "passwort123", "confirm": "passwort123"},
    )
    assert resp.status_code == 200
    assert "gibt es schon" in resp.text


def test_startdatum_wird_beim_ersten_login_gesetzt(client):
    uid = _make_user("jonas")
    _login(client, "jonas")
    client.get("/")
    with SessionLocal() as session:
        assert session.get(User, uid).started_on == date.today()


def test_tuerchen_oeffnen_liefert_meme(client, fake_youtube):
    _add_channel()
    _make_user("jonas", started_on=date.today())
    _login(client, "jonas")

    resp = client.get("/door/1")
    assert resp.status_code == 200
    assert "youtube-nocookie.com/embed/" in resp.text


def test_zuordnung_bleibt_stabil(client, fake_youtube):
    """Ein geöffnetes Türchen zeigt für immer dasselbe Meme."""
    _add_channel()
    _make_user("jonas", started_on=date.today())
    _login(client, "jonas")

    first = client.get("/door/1").text
    second = client.get("/door/1").text
    assert first == second


def test_alle_user_sehen_bei_gleichem_index_dasselbe(client, fake_youtube):
    """Geteilte Sequenz, nur zeitversetzt (siehe AGENDS.md)."""
    _add_channel()
    _make_user("jonas", started_on=date.today())
    _make_user("zweiter", started_on=date.today())

    _login(client, "jonas")
    a = client.get("/door/1").text
    client.post("/logout", follow_redirects=False)

    _login(client, "zweiter")
    b = client.get("/door/1").text
    assert a == b


def test_zukuenftiges_tuerchen_bleibt_zu(client, fake_youtube):
    _add_channel()
    # Start heute => nur Türchen 1 ist frei.
    _make_user("jonas", started_on=date.today())
    _login(client, "jonas")

    resp = client.get("/door/5")
    assert resp.status_code == 403
    assert "zu früh" in resp.text


def test_zukuenftiges_tuerchen_verbraucht_kein_meme(client, fake_youtube):
    _add_channel()
    _make_user("jonas", started_on=date.today())
    _login(client, "jonas")

    client.get("/door/5")
    with SessionLocal() as session:
        assert service.assignment_for(session, "memes", 5) is None


def test_ohne_kanaele_gibt_es_eine_klare_fehlermeldung(client):
    _make_user("jonas", started_on=date.today())
    _login(client, "jonas")

    resp = client.get("/door/1")
    assert resp.status_code == 200
    assert "Kanäle konfiguriert" in resp.text


def test_zu_lange_videos_landen_nicht_im_pool(client, monkeypatch, fake_youtube):
    _add_channel()
    monkeypatch.setattr(
        service.youtube,
        "fetch_video_meta",
        lambda vid, client=None: youtube.VideoMeta(duration=600, embeddable=True),
    )
    with SessionLocal() as session:
        assert service.refill_pool(session) == 0


def test_nicht_einbettbare_videos_werden_uebersprungen(client, monkeypatch, fake_youtube):
    _add_channel()
    monkeypatch.setattr(
        service.youtube,
        "fetch_video_meta",
        lambda vid, client=None: youtube.VideoMeta(duration=30, embeddable=False),
    )
    with SessionLocal() as session:
        assert service.refill_pool(session) == 0


def test_drosselung_wird_nicht_als_leerer_pool_missdeutet(
    client, monkeypatch, fake_youtube
):
    """429 heißt 'später nochmal', nicht 'alle Videos unbrauchbar'."""
    _add_channel()

    def rate_limited(vid, client=None):
        raise youtube.RateLimited("HTTP 429")

    monkeypatch.setattr(service.youtube, "fetch_video_meta", rate_limited)

    with SessionLocal() as session:
        with pytest.raises(youtube.RateLimited):
            service.refill_pool(session)


def test_drosselung_verbraucht_kein_tuerchen(client, monkeypatch, fake_youtube):
    _add_channel()

    def rate_limited(vid, client=None):
        raise youtube.RateLimited("HTTP 429")

    monkeypatch.setattr(service.youtube, "fetch_video_meta", rate_limited)
    _make_user("jonas", started_on=date.today())
    _login(client, "jonas")

    resp = client.get("/door/1")
    assert resp.status_code == 200
    assert "drosselt" in resp.text
    with SessionLocal() as session:
        assert service.assignment_for(session, "memes", 1) is None


def test_request_pfad_holt_nur_ein_video(client, monkeypatch, fake_youtube):
    """Das Öffnen darf nicht auf die volle Reserve warten."""
    _add_channel()
    checked: list[str] = []

    def counting_meta(vid, client=None):
        checked.append(vid)
        return youtube.VideoMeta(duration=30, embeddable=True)

    monkeypatch.setattr(service.youtube, "fetch_video_meta", counting_meta)

    with SessionLocal() as session:
        service.get_or_assign(session, "memes", 1)

    # Ein Batch parallel ist ok, die volle Reserve wäre es nicht.
    assert len(checked) <= service._MAX_PARALLEL_CHECKS


def test_memes_wiederholen_sich_nicht(client, fake_youtube):
    _add_channel()
    _make_user("jonas", started_on=date.today())
    with SessionLocal() as session:
        seen = set()
        for index in range(1, 11):
            meme = service.get_or_assign(session, "memes", index)
            assert meme.video_id not in seen
            seen.add(meme.video_id)


def test_variante_ist_sortiert_und_bereinigt():
    assert service.variant_of("hunde,memes") == "hunde,memes"
    assert service.variant_of("memes,hunde") == "hunde,memes"
    assert service.variant_of("memes,quatsch") == "memes"
    assert service.variant_of("") == "memes"
    assert service.variant_of(["katzen", "katzen"]) == "katzen"


def test_kategorie_bestimmt_woraus_gezogen_wird(client, fake_youtube):
    _add_channel("memes", "01")
    _add_channel("katzen", "02")

    with SessionLocal() as session:
        katzen = service.get_or_assign(session, "katzen", 1)
        video = session.query(Video).filter_by(video_id=katzen.video_id).one()
        assert video.category == "katzen"


def test_unterschiedliche_auswahl_eigene_sequenz(client, fake_youtube):
    """Wer anderes sehen will, teilt die Sequenz nicht mehr."""
    _add_channel("memes", "01")
    _add_channel("katzen", "02")

    with SessionLocal() as session:
        a = service.get_or_assign(session, "memes", 1)
        b = service.get_or_assign(session, "katzen", 1)
        assert a.video_id != b.video_id


def test_gleiche_auswahl_teilt_die_sequenz(client, fake_youtube):
    _add_channel("memes", "01")
    _make_user("jonas", started_on=date.today(), categories="memes")
    _make_user("zweiter", started_on=date.today(), categories="memes")

    _login(client, "jonas")
    a = client.get("/door/1").text
    client.post("/logout", follow_redirects=False)
    _login(client, "zweiter")
    assert client.get("/door/1").text == a


def test_wiederholungsfrei_gilt_pro_variante(client, fake_youtube):
    """Zwei Varianten dürfen dasselbe Video zeigen, eine Variante nicht doppelt."""
    _add_channel("memes", "01")

    with SessionLocal() as session:
        seen = set()
        for index in range(1, 6):
            seen.add(service.get_or_assign(session, "memes", index).video_id)
        assert len(seen) == 5
        # Andere Variante, gleicher Kanal-Pool: Wiederverwendung ist ok.
        assert service.get_or_assign(session, "hunde,memes", 1).video_id in seen


def test_user_kann_kategorien_umstellen(client, fake_youtube):
    _add_channel("memes", "01")
    _add_channel("katzen", "02")
    uid = _make_user("jonas", started_on=date.today())
    _login(client, "jonas")

    resp = client.post(
        "/einstellungen", data={"category": ["katzen"]}, follow_redirects=False
    )
    assert resp.status_code == 303
    with SessionLocal() as session:
        assert session.get(User, uid).categories == "katzen"


def test_mehrfachauswahl_moeglich(client, fake_youtube):
    uid = _make_user("jonas", started_on=date.today())
    _login(client, "jonas")

    client.post(
        "/einstellungen",
        data={"category": ["hunde", "katzen"]},
        follow_redirects=False,
    )
    with SessionLocal() as session:
        assert session.get(User, uid).categories == "hunde,katzen"


def test_leere_auswahl_faellt_auf_memes_zurueck(client, fake_youtube):
    uid = _make_user("jonas", started_on=date.today(), categories="katzen")
    _login(client, "jonas")

    client.post("/einstellungen", data={}, follow_redirects=False)
    with SessionLocal() as session:
        assert session.get(User, uid).categories == "memes"


def test_geoeffnetes_tuerchen_ueberlebt_kategoriewechsel(client, fake_youtube):
    """Umstellen darf die Vergangenheit nicht umschreiben."""
    _add_channel("memes", "01")
    _add_channel("katzen", "02")
    _make_user("jonas", started_on=date.today(), categories="memes")
    _login(client, "jonas")

    vorher = client.get("/door/1").text
    client.post("/einstellungen", data={"category": ["katzen"]},
                follow_redirects=False)
    assert client.get("/door/1").text == vorher


def test_admin_kann_freischalten(client):
    _make_user("chef", admin=True)
    wartend = _make_user("wartend", approved=False)
    _login(client, "chef")

    resp = client.post(f"/admin/users/{wartend}/approve", follow_redirects=False)
    assert resp.status_code == 303
    with SessionLocal() as session:
        assert session.get(User, wartend).is_approved


def test_normaler_user_kommt_nicht_ins_admin(client):
    _make_user("jonas")
    _login(client, "jonas")
    assert client.get("/admin").status_code == 403


def test_admin_kann_sich_nicht_selbst_sperren(client):
    uid = _make_user("chef", admin=True)
    _login(client, "chef")
    client.post(f"/admin/users/{uid}/revoke", follow_redirects=False)
    with SessionLocal() as session:
        assert session.get(User, uid).is_approved


def test_enddatum_ist_einstellbar(client):
    _make_user("chef", admin=True)
    _login(client, "chef")

    resp = client.post(
        "/admin/settings", data={"end_date": "2027-03-01"}, follow_redirects=False
    )
    assert resp.status_code == 303
    with SessionLocal() as session:
        from memecal.calendar_view import effective_end_date

        assert effective_end_date(session) == date(2027, 3, 1)


def test_user_kann_eigenes_enddatum_setzen(client):
    uid = _make_user("jonas", started_on=date.today())
    _login(client, "jonas")

    resp = client.post(
        "/einstellungen", data={"end_date": "2027-06-01"}, follow_redirects=False
    )
    assert resp.status_code == 303
    with SessionLocal() as session:
        assert session.get(User, uid).end_date == date(2027, 6, 1)


def test_eigenes_enddatum_ueberschreibt_das_globale(client):
    from memecal.calendar_view import effective_end_date_for

    _make_user("chef", admin=True)
    uid = _make_user("jonas", started_on=date.today())
    _login(client, "chef")
    client.post("/admin/settings", data={"end_date": "2027-03-01"})

    with SessionLocal() as session:
        user = session.get(User, uid)
        assert effective_end_date_for(session, user) == date(2027, 3, 1)

        user.end_date = date(2027, 12, 24)
        session.commit()
        assert effective_end_date_for(session, user) == date(2027, 12, 24)


def test_leeres_enddatum_faellt_auf_global_zurueck(client):
    uid = _make_user("jonas", started_on=date.today())
    with SessionLocal() as session:
        session.get(User, uid).end_date = date(2027, 1, 1)
        session.commit()

    _login(client, "jonas")
    client.post("/einstellungen", data={"end_date": ""}, follow_redirects=False)
    with SessionLocal() as session:
        assert session.get(User, uid).end_date is None


def test_ungueltiges_enddatum_wird_abgelehnt(client):
    uid = _make_user("jonas", started_on=date.today())
    _login(client, "jonas")

    resp = client.post("/einstellungen", data={"end_date": "nicht-ein-datum"})
    assert resp.status_code == 200
    assert "Format JJJJ-MM-TT" in resp.text
    with SessionLocal() as session:
        assert session.get(User, uid).end_date is None


def test_eigenes_enddatum_wirkt_auf_tuerchenzahl(client, fake_youtube):
    """Ein kürzeres persönliches Enddatum lässt weniger Türchen zu."""
    from memecal.days import total_doors

    start = date.today()
    end = start + timedelta(days=13)  # knapp zwei Wochen, unabhängig vom Wochentag
    expected = total_doors(start, end)
    assert expected > 0

    _add_channel()
    uid = _make_user("jonas", started_on=start)
    with SessionLocal() as session:
        session.get(User, uid).end_date = end
        session.commit()
    _login(client, "jonas")

    resp = client.get("/")
    assert resp.status_code == 200
    assert f"{expected} Türchen" in resp.text
    # Türchen jenseits des persönlichen Enddatums existiert nicht mehr.
    assert client.get(f"/door/{expected + 1}").status_code == 404


def test_anonymer_zugriff_wird_umgeleitet(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_favorit_markieren_und_wieder_entfernen(client, fake_youtube):
    _add_channel()
    _make_user("jonas", started_on=date.today())
    _login(client, "jonas")
    client.get("/door/1")

    resp = client.post("/door/1/favorite")
    assert resp.status_code == 200
    assert "is-fav" in resp.text
    assert 'aria-pressed="true"' in resp.text

    resp = client.post("/door/1/favorite")
    assert "is-fav" not in resp.text
    assert 'aria-pressed="false"' in resp.text


def test_favorit_auf_ungeoeffnetem_tuerchen_schlaegt_fehl(client, fake_youtube):
    _add_channel()
    _make_user("jonas", started_on=date.today())
    _login(client, "jonas")

    resp = client.post("/door/1/favorite")
    assert resp.status_code == 404


def test_favoriten_seite_zeigt_nur_markierte_tuerchen(client, fake_youtube):
    import re

    _add_channel()
    _make_user("jonas", started_on=date.today() - timedelta(days=3))
    _login(client, "jonas")

    door1 = client.get("/door/1").text
    client.get("/door/2")
    client.post("/door/1/favorite")

    title1 = re.search(r'<p class="meme-title">(.*?)</p>', door1).group(1)

    resp = client.get("/favoriten")
    assert resp.status_code == 200
    assert title1 in resp.text
    assert resp.text.count('class="fav-card"') == 1


def test_favoriten_seite_leer_ohne_favoriten(client):
    _make_user("jonas", started_on=date.today())
    _login(client, "jonas")

    resp = client.get("/favoriten")
    assert resp.status_code == 200
    assert "Noch keine Favoriten" in resp.text
