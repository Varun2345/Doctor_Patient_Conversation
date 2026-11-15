"""Long-term home of finished audio files: MongoDB GridFS or local disk (AUDIO_STORE).

The original is written once and never modified. Its SHA-256 is recorded at save
time and re-checked after any processing.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Iterator

import gridfs
from bson import ObjectId

from . import storage
from .config import settings
from .db import get_db

BUCKET = "audio"


def _bucket() -> gridfs.GridFSBucket:
    return gridfs.GridFSBucket(get_db(), bucket_name=BUCKET)


def persist(cid: str, kind: str, local_path: Path, mime: str, sha256: str) -> dict:
    """Move a finished local file into long-term storage. Returns the audio metadata."""
    size = local_path.stat().st_size
    ext = local_path.suffix.lstrip(".")
    if settings.audio_store == "disk":
        return {"store": "disk", "path": storage.relative(local_path), "sizeBytes": size,
                "sha256": sha256, "mimeType": mime, "ext": ext}

    filename = f"{cid}/{kind}.{ext}"
    with open(local_path, "rb") as fh:
        file_id = _bucket().upload_from_stream(
            filename, fh,
            metadata={"consultationId": cid, "kind": kind, "sha256": sha256, "contentType": mime},
        )
    # Verify the stored copy before removing the local one.
    stored_sha = sha256_stored({"store": "gridfs", "fileId": file_id})
    if stored_sha != sha256:
        with contextlib.suppress(Exception):
            _bucket().delete(file_id)
        raise OSError(f"GridFS verification failed for {filename}")
    with contextlib.suppress(OSError):
        os.chmod(local_path, stat.S_IWRITE | stat.S_IREAD)
        local_path.unlink()
    return {"store": "gridfs", "fileId": str(file_id), "filename": filename, "sizeBytes": size,
            "sha256": sha256, "mimeType": mime, "ext": ext}


def size_of(meta: dict) -> int:
    return int(meta["sizeBytes"])


def iter_range(meta: dict, start: int, end: int, block: int = 256 * 1024) -> Iterator[bytes]:
    """Yield bytes [start, end] inclusive."""
    remaining = end - start + 1
    if meta["store"] == "disk":
        fh = open(storage.absolute(meta["path"]), "rb")
    else:
        fh = _bucket().open_download_stream(ObjectId(meta["fileId"]))
    try:
        fh.seek(start)
        while remaining > 0:
            data = fh.read(min(block, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data
    finally:
        fh.close()


def sha256_stored(meta: dict) -> str:
    digest = hashlib.sha256()
    if meta["store"] == "disk":
        return storage.sha256_file(storage.absolute(meta["path"]))
    with _bucket().open_download_stream(ObjectId(str(meta["fileId"]))) as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextlib.contextmanager
def local_copy(meta: dict):
    """Yield a local path for processing. For GridFS this is a temporary copy."""
    if meta["store"] == "disk":
        yield storage.absolute(meta["path"])
        return
    tmpdir = Path(tempfile.mkdtemp(prefix="consult-"))
    path = tmpdir / f"audio.{meta.get('ext', 'bin')}"
    try:
        with open(path, "wb") as out:
            _bucket().download_to_stream(ObjectId(meta["fileId"]), out)
        yield path
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
