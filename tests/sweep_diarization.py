"""Compare speaker-embedding models / clustering thresholds on the test conversations.

    python tests/sweep_diarization.py

Scores = turns whose dominant label maps to the right person (out of all real turns),
for each test-audio/*.truth.json file. Diarization only (no transcription).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import sherpa_onnx
from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models" / "diarization"
SEG = MODELS / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
FILES = sorted((ROOT / "test-audio").glob("*.truth.json"))


def ov(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def score(turns, real):
    cover = defaultdict(lambda: defaultdict(float))
    for t in turns:
        for r in real:
            cover[t[2]][r["speaker"]] += ov(t[0], t[1], r["start"], r["end"])
    mapping = {lab: max(c, key=c.get) for lab, c in cover.items() if c}
    ok = 0
    for r in real:
        by = defaultdict(float)
        for t in turns:
            by[t[2]] += ov(t[0], t[1], r["start"], r["end"])
        if by and mapping.get(max(by, key=by.get)) == r["speaker"]:
            ok += 1
    return ok, len({t[2] for t in turns})


def run(emb: Path, clusters: int, thr: float):
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEG)), num_threads=8),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb), num_threads=8),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=clusters, threshold=thr),
        min_duration_on=0.3, min_duration_off=0.5)
    sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    out = []
    for f in FILES:
        truth = json.loads(f.read_text(encoding="utf-8"))
        audio = decode_audio(str(f.with_suffix("").with_suffix(".wav")), sampling_rate=sd.sample_rate)
        turns = [(r.start, r.end, r.speaker) for r in sd.process(audio).sort_by_start_time()]
        ok, n = score(turns, truth["turns"])
        out.append(f"{ok:>2}/{len(truth['turns'])} ({n} spk)")
    return out


def main():
    embs = sorted(p for p in MODELS.glob("*.onnx"))
    names = [f.name.split(".")[0].replace("three-speakers-", "") for f in FILES]
    print(f"{'embedding model':<58} {'setting':<10} " + "  ".join(f"{n:<14}" for n in names))
    for emb in embs:
        for clusters, thr in ((-1, 0.5), (-1, 0.4), (-1, 0.3), (3, 0.5)):
            label = f"auto t={thr}" if clusters < 0 else f"fixed {clusters}"
            try:
                res = run(emb, clusters, thr)
            except Exception as exc:  # incompatible model
                res = [f"error: {exc}"[:40]]
            print(f"{emb.name:<58} {label:<10} " + "  ".join(f"{r:<14}" for r in res), flush=True)


if __name__ == "__main__":
    sys.exit(main())
