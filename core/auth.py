"""
Handles the ONE-TIME UI login flow (captcha requires a human), then persists
the resulting session (cookies) via Playwright's storage_state so every other
test -- UI or API -- can reuse it without logging in again.

Usage (typically only called once per suite run, via a session-scoped fixture):

    from core.auth import ensure_logged_in

    state_path = ensure_logged_in(account_type="b2b")
    # state_path now points at a fresh .auth/b2b_state.json

Design notes
------------
- Session cookie on this demo env expires after ~1 hour (observed via
  Set-Cookie `Expires` header on the login response). We store the login
  timestamp alongside the state file and re-login automatically once stale,
  rather than trusting the file forever.
- The captcha ("I'm not a robot") cannot be solved by automation. When no
  fresh session exists, this pauses with `input()` so a human can solve it
  in the visible browser, then continues.
"""
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import settings


def _meta_path(state_path: Path) -> Path:
    return state_path.with_suffix(".meta.json")


def _is_state_fresh(state_path: Path) -> bool:
    """True if a storage_state file exists and is within our max-age window."""
    meta_path = _meta_path(state_path)
    if not state_path.exists() or not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    age = time.time() - meta.get("captured_at", 0)
    return age < settings.SESSION_MAX_AGE_SECONDS


def _credentials_for(account_type: str):
    if account_type == "b2b":
        return settings.B2B_EMAIL, settings.B2B_PASSWORD, "business"
    if account_type == "c2c":
        return settings.C2C_EMAIL, settings.C2C_PASSWORD, "individual"
    raise ValueError(f"Unknown account_type: {account_type!r} (expected 'b2b' or 'c2c')")


def perform_ui_login(account_type: str = "b2b", headless: bool = None) -> Path:
    """
    Drives the real login UI (fills credentials, submits, PAUSES for a human
    to solve the captcha, then clicks submit and waits for /dashboard).

    Returns the path to the freshly-written storage_state JSON.
    """
    email, password, user_type = _credentials_for(account_type)
    headless = settings.UI_HEADLESS if headless is None else headless
    state_path = settings.STORAGE_STATE_PATH[account_type]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(settings.LOGIN_URL)

        # "Login as business" is selected by default; only switch for c2c.
        if account_type == "c2c":
            page.get_by_text("Login as individual", exact=False).click()

        page.get_by_label("Email/Mobile Number").fill(email)
        page.get_by_label("Password", exact=False).fill(password)

        print(
            "\n"
            "==================== ACTION NEEDED ====================\n"
            f"  Browser is open on {settings.LOGIN_URL}\n"
            "  Please solve the 'I'm not a robot' captcha now.\n"
            "  Once it's checked/solved, type:  yes done\n"
            "  then press ENTER. (do NOT click Submit yourself)\n"
            "========================================================\n"
        )
        confirmation = ""
        while confirmation.strip().lower() != "yes done":
            confirmation = input("Type 'yes done' once the captcha is solved: ")

        submit_button = page.get_by_role("button", name="Submit")
        try:
            submit_button.click(timeout=15_000)
        except Exception as exc:
            # Most likely cause: captcha wasn't actually completed (still
            # unchecked/expired), so the button never became enabled/clickable.
            debug_path = state_path.with_name(f"{account_type}_login_failure.png")
            page.screenshot(path=str(debug_path))
            raise RuntimeError(
                "Could not click Submit -- it likely never became enabled, which "
                "usually means the captcha wasn't actually completed (or expired) "
                f"before confirming. A screenshot was saved to {debug_path} -- "
                "open it to check the captcha's state. Re-run the test and make "
                "sure the checkbox shows a green checkmark before typing 'yes done'."
            ) from exc

        page.wait_for_url("**/dashboard", timeout=settings.DEFAULT_TIMEOUT_MS)

        context.storage_state(path=str(state_path))
        browser.close()

    _meta_path(state_path).write_text(json.dumps({"captured_at": time.time()}))
    print(f"[auth] Session captured -> {state_path}")
    return state_path


def ensure_logged_in(account_type: str = "b2b", force: bool = False) -> Path:
    """
    Returns a path to a fresh storage_state file, performing UI login only
    if we don't already have one that's still within the freshness window.
    """
    state_path = settings.STORAGE_STATE_PATH[account_type]
    if not force and _is_state_fresh(state_path):
        print(f"[auth] Reusing existing session for {account_type} ({state_path})")
        return state_path
    return perform_ui_login(account_type=account_type)
