"""Pilot authentication: doctor accounts (bcrypt) + signed, HTTP-only session cookie.

Access model: every doctor sees ONLY their own patients and consultations.
There is no admin/shared view in the pilot.
"""
from __future__ import annotations

import threading
import time

import bcrypt
from fastapi import HTTPException, Request

from .db import get_db

_failed: dict[str, list[float]] = {}
_failed_lock = threading.Lock()
MAX_FAILURES = 8
WINDOW_SECONDS = 15 * 60


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def too_many_failures(key: str) -> bool:
    cutoff = time.time() - WINDOW_SECONDS
    with _failed_lock:
        attempts = [t for t in _failed.get(key, []) if t > cutoff]
        _failed[key] = attempts
        return len(attempts) >= MAX_FAILURES


def record_failure(key: str) -> None:
    with _failed_lock:
        _failed.setdefault(key, []).append(time.time())


def clear_failures(key: str) -> None:
    with _failed_lock:
        _failed.pop(key, None)


def current_doctor(request: Request) -> dict:
    doctor_id = request.session.get("doctorId")
    if not doctor_id:
        raise HTTPException(status_code=401, detail="Not signed in")
    doctor = get_db().doctors.find_one({"doctorId": doctor_id, "active": {"$ne": False}})
    if not doctor:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not signed in")
    return doctor
