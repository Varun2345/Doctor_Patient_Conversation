"""End-to-end pilot test with REAL audio through a real browser recorder.

Chromium is given a synthetic two-voice consultation (tests/output/audio/dialogue.wav)
as its microphone. The test drives the actual UI and then checks the stored data.

    python tests/build_dialogue.py tests/output/audio      (after make_dialogue.ps1)
    python tests/e2e_test.py

Requires the server running at BASE_URL and a test doctor in tests/output/test-account.env.
Only synthetic data is used.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import av
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8765")
ROOT = Path(__file__).resolve().parent
APP = ROOT.parent
OUT = ROOT / "output"
SHOTS = APP / "docs" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
WAV = (OUT / "audio" / "dialogue.wav").as_posix()
ACCOUNT = dict(l.strip().split("=", 1) for l in open(OUT / "test-account.env")
               if "=" in l and not l.startswith("#"))

results: list[dict] = []


def check(name: str, cond, detail="") -> bool:
    results.append({"check": name, "ok": bool(cond), "detail": str(detail)})
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail != "" else ""), flush=True)
    return bool(cond)


def shot(page, name, full=False):
    page.wait_for_timeout(300)
    page.screenshot(path=str(SHOTS / name), full_page=full)


def login(page, email, password):
    page.goto(BASE + "/#/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")
    page.wait_for_selector("a.big.new")


def get(page, path):
    r = page.request.get(BASE + path)
    try:
        return r.status, r.json()
    except Exception:
        return r.status, r.body()


def latest(page):
    return get(page, "/api/consultations?limit=1")[1]["consultations"][0]


def decoded_seconds(data: bytes) -> float:
    samples, rate = 0, 48000
    with av.open(io.BytesIO(data)) as container:
        for frame in container.decode(audio=0):
            samples += frame.samples
            rate = frame.sample_rate
    return samples / rate


def wait_for(fn, timeout, interval=2.0):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return None


def record(page, seconds, during=None):
    page.click("button.big.start")
    page.wait_for_selector("#rec-live", timeout=15000)
    t0 = time.time()
    if during:
        during(t0)
    remaining = seconds - (time.time() - t0)
    if remaining > 0:
        page.wait_for_timeout(int(remaining * 1000))
    page.click("button.big.stop")
    page.wait_for_selector(".result.ok, .result.err", timeout=180000)


def choose_patient(page, name):
    """On the recorder screen the patient picker is open; pick an existing patient."""
    page.wait_for_selector(".picker input[type=search]")
    page.fill(".picker input[type=search]", name)
    page.click(f".picker .row:has-text('{name}')")
    page.wait_for_selector("button.big.start:not([disabled])")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-audio-capture={WAV}%noloop",
        ])
        ctx = browser.new_context(viewport={"width": 1280, "height": 880}, accept_downloads=True)
        page = ctx.new_page()
        page.on("pageerror", lambda e: check("no JS errors", False, e))

        # ---------------------------------------------------------- 1. sign-in
        page.goto(BASE + "/#/login")
        page.wait_for_selector("input[name=email]")
        shot(page, "01-sign-in.png")
        page.fill("input[name=email]", ACCOUNT["TEST_DOCTOR_EMAIL"])
        page.fill("input[name=password]", "wrong-password")
        page.click("button[type=submit]")
        page.wait_for_selector(".notice.err:not([hidden])")
        check("wrong password rejected", "incorrect" in page.inner_text(".notice.err"))
        login(page, ACCOUNT["TEST_DOCTOR_EMAIL"], ACCOUNT["TEST_DOCTOR_PASSWORD"])
        check("doctor signed in", page.is_visible("text=Dr. Test Doctor"))

        # ---------------------------------------------------------- 2. full consultation, with patient + network outage
        page.click("a.big.new")
        page.wait_for_selector("button.big.start")
        page.wait_for_selector(".picker")
        check("Start is disabled until a patient is chosen", page.is_disabled("button.big.start"))
        r = page.request.post(BASE + "/api/consultations", data={"patientId": None, "capture": {"mimeType": "audio/webm"}})
        check("server refuses a consultation without a patient", r.status == 422, r.status)
        r = page.request.post(BASE + "/api/patients", data={"age": 40})
        check("patient without a name refused", r.status == 422, r.status)
        page.fill(".picker input[maxlength='120']", "Test Patient One")
        page.fill(".picker input[type=number]", "34")
        page.select_option(".picker select", "female")
        page.click("text=Add and select")
        page.wait_for_selector("button.big.start:not([disabled])")
        check("Start enabled once the patient is added", not page.is_disabled("button.big.start"))
        shot(page, "03-new-consultation.png")

        def outage(t0):
            page.wait_for_timeout(12000)
            shot(page, "04-recording.png")
            page.route("**/chunks/**", lambda route: route.abort())
            page.wait_for_selector(".save-line.warn", timeout=20000)
            check("upload outage shown while recording continues",
                  "still recording" in page.inner_text("#save-line"), page.inner_text("#save-line"))
            shot(page, "05-recording-server-unreachable.png")
            page.wait_for_timeout(14000)
            page.unroute("**/chunks/**")

        record(page, 80, during=outage)
        check("saved screen shown", page.is_visible(".result.ok"), page.inner_text(".rec-shell")[:200])
        shot(page, "06-saved.png")
        c = latest(page)
        cid = c["consultationId"]
        check("status saved", c["status"] == "saved", c["status"])
        check("doctor + patient linked", c["doctorName"] == "Dr. Test Doctor" and c["patient"]["fullName"] == "Test Patient One")
        check("duration ~80 s", 78 <= c["durationSec"] <= 86, c["durationSec"])
        status, data = page.request.get(f"{BASE}/api/consultations/{cid}/audio/original").status, \
            page.request.get(f"{BASE}/api/consultations/{cid}/audio/original").body()
        sha = hashlib.sha256(data).hexdigest()
        check("original downloadable", status == 200 and len(data) > 100_000, f"{len(data)} bytes")
        check("downloaded original matches stored SHA-256", sha == c["audio"]["original"]["sha256"])
        secs = decoded_seconds(data)
        check("decoded audio length matches recording (no gaps lost in outage)",
              abs(secs - c["durationSec"]) < 2.5, f"decoded {secs:.1f}s vs recorded {c['durationSec']:.1f}s")
        r = page.request.get(f"{BASE}/api/consultations/{cid}/audio/original", headers={"Range": "bytes=0-99"})
        check("range requests (seeking) work", r.status == 206 and len(r.body()) == 100)

        latencies = []

        def processed():
            t = time.time()
            d = get(page, f"/api/consultations/{cid}")[1]["consultation"]
            h0 = time.time()
            page.request.get(BASE + "/api/health")
            latencies.extend([h0 - t, time.time() - h0])
            done = d["transcript"]["status"] in ("done", "failed") \
                and d["audio"]["original"].get("verified") is not None
            return d if done else None
        d = wait_for(processed, 600, interval=1.0)
        check("server stays responsive while transcribing + labelling speakers",
              latencies and max(latencies) < 3.0, f"max response {max(latencies or [0]):.2f}s over {len(latencies)} requests")
        check("transcript produced", d and d["transcript"]["status"] == "done", d and d["transcript"].get("error"))
        text = (d or {}).get("transcript", {}).get("text") or ""
        (OUT / "transcript-sample.txt").write_text(text, encoding="utf-8")
        low = text.lower()
        check("transcript has opening speech", "headache" in low, text[:120])
        check("transcript continues after 14 s silent pause", "blood pressure" in low and "monday" in low)
        segs = (d or {}).get("transcript", {}).get("segments") or []
        diar = (d or {}).get("diarization") or {}
        check("speaker labels added to every transcript line",
              diar.get("status") == "done" and segs and all(s.get("speaker") for s in segs), diar.get("error"))
        check("two speakers detected (automatic count)", diar.get("speakers") == 2, diar.get("speakers"))

        def spk(word):
            return next((s["speaker"] for s in segs if word in s["text"].lower()), None)
        voice1 = {spk("brings you"), spk("temperature"), spk("normal"), spk("monday")}
        voice2 = {spk("headache"), spk("paracetamol"), spk("tired"), spk("next week")}
        check("speaker labels follow who actually spoke (incl. after 14 s pause)",
              len(voice1) == 1 and len(voice2) == 1 and voice1 != voice2 and None not in voice1 | voice2,
              f"{voice1} / {voice2}")
        files = page.request.get(f"{BASE}/api/consultations/{cid}").json()["consultation"]["audio"]
        check("audio is still ONE original file (speaker labels are text only)",
              set(k for k, v in files.items() if v) == {"original"}, list(files))
        check("original integrity re-verified after processing", d and d["audio"]["original"]["verified"] is True)

        page.goto(f"{BASE}/#/c/{cid}")
        page.wait_for_selector("#orig-audio")
        page.wait_for_selector(".speakers:has-text('2 speakers')")
        check("speaker summary shown on consultation page", True)
        check("no Change patient button on a patient's consultation", not page.is_visible("text=Change patient"))
        page.wait_for_timeout(800)
        shot(page, "07-consultation-detail.png", full=True)

        page.click("text=Edit transcript")
        page.wait_for_selector(".seg-edit")
        boxes = page.locator("textarea.seg-text")
        for i in range(boxes.count()):
            if "paracetamol" in boxes.nth(i).input_value().lower():
                boxes.nth(i).fill("Only paracetamol 500 mg, twice a day.")
                break
        rename = page.locator(".rename input").first
        rename.fill("Doctor")
        shot(page, "08-transcript-editor.png", full=True)
        page.click("text=Save transcript")
        page.wait_for_selector("text=Last edited by")
        d2 = get(page, f"/api/consultations/{cid}")[1]["consultation"]
        t2 = d2["transcript"]
        esegs = t2.get("segments") or []
        check("edit replaces the stored transcript (one version)",
              "500 mg" in (t2.get("text") or "") and t2.get("edited") is True
              and not any(k in t2 for k in ("editedText", "editedSegments", "machineText", "revisions")), sorted(t2))
        check("only the final transcript is shown", not page.is_visible("text=Show automatic version")
              and "500 mg" in page.inner_text(".segments"))
        check("renaming a speaker changes every one of its lines",
              any(x["speaker"] == "Doctor" for x in esegs) and not any(x["speaker"] == "Speaker A" for x in esegs))
        data2 = page.request.get(f"{BASE}/api/consultations/{cid}/audio/original").body()
        check("original audio unchanged after edit", hashlib.sha256(data2).hexdigest() == sha)
        shot(page, "08-transcript-edited.png", full=True)

        # ---------------------------------------------------------- 3. second consultation for the same patient
        page.goto(BASE + "/#/new")
        choose_patient(page, "Test Patient One")
        record(page, 12)
        c2 = latest(page)
        check("second consultation linked to the chosen patient", c2["status"] == "saved" and c2["patientId"] == c["patientId"])
        status, pdata = get(page, f"/api/patients/{c['patientId']}")
        check("patient history has both consultations", len(pdata["consultations"]) == 2, len(pdata["consultations"]))
        page.goto(f"{BASE}/#/p/{c['patientId']}")
        page.wait_for_selector("text=Consultations (2)")
        shot(page, "09-patient-history.png")

        # ---------------------------------------------------------- 4. browser crash mid-recording -> recovery from device
        page.goto(BASE + "/#/new")
        choose_patient(page, "Test Patient One")
        page.click("button.big.start")
        page.wait_for_selector("#rec-live")
        page.wait_for_timeout(8000)
        page.route("**/chunks/**", lambda route: route.abort())
        page.wait_for_timeout(14000)
        crashed = latest(page)
        page.close(run_before_unload=False)  # simulates closed tab / crash: no Stop pressed
        page = ctx.new_page()
        page.goto(BASE + "/#/")
        page.wait_for_selector("text=Unsaved recording on this device", timeout=20000)
        shot(page, "10-recovery-on-device.png")
        page.click("text=Upload and save now")
        page.wait_for_selector("text=Recovered recording saved", timeout=60000)
        rc = get(page, f"/api/consultations/{crashed['consultationId']}")[1]["consultation"]
        audio = page.request.get(f"{BASE}/api/consultations/{rc['consultationId']}/audio/original").body()
        secs = decoded_seconds(audio)
        check("crashed recording recovered from device", rc["status"] == "saved" and rc["endReason"] == "recovered",
              f"{secs:.1f}s recovered of ~22s")
        check("recovered audio covers time while server was unreachable", secs >= 17, f"{secs:.1f}s")

        # ---------------------------------------------------------- 5. home screen (desktop + phone width)
        page.goto(BASE + "/#/")
        page.wait_for_selector(".row")
        shot(page, "02-home.png", full=True)
        phone = ctx.new_page()
        phone.set_viewport_size({"width": 390, "height": 844})
        phone.goto(BASE + "/#/")
        phone.wait_for_selector(".row")
        shot(phone, "12-home-phone.png", full=True)
        phone.close()

        # ---------------------------------------------------------- 6. microphone permission denied
        before = get(page, "/api/consultations?limit=100")[1]["consultations"]
        denied = ctx.new_page()
        denied.add_init_script("""navigator.mediaDevices.getUserMedia = () =>
            Promise.reject(new DOMException('denied', 'NotAllowedError'));""")
        denied.goto(BASE + "/#/new")
        choose_patient(denied, "Test Patient One")
        denied.click("button.big.start")
        denied.wait_for_selector(".notice.err")
        msg = denied.inner_text(".notice.err")
        check("mic permission denial explained", "permission was denied" in msg, msg[:100])
        after = get(page, "/api/consultations?limit=100")[1]["consultations"]
        check("no consultation created when mic denied", len(after) == len(before))
        shot(denied, "11-microphone-denied.png")
        denied.close()

        # ---------------------------------------------------------- 7. access separation between doctors
        email_b = f"test.doctor.b+{secrets.token_hex(3)}@pilot.local"
        pw_b = "Test-" + secrets.token_urlsafe(9)
        subprocess.run([sys.executable, "-m", "app.manage", "create-doctor", "--email", email_b,
                        "--name", "Dr. Second Test", "--password-stdin"], input=pw_b + "\n",
                       text=True, cwd=APP, check=True, capture_output=True)
        ctx_b = browser.new_context()
        pb = ctx_b.new_page()
        login(pb, email_b, pw_b)
        check("other doctor cannot open consultation", pb.request.get(f"{BASE}/api/consultations/{cid}").status == 404)
        check("other doctor cannot fetch audio", pb.request.get(f"{BASE}/api/consultations/{cid}/audio/original").status == 404)
        check("other doctor sees no patients", get(pb, "/api/patients")[1]["patients"] == [])
        anon = browser.new_context().new_page()
        check("signed-out request refused", anon.request.get(f"{BASE}/api/consultations/{cid}/audio/original").status == 401)
        subprocess.run([sys.executable, "-m", "app.manage", "deactivate-doctor", "--email", email_b],
                       cwd=APP, check=True, capture_output=True)
        browser.close()

    (OUT / "e2e-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    failed = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
