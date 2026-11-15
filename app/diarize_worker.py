"""Speaker diarization in a separate process.

sherpa-onnx keeps Python's GIL while it runs, which would freeze the web server
(including uploads from doctors who are recording) for the whole duration. Running
it here, in a child process at lower CPU priority, keeps the server responsive.

    python -m app.diarize_worker <audio file>   ->  prints {"turns": [...]} as JSON
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    from app.processing import diarize_in_process

    turns = diarize_in_process(Path(sys.argv[1]))
    sys.stdout.write(json.dumps({"turns": turns}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
