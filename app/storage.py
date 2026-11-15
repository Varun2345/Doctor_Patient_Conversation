"""Audio file storage on the hospital server's own disk.

Layout (under DATA_DIR):
    recordings/<consultationId>/chunks/000000.part ...   while recording (internal only)
    recordings/<consultationId>/original.<ext>           the unmodified recording

The browser uploads the recording in small sequential parts so that a crash or
network drop loses at most a few seconds. The parts are byte-for-byte pieces of a
single continuous MediaRecorder stream; on Stop they are concatenated in order into
ONE original file. Gaps are detected and reported, never silently skipped.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
from pathlib import Path

from .config import settings

CID_RE = re.compile(r"^CON-\d{8}-[0-9a-f]{6}$")

MIME_EXT = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
}


def ext_for_mime(mime: str) -> str:
    return MIME_EXT.get((mime or "").split(";", 1)[0].strip().lower(), "bin")


def consultation_dir(cid: str) -> Path:
    if not CID_RE.match(cid):
        raise ValueError("invalid consultation id")
    return settings.recordings_dir / cid


def chunks_dir(cid: str) -> Path:
    return consultation_dir(cid) / "chunks"


def relative(path: Path) -> str:
    return path.resolve().relative_to(settings.data_dir).as_posix()


def absolute(rel: str) -> Path:
    path = (settings.data_dir / rel).resolve()
    if settings.data_dir not in path.parents:
        raise ValueError("path outside data dir")
    return path


def _fsync_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_chunk(cid: str, seq: int, data: bytes) -> int:
    """Store one part. Idempotent: re-uploading the same seq overwrites it."""
    directory = chunks_dir(cid)
    directory.mkdir(parents=True, exist_ok=True)
    _fsync_write(directory / f"{seq:06d}.part", data)
    return len(data)


def received_chunks(cid: str) -> dict[int, Path]:
    directory = chunks_dir(cid)
    if not directory.exists():
        return {}
    found = {}
    for entry in directory.iterdir():
        if entry.suffix == ".part" and entry.stem.isdigit():
            found[int(entry.stem)] = entry
    return found


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assemble(cid: str, total_chunks: int, ext: str) -> dict:
    """Concatenate parts 0..total-1 into the original file.

    Returns {"missing": [...]} if any part is absent (nothing is written), else
    {"path", "sizeBytes", "sha256", "chunks"}.
    """
    parts = received_chunks(cid)
    missing = [i for i in range(total_chunks) if i not in parts]
    if missing:
        return {"missing": missing}

    target = consultation_dir(cid) / f"original.{ext}"
    tmp = target.with_suffix(target.suffix + ".assembling")
    digest = hashlib.sha256()
    expected = 0
    with open(tmp, "wb") as out:
        for i in range(total_chunks):
            data = parts[i].read_bytes()
            expected += len(data)
            digest.update(data)
            out.write(data)
        out.flush()
        os.fsync(out.fileno())
    size = tmp.stat().st_size
    if size != expected:
        tmp.unlink(missing_ok=True)
        raise OSError(f"assembled size {size} != expected {expected}")
    if target.exists():  # re-finalize after recovery: replace the earlier partial file
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
    os.replace(tmp, target)
    # Mark the original read-only so later processing cannot modify it by accident.
    os.chmod(target, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    if not settings.keep_chunks:
        shutil.rmtree(chunks_dir(cid), ignore_errors=True)
    return {"path": target, "sizeBytes": size, "sha256": digest.hexdigest(), "chunks": total_chunks}


def disk_free_bytes() -> int:
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(settings.recordings_dir).free
