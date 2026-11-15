"""Score the app's speaker labelling against the known timings of a test conversation.

    python tests/eval_diarization.py test-audio/three-speakers-english.wav [--multilingual] [--speakers N]

Uses exactly the server's pipeline: faster-whisper (word timestamps) + sherpa-onnx
diarization (separate process) + label_segments(). Prints the labelled transcript and:
  - speakers detected vs real
  - turn accuracy: share of real turns whose dominant label maps to the right person
  - time accuracy: share of speech time attributed to the right person
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--multilingual", action="store_true", help="per-segment language detection")
    ap.add_argument("--speakers", type=int, default=0, help="fix the number of speakers (0 = auto)")
    args = ap.parse_args()
    if args.speakers:
        os.environ["DIARIZATION_NUM_SPEAKERS"] = str(args.speakers)
    os.environ["DIARIZATION_ENGINE"] = "sherpa-onnx"
    from app import processing

    wav = Path(args.wav)
    truth = json.loads(wav.with_suffix(".truth.json").read_text(encoding="utf-8"))
    real = truth["turns"]

    model = processing._load_whisper()
    seg_iter, info = model.transcribe(str(wav), vad_filter=True, beam_size=5, word_timestamps=True,
                                      multilingual=args.multilingual)
    segs = [{"start": s.start, "end": s.end, "text": s.text.strip(),
             "words": [(w.start, w.end, w.word) for w in s.words or []]} for s in seg_iter if s.text.strip()]
    turns = processing.diarize_file(wav)
    labelled, n = processing.label_segments(segs, turns)

    # map each predicted label to the real speaker it overlaps most
    cover = defaultdict(lambda: defaultdict(float))
    for t in turns:
        for r in real:
            cover[t["speaker"]][r["speaker"]] += overlap(t["start"], t["end"], r["start"], r["end"])
    mapping = {lab: max(c, key=c.get) for lab, c in cover.items() if c}

    ok_turns, ok_time, total_time = 0, 0.0, 0.0
    rows = []
    for r in real:
        by_label = defaultdict(float)
        for t in turns:
            by_label[t["speaker"]] += overlap(t["start"], t["end"], r["start"], r["end"])
        dom = max(by_label, key=by_label.get) if by_label else None
        right = sum(v for lab, v in by_label.items() if mapping.get(lab) == r["speaker"])
        total_time += r["end"] - r["start"]
        ok_time += right
        good = dom is not None and mapping.get(dom) == r["speaker"]
        ok_turns += good
        rows.append((r, dom, good))

    print(f"\n== {wav.name}  ({truth['durationSec']} s, {len(real)} real turns)")
    print(f"Whisper language: {info.language} ({info.language_probability:.2f})"
          f"{'  [multilingual mode]' if args.multilingual else ''}")
    print(f"Speakers detected: {n}  (real: {len(truth['speakers'])})   label -> person: {mapping}")
    print(f"Turn accuracy: {ok_turns}/{len(real)}   Time accuracy: {100 * ok_time / total_time:.0f}%")
    print("\nReal turn                       -> label")
    for r, dom, good in rows:
        print(f"  {r['start']:5.1f}s {r['speaker']:<9} {r['language']:<8} -> {dom or '-':<10} {'OK' if good else 'WRONG'}")
    print("\nTranscript as the app would show it:")
    for s in labelled:
        print(f"  [{processing._fmt_ts(s['start'])}] {s.get('speaker') or '?'}: {s['text']}")


if __name__ == "__main__":
    main()
