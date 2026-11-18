"""All runtime configuration comes from environment variables (or a local .env file).

Every hospital-specific decision (branding, PI name, consent wording, storage
location, transcription engine) is a setting here rather than hard-coded.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("consult")


def _str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _path(name: str, default: str) -> Path:
    """Relative paths are taken relative to the application folder, not the CWD."""
    path = Path(_str(name, default))
    return (path if path.is_absolute() else BASE_DIR / path).resolve()


def _choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    value = _str(name, default).lower()
    if value not in allowed:
        log.warning("%s=%r is not one of %s; using %r", name, value, allowed, default)
        return default
    return value


def _mongo_hosts(uri: str) -> list[str]:
    try:
        netloc = urlsplit(uri).netloc
    except ValueError:
        return []
    netloc = netloc.rsplit("@", 1)[-1]  # drop credentials
    hosts = []
    for part in netloc.split(","):
        host = part.strip()
        if host.startswith("["):
            host = host[1:].split("]", 1)[0]
        else:
            host = host.split(":", 1)[0]
        if host:
            hosts.append(host.lower())
    return hosts


def _host_is_internal(host: str, trusted: set[str]) -> bool:
    if host in trusted or host in ("localhost", "mongo", "mongodb"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        pass
    # Single-label names (e.g. a Docker service name) resolve inside the local network.
    return "." not in host


class Settings:
    def __init__(self) -> None:
        # --- Storage -----------------------------------------------------
        self.mongo_uri = _str("MONGO_URI", "mongodb://localhost:27017")
        self.mongo_db = _str("MONGO_DB_NAME", "doctor_patient_conversations")
        self.data_dir = _path("DATA_DIR", "data")
        self.keep_chunks = _bool("KEEP_CHUNKS_AFTER_SAVE", False)
        # Where the finished original recording lives:
        #   gridfs: MongoDB GridFS in MONGO_DB_NAME (bucket "audio") - same place as metadata
        #   disk:   files under DATA_DIR/recordings on this server
        # Upload parts are always staged on local disk while recording.
        self.audio_store = _choice("AUDIO_STORE", "gridfs", ("gridfs", "disk"))

        # Is the metadata database inside the hospital? "auto" uses the host name.
        trusted = {h.strip().lower() for h in _str("TRUSTED_DB_HOSTS").split(",") if h.strip()}
        location = _choice("DB_IS_HOSPITAL_INTERNAL", "auto", ("auto", "true", "false"))
        hosts = _mongo_hosts(self.mongo_uri)
        if location == "auto":
            self.db_internal = (
                not self.mongo_uri.startswith("mongodb+srv://")
                and bool(hosts)
                and all(_host_is_internal(h, trusted) for h in hosts)
            )
        else:
            self.db_internal = location == "true"
        self.db_hosts = hosts

        # --- Auth / sessions -------------------------------------------
        self.secret_key = _str("SECRET_KEY")
        self.secret_key_generated = False
        if not self.secret_key:
            self.secret_key = secrets.token_urlsafe(48)
            self.secret_key_generated = True
        self.session_hours = _int("SESSION_HOURS", 12)
        self.cookie_secure = _bool("COOKIE_SECURE", False)
        # Self sign-up for doctors. With SIGNUP_CODE set, the code is required to register
        # (share it only with pilot doctors). New accounts only ever see their own records.
        self.signup_enabled = _bool("SIGNUP_ENABLED", True)
        self.signup_code = _str("SIGNUP_CODE")

        # --- Branding (do not invent names: leave blank until confirmed) --
        self.app_title = _str("APP_TITLE", "ClinCord")
        self.app_subtitle = _str("APP_SUBTITLE", "Clinical Conversation Recording")
        self.institution_name = _str("INSTITUTION_NAME", "IIIT Hyderabad")
        self.pi_name = _str("PI_NAME", "Parameshwari Krishnamurthy")  # shown in the footer
        # Names shown in the footer, comma separated.
        self.team_names = [n.strip() for n in _str("TEAM_NAMES", "Varun, Akash, Jaswanth").split(",")
                           if n.strip()]
        self.branding_dir = _path("BRANDING_DIR", "branding")

        # --- Consent reminder (hospital process; this is only a prompt) --
        # A patient must be chosen or added before recording can start.
        self.require_patient = _bool("REQUIRE_PATIENT", True)
        self.consent_mode = _choice("CONSENT_MODE", "reminder", ("off", "reminder", "confirm"))
        self.consent_text = _str(
            "CONSENT_TEXT",
            "Before recording, follow the hospital's consent process for this consultation.",
        )

        # --- Audio capture ----------------------------------------------
        # raw: browser echo-cancellation / noise-suppression / auto-gain OFF, so the
        #      saved original is as close to the microphone signal as the browser allows.
        # browser: let the browser apply its call-oriented processing while recording.
        self.capture_processing = _choice("AUDIO_CAPTURE_PROCESSING", "raw", ("raw", "browser"))
        self.audio_bitrate = _int("AUDIO_BITRATE", 128000)
        self.chunk_seconds = max(1, min(_int("UPLOAD_CHUNK_SECONDS", 5), 60))
        self.max_chunk_bytes = _int("MAX_CHUNK_BYTES", 25 * 1024 * 1024)
        self.stale_minutes = _int("STALE_RECORDING_MINUTES", 2)

        # --- Post-processing (all local; nothing leaves this server) -----
        self.transcription_engine = _choice(
            "TRANSCRIPTION_ENGINE", "none", ("none", "faster-whisper")
        )
        self.whisper_model = _str("WHISPER_MODEL", "small")
        # auto = use an NVIDIA GPU when available, otherwise the CPU
        self.whisper_device = _choice("WHISPER_DEVICE", "auto", ("auto", "cpu", "cuda"))
        # auto = int8 on CPU, float16 on GPU
        self.whisper_compute_type = _str("WHISPER_COMPUTE_TYPE", "auto")
        self.whisper_language = _str("WHISPER_LANGUAGE")  # blank = auto-detect
        # Detect the language per segment (doctor and patient may speak different languages).
        self.whisper_multilingual = _bool("WHISPER_MULTILINGUAL", True)
        # Languages that may appear in a consultation. Each speech segment is assigned the
        # most likely of THESE (prevents e.g. Chinese/Urdu being picked for short segments).
        self.whisper_languages = [l.strip().lower() for l in _str("WHISPER_LANGUAGES", "en,hi,te").split(",")
                                  if l.strip()]
        self.model_dir = _path("MODEL_DIR", "models")
        self.whisper_local_files_only = _bool("HF_HUB_OFFLINE", False)
        # Speaker labels (anonymous Speaker A/B/...) for the after-session transcript.
        # sherpa-onnx runs locally on CPU (pyannote segmentation + speaker embeddings, ONNX).
        self.diarization_engine = _choice("DIARIZATION_ENGINE", "none", ("none", "sherpa-onnx"))
        try:
            self.diarization_threshold = float(_str("DIARIZATION_THRESHOLD", "0.5"))
        except ValueError:
            self.diarization_threshold = 0.5
        self.diarization_threads = max(1, _int("DIARIZATION_THREADS", max(1, (os.cpu_count() or 2) // 2)))
        self.diarization_segmentation_model = _path(
            "DIARIZATION_SEGMENTATION_MODEL",
            "models/diarization/sherpa-onnx-pyannote-segmentation-3-0/model.onnx")
        self.diarization_embedding_model = _path(
            "DIARIZATION_EMBEDDING_MODEL", "models/diarization/wespeaker_en_voxceleb_resnet34_LM.onnx")

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    def boundary_report(self) -> list[str]:
        """Human-readable warnings about data leaving the hospital environment."""
        notes = []
        if not self.db_internal:
            notes.append(
                "Metadata database host(s) %s look EXTERNAL to the hospital network. "
                "Patient names and transcripts stored there leave the hospital. "
                "Use only synthetic test data, or set DB_IS_HOSPITAL_INTERNAL=true if this "
                "host is confirmed to be hospital-controlled." % (", ".join(self.db_hosts) or "?")
            )
        if self.secret_key_generated:
            notes.append("SECRET_KEY not set: a random key is used and sign-ins reset on restart.")
        if self.signup_enabled and not self.signup_code:
            notes.append("Sign-up is open to anyone who can reach this server (SIGNUP_CODE not set).")
        if not self.cookie_secure:
            notes.append("COOKIE_SECURE=false: use only on localhost or behind HTTPS on a trusted LAN.")
        return notes


settings = Settings()
