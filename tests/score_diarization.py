"""Score a diarization engine on the test conversations, using the app's own code path.

    python tests/score_diarization.py --engine pyannote [--speakers N]
    python tests/score_diarization.py --engine sherpa-onnx

For every test-audio/*.truth.json: speakers found vs real, turns whose dominant label maps to
the right person, and whether that mapping is one-to-one (each label = one real person).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def ov(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="pyannote")
    ap.add_argument("--speakers", type=int, default=0)
    args = ap.parse_args()
    os.environ["DIARIZATION_ENGINE"] = args.engine
    os.environ["DIARIZATION_NUM_SPEAKERS"] = str(args.speakers) if args.speakers else ""
    from app import processing

    print(f"engine={args.engine} speakers={'auto' if not args.speakers else args.speakers}")
    for f in sorted((ROOT / "test-audio").glob("*.truth.json")):
        truth = json.loads(f.read_text(encoding="utf-8"))
        real = truth["turns"]
        t = time.time()
        turns = processing.diarize_file(f.with_name(truth["file"]))
        took = time.time() - t
        cover = defaultdict(lambda: defaultdict(float))
        for x in turns:
            for r in real:
                cover[x["speaker"]][r["speaker"]] += ov(x["start"], x["end"], r["start"], r["end"])
        mapping = {lab: max(c, key=c.get) for lab, c in cover.items() if sum(c.values()) > 0}
        ok, rows = 0, []
        for r in real:
            by = defaultdict(float)
            for x in turns:
                by[x["speaker"]] += ov(x["start"], x["end"], r["start"], r["end"])
            dom = max(by, key=by.get) if by and max(by.values()) > 0 else None
            good = dom is not None and mapping.get(dom) == r["speaker"]
            ok += good
            rows.append(f"    {r['start']:5.1f}s {r['speaker']:<9} -> {dom or '-':<10} {'OK' if good else 'WRONG'}")
        one_to_one = len(set(mapping.values())) == len(mapping) == len(truth["speakers"])
        print(f"\n{f.name.replace('.truth.json', '')}: {len(set(x['speaker'] for x in turns))} speakers found "
              f"(real {len(truth['speakers'])}), {ok}/{len(real)} turns right, "
              f"{'one label per person' if one_to_one else 'labels do not match people 1:1'}, {took:.0f}s")
        print("\n".join(rows))


if __name__ == "__main__":
    main()
