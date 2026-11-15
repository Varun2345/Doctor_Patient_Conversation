"""Sign-up flow test (synthetic accounts, removed at the end).

Checks the open sign-up page on the running server (BASE_URL), then starts two
extra temporary servers to check SIGNUP_CODE and SIGNUP_ENABLED=false.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import urllib.request
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8765")
APP = Path(__file__).resolve().parent.parent
SHOTS = APP / "docs" / "screenshots"
results = []
created_emails: list[str] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail != "" else ""), flush=True)


def new_email():
    email = f"signup.test+{secrets.token_hex(3)}@pilot.local"
    created_emails.append(email)
    return email


def start_server(port, env_extra):
    env = {**os.environ, **env_extra}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=APP, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=2)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("server did not start")


def main():
    password = "Test-" + secrets.token_urlsafe(9)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 880})

        # --- open sign-up via the sign-in page link
        page.goto(BASE + "/#/login")
        page.click("text=Create a doctor account")
        page.wait_for_selector("input[name=fullName]")
        check("sign-in page links to sign-up", page.url.endswith("#/signup"))
        email = new_email()
        page.fill("input[name=fullName]", "Dr. Signup Test")
        page.fill("input[name=email]", email)
        page.fill("input[name=password]", password)
        page.fill("input[name=password2]", password + "x")
        page.screenshot(path=str(SHOTS / "01b-sign-up.png"))
        page.click("button[type=submit]")
        page.wait_for_selector(".notice.err:not([hidden])")
        check("mismatched passwords caught", "do not match" in page.inner_text(".notice.err"))

        page.fill("input[name=password2]", password)
        page.click("button[type=submit]")
        page.wait_for_selector("a.big.new")
        check("account created and signed in", page.is_visible("text=Dr. Signup Test"))
        try:
            page.wait_for_selector(".empty-state:has-text('No consultations')", timeout=15000)
            empty = True
        except Exception:
            empty = False
        check("new doctor starts with no records", empty)
        pats = page.request.get(BASE + "/api/patients").json()["patients"]
        check("new doctor cannot see other doctors' patients", pats == [])

        page.click("text=Sign out")
        page.wait_for_selector("input[name=email]")
        page.fill("input[name=email]", email)
        page.fill("input[name=password]", password)
        page.click("button[type=submit]")
        page.wait_for_selector("a.big.new")
        check("new account can sign in again", page.is_visible("text=Dr. Signup Test"))

        r = page.request.post(BASE + "/api/auth/signup", data={"fullName": "Dup", "email": email.upper(), "password": password})
        check("duplicate email refused", r.status == 409, r.status)
        r = page.request.post(BASE + "/api/auth/signup", data={"fullName": "Short", "email": new_email(), "password": "a"})
        check("short password accepted (no password rule)", r.status == 201, r.status)
        r = page.request.post(BASE + "/api/auth/signup", data={"fullName": "Empty", "email": new_email(), "password": ""})
        check("empty password refused", r.status == 422, r.status)
        r = page.request.post(BASE + "/api/auth/signup", data={"fullName": "Bad", "email": "not-an-email", "password": password})
        check("invalid email refused", r.status == 422, r.status)

        # --- code-protected sign-up
        code = "PILOT-" + secrets.token_hex(3)
        srv = start_server(8766, {"SIGNUP_CODE": code})
        try:
            cp = browser.new_page(viewport={"width": 1280, "height": 960})
            cp.goto("http://127.0.0.1:8766/#/signup")
            cp.wait_for_selector("input[name=signupCode]")
            check("sign-up code field shown when required", True)
            cp.screenshot(path=str(SHOTS / "01c-sign-up-with-code.png"))
            r = cp.request.post("http://127.0.0.1:8766/api/auth/signup",
                                data={"fullName": "No Code", "email": new_email(), "password": password, "signupCode": "wrong"})
            check("wrong sign-up code refused", r.status == 403, r.status)
            r = cp.request.post("http://127.0.0.1:8766/api/auth/signup",
                                data={"fullName": "With Code", "email": new_email(), "password": password, "signupCode": code})
            check("correct sign-up code accepted", r.status == 201, r.status)
        finally:
            srv.kill()

        # --- sign-up turned off
        srv = start_server(8767, {"SIGNUP_ENABLED": "false"})
        try:
            off = browser.new_page()
            off.goto("http://127.0.0.1:8767/#/login")
            off.wait_for_selector("input[name=email]")
            check("no sign-up link when disabled", not off.is_visible("text=Create a doctor account"))
            r = off.request.post("http://127.0.0.1:8767/api/auth/signup",
                                 data={"fullName": "Off", "email": new_email(), "password": password})
            check("sign-up API refused when disabled", r.status == 403, r.status)
        finally:
            srv.kill()
        browser.close()

    sys.path.insert(0, str(APP))
    from app.db import get_db
    removed = get_db().doctors.delete_many({"email": {"$in": created_emails}}).deleted_count
    print(f"cleaned up {removed} synthetic sign-up account(s)")
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
