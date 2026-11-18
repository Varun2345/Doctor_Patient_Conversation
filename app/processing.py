"""Background post-processing after a recording is saved.

Runs entirely on this server. Nothing here calls an external API. Each step is
independent: a failed transcript never affects the saved original audio.

  1. transcription (faster-whisper, local model)            - optional
  2. speaker labels (sherpa-onnx, local model)  - optional; anonymous Speaker A/B
  3. re-verify the original's SHA-256 is unchanged
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import traceback
from pathlib import Path

from . import audio_store
from .config import settings
from .db import get_db, now

log = logging.getLogger("consult")

_queue: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None
_whisper_model = None
_diarizer = None

def enqueue(cid: str) -> None:
    _queue.put(cid)


def start_worker() -> None:
    global _worker
    if _worker and _worker.is_alive():
        return
    _worker = threading.Thread(target=_loop, name="postprocess", daemon=True)
    _worker.start()


TS_LINE = re.compile(r"^\[(?:(\d+):)?(\d{1,2}):(\d{2})\]\s*(.*)$")


def _parse_text(text: str, known_speakers: set[str], old_segments: list[dict]) -> list[dict]:
    """Turn '[mm:ss] Speaker A: words' lines (older free-text edits) back into segments."""
    segs: list[dict] = []
    for line in (text or "").splitlines():
        m = TS_LINE.match(line.strip())
        if not m:
            if segs and line.strip():
                segs[-1]["text"] = (segs[-1]["text"] + " " + line.strip()).strip()
            continue
        hrs, mins, secs, rest = m.groups()
        start = int(hrs or 0) * 3600 + int(mins) * 60 + int(secs)
        speaker = None
        if ": " in rest:
            head, tail = rest.split(": ", 1)
            if head in known_speakers or re.fullmatch(r"Speaker [A-Z]{1,2}", head):
                speaker, rest = head, tail
        segs.append({"start": float(start), "end": float(start), "speaker": speaker, "text": rest.strip()})
    for i, sg in enumerate(segs):  # end = original end if the start matches, else next start
        match = next((o for o in old_segments if int(o.get("start", -1)) == int(sg["start"])), None)
        nxt = segs[i + 1]["start"] if i + 1 < len(segs) else sg["start"] + 5
        sg["end"] = float(match["end"]) if match else float(max(nxt, sg["start"]))
    return [sg for sg in segs if sg["text"]]


def migrate_transcripts() -> int:
    """Older records kept the automatic transcript, the edit and its revisions side by side.
    Fold them into the single-transcript format: the latest version is the transcript."""
    db = get_db()
    changed = 0
    query = {"$or": [{"transcript.machineText": {"$exists": True}},
                     {"transcript.editedText": {"$exists": True}},
                     {"transcript.editedSegments": {"$exists": True}},
                     {"transcript.revisions": {"$exists": True}}]}
    for c in db.consultations.find(query, {"transcript": 1}):
        tr = c.get("transcript") or {}
        segments = tr.get("segments") or []
        sets: dict = {}
        if tr.get("editedSegments"):
            segments = tr["editedSegments"]
            sets["transcript.edited"] = True
        elif tr.get("editedText"):
            known = {s.get("speaker") for s in segments if s.get("speaker")}
            segments = _parse_text(tr["editedText"], known, segments) or segments
            sets["transcript.edited"] = True
        sets["transcript.segments"] = segments
        sets["transcript.text"] = transcript_text(segments) if segments else (
            tr.get("editedText") or tr.get("machineText") or "")
        db.consultations.update_one({"_id": c["_id"]}, {
            "$set": sets,
            "$unset": {"transcript.machineText": "", "transcript.editedText": "",
                       "transcript.editedSegments": "", "transcript.revisions": ""}})
        changed += 1
    return changed


def requeue_unfinished() -> int:
    db = get_db()
    count = 0
    for c in db.consultations.find(
        {"status": "saved", "processing.status": {"$in": ["queued", "running"]}},
        {"consultationId": 1},
    ):
        enqueue(c["consultationId"])
        count += 1
    return count


def _loop() -> None:
    while True:
        cid = _queue.get()
        try:
            process(cid)
        except Exception:  # never let the worker die
            log.error("post-processing crashed for %s\n%s", cid, traceback.format_exc())
        finally:
            _queue.task_done()


def _set(cid: str, fields: dict, event: str | None = None) -> None:
    update: dict = {"$set": {**fields, "updatedAt": now()}}
    if event:
        update["$push"] = {"events": {"at": now(), "type": event, "by": "server"}}
    get_db().consultations.update_one({"consultationId": cid}, update)


def initial_states() -> dict:
    """Statuses written when a recording is saved."""
    tr = "queued" if settings.transcription_engine != "none" else "disabled"
    di = "queued" if settings.diarization_engine != "none" else "disabled"
    if tr == "disabled":
        di = "disabled"
    return {
        "processing.status": "queued",
        "transcript.status": tr,
        "transcript.engine": settings.transcription_engine,
        "diarization.status": di,
        "diarization.engine": settings.diarization_engine,
    }


def process(cid: str) -> None:
    db = get_db()
    c = db.consultations.find_one({"consultationId": cid})
    if not c or c.get("status") != "saved":
        return
    original = c["audio"]["original"]
    _set(cid, {"processing.status": "running"})

    with audio_store.local_copy(original) as src:
        if settings.transcription_engine != "none":
            _transcribe(cid, src)

    # The original must be byte-identical to what was saved.
    current = audio_store.sha256_stored(original)
    ok = current == original["sha256"]
    _set(cid, {"processing.status": "done", "audio.original.verifiedAt": now(),
               "audio.original.verified": ok},
         event=None if ok else "original-checksum-mismatch")
    if not ok:
        log.critical("ORIGINAL AUDIO CHECKSUM MISMATCH for %s", cid)


def add_cuda_libraries() -> None:
    """Make pip-installed NVIDIA libraries (nvidia-cublas-cu12, nvidia-cudnn-cu12) loadable."""
    import os
    import site

    for base in [*site.getsitepackages(), site.getusersitepackages()]:
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            d = Path(base) / "nvidia" / sub / "bin"
            if d.is_dir() and str(d) not in os.environ.get("PATH", ""):
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(str(d))


def whisper_runtime() -> tuple[str, str]:
    device = settings.whisper_device
    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    compute = settings.whisper_compute_type
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    return device, compute


def _load_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel  # optional dependency
        from huggingface_hub.errors import LocalEntryNotFoundError

        settings.model_dir.mkdir(parents=True, exist_ok=True)
        device, compute = whisper_runtime()
        if device == "cuda":
            add_cuda_libraries()
        try:
            model = WhisperModel(settings.whisper_model, device=device, compute_type=compute,
                                 download_root=str(settings.model_dir),
                                 local_files_only=settings.whisper_local_files_only)
            if device == "cuda":  # fail fast here if the CUDA libraries are missing
                import numpy as np
                model.detect_language(np.zeros(16000, dtype=np.float32))
        except LocalEntryNotFoundError as exc:
            raise RuntimeError(
                "Whisper model files are not available locally. For first-time setup, "
                "set HF_HUB_OFFLINE=0 and run 'python -m app.manage download-model' "
                "with internet access; then enable offline mode after the download. "
                "No account or token is required."
            ) from exc
        except Exception as exc:
            if device != "cuda":
                raise
            log.warning("GPU not usable for Whisper (%s); using the CPU instead", exc)
            device, compute = "cpu", "int8"
            model = WhisperModel(settings.whisper_model, device=device, compute_type=compute,
                                 download_root=str(settings.model_dir),
                                 local_files_only=settings.whisper_local_files_only)
        log.info("Whisper %s loaded on %s (%s)", settings.whisper_model, device, compute)
        _whisper_model = model
    return _whisper_model


def _fmt_ts(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def transcript_text(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        who = f"{seg['speaker']}: " if seg.get("speaker") else ""
        lines.append(f"[{_fmt_ts(seg['start'])}] {who}{seg['text']}")
    return "\n".join(lines)


def transcribe_audio(model, src: Path, word_timestamps: bool) -> tuple[list[dict], str, float]:
    """Transcribe the whole recording. Returns (segments, main language, its probability).

    With several allowed languages, the recording is split at pauses and each piece is
    assigned the most likely ALLOWED language, then transcribed in that language. Every
    segment keeps its "language" so mixed consultations stay readable."""
    from collections import Counter

    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    allowed = [l for l in settings.whisper_languages if l]
    if settings.whisper_language or len(allowed) <= 1 or not settings.whisper_multilingual:
        lang = settings.whisper_language or (allowed[0] if len(allowed) == 1 else None)
        seg_iter, info = model.transcribe(str(src), language=lang, vad_filter=True, beam_size=5,
                                          word_timestamps=word_timestamps)
        segs = [_seg(s, 0.0, info.language) for s in seg_iter if s.text.strip()]
        return segs, info.language, float(info.language_probability)

    sr = 16000
    audio = decode_audio(str(src), sampling_rate=sr)
    regions = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=400, speech_pad_ms=200))
    chunks = []
    for r in regions:  # whisper works on <= 30 s windows
        start, end = r["start"], r["end"]
        while end - start > 28 * sr:
            chunks.append((start, start + 28 * sr))
            start += 28 * sr
        chunks.append((start, end))

    segments: list[dict] = []
    seconds: Counter = Counter()
    probs_seen: dict[str, list[float]] = {}
    previous = None
    labelled = []
    for start, end in chunks:  # 1) language of each piece, chosen among the allowed ones
        piece = audio[start:end]
        if end - start >= int(0.8 * sr) or previous is None:
            _, _, all_probs = model.detect_language(piece)
            probs = dict(all_probs)
            lang = max(allowed, key=lambda code: probs.get(code, 0.0))
            total = sum(probs.get(code, 0.0) for code in allowed) or 1.0
            probs_seen.setdefault(lang, []).append(probs.get(lang, 0.0) / total)
        else:  # too short to judge: keep the previous piece's language
            lang = previous
        previous = lang
        seconds[lang] += (end - start) / sr
        labelled.append((start, end, lang))

    windows: list[list] = []  # 2) join neighbouring same-language pieces into <= 28 s windows
    for start, end, lang in labelled:
        last = windows[-1] if windows else None
        if last and last[2] == lang and end - last[0] <= 28 * sr and start - last[1] <= 2 * sr:
            last[1] = end
        else:
            windows.append([start, end, lang])

    for start, end, lang in windows:  # 3) transcribe each window in its language
        seg_iter, _ = model.transcribe(audio[start:end], language=lang, beam_size=5, vad_filter=False,
                                       word_timestamps=word_timestamps,
                                       condition_on_previous_text=False)
        for s in seg_iter:
            if s.text.strip():
                segments.append(_seg(s, start / sr, lang))
    if not seconds:
        return [], allowed[0], 0.0
    main = seconds.most_common(1)[0][0]
    p = probs_seen.get(main) or [0.0]
    return segments, main, sum(p) / len(p)


SCRIPT_RANGES = {
    "latin": [(0x0041, 0x024F)], "devanagari": [(0x0900, 0x097F)], "telugu": [(0x0C00, 0x0C7F)],
    "tamil": [(0x0B80, 0x0BFF)], "kannada": [(0x0C80, 0x0CFF)], "bengali": [(0x0980, 0x09FF)],
    "arabic": [(0x0600, 0x06FF)], "cjk": [(0x3040, 0x30FF), (0x4E00, 0x9FFF)], "hangul": [(0xAC00, 0xD7AF)],
}
# Scripts a line in each language may reasonably be written in (incl. romanised speech).
EXPECTED_SCRIPTS = {"en": {"latin"}, "hi": {"devanagari", "latin"}, "te": {"telugu", "devanagari", "latin"},
                    "ta": {"tamil", "latin"}, "kn": {"kannada", "latin"}, "bn": {"bengali", "latin"},
                    "ur": {"arabic", "latin"}}
UNCLEAR = "[unclear – listen to the recording]"


def looks_garbled(text: str, lang: str) -> bool:
    """True when the model produced a jumble (wrong / mixed scripts, replacement chars)."""
    if "�" in text:
        return True
    counts: dict[str, int] = {}
    for ch in text:
        if not ch.isalpha():
            continue
        code = ord(ch)
        for name, ranges in SCRIPT_RANGES.items():
            if any(lo <= code <= hi for lo, hi in ranges):
                counts[name] = counts.get(name, 0) + 1
                break
    letters = sum(counts.values())
    if letters < 4:
        return False
    expected = EXPECTED_SCRIPTS.get(lang)
    if expected is None:
        return len(counts) >= 3
    ok = sum(n for name, n in counts.items() if name in expected)
    return ok / letters < 0.7


def _seg(s, offset: float, lang: str) -> dict:
    text = s.text.strip()
    seg = {"start": round(s.start + offset, 2), "end": round(s.end + offset, 2),
           "text": text, "language": lang}
    if looks_garbled(text, lang):
        seg["text"] = UNCLEAR
        seg["unclear"] = True
        return seg  # no word timestamps for a line we do not trust
    if s.words:
        seg["words"] = [(round(w.start + offset, 2), round(w.end + offset, 2), w.word) for w in s.words]
    return seg


def _transcribe(cid: str, src: Path) -> None:
    diarize = settings.diarization_engine != "none"
    _set(cid, {"transcript.status": "running", "transcript.stage": "transcribing",
               "transcript.error": None})
    try:
        model = _load_whisper()
        # Word timestamps let a speaker change be placed inside a sentence.
        segments, language, probability = transcribe_audio(model, src, word_timestamps=diarize)
        fields = {
            "transcript.model": settings.whisper_model,
            "transcript.language": language,
            "transcript.languages": sorted({s["language"] for s in segments if s.get("language")}),
            "transcript.languageProbability": round(float(probability), 3),
        }
    except Exception as exc:
        log.warning("transcription failed for %s: %s", cid, exc)
        _set(cid, {"transcript.status": "failed", "transcript.error": str(exc)[:500],
                   "transcript.stage": None,
                   "diarization.status": "skipped" if diarize else "disabled"})
        return

    if diarize and segments:
        _set(cid, {**fields, "transcript.stage": "speakers", "diarization.status": "running",
                   "diarization.error": None})
        try:
            turns = diarize_file(src)
            segments, speakers = label_segments(segments, turns)
            fields.update({"diarization.status": "done", "diarization.engine": settings.diarization_engine,
                           "diarization.speakers": speakers,
                           "diarization.speakerCountMode": "auto",
                           "diarization.turns": turns, "diarization.updatedAt": now()})
        except Exception as exc:
            log.warning("diarization failed for %s: %s", cid, exc)
            fields.update({"diarization.status": "failed", "diarization.error": str(exc)[:500]})

    for seg in segments:
        seg.pop("words", None)
    fields.update({
        "transcript.status": "done",
        "transcript.stage": None,
        "transcript.segments": segments,
        "transcript.text": transcript_text(segments),
        "transcript.edited": False,
        "transcript.editedAt": None,
        "transcript.editedBy": None,
        "transcript.editedByName": None,
        "transcript.updatedAt": now(),
    })
    _set(cid, fields)


# --------------------------------------------------------------------------- diarization
def _load_diarizer():
    global _diarizer
    if _diarizer is None:
        import sherpa_onnx  # optional dependency

        for path in (settings.diarization_segmentation_model, settings.diarization_embedding_model):
            if not Path(path).is_file():
                raise FileNotFoundError(f"diarization model missing: {path}")
        threads = settings.diarization_threads
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(settings.diarization_segmentation_model)),
                num_threads=threads),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(settings.diarization_embedding_model), num_threads=threads),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=-1,  # infer the speaker count from the voice embeddings
                threshold=settings.diarization_threshold),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not config.validate():
            raise RuntimeError("invalid sherpa-onnx diarization configuration")
        _diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    return _diarizer


def diarize_file(src: Path) -> list[dict]:
    """Who spoke when: [{start, end, speaker: 'Speaker A'}, ...] in time order.
    Labels are anonymous and say nothing about who is the doctor or the patient.

    Runs in a child process (see diarize_worker) so the web server never freezes."""
    import json
    import os
    import subprocess
    import sys

    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.BELOW_NORMAL_PRIORITY_CLASS
    else:
        kwargs["preexec_fn"] = lambda: os.nice(10)
    root = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    cmd = [sys.executable, "-m", "app.diarize_worker", str(src)]
    result = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=4 * 3600, **kwargs)
    if result.returncode != 0:
        lines = [l for l in (result.stderr or "").strip().splitlines() if l.strip()]
        raise RuntimeError(f"diarization process failed ({result.returncode}): "
                           f"{(lines[-1] if lines else 'no error output')[:300]}")
    return json.loads(result.stdout.strip().splitlines()[-1])["turns"]


def diarize_in_process(src: Path) -> list[dict]:
    """The actual sherpa-onnx call. Only run this in the worker process."""
    from faster_whisper.audio import decode_audio

    sd = _load_diarizer()
    audio = decode_audio(str(src), sampling_rate=sd.sample_rate)
    result = sd.process(audio).sort_by_start_time()
    names: dict[int, str] = {}
    turns = []
    for r in result:
        if r.speaker not in names:
            names[r.speaker] = speaker_name(len(names))
        turns.append({"start": round(r.start, 2), "end": round(r.end, 2),
                      "speaker": names[r.speaker]})
    return turns


def speaker_name(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return "Speaker " + letters


def _speaker_at(turns: list[dict], start: float, end: float) -> str | None:
    best, best_overlap = None, 0.0
    for t in turns:
        overlap = min(t["end"], end) - max(t["start"], start)
        if overlap > best_overlap:
            best, best_overlap = t["speaker"], overlap
    if best:
        return best
    mid = (start + end) / 2  # no overlap: nearest turn within 1 s
    nearest, dist = None, 1.0
    for t in turns:
        d = min(abs(mid - t["start"]), abs(mid - t["end"]))
        if d < dist:
            nearest, dist = t["speaker"], d
    return nearest


def label_segments(segments: list[dict], turns: list[dict]) -> tuple[list[dict], int]:
    """Give every word the speaker talking at that moment, then split transcript lines
    wherever the speaker changes. Returns (new_segments, number_of_speakers)."""
    out: list[dict] = []
    for seg in segments:
        words = seg.get("words") or []
        if not words:
            out.append({**seg, "speaker": _speaker_at(turns, seg["start"], seg["end"])})
            continue
        labels = [_speaker_at(turns, w[0], w[1]) for w in words]
        for i in range(len(labels)):  # fill gaps from neighbours
            if labels[i] is None:
                labels[i] = labels[i - 1] if i else next((x for x in labels if x), None)
        for i in range(1, len(labels) - 1):  # drop single short flips: A A B A A
            if labels[i - 1] == labels[i + 1] != labels[i] and words[i][1] - words[i][0] < 0.4:
                labels[i] = labels[i - 1]
        run_start = 0
        for i in range(1, len(words) + 1):
            if i == len(words) or labels[i] != labels[run_start]:
                chunk = words[run_start:i]
                text = "".join(w[2] for w in chunk).strip()
                if text:
                    item = {"start": chunk[0][0], "end": chunk[-1][1], "text": text,
                            "speaker": labels[run_start]}
                    if seg.get("language"):
                        item["language"] = seg["language"]
                    out.append(item)
                run_start = i
    return out, len({t["speaker"] for t in turns})


def capabilities() -> dict:
    return {
        "transcription": settings.transcription_engine,
        "transcriptionModel": settings.whisper_model
        if settings.transcription_engine != "none" else None,
        "diarization": settings.diarization_engine,
        "diarizationSpeakers": -1  # automatic speaker counting
        if settings.diarization_engine != "none" else None,
    }

