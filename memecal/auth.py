"""Login, Registrierung und Zugriffsschutz.

Selbstregistrierung ist erlaubt, der Account bleibt aber gesperrt, bis ein
Admin ihn freischaltet (siehe AGENDS.md).
"""

from __future__ import annotations

import re
from datetime import date

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import User

_hasher = PasswordHasher()

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD_LENGTH = 8

SESSION_KEY = "uid"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    try:
        _hasher.verify(hashed, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def validate_registration(username: str, password: str, confirm: str) -> str | None:
    """Gibt eine Fehlermeldung zurück, oder None wenn alles passt."""
    if not USERNAME_RE.match(username):
        return "Benutzername: 3-32 Zeichen, nur Buchstaben, Ziffern, . _ -"
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben."
    if password != confirm:
        return "Die Passwörter stimmen nicht überein."
    return None


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        # Dummy-Hash, damit ein unbekannter Name nicht schneller antwortet.
        _hasher.hash(password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    return user


def current_user(
    request: Request, session: Session = Depends(get_session)
) -> User | None:
    uid = request.session.get(SESSION_KEY)
    if uid is None:
        return None
    return session.get(User, uid)


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht eingeloggt"
        )
    if not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Noch nicht freigeschaltet"
        )
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin-Rechte nötig"
        )
    return user


def ensure_started(session: Session, user: User, today: date) -> User:
    """Setzt das persönliche Startdatum beim ersten Login nach Freischaltung."""
    if user.is_approved and user.started_on is None:
        user.started_on = today
        session.add(user)
        session.commit()
    return user
