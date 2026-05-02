"""
Per-user admin identity — CRUD helpers for the Firestore `users` collection.

These functions are intentionally read-heavy; writes only happen during
bootstrap (get_or_create_user) and explicit admin actions (disable_user).

Firestore is accessed via the shared THODatabase singleton so the lazy-load
and project-ID detection logic is reused automatically.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from database.models import Role, User

logger = logging.getLogger(__name__)

USERS_COLLECTION = "users"

# Emails allowed to auto-register on first PIN login.
# Populated from the ADMIN_USER_EMAILS env var (comma-separated) or left empty
# to disable auto-registration (operator must seed users manually).
_ADMIN_USER_EMAILS: set[str] = {
    e.strip().lower()
    for e in os.environ.get("ADMIN_USER_EMAILS", "").split(",")
    if e.strip()
}


def _db():
    """Lazy import to avoid circular dependency on startup."""
    from database.firestore_client import get_database
    return get_database()


def get_user_by_email(email: str) -> Optional[User]:
    """Return the User record for *email*, or None if not found."""
    email = email.lower().strip()
    try:
        docs = (
            _db()
            .db.collection(USERS_COLLECTION)
            .where("email", "==", email)
            .limit(1)
            .stream()
        )
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            return User(**data)
    except Exception:
        logger.exception("get_user_by_email failed for %s", email)
    return None


def get_user_by_id(user_id: str) -> Optional[User]:
    """Return the User record for *user_id*, or None if not found."""
    try:
        doc = _db().db.collection(USERS_COLLECTION).document(user_id).get()
        if doc.exists:
            data = doc.to_dict()
            data["id"] = doc.id
            return User(**data)
    except Exception:
        logger.exception("get_user_by_id failed for %s", user_id)
    return None


def get_or_create_user(
    email: str,
    display_name: str = "",
    role: Role = Role.STAFF,
) -> Optional[User]:
    """Return an existing User or auto-create one if the email is in the allowlist.

    Returns None if *email* is not in ADMIN_USER_EMAILS and no existing record
    was found — callers should treat this as an authorization failure.
    """
    email = email.lower().strip()

    existing = get_user_by_email(email)
    if existing:
        if existing.disabled:
            logger.warning("Blocked login attempt for disabled user %s", email)
            return None
        _touch_last_login(existing.id)
        existing.last_login_at = datetime.utcnow()
        return existing

    if email not in _ADMIN_USER_EMAILS:
        logger.warning("Auto-registration denied for unrecognised email %s", email)
        return None

    user = User(
        email=email,
        display_name=display_name or email.split("@")[0],
        role=role,
    )
    try:
        _db().db.collection(USERS_COLLECTION).document(user.id).set(
            {
                "email": user.email,
                "display_name": user.display_name,
                "role": user.role,
                "created_at": user.created_at,
                "last_login_at": None,
                "disabled": False,
            }
        )
        logger.info("Auto-created user record for %s (id=%s)", email, user.id)
    except Exception:
        logger.exception("Failed to create user record for %s", email)
        return None

    return user


def disable_user(user_id: str) -> bool:
    """Set disabled=True on a user record. Returns True on success."""
    try:
        _db().db.collection(USERS_COLLECTION).document(user_id).update(
            {"disabled": True}
        )
        logger.info("Disabled user %s", user_id)
        return True
    except Exception:
        logger.exception("disable_user failed for %s", user_id)
        return False


def list_active_users() -> list[User]:
    """Return all non-disabled User records."""
    results: list[User] = []
    try:
        docs = (
            _db()
            .db.collection(USERS_COLLECTION)
            .where("disabled", "==", False)
            .stream()
        )
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            try:
                results.append(User(**data))
            except Exception:
                logger.warning("Skipping malformed user doc %s", doc.id)
    except Exception:
        logger.exception("list_active_users failed")
    return results


# ── Internal helpers ──────────────────────────────────────────────────────────


def _touch_last_login(user_id: str) -> None:
    try:
        _db().db.collection(USERS_COLLECTION).document(user_id).update(
            {"last_login_at": datetime.utcnow()}
        )
    except Exception:
        logger.warning("Failed to update last_login_at for %s", user_id)
