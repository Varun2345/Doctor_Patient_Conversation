"""Consultation Recorder - pilot API + static frontend."""
from __future__ import annotations

import logging
import re
import secrets
import threading
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError, PyMongoError
from starlette.middleware.sessions import SessionMiddleware

from . import audio_store, processing, storage
from .config import BASE_DIR, settings
from .db import get_db, new_consultation_id, new_doctor_id, new_patient_id, now
from .security import (clear_failures, current_doctor, hash_password, record_failure,
                       too_many_failures, verify_password)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("consult")

STATIC_DIR = BASE_DIR / "static"
app = FastAPI(title=settings.app_title, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="cr_session",
    max_age=settings.session_hours * 3600,
    same_site="strict",
    https_only=settings.cookie_secure,
)

CSP = ("default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/") or request.url.path in ("/", "/index.html"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(PyMongoError)
async def mongo_error(_: Request, exc: PyMongoError):
    log.error("database error: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


@app.on_event("startup")
def startup() -> None:
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    for note in settings.boundary_report():
        log.warning("DATA BOUNDARY / CONFIG: %s", note)
    log.info("audio store: %s | staging dir: %s", settings.audio_store, settings.recordings_dir)
    log.info("post-processing: %s", processing.capabilities())
    try:
        get_db()
        migrated = processing.migrate_transcripts()
        if migrated:
            log.info("converted %d transcript(s) to the single-transcript format", migrated)
        n = processing.requeue_unfinished()
        if n:
            log.info("re-queued %d unfinished processing jobs", n)
    except PyMongoError as exc:
        log.error("database not reachable at startup (will retry on demand): %s", exc)
    processing.start_worker()


# --------------------------------------------------------------------------- helpers
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def public_doctor(d: dict) -> dict:
    return {"doctorId": d["doctorId"], "fullName": d.get("fullName", ""),
            "email": d.get("email", ""), "specialization": d.get("specialization")}


def public_patient(p: dict | None) -> dict | None:
    if not p:
        return None
    return {k: iso(p.get(k)) for k in ("patientId", "fullName", "hospitalId", "age", "gender",
                                         "createdAt", "lastConsultationAt")}


def display_status(c: dict) -> str:
    status = c.get("status", "recording")
    if status == "recording":
        last = c.get("lastChunkAt") or c.get("startedAt")
        if last and now() - last > timedelta(minutes=settings.stale_minutes):
            return "interrupted"
    return status


def audio_public(meta: dict | None) -> dict | None:
    if not meta:
        return None
    out = {k: iso(meta.get(k)) for k in ("status", "sizeBytes", "sha256", "mimeType", "savedAt",
                                         "verified", "verifiedAt", "error", "filter", "store")}
    out["available"] = bool(meta.get("sha256"))
    return out


def public_consultation(c: dict, patient: dict | None = None, detail: bool = False) -> dict:
    audio = c.get("audio") or {}
    tr = c.get("transcript") or {}
    out = {
        "consultationId": c["consultationId"],
        "doctorId": c["doctorId"],
        "doctorName": c.get("doctorName"),
        "patientId": c.get("patientId"),
        "patient": public_patient(patient),
        "status": display_status(c),
        "startedAt": iso(c.get("startedAt")),
        "endedAt": iso(c.get("endedAt")),
        "durationSec": c.get("durationSec"),
        "endReason": c.get("endReason"),
        "chunksReceived": c.get("chunksReceived", 0),
        "missingChunks": c.get("missingChunks", []),
        "audio": {"original": audio_public(audio.get("original"))},
        "transcript": {"status": tr.get("status", "disabled"),
                       "edited": bool(tr.get("edited"))},
        "diarization": {"status": (c.get("diarization") or {}).get("status", "disabled")},
    }
    if detail:
        out["transcript"].update({k: iso(tr.get(k)) for k in (
            "engine", "model", "language", "languageProbability", "segments", "text", "stage",
            "editedAt", "editedByName", "error", "updatedAt")})
        out["diarization"] = {k: (c.get("diarization") or {}).get(k)
                              for k in ("status", "engine", "speakers", "error", "turns")}
        out["capture"] = c.get("capture")
        out["consent"] = c.get("consent")
        out["events"] = [{**e, "at": iso(e.get("at"))} for e in c.get("events", [])][-50:]
    return out


def owned_consultation(cid: str, doctor: dict) -> dict:
    c = get_db().consultations.find_one({"consultationId": cid, "doctorId": doctor["doctorId"]})
    if not c:
        raise HTTPException(status_code=404, detail="Consultation not found")
    return c


def owned_patient(pid: str, doctor: dict) -> dict:
    p = get_db().patients.find_one({"patientId": pid, "ownerDoctorId": doctor["doctorId"]})
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found")
    return p


def add_event(cid: str, etype: str, by: str, detail: str | None = None) -> None:
    get_db().consultations.update_one(
        {"consultationId": cid},
        {"$push": {"events": {"at": now(), "type": etype, "by": by, "detail": detail}}},
    )


# --------------------------------------------------------------------------- public config
def logo_file(stem: str = "logo"):
    """branding/logo.* = square emblem (top-left box); branding/logo-full.* = full logo (footer)."""
    for ext in ("svg", "png", "jpg", "jpeg", "webp"):
        path = settings.branding_dir / f"{stem}.{ext}"
        if path.is_file():
            return path
    return None


@app.get("/api/config")
def public_config():
    return {
        "appTitle": settings.app_title,
        "institutionName": settings.institution_name,
        "piName": settings.pi_name or None,
        "logoUrl": "/branding/logo" if logo_file() else None,
        "logoFullUrl": "/branding/logo-full" if logo_file("logo-full") else None,
        "teamNames": settings.team_names,
        "dbInternal": settings.db_internal,
        "audioStore": settings.audio_store,
        "consent": {"mode": settings.consent_mode, "text": settings.consent_text},
        "requirePatient": settings.require_patient,
        "capture": {"processing": settings.capture_processing,
                    "bitrate": settings.audio_bitrate,
                    "chunkSeconds": settings.chunk_seconds},
        "processing": processing.capabilities(),
        "signup": {"enabled": settings.signup_enabled, "codeRequired": bool(settings.signup_code)},
    }


@app.get("/branding/logo")
def branding_logo():
    path = logo_file()
    if not path:
        raise HTTPException(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/branding/logo-full")
def branding_logo_full():
    path = logo_file("logo-full")
    if not path:
        raise HTTPException(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/health")
def health():
    db_ok = True
    try:
        get_db().command("ping")
    except PyMongoError:
        db_ok = False
    free = storage.disk_free_bytes()
    return {"ok": db_ok, "database": db_ok, "stagingDiskFreeBytes": free,
            "audioStore": settings.audio_store}


# --------------------------------------------------------------------------- auth
class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request):
    email = body.email.strip().lower()
    if too_many_failures(email):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in 15 minutes.")
    doctor = get_db().doctors.find_one({"email": email, "active": {"$ne": False}})
    if not doctor or not verify_password(body.password, doctor.get("passwordHash", "")):
        record_failure(email)
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    clear_failures(email)
    request.session.clear()
    request.session["doctorId"] = doctor["doctorId"]
    get_db().doctors.update_one({"_id": doctor["_id"]}, {"$set": {"lastLoginAt": now()}})
    return {"doctor": public_doctor(doctor)}


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupIn(BaseModel):
    fullName: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)
    specialization: Optional[str] = Field(default=None, max_length=120)
    signupCode: Optional[str] = Field(default=None, max_length=200)


@app.post("/api/auth/signup", status_code=201)
def signup(body: SignupIn, request: Request):
    if not settings.signup_enabled:
        raise HTTPException(status_code=403, detail="Sign-up is turned off. Ask the pilot administrator for an account.")
    client = "signup:" + (request.client.host if request.client else "?")
    if too_many_failures(client):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in 15 minutes.")
    if settings.signup_code:
        if not secrets.compare_digest((body.signupCode or "").strip(), settings.signup_code):
            record_failure(client)
            raise HTTPException(status_code=403, detail="The sign-up code is not correct.")
    name = body.fullName.strip()
    email = body.email.strip().lower()
    if not name:
        raise HTTPException(status_code=422, detail="Enter your full name")
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    if not body.password:
        raise HTTPException(status_code=422, detail="Enter a password")
    db = get_db()
    if db.doctors.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="An account with this email already exists. Sign in instead.")
    doc = {"fullName": name, "email": email, "passwordHash": hash_password(body.password),
           "specialization": (body.specialization or "").strip() or None, "active": True,
           "createdVia": "signup", "createdAt": now(), "updatedAt": now(), "lastLoginAt": now()}
    for _ in range(5):
        doc["doctorId"] = new_doctor_id()
        doc.pop("_id", None)
        try:
            db.doctors.insert_one(doc)
            break
        except DuplicateKeyError as exc:
            if "email" in str(exc):
                raise HTTPException(status_code=409, detail="An account with this email already exists. Sign in instead.")
    else:
        raise HTTPException(status_code=500, detail="Could not create the account")
    log.info("new doctor account via sign-up: %s", doc["doctorId"])
    request.session.clear()
    request.session["doctorId"] = doc["doctorId"]
    return {"doctor": public_doctor(doc)}


@app.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(doctor: dict = Depends(current_doctor)):
    return {"doctor": public_doctor(doctor)}


# --------------------------------------------------------------------------- patients
class PatientIn(BaseModel):
    fullName: Optional[str] = Field(default=None, max_length=120)
    hospitalId: Optional[str] = Field(default=None, max_length=60)
    age: Optional[int] = Field(default=None, ge=0, le=130)
    gender: Optional[Literal["female", "male", "other"]] = None


def _clean_patient(body: PatientIn) -> dict:
    data = body.model_dump(exclude_unset=True)
    for key in ("fullName", "hospitalId"):
        if key in data:
            data[key] = (data[key] or "").strip() or None
    return data


@app.get("/api/patients")
def list_patients(q: str = Query(default="", max_length=100),
                  limit: int = Query(default=20, ge=1, le=100),
                  doctor: dict = Depends(current_doctor)):
    db = get_db()
    query: dict = {"ownerDoctorId": doctor["doctorId"]}
    q = q.strip()
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        query["$or"] = [{"fullName": rx}, {"hospitalId": rx}, {"patientId": rx}]
    patients = list(db.patients.find(query).sort([("lastConsultationAt", -1),
                                                  ("createdAt", -1)]).limit(limit))
    counts = {r["_id"]: r["n"] for r in db.consultations.aggregate([
        {"$match": {"doctorId": doctor["doctorId"],
                    "patientId": {"$in": [p["patientId"] for p in patients]}}},
        {"$group": {"_id": "$patientId", "n": {"$sum": 1}}}])}
    return {"patients": [{**public_patient(p), "consultationCount": counts.get(p["patientId"], 0)}
                         for p in patients]}


@app.post("/api/patients", status_code=201)
def create_patient(body: PatientIn, doctor: dict = Depends(current_doctor)):
    data = _clean_patient(body)
    if not data.get("fullName"):
        raise HTTPException(status_code=422, detail="Enter the patient's name")
    if data.get("hospitalId"):
        existing = get_db().patients.find_one({
            "ownerDoctorId": doctor["doctorId"],
            "hospitalId": {"$regex": f"^{re.escape(data['hospitalId'])}$", "$options": "i"}})
        if existing:
            raise HTTPException(status_code=409, detail=(
                f"A patient with hospital ID {data['hospitalId']} already exists "
                f"({existing.get('fullName') or existing['patientId']}). Search and select them instead."))
    for _ in range(5):
        doc = {"patientId": new_patient_id(), "ownerDoctorId": doctor["doctorId"],
               "createdBy": doctor["doctorId"], "createdAt": now(), "updatedAt": now(),
               "lastConsultationAt": None, **data}
        try:
            get_db().patients.insert_one(doc)
            return {"patient": public_patient(doc)}
        except DuplicateKeyError:
            continue
    raise HTTPException(status_code=500, detail="Could not allocate a patient ID")


@app.get("/api/patients/{pid}")
def get_patient(pid: str, doctor: dict = Depends(current_doctor)):
    p = owned_patient(pid, doctor)
    cons = get_db().consultations.find(
        {"doctorId": doctor["doctorId"], "patientId": pid}).sort("startedAt", -1).limit(200)
    return {"patient": public_patient(p),
            "consultations": [public_consultation(c, p) for c in cons]}


@app.patch("/api/patients/{pid}")
def update_patient(pid: str, body: PatientIn, doctor: dict = Depends(current_doctor)):
    p = owned_patient(pid, doctor)
    data = _clean_patient(body)
    merged = {**p, **data}
    if not merged.get("fullName"):
        raise HTTPException(status_code=422, detail="Enter the patient's name")
    get_db().patients.update_one({"_id": p["_id"]}, {"$set": {**data, "updatedAt": now()}})
    return {"patient": public_patient(merged)}


# --------------------------------------------------------------------------- consultations
class CaptureInfo(BaseModel):
    processing: Optional[str] = Field(default=None, max_length=20)
    mimeType: str = Field(max_length=80)
    bitrate: Optional[int] = None
    sampleRate: Optional[int] = None
    channelCount: Optional[int] = None
    echoCancellation: Optional[bool] = None
    noiseSuppression: Optional[bool] = None
    autoGainControl: Optional[bool] = None
    deviceLabel: Optional[str] = Field(default=None, max_length=200)
    userAgent: Optional[str] = Field(default=None, max_length=400)


class ConsultationCreate(BaseModel):
    patientId: Optional[str] = Field(default=None, max_length=20)
    consentConfirmed: Optional[bool] = None
    capture: CaptureInfo


@app.get("/api/consultations")
def list_consultations(limit: int = Query(default=30, ge=1, le=100),
                       before: Optional[str] = Query(default=None),
                       since: Optional[str] = Query(default=None),
                       doctor: dict = Depends(current_doctor)):
    db = get_db()
    query: dict = {"doctorId": doctor["doctorId"]}
    window: dict = {}
    try:
        if before:
            window["$lt"] = datetime.fromisoformat(before.replace("Z", "+00:00"))
        if since:
            window["$gte"] = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="bad date filter")
    if window:
        query["startedAt"] = window
    cons = list(db.consultations.find(query, {"events": 0, "transcript.segments": 0,
                                              "transcript.text": 0})
                .sort("startedAt", -1).limit(limit))
    pids = {c["patientId"] for c in cons if c.get("patientId")}
    patients = {p["patientId"]: p for p in db.patients.find(
        {"patientId": {"$in": list(pids)}, "ownerDoctorId": doctor["doctorId"]})}
    return {"consultations": [public_consultation(c, patients.get(c.get("patientId")))
                              for c in cons],
            "hasMore": len(cons) == limit}


@app.post("/api/consultations", status_code=201)
def create_consultation(body: ConsultationCreate, doctor: dict = Depends(current_doctor)):
    if body.patientId:
        owned_patient(body.patientId, doctor)
    elif settings.require_patient:
        raise HTTPException(status_code=422, detail="Choose or add the patient before recording")
    if settings.consent_mode == "confirm" and not body.consentConfirmed:
        raise HTTPException(status_code=422, detail="Consent confirmation is required")
    if storage.disk_free_bytes() < 500 * 1024 * 1024:
        raise HTTPException(status_code=507, detail="Server storage is almost full")
    started = now()
    for _ in range(5):
        cid = new_consultation_id()
        doc = {
            "consultationId": cid,
            "doctorId": doctor["doctorId"],
            "doctorName": doctor.get("fullName"),
            "patientId": body.patientId,
            "status": "recording",
            "startedAt": started,
            "lastChunkAt": None,
            "chunksReceived": 0,
            "capture": body.capture.model_dump(),
            "consent": {"mode": settings.consent_mode, "text": settings.consent_text,
                        "confirmed": body.consentConfirmed},
            "audio": {"original": None},
            "transcript": {"status": "pending"},
            "diarization": {"status": "pending"},
            "events": [{"at": started, "type": "started", "by": doctor["doctorId"]}],
            "createdAt": started,
            "updatedAt": started,
        }
        try:
            get_db().consultations.insert_one(doc)
            break
        except DuplicateKeyError:
            continue
    else:
        raise HTTPException(status_code=500, detail="Could not allocate a consultation ID")
    storage.chunks_dir(cid).mkdir(parents=True, exist_ok=True)
    return {"consultation": public_consultation(doc)}


@app.get("/api/consultations/{cid}")
def get_consultation(cid: str, doctor: dict = Depends(current_doctor)):
    c = owned_consultation(cid, doctor)
    patient = get_db().patients.find_one({"patientId": c["patientId"]}) if c.get("patientId") else None
    return {"consultation": public_consultation(c, patient, detail=True)}


@app.put("/api/consultations/{cid}/chunks/{seq}")
async def upload_chunk(cid: str, seq: int, request: Request,
                       doctor: dict = Depends(current_doctor)):
    if seq < 0 or seq > 999_999:
        raise HTTPException(status_code=422, detail="bad part number")
    c = owned_consultation(cid, doctor)
    if c["status"] not in ("recording", "incomplete"):
        raise HTTPException(status_code=409, detail=f"Consultation is already {c['status']}")
    body = bytearray()
    async for piece in request.stream():
        body.extend(piece)
        if len(body) > settings.max_chunk_bytes:
            raise HTTPException(status_code=413, detail="Part too large")
    if not body:
        raise HTTPException(status_code=422, detail="Empty part")
    try:
        storage.write_chunk(cid, seq, bytes(body))
    except OSError as exc:
        log.error("storage failure writing %s/%d: %s", cid, seq, exc)
        raise HTTPException(status_code=507, detail="Server storage failure")
    get_db().consultations.update_one(
        {"consultationId": cid},
        {"$max": {"chunksReceived": seq + 1}, "$set": {"lastChunkAt": now(), "updatedAt": now()}},
    )
    return {"ok": True, "seq": seq, "bytes": len(body)}


class FinalizeIn(BaseModel):
    totalChunks: Optional[int] = Field(default=None, ge=0, le=999_999)
    durationSec: Optional[float] = Field(default=None, ge=0, le=24 * 3600)
    reason: Literal["stopped", "interrupted", "recovered", "server-recovery"] = "stopped"
    detail: Optional[str] = Field(default=None, max_length=500)


@app.post("/api/consultations/{cid}/finalize")
def finalize(cid: str, body: FinalizeIn, doctor: dict = Depends(current_doctor)):
    with lock_for(cid):
        c = owned_consultation(cid, doctor)
        if c["status"] == "saved":
            return {"consultation": public_consultation(c)}  # idempotent retry
        received = storage.received_chunks(cid)
        total = body.totalChunks if body.totalChunks is not None else 0
        if received:
            total = max(total, max(received) + 1)
        db = get_db()
        if total == 0:
            db.consultations.update_one({"consultationId": cid}, {"$set": {
                "status": "failed", "endReason": "no-audio", "updatedAt": now()}})
            add_event(cid, "finalize-no-audio", doctor["doctorId"], body.detail)
            return {"consultation": public_consultation(owned_consultation(cid, doctor))}

        ext = storage.ext_for_mime(c["capture"]["mimeType"])
        try:
            result = storage.assemble(cid, total, ext)
        except OSError as exc:
            log.error("assembly failed for %s: %s", cid, exc)
            add_event(cid, "assembly-failed", "server", str(exc)[:300])
            raise HTTPException(status_code=507, detail="Server storage failure while saving")

        if result.get("missing"):
            db.consultations.update_one({"consultationId": cid}, {"$set": {
                "status": "incomplete", "missingChunks": result["missing"][:1000],
                "expectedChunks": total, "updatedAt": now()}})
            add_event(cid, "finalize-incomplete", doctor["doctorId"],
                      f"missing {len(result['missing'])} of {total} parts")
            return {"consultation": public_consultation(owned_consultation(cid, doctor))}

        try:
            meta = audio_store.persist(cid, "original", result["path"],
                                       c["capture"]["mimeType"], result["sha256"])
        except Exception as exc:
            log.error("could not persist original for %s: %s", cid, exc)
            add_event(cid, "persist-failed", "server", str(exc)[:300])
            raise HTTPException(status_code=507, detail="Could not store the recording")

        meta.update({"savedAt": now(), "chunks": total})
        duration = body.durationSec
        ended = c["startedAt"] + timedelta(seconds=duration) if duration is not None else now()
        db.consultations.update_one({"consultationId": cid}, {"$set": {
            "status": "saved", "missingChunks": [], "audio.original": meta,
            "endedAt": ended, "durationSec": duration, "endReason": body.reason,
            "finalizedAt": now(), "updatedAt": now(), **processing.initial_states()}})
        add_event(cid, f"saved-{body.reason}", doctor["doctorId"], body.detail)
        if c.get("patientId"):
            db.patients.update_one({"patientId": c["patientId"]},
                                   {"$max": {"lastConsultationAt": c["startedAt"]}})
        processing.enqueue(cid)
        return {"consultation": public_consultation(owned_consultation(cid, doctor))}


class EventIn(BaseModel):
    type: str = Field(max_length=64)
    detail: Optional[str] = Field(default=None, max_length=2000)


@app.post("/api/consultations/{cid}/events")
def client_event(cid: str, body: EventIn, doctor: dict = Depends(current_doctor)):
    owned_consultation(cid, doctor)
    add_event(cid, "client:" + re.sub(r"[^a-z0-9_-]", "", body.type.lower())[:60],
              doctor["doctorId"], body.detail)
    return {"ok": True}


class AssignIn(BaseModel):
    patientId: Optional[str] = Field(default=None, max_length=20)


@app.patch("/api/consultations/{cid}")
def assign_patient(cid: str, body: AssignIn, doctor: dict = Depends(current_doctor)):
    c = owned_consultation(cid, doctor)
    patient = owned_patient(body.patientId, doctor) if body.patientId else None
    db = get_db()
    db.consultations.update_one({"_id": c["_id"]}, {"$set": {
        "patientId": body.patientId, "updatedAt": now()}})
    add_event(cid, "patient-assigned", doctor["doctorId"], body.patientId or "none")
    if patient:
        db.patients.update_one({"_id": patient["_id"]},
                               {"$max": {"lastConsultationAt": c["startedAt"]}})
    return get_consultation(cid, doctor)


class SegmentIn(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    speaker: Optional[str] = Field(default=None, max_length=60)
    language: Optional[str] = Field(default=None, max_length=10)
    text: str = Field(max_length=10_000)


class TranscriptEditIn(BaseModel):
    segments: list[SegmentIn] = Field(max_length=20_000)


@app.put("/api/consultations/{cid}/transcript")
def edit_transcript(cid: str, body: TranscriptEditIn, doctor: dict = Depends(current_doctor)):
    c = owned_consultation(cid, doctor)
    tr = c.get("transcript") or {}
    if tr.get("status") != "done":
        raise HTTPException(status_code=409, detail="No transcript to edit yet")
    # The edit REPLACES the stored transcript: there is one transcript per consultation.
    segments = [{"start": round(s.start, 2), "end": round(s.end, 2),
                 "speaker": (s.speaker or "").strip() or None, "text": s.text.strip(),
                 **({"language": s.language} if s.language else {})}
                for s in body.segments if s.text.strip()]
    get_db().consultations.update_one({"_id": c["_id"]}, {"$set": {
        "transcript.segments": segments,
        "transcript.text": processing.transcript_text(segments),
        "transcript.edited": True,
        "transcript.editedAt": now(),
        "transcript.editedBy": doctor["doctorId"],
        "transcript.editedByName": doctor.get("fullName"),
        "updatedAt": now()}})
    add_event(cid, "transcript-edited", doctor["doctorId"])
    return get_consultation(cid, doctor)


@app.post("/api/consultations/{cid}/transcribe")
def retranscribe(cid: str, doctor: dict = Depends(current_doctor)):
    c = owned_consultation(cid, doctor)
    if settings.transcription_engine == "none":
        raise HTTPException(status_code=409, detail="Transcription is disabled on this server")
    if c["status"] != "saved":
        raise HTTPException(status_code=409, detail="Recording is not saved")
    get_db().consultations.update_one({"_id": c["_id"]}, {"$set": {
        "transcript.status": "queued", "processing.status": "queued",
        "diarization.status": "queued" if settings.diarization_engine != "none" else "disabled"}})
    add_event(cid, "transcription-requested", doctor["doctorId"])
    processing.enqueue(cid)
    return get_consultation(cid, doctor)


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


@app.get("/api/consultations/{cid}/audio/{kind}")
def get_audio(cid: str, kind: Literal["original"], request: Request,
              download: bool = False, doctor: dict = Depends(current_doctor)):
    c = owned_consultation(cid, doctor)
    meta = (c.get("audio") or {}).get(kind)
    if not meta or not meta.get("sha256"):
        raise HTTPException(status_code=404, detail="Audio not available")
    size = audio_store.size_of(meta)
    start, end, status = 0, size - 1, 200
    match = RANGE_RE.match(request.headers.get("range", ""))
    if match and size:
        a, b = match.groups()
        if a:
            start = int(a)
            end = min(int(b), size - 1) if b else size - 1
        elif b:
            start = max(size - int(b), 0)
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
    filename = f"{cid}-{kind}.{meta.get('ext') or storage.ext_for_mime(meta['mimeType'])}"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, no-store",
        "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{filename}"',
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(audio_store.iter_range(meta, start, end), status_code=status,
                             media_type=meta["mimeType"].split(";")[0], headers=headers)


# --------------------------------------------------------------------------- frontend
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
