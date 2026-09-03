"""FastAPI-App: Kalender, Login und Admin-UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import service, youtube
from .auth import (
    SESSION_KEY,
    authenticate,
    current_user,
    ensure_started,
    hash_password,
    require_admin,
    require_user,
    validate_registration,
)
from .calendar_view import (
    build_doors,
    door_count,
    effective_end_date,
    effective_end_date_for,
)
from .config import CATEGORIES, DEFAULT_CATEGORY, settings
from .db import get_session, init_db, session_scope
from .models import Channel, User, UserDoor
from .workdays import unlocked_count

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    _seed_admin()
    _seed_channels()
    yield


app = FastAPI(
    title="Meme-Kalender", docs_url=None, redoc_url=None, lifespan=lifespan
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.resolve_secret_key(),
    https_only=False,  # TLS terminiert der Reverse-Proxy
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def today() -> date:
    return date.today()


def _seed_admin() -> None:
    if not settings.admin_password:
        return
    with session_scope() as session:
        existing = session.scalar(
            select(User).where(User.username == settings.admin_username)
        )
        if existing is not None:
            return
        session.add(
            User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                is_admin=True,
                is_approved=True,
            )
        )
        log.info("Admin-Account '%s' angelegt", settings.admin_username)


def _seed_channels() -> None:
    """Default-Kanäle beim ersten Start übernehmen. Leer ist ok."""
    if not settings.default_channels:
        return
    with session_scope() as session:
        if session.scalar(select(Channel).limit(1)) is not None:
            return
        for entry in settings.default_channels:
            ref, _, category = entry.partition(":")
            category = category or DEFAULT_CATEGORY
            try:
                service.add_channel(session, ref, category)
                log.info("Default-Kanal '%s' (%s) übernommen", ref, category)
            except Exception as exc:
                log.warning("Default-Kanal '%s' übersprungen: %s", ref, exc)


def render(
    request: Request, name: str, status_code: int = 200, **context
) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, status_code=status_code)


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user: User | None = Depends(current_user)):
    if user is not None and user.is_approved:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "login.html")


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    user = authenticate(session, username.strip(), password)
    if user is None:
        return render(
            request, "login.html", error="Benutzername oder Passwort stimmt nicht."
        )
    request.session[SESSION_KEY] = user.id
    if not user.is_approved:
        return RedirectResponse("/pending", status_code=status.HTTP_303_SEE_OTHER)
    ensure_started(session, user, today())
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


def _no_admin_yet(session: Session) -> bool:
    return session.scalar(select(User).where(User.is_admin.is_(True))) is None


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request, session: Session = Depends(get_session)):
    return render(request, "register.html", no_admin_yet=_no_admin_yet(session))


@app.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    session: Session = Depends(get_session),
):
    username = username.strip()
    error = validate_registration(username, password, confirm)
    if error:
        return render(
            request,
            "register.html",
            error=error,
            username=username,
            no_admin_yet=_no_admin_yet(session),
        )

    # Ohne Admin gäbe es niemanden, der einen wartenden Account freischalten
    # könnte - deshalb wird der erste registrierte User selbst zum Admin.
    is_first = _no_admin_yet(session)
    user = User(
        username=username,
        password_hash=hash_password(password),
        is_admin=is_first,
        is_approved=is_first,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return render(
            request,
            "register.html",
            error="Diesen Benutzernamen gibt es schon.",
            username=username,
            no_admin_yet=_no_admin_yet(session),
        )

    request.session[SESSION_KEY] = user.id
    if is_first:
        ensure_started(session, user, today())
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/pending", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/pending", response_class=HTMLResponse)
def pending(request: Request, user: User | None = Depends(current_user)):
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if user.is_approved:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "pending.html", user=user)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------
# Kalender
# --------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def calendar(
    request: Request,
    user: User | None = Depends(current_user),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not user.is_approved:
        return RedirectResponse("/pending", status_code=status.HTTP_303_SEE_OTHER)

    ensure_started(session, user, today())
    end = effective_end_date_for(session, user)
    doors = build_doors(session, user, today(), end)
    unlocked = unlocked_count(
        user.started_on, today(), end, settings.holiday_subdiv
    )
    return render(
        request,
        "calendar.html",
        user=user,
        doors=doors,
        end_date=end,
        total=door_count(user, end),
        unlocked=unlocked,
        columns=settings.grid_columns,
        selection=", ".join(
            CATEGORIES[c] for c in service.normalize_categories(user.categories)
        ),
    )


@app.get("/einstellungen", response_class=HTMLResponse)
def settings_form(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
    message: str | None = None,
    error: str | None = None,
):
    return render(
        request,
        "settings.html",
        user=user,
        categories=CATEGORIES,
        chosen=service.normalize_categories(user.categories),
        end_date=effective_end_date_for(session, user),
        has_custom_end_date=user.end_date is not None,
        message=message,
        error=error,
    )


@app.post("/einstellungen")
def update_user_settings(
    request: Request,
    category: Annotated[list[str] | None, Form()] = None,
    end_date: str = Form(""),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    chosen = service.normalize_categories(category)
    user.categories = ",".join(chosen)

    end_date = end_date.strip()
    if end_date:
        try:
            user.end_date = date.fromisoformat(end_date)
        except ValueError:
            return render(
                request,
                "settings.html",
                user=user,
                categories=CATEGORIES,
                chosen=chosen,
                end_date=user.end_date or effective_end_date_for(session, user),
                has_custom_end_date=user.end_date is not None,
                error="Enddatum muss im Format JJJJ-MM-TT sein.",
            )
    else:
        # Leeres Feld heißt zurück zum globalen Default, nicht "keinen Kalender".
        user.end_date = None

    session.add(user)
    session.commit()
    labels = ", ".join(CATEGORIES[c] for c in chosen)
    return RedirectResponse(
        f"/einstellungen?message={quote(f'Gespeichert: {labels}')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/door/{index}", response_class=HTMLResponse)
def open_door(
    index: int,
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Öffnet ein Türchen. Hier hängt der lazy Feed-Abruf dran."""
    end = effective_end_date_for(session, user)
    total = door_count(user, end)
    if index < 1 or index > total:
        raise HTTPException(status_code=404, detail="Dieses Türchen gibt es nicht.")

    unlocked = unlocked_count(
        user.started_on, today(), end, settings.holiday_subdiv
    )
    if index > unlocked:
        return render(
            request, "partials/door_locked.html", status_code=403, index=index
        )

    # Ein bereits geöffnetes Türchen behält die Variante von damals.
    existing = session.scalar(
        select(UserDoor).where(UserDoor.user_id == user.id, UserDoor.index == index)
    )
    variant = existing.variant if existing else service.variant_of(user.categories)

    try:
        meme = service.get_or_assign(session, variant, index)
    except service.NoMemeAvailable as exc:
        return render(request, "partials/door_error.html", index=index, message=str(exc))

    if existing is None:
        session.add(UserDoor(user_id=user.id, index=index, variant=variant))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()

    # Erst antworten, dann nachlegen - der User wartet nicht auf die Reserve.
    background.add_task(service.top_up_reserve, variant)
    return render(request, "partials/door_open.html", index=index, meme=meme)


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------


@app.get("/admin", response_class=HTMLResponse)
def admin(
    request: Request,
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
    message: str | None = None,
    error: str | None = None,
):
    users = list(session.scalars(select(User).order_by(User.created_at)).all())
    channels = list(session.scalars(select(Channel).order_by(Channel.created_at)).all())
    return render(
        request,
        "admin.html",
        user=admin_user,
        users=users,
        end_dates={u.id: effective_end_date_for(session, u) for u in users},
        channels=channels,
        end_date=effective_end_date(session),
        pool=service.pool_by_category(session),
        categories=CATEGORIES,
        message=message,
        error=error,
    )


def _admin_redirect(message: str | None = None, error: str | None = None) -> Response:
    params = {k: v for k, v in {"message": message, "error": error}.items() if v}
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/admin{suffix}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/users/{user_id}/approve")
def approve_user(
    user_id: int,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    target = session.get(User, user_id)
    if target is None:
        return _admin_redirect(error="Unbekannter User.")
    target.is_approved = True
    session.commit()
    return _admin_redirect(message=f"{target.username} freigeschaltet.")


@app.post("/admin/users/{user_id}/revoke")
def revoke_user(
    user_id: int,
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    target = session.get(User, user_id)
    if target is None:
        return _admin_redirect(error="Unbekannter User.")
    if target.id == admin_user.id:
        return _admin_redirect(error="Du kannst dich nicht selbst sperren.")
    target.is_approved = False
    session.commit()
    return _admin_redirect(message=f"{target.username} gesperrt.")


@app.post("/admin/channels")
def add_channel(
    ref: str = Form(...),
    category: str = Form(DEFAULT_CATEGORY),
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    ref = ref.strip()
    if not ref:
        return _admin_redirect(error="Kein Kanal angegeben.")
    try:
        channel = service.add_channel(session, ref, category)
    except (ValueError, youtube.YouTubeError) as exc:
        return _admin_redirect(error=str(exc))
    return _admin_redirect(
        message=f"Kanal '{channel.title}' als {CATEGORIES[channel.category]} "
        "hinzugefügt."
    )


@app.post("/admin/channels/{channel_id}/toggle")
def toggle_channel(
    channel_id: int,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    channel = session.get(Channel, channel_id)
    if channel is None:
        return _admin_redirect(error="Unbekannter Kanal.")
    channel.enabled = not channel.enabled
    session.commit()
    state = "aktiviert" if channel.enabled else "deaktiviert"
    return _admin_redirect(message=f"Kanal '{channel.title}' {state}.")


@app.post("/admin/channels/{channel_id}/delete")
def delete_channel(
    channel_id: int,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    channel = session.get(Channel, channel_id)
    if channel is None:
        return _admin_redirect(error="Unbekannter Kanal.")
    session.delete(channel)
    session.commit()
    return _admin_redirect(message=f"Kanal '{channel.title}' entfernt.")


@app.post("/admin/settings")
def update_settings(
    end_date: str = Form(...),
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    try:
        parsed = date.fromisoformat(end_date.strip())
    except ValueError:
        return _admin_redirect(error="Enddatum muss im Format JJJJ-MM-TT sein.")
    service.set_setting(session, "end_date", parsed.isoformat())
    return _admin_redirect(message=f"Enddatum auf {parsed.isoformat()} gesetzt.")


@app.post("/admin/pool/refill")
def refill_pool(
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    try:
        added = service.refill_pool(session)
    except Exception as exc:  # pragma: no cover
        return _admin_redirect(error=f"Nachschub fehlgeschlagen: {exc}")
    return _admin_redirect(message=f"{added} neue Videos in den Pool geholt.")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
