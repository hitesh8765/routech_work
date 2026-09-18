"""
Central configuration for the Routech automation framework.

Credentials are read from environment variables so nothing sensitive
is hardcoded/committed. See .env.example for the expected keys.
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
AUTH_DIR = ROOT_DIR / ".auth"
AUTH_DIR.mkdir(exist_ok=True)

# Where we persist captured session state per account type (b2b / c2c)
STORAGE_STATE_PATH = {
    "b2b": AUTH_DIR / "b2b_state.json",
    "c2c": AUTH_DIR / "c2c_state.json",
}

# ---------------------------------------------------------------------------
# Environment / URLs
# ---------------------------------------------------------------------------
BASE_URL = os.getenv("ROUTECH_BASE_URL", "https://liveroutech.demoe2.com")
LOGIN_URL = f"{BASE_URL}/login"

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
# B2B (business) login
B2B_EMAIL = os.getenv("ROUTECH_B2B_EMAIL", "neeraj@mailinator.com")
B2B_PASSWORD = os.getenv("ROUTECH_B2B_PASSWORD", "123456")

# C2C (individual) login
C2C_EMAIL = os.getenv("ROUTECH_C2C_EMAIL", "bharti@mailinator.com")
C2C_PASSWORD = os.getenv("ROUTECH_C2C_PASSWORD", "Test@123")

# ---------------------------------------------------------------------------
# Behavior
# ---------------------------------------------------------------------------
# Observed session cookie lifetime is ~1 hour on the demo env. We refresh
# proactively a bit before that to avoid mid-suite auth failures.
SESSION_MAX_AGE_SECONDS = int(os.getenv("ROUTECH_SESSION_MAX_AGE", 55 * 60))

# Headless UI login by default; set to "0" to watch it / solve captcha visibly.
UI_HEADLESS = os.getenv("ROUTECH_UI_HEADLESS", "0") == "1"

DEFAULT_TIMEOUT_MS = 30_000
