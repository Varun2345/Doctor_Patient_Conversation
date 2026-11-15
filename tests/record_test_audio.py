"""Record the test-audio WAVs as real consultations in the running app.

Each WAV is fed to Chromium as its microphone; the script signs in as the synthetic test
doctor, adds a test patient, presses Start, waits for the audio to finish, presses Stop,
then waits for the transcript and speaker labels. Prints the consultation links.

    python tests/record_test_audio.py [test-audio/*.wav ...]
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8765")
ROOT = Path(__file__).resolve().parent.parent
ACCOUNT = dict(l.strip().split("=", 1) for l in open(ROOT / "tests" / "output" / "test-account.env")
               if "=" in l and not l.startswith("#"))
PATIENTS = {
    "two-speakers-english": "TTS Test – 2 speakers English",
    "two-speakers-multilingual": "TTS Test – English Hindi",
    "three-speakers-english": "TTS Test – 3 speakers English",
    "three-speakers-multilingual": "TTS Test – English Hindi Telugu",
}


def record(p, wav: Path) -> str:
    truth = json.loads(wav.with_suffix(".truth.json").read_text(encoding="utf-8"))
    browser = p.chromium.launch(args=[
        "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={wav.as_posix()}%noloop"])
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.set_default_timeout(90000)
    page.goto(BASE + "/#/login")
    page.fill("input[name=email]", ACCOUNT["TEST_DOCTOR_EMAIL"])
    page.fill("input[name=password]", ACCOUNT["TEST_DOCTOR_PASSWORD"])
    page.click("button[type=submit]")
    page.wait_for_selector("a.big.new")

    name = PATIENTS.get(wav.stem, wav.stem)
    page.goto(BASE + "/#/new")
    page.wait_for_selector(".picker input[type=search]")
    page.fill(".picker input[type=search]", name)
    page.wait_for_timeout(800)
    existing = page.locator(f".picker .row:has-text('{name}')")
    if existing.count():
        existing.first.click()
    else:
        page.fill(".picker input[maxlength='120']", name)
        page.click("text=Add and select")
    page.wait_for_selector("button.big.start:not([disabled])")

    page.click("button.big.start")
    page.wait_for_selector("#rec-live")
    page.wait_for_timeout(int((truth["durationSec"] + 2.5) * 1000))
    page.click("button.big.stop")
    page.wait_for_selector(".result.ok", timeout=180000)
    cons = page.request.get(BASE + "/api/consultations?limit=1").json()["consultations"][0]
    cid = cons["consultationId"]
    print(f"{wav.name}: recorded {cons['durationSec']:.1f} s as {cid}; transcribing...", flush=True)

    end = time.time() + 900
    while time.time() < end:
        c = page.request.get(f"{BASE}/api/consultations/{cid}").json()["consultation"]
        if c["transcript"]["status"] in ("done", "failed"):
            break
        time.sleep(5)
    segs = c["transcript"].get("segments") or []
    speakers = sorted({s["speaker"] for s in segs if s.get("speaker")})
    print(f"  transcript: {c['transcript']['status']}, {len(segs)} lines, speakers {speakers}", flush=True)
    browser.close()
    return cid


def main() -> int:
    files = [Path(a) for a in sys.argv[1:]] or sorted((ROOT / "test-audio").glob("*.wav"))
    with sync_playwright() as p:
        links = [(f.name, record(p, f)) for f in files]
    print("\nOpen (signed in as the test doctor):")
    for name, cid in links:
        print(f"  {name}: http://localhost:8765/#/c/{cid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
