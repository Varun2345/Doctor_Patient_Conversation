"""Benchmark Whisper model sizes on the test conversations (speed + accuracy).

    python tests/bench_asr.py base small large-v3-turbo [--device cpu|cuda]

Uses the app's own transcribe_audio() (languages restricted to WHISPER_LANGUAGES).
Reports, per model:
  - processing time and real-time factor (lower = faster; 0.25 = 4x faster than real time)
  - English file: word error rate (WER) against the script
  - mixed file: turns with the right language, turns in the right script, and
    character error rate (CER) per language against the script
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LANG_CODE = {"English": "en", "Hindi": "hi", "Telugu": "te"}
SCRIPT = {"hi": (0x0900, 0x097F), "te": (0x0C00, 0x0C7F)}


def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def norm(t):
    t = unicodedata.normalize("NFC", t.lower())
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def words(t):
    return norm(t).split()


def in_script(text, lang):
    if lang not in SCRIPT:
        return True
    lo, hi = SCRIPT[lang]
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(lo <= ord(c) <= hi for c in letters) / len(letters) > 0.6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()
    from faster_whisper import WhisperModel

    from app import processing
    from app.config import settings

    files = sorted((ROOT / "test-audio").glob("*.truth.json"))
    for name in args.models:
        compute = "int8" if args.device == "cpu" else "float16"
        if args.device == "cuda":
            processing.add_cuda_libraries()
        t = time.time()
        model = WhisperModel(name, device=args.device, compute_type=compute, download_root=str(settings.model_dir))
        load = time.time() - t
        print(f"\n### {name}  ({args.device}, {compute}; load {load:.1f}s)", flush=True)
        for f in files:
            truth = json.loads(f.read_text(encoding="utf-8"))
            wav = f.with_name(truth["file"])
            t = time.time()
            segs, lang, _ = processing.transcribe_audio(model, wav, word_timestamps=True)
            took = time.time() - t
            rtf = took / truth["durationSec"]
            real = truth["turns"]
            if all(r["language"] == "English" for r in real):
                ref = words(" ".join(r["text"] for r in real))
                hyp = words(" ".join(s["text"] for s in segs))
                wer = lev(ref, hyp) / len(ref)
                print(f"  {wav.name:<34} {took:5.1f}s  RTF {rtf:.2f}  WER {wer:.0%}", flush=True)
                continue
            ok_lang = ok_script = 0
            cer = {}
            for r in real:
                code = LANG_CODE[r["language"]]
                mine = [s for s in segs if min(s["end"], r["end"]) - max(s["start"], r["start"]) > 0.3]
                text = " ".join(s["text"] for s in mine)
                langs = {s.get("language") for s in mine}
                ok_lang += langs == {code}
                ok_script += in_script(text, code)
                ref = norm(r["text"]).replace(" ", "")
                hyp = norm(text).replace(" ", "")
                cer.setdefault(code, []).append(min(1.0, lev(ref, hyp) / max(1, len(ref))))
            cers = "  ".join(f"CER {k} {sum(v)/len(v):.0%}" for k, v in sorted(cer.items()))
            print(f"  {wav.name:<34} {took:5.1f}s  RTF {rtf:.2f}  language {ok_lang}/{len(real)}  "
                  f"script {ok_script}/{len(real)}  {cers}", flush=True)
            for s in segs:
                print(f"      [{s['start']:5.1f}] {s.get('language')}: {s['text'][:90]}")


if __name__ == "__main__":
    main()
