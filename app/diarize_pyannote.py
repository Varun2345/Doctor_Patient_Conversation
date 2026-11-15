"""Speaker diarization with pyannote.audio (default: speaker-diarization-community-1).

Runs in its OWN Python environment (.venv-diar: PyTorch + pyannote.audio), started by
processing.diarize_file() as a separate low-priority process. It only needs the standard
library, numpy, torch and pyannote - nothing from the main app environment.

    python -m app.diarize_pyannote <16 kHz mono WAV> --pipeline ID [--speakers N] [--cache DIR]

The Hugging Face token (needed once, to download the gated model) is read from HF_TOKEN.
After the first download the model is loaded from the cache and no network is needed.
Prints {"turns": [{"start", "end", "speaker"}...]} as JSON on the last line.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import wave


def speaker_name(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return "Speaker " + letters


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--pipeline", default="pyannote/speaker-diarization-community-1")
    ap.add_argument("--speakers", type=int, default=0)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--threads", type=int, default=0)
    args = ap.parse_args()

    import numpy as np
    import torch
    from pyannote.audio import Pipeline

    if args.threads:
        torch.set_num_threads(args.threads)
    token = os.environ.get("HF_TOKEN") or None
    pipeline = Pipeline.from_pretrained(args.pipeline, token=token, cache_dir=args.cache)
    if pipeline is None:
        raise SystemExit("Could not load the pyannote pipeline (check HF_TOKEN and that its "
                         "conditions were accepted on huggingface.co).")

    with wave.open(args.wav, "rb") as w:
        rate = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    audio = {"waveform": torch.from_numpy(pcm).unsqueeze(0), "sample_rate": rate}

    kwargs = {"num_speakers": args.speakers} if args.speakers > 0 else {}
    result = pipeline(audio, **kwargs)
    # pyannote.audio 4 returns an object; prefer the version without overlapping turns,
    # which is what transcript lines need. Older versions return the annotation directly.
    annotation = getattr(result, "exclusive_speaker_diarization", None) \
        or getattr(result, "speaker_diarization", None) or result

    names: dict[str, str] = {}
    turns = []
    for segment, _, label in sorted(annotation.itertracks(yield_label=True), key=lambda x: x[0].start):
        if label not in names:
            names[label] = speaker_name(len(names))
        turns.append({"start": round(float(segment.start), 2), "end": round(float(segment.end), 2),
                      "speaker": names[label]})
    sys.stdout.write("\n" + json.dumps({"turns": turns}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
