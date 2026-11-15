"""Admin command line for the pilot (run on the hospital server).

    python -m app.manage check
    python -m app.manage create-doctor --email a@b.org --name "Dr. A"   (prompts for password)
    python -m app.manage set-password --email a@b.org
    python -m app.manage deactivate-doctor --email a@b.org
    python -m app.manage list-doctors
    python -m app.manage import-doctors --source-db opd_conversation_assistant
    python -m app.manage download-model        (needs internet once; weights only, no data sent)
"""
from __future__ import annotations

import argparse
import getpass
import sys

from pymongo.errors import DuplicateKeyError

from . import processing, storage
from .config import settings
from .db import get_db, new_doctor_id, now
from .security import hash_password


def _password(args) -> str:
    if getattr(args, "password_stdin", False):
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Repeat password: "):
            sys.exit("Passwords do not match")
    if not pw:
        sys.exit("Password cannot be empty")
    return pw


def cmd_check(_args) -> None:
    print(f"Database        : {settings.mongo_db} on {', '.join(settings.db_hosts) or '?'}")
    print(f"DB inside hospital (per config): {settings.db_internal}")
    print(f"Audio store     : {settings.audio_store}")
    print(f"Staging dir     : {settings.recordings_dir}  (free {storage.disk_free_bytes() / 1e9:.1f} GB)")
    print(f"Patient required: {settings.require_patient}")
    print(f"Processing      : {processing.capabilities()}")
    if settings.diarization_engine != "none":
        for label, path in (("Diar. segment.", settings.diarization_segmentation_model),
                            ("Diar. embedding", settings.diarization_embedding_model)):
            print(f"{label:<16}: {path} ({'ok' if path.is_file() else 'MISSING - run download-diarization-models'})")
    print(f"Consent mode    : {settings.consent_mode}")
    print(f"PI name         : {settings.pi_name or '(not configured)'}")
    get_db().command("ping")
    print("Database ping   : ok")
    for note in settings.boundary_report():
        print("WARNING:", note)


def cmd_create(args) -> None:
    doc = {"doctorId": new_doctor_id(), "fullName": args.name.strip(),
           "email": args.email.strip().lower(), "passwordHash": hash_password(_password(args)),
           "specialization": args.specialization, "active": True,
           "createdAt": now(), "updatedAt": now()}
    try:
        get_db().doctors.insert_one(doc)
    except DuplicateKeyError:
        sys.exit("A doctor with that email already exists")
    print(f"Created {doc['doctorId']} {doc['fullName']} <{doc['email']}>")


def cmd_set_password(args) -> None:
    res = get_db().doctors.update_one({"email": args.email.strip().lower()}, {"$set": {
        "passwordHash": hash_password(_password(args)), "updatedAt": now()}})
    print("Updated" if res.matched_count else "No such doctor")


def cmd_deactivate(args) -> None:
    res = get_db().doctors.update_one({"email": args.email.strip().lower()},
                                      {"$set": {"active": False, "updatedAt": now()}})
    print("Deactivated" if res.matched_count else "No such doctor")


def cmd_list(_args) -> None:
    for d in get_db().doctors.find({}, {"passwordHash": 0}).sort("createdAt", 1):
        state = "active" if d.get("active", True) else "inactive"
        print(f"{d['doctorId']}  {d.get('fullName', '')}  <{d.get('email', '')}>  {state}")


def cmd_import(args) -> None:
    """Copy doctor accounts (bcrypt hashes) from the earlier prototype database."""
    db = get_db()
    source = db.client[args.source_db].doctors
    copied = 0
    for d in source.find({}):
        if not d.get("email") or not d.get("passwordHash"):
            continue
        doc = {"doctorId": d.get("doctorId") or new_doctor_id(), "fullName": d.get("fullName", ""),
               "email": d["email"].strip().lower(), "passwordHash": d["passwordHash"],
               "specialization": d.get("specialization"), "active": True,
               "importedFrom": args.source_db, "createdAt": now(), "updatedAt": now()}
        try:
            db.doctors.insert_one(doc)
            copied += 1
        except DuplicateKeyError:
            print(f"skip (exists): {doc['email']}")
    print(f"Imported {copied} doctor account(s)")


def cmd_download_model(_args) -> None:
    if settings.transcription_engine != "faster-whisper":
        print("Note: TRANSCRIPTION_ENGINE is not faster-whisper; downloading anyway.")
    processing._load_whisper()
    print(f"Model '{settings.whisper_model}' ready in {settings.model_dir}")


DIARIZATION_MODELS = {
    # (download URL, file inside archive or None) -> configured path
    "segmentation": ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
                     "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2", "model.onnx"),
    "embedding": ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
                  "wespeaker_en_voxceleb_resnet34_LM.onnx", None),
}


def cmd_download_diarization(_args) -> None:
    """One-time download of the two sherpa-onnx model files (weights only; no data is sent)."""
    import tarfile
    import tempfile
    import urllib.request
    from pathlib import Path

    targets = {"segmentation": settings.diarization_segmentation_model,
               "embedding": settings.diarization_embedding_model}
    for key, (url, member) in DIARIZATION_MODELS.items():
        target = Path(targets[key])
        if target.is_file():
            print(f"{key}: already present at {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"{key}: downloading {url}")
        with tempfile.TemporaryDirectory() as tmp:
            dl = Path(tmp) / url.rsplit("/", 1)[-1]
            urllib.request.urlretrieve(url, dl)
            if member is None:
                dl.replace(target)
            else:
                with tarfile.open(dl, "r:bz2") as tar:
                    entry = next(m for m in tar.getmembers()
                                 if m.isfile() and m.name.endswith("/" + member))
                    with tar.extractfile(entry) as src, open(target, "wb") as out:
                        out.write(src.read())
        print(f"{key}: saved {target} ({target.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    p = sub.add_parser("create-doctor")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--specialization")
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(fn=cmd_create)
    p = sub.add_parser("set-password")
    p.add_argument("--email", required=True)
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(fn=cmd_set_password)
    p = sub.add_parser("deactivate-doctor")
    p.add_argument("--email", required=True)
    p.set_defaults(fn=cmd_deactivate)
    sub.add_parser("list-doctors").set_defaults(fn=cmd_list)
    p = sub.add_parser("import-doctors")
    p.add_argument("--source-db", required=True)
    p.set_defaults(fn=cmd_import)
    sub.add_parser("download-model").set_defaults(fn=cmd_download_model)
    sub.add_parser("download-diarization-models").set_defaults(fn=cmd_download_diarization)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
