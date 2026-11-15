"""Unit tests for joining Whisper word timestamps with speaker turns (no models needed).

    python tests/test_labeling.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.processing import label_segments, speaker_name, transcript_text  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    failures += 0 if cond else 1
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


A, B = "Speaker A", "Speaker B"
turns = [{"start": 0.0, "end": 2.0, "speaker": A}, {"start": 2.1, "end": 4.0, "speaker": B}]

# One Whisper segment in which the speaker changes mid-sentence.
seg = {"start": 0.0, "end": 4.0, "text": "How are you? Fine thanks doctor.",
       "words": [(0.0, 0.4, " How"), (0.4, 0.7, " are"), (0.7, 1.2, " you?"),
                 (2.2, 2.6, " Fine"), (2.6, 3.0, " thanks"), (3.0, 3.6, " doctor.")]}
out, n = label_segments([seg], turns)
check("mid-sentence speaker change splits the line", [s["speaker"] for s in out] == [A, B], out)
check("text is kept word-for-word", " ".join(s["text"] for s in out) == "How are you? Fine thanks doctor.")
check("split line starts at the first word of the new speaker", out[1]["start"] == 2.2)
check("speaker count", n == 2)

# A single short misattributed word between two of A's words is smoothed away.
turns2 = [{"start": 0, "end": 1.0, "speaker": A}, {"start": 1.0, "end": 1.2, "speaker": B},
          {"start": 1.2, "end": 3.0, "speaker": A}]
seg2 = {"start": 0, "end": 3, "text": "a b c", "words": [(0.2, 0.8, " a"), (1.0, 1.2, " b"), (1.3, 2.0, " c")]}
out2, _ = label_segments([seg2], turns2)
check("single short flip is smoothed", len(out2) == 1 and out2[0]["speaker"] == A, out2)

# Words in a gap between turns take the nearest speaker; nothing is dropped.
seg3 = {"start": 5.0, "end": 5.5, "text": "okay", "words": [(5.0, 5.5, " okay")]}
out3, _ = label_segments([seg3], turns)
check("no-overlap word beyond 1 s keeps text, no label", out3[0]["text"] == "okay" and out3[0]["speaker"] is None, out3)

# Segment without word timestamps falls back to whole-segment overlap.
out4, _ = label_segments([{"start": 2.5, "end": 3.5, "text": "x"}], turns)
check("segment without words uses overlap", out4[0]["speaker"] == B)

check("speaker names A..Z, AA", [speaker_name(0), speaker_name(25), speaker_name(26)] == [A, "Speaker Z", "Speaker AA"])
check("transcript text format", transcript_text(out).splitlines()[1] == "[00:02] Speaker B: Fine thanks doctor.",
      transcript_text(out))

print("\nall passed" if not failures else f"\n{failures} failed")
sys.exit(1 if failures else 0)
