"""MongoDB access: metadata only. Audio bytes are never stored in MongoDB."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from .config import settings

log = logging.getLogger("consult")

_client: MongoClient | None = None
_indexes_ready = False


def get_db() -> Database:
    global _client, _indexes_ready
    if _client is None:
        _client = MongoClient(settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=8000)
    db = _client[settings.mongo_db]
    if not _indexes_ready:
        ensure_indexes(db)
        _indexes_ready = True
    return db


def ensure_indexes(db: Database) -> None:
    db.doctors.create_index("doctorId", unique=True)
    db.doctors.create_index("email", unique=True)
    db.patients.create_index("patientId", unique=True)
    db.patients.create_index([("ownerDoctorId", ASCENDING), ("lastConsultationAt", DESCENDING)])
    db.consultations.create_index("consultationId", unique=True)
    db.consultations.create_index([("doctorId", ASCENDING), ("startedAt", DESCENDING)])
    db.consultations.create_index(
        [("doctorId", ASCENDING), ("patientId", ASCENDING), ("startedAt", DESCENDING)]
    )
    db.consultations.create_index("transcript.status")


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_doctor_id() -> str:
    return "DOC-" + secrets.token_hex(3).upper()


def new_patient_id() -> str:
    return "PAT-%06d" % secrets.randbelow(1_000_000)


def new_consultation_id() -> str:
    return "CON-%s-%s" % (now().strftime("%Y%m%d"), secrets.token_hex(3))
