# Routech Automation Framework

Hybrid UI + API test automation for the Routech shipping/booking demo app.
Login is UI-driven (one-time, captcha solved by a human); everything else
(booking creation, etc.) is pure API automation reusing that session.

📄 **Read `handoff.md` first** — it has the full project context, business
rules, captured API contract, and known open items.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env           # defaults already match the given demo creds
```

## Running tests

```bash
pytest                     # everything
pytest -m parcel           # just parcel booking tests
pytest -m api              # API-only tests
```

On the **first run** (or whenever the cached session is stale), a browser
window opens on the login page with credentials pre-filled. Solve the
"I'm not a robot" captcha, then come back to the terminal and press
**ENTER** — the framework takes it from there and reuses that session for
every test afterward (cached in `.auth/`, auto-refreshed after ~55 min).

## Layout

See `handoff.md` §7 for the full structure breakdown.
