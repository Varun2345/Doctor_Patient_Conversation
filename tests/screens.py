"""Capture review screenshots of every screen (uses the synthetic test account).

    python tests/screens.py [out_dir]
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8765")
ROOT = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
acct = dict(l.strip().split("=", 1) for l in open(ROOT / "output" / "test-account.env")
            if "=" in l and not l.startswith("#"))
wav = (ROOT / "output" / "audio" / "dialogue.wav").as_posix()

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                f"--use-file-for-fake-audio-capture={wav}%noloop"])
    pg = b.new_page(viewport={"width": 1280, "height": 880})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(BASE + "/#/login"); pg.wait_for_selector("input[name=email]"); pg.wait_for_timeout(400)
    pg.screenshot(path=str(OUT / "01-sign-in.png"), full_page=True)
    pg.goto(BASE + "/#/signup"); pg.wait_for_selector("input[name=fullName]"); pg.wait_for_timeout(300)
    pg.screenshot(path=str(OUT / "01b-sign-up.png"), full_page=True)
    pg.goto(BASE + "/#/login"); pg.wait_for_selector("input[name=email]")
    pg.fill("input[name=email]", acct["TEST_DOCTOR_EMAIL"]); pg.fill("input[name=password]", acct["TEST_DOCTOR_PASSWORD"])
    pg.click("button[type=submit]"); pg.wait_for_selector("a.big.new"); pg.wait_for_selector(".row"); pg.wait_for_timeout(600)
    pg.screenshot(path=str(OUT / "02-home.png"), full_page=True)
    pg.fill(".big-search input", "Test"); pg.wait_for_selector(".search-results:not([hidden]) .row"); pg.wait_for_timeout(300)
    pg.screenshot(path=str(OUT / "02b-home-search.png"))
    pg.fill(".big-search input", ""); pg.click(".add-patient"); pg.wait_for_selector("dialog.modal[open]"); pg.wait_for_timeout(300)
    pg.screenshot(path=str(OUT / "02c-add-patient.png"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
    cons = pg.request.get(BASE + "/api/consultations?limit=30").json()["consultations"]
    best = next((c for c in cons if c["transcript"]["status"] == "done" and c["patient"]), cons[0])
    pg.goto(f"{BASE}/#/c/{best['consultationId']}"); pg.wait_for_selector("#orig-audio"); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(OUT / "07-consultation-detail.png"), full_page=True)
    if best["patient"]:
        pg.goto(f"{BASE}/#/p/{best['patient']['patientId']}"); pg.wait_for_selector(".row"); pg.wait_for_timeout(400)
        pg.screenshot(path=str(OUT / "09-patient-history.png"), full_page=True)
    pg.goto(BASE + "/#/new"); pg.wait_for_selector(".picker"); pg.wait_for_timeout(400)
    pg.screenshot(path=str(OUT / "03a-choose-patient.png"), full_page=True)
    pg.fill(".picker input[type=search]", "Test Patient One"); pg.click(".picker .row:has-text('Test Patient One')")
    pg.wait_for_selector("button.big.start:not([disabled])"); pg.wait_for_timeout(300)
    pg.screenshot(path=str(OUT / "03-new-consultation.png"), full_page=True)
    pg.click("button.big.start"); pg.wait_for_selector("#rec-live"); pg.wait_for_timeout(6500)
    pg.screenshot(path=str(OUT / "04-recording.png"), full_page=True)
    pg.click("button.big.stop"); pg.wait_for_selector(".result.ok", timeout=60000); pg.wait_for_timeout(300)
    pg.screenshot(path=str(OUT / "06-saved.png"), full_page=True)
    phone = b.new_page(viewport={"width": 390, "height": 844})
    phone.goto(BASE + "/#/login")
    phone.fill("input[name=email]", acct["TEST_DOCTOR_EMAIL"]); phone.fill("input[name=password]", acct["TEST_DOCTOR_PASSWORD"])
    phone.click("button[type=submit]"); phone.wait_for_selector(".row"); phone.wait_for_timeout(500)
    phone.screenshot(path=str(OUT / "12-home-phone.png"), full_page=True)
    b.close()
print("JS errors:", errors or "none")
print("saved to", OUT)
