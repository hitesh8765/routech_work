"""
Cross-booking diversity + coverage tracking, driven by the user's rules.

LOCATIONS (user rules, 2026-09-21/22 -- apply PER BOOKING, not per type)
- Every booking uses a genuinely different pickup/dropoff location.
- A non-Saudi location may only repeat after ~15-20 OTHER distinct non-Saudi
  locations (cooldown here: 17); a Saudi one after ~6-8 (cooldown: 7).
- Exactly one side is Saudi (enforced by the caller) and the Saudi side
  strictly alternates booking to booking, globally.
- Locations the server rejected are blacklisted (mark_location_bad).

DELIVERY PARTNERS (user rule, 2026-10-09 -- supersedes the old global
round-robin and every earlier include/exclude list)
- EVERY partner that shows a quoted amount for a route is a candidate --
  all DHL variants and all FedEx variants (freight included) are in. There
  is no hand-maintained rotation list any more.
- The goal is to test every available partner for EVERY booking type, so
  coverage is tracked PER BOOKING TYPE: for each (type, partner) we count
  attempts, passes and fails.
- For a booking, the partner chosen is the LEAST-TESTED one (for that
  booking type) among the partners quoted on that route; ties go to the one
  tested longest ago. A partner that was tested -- including one skipped
  because the server wouldn't price it -- moves to the back of the line and
  comes round again once the others have had their turn.
- Previously one global rotation index was shared by all booking types, so
  with two tests per run Parcel always got the same partners and Pallet the
  others, and a retry loop silently skipped every partner that failed.

All state persists to reports/diversity_state.json (survives across runs);
a readable coverage table is rewritten to reports/partner_coverage.csv.
This is separate from reporting/excel_report.py's report.

Each "next_*" function is atomic (picks AND records), so tests can't forget
to record and leave state inconsistent.
"""
import csv
import json
import random
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent.parent / "reports" / "diversity_state.json"

SAUDI_LOCATION_COOLDOWN = 7    # within the given 6-8 range
INTL_LOCATION_COOLDOWN = 17    # within the given 15-20 range

# Every partner the app has shown (the delivery_rate_* fields of
# /bookings/add). NOT a selection list -- any partner with a quoted amount
# can be chosen -- it only lets the coverage table show partners that have
# never been quoted yet. Includes ALL DHL and ALL FedEx variants.
KNOWN_PARTNERS = [
    "ups", "aramex", "darb",
    "dhl", "dhl_express_worldwide", "dhl_express_easy", "dhl_economy_select",
    "dhl_express_domestic", "dhl_medical_express", "dhl_freight_worldwide",
    "fedex", "fedex_priority", "fedex_express", "fedex_regional", "fedex_connect_plus",
    "fedex_priority_freight", "fedex_regional_economy_freight", "fedex_economy_freight",
]
DELIVERY_PARTNER_ROTATION = KNOWN_PARTNERS  # backwards-compatible alias; no longer drives selection

_DEFAULT_STATE = {
    "used_saudi_location_ids": [],
    "used_intl_location_ids": [],
    "last_saudi_side": "pickup",
    "bad_location_ids": {},
    "partner_coverage": {},   # {booking_type: {partner: {attempts, ok, bad, last_seq, last_outcome}}}
    "partner_seq": 0,
}


def _load() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return json.loads(json.dumps(_DEFAULT_STATE))  # deep copy, avoid mutating the constant


def _save(state: dict):
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Saudi side
# ---------------------------------------------------------------------------
def next_saudi_side() -> str:
    """Strictly alternates which side is Saudi, booking to booking, globally. Atomic (picks + records)."""
    state = _load()
    last = state.get("last_saudi_side", "dropoff")
    new_side = "dropoff" if last == "pickup" else "pickup"
    state["last_saudi_side"] = new_side
    _save(state)
    return new_side


def set_last_saudi_side(side: str):
    """For tests whose direction is fixed externally -- keeps the persisted 'last side used' truthful."""
    state = _load()
    state["last_saudi_side"] = side
    _save(state)


# ---------------------------------------------------------------------------
# Delivery-partner coverage (per booking type)
# ---------------------------------------------------------------------------
def _entry(state: dict, booking_type: str, partner: str) -> dict:
    cov = state.setdefault("partner_coverage", {}).setdefault(booking_type, {})
    return cov.setdefault(partner, {"attempts": 0, "ok": 0, "bad": 0, "last_seq": 0, "last_outcome": ""})


def register_seen_partners(booking_type: str, available_partners: list):
    """Remember that these partners were quoted for this booking type (so 'never tested' is visible)."""
    state = _load()
    for partner in available_partners:
        _entry(state, booking_type, partner)
    _save(state)


def rank_partners(booking_type: str, available_partners: list) -> list:
    """
    The quoted partners ordered by how much they NEED testing for this
    booking type: fewest attempts first, then tested-longest-ago, then name
    (so the order is deterministic and explainable).
    """
    state = _load()
    def key(partner):
        e = _entry(state, booking_type, partner)
        return (e["attempts"], e["last_seq"], partner)
    return sorted(available_partners, key=key)


def partner_attempts(booking_type: str, partner: str) -> int:
    return _entry(_load(), booking_type, partner)["attempts"]


def min_attempts_seen(booking_type: str) -> int:
    """Lowest attempt count among every partner ever quoted for this booking type (0 if none yet)."""
    cov = _load().get("partner_coverage", {}).get(booking_type, {})
    return min((e["attempts"] for e in cov.values()), default=0)


def commit_partner(booking_type: str, partner: str) -> str:
    """Record that this partner is being tried now for this booking type. Atomic."""
    state = _load()
    state["partner_seq"] = state.get("partner_seq", 0) + 1
    e = _entry(state, booking_type, partner)
    e["attempts"] += 1
    e["last_seq"] = state["partner_seq"]
    _save(state)
    write_partner_coverage_csv()
    return partner


def record_partner_outcome(booking_type: str, partner: str, ok: bool, detail: str = ""):
    """Record how a tried partner ended up (ok = reached a success status)."""
    state = _load()
    e = _entry(state, booking_type, partner)
    e["ok" if ok else "bad"] += 1
    e["last_outcome"] = (detail or ("ok" if ok else "failed"))[:120]
    _save(state)
    write_partner_coverage_csv()


def next_delivery_partner(booking_type: str, available_partners: list) -> str:
    """
    Pick the least-tested quoted partner for this booking type and record
    the attempt. `available_partners` must be the partners with a quoted
    amount for the route (see data/booking_payloads.extract_available_partners).
    """
    if not available_partners:
        raise ValueError("next_delivery_partner() needs at least one partner with a quoted amount.")
    register_seen_partners(booking_type, available_partners)
    return commit_partner(booking_type, rank_partners(booking_type, available_partners)[0])


def partner_coverage(booking_type: str = None) -> dict:
    cov = _load().get("partner_coverage", {})
    return cov if booking_type is None else cov.get(booking_type, {})


def format_partner_coverage(booking_type: str) -> str:
    """One-line summary for step() output, e.g. 'tested 3/9 | untested: aramex, darb, ...'."""
    cov = partner_coverage(booking_type)
    untested = sorted(p for p, e in cov.items() if e["attempts"] == 0)
    tested = len(cov) - len(untested)
    return f"tested {tested}/{len(cov)} seen" + (f" | untested: {', '.join(untested)}" if untested else " | all seen partners tested")


def write_partner_coverage_csv():
    """Rewrite reports/partner_coverage.csv: one row per (booking type, partner), incl. known-but-never-quoted ones."""
    path = STATE_PATH.parent / "partner_coverage.csv"
    cov = _load().get("partner_coverage", {})
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["booking_type", "partner", "attempts", "ok", "bad", "last_outcome"])
        for booking_type in sorted(cov):
            for partner in sorted(set(cov[booking_type]) | set(KNOWN_PARTNERS)):
                e = cov[booking_type].get(partner)
                if e is None:
                    w.writerow([booking_type, partner, 0, 0, 0, "never quoted yet"])
                else:
                    w.writerow([booking_type, partner, e["attempts"], e["ok"], e["bad"], e["last_outcome"] or ("untested" if e["attempts"] == 0 else "")])


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
def mark_location_bad(location_id: str, reason: str):
    """
    Permanently excludes a saved location whose data the server rejected
    ("Missing parameters" / "Please enter valid location" on its address).
    Persisted under "bad_location_ids" -> {id: reason}; delete the entry by
    hand to re-enable a location after fixing its data in the app.
    """
    if not location_id:
        return
    state = _load()
    state.setdefault("bad_location_ids", {})[location_id] = reason
    _save(state)


def get_bad_location_ids() -> dict:
    return dict(_load().get("bad_location_ids", {}))


def next_fresh_location(locations: list, is_saudi: bool, record: bool = True, exclude_ids=()) -> dict:
    """
    Picks a location not used within the cooldown window (Saudi ~7 other
    locations since, non-Saudi ~17), never a blacklisted one and never one in
    `exclude_ids`. Falls back to the full allowed pool if all are in cooldown.
    record=False picks WITHOUT consuming cooldown (used while shopping for a
    route; call record_location_usage() for the one actually used).
    """
    state = _load()
    bad = state.get("bad_location_ids", {})
    locations = [loc for loc in locations if loc.get("id") not in bad and loc.get("id") not in exclude_ids]
    if not locations:
        raise ValueError("No usable locations left (blacklisted or already tried).")

    key = "used_saudi_location_ids" if is_saudi else "used_intl_location_ids"
    cooldown = SAUDI_LOCATION_COOLDOWN if is_saudi else INTL_LOCATION_COOLDOWN
    used_list = state.get(key, [])
    recent = used_list[-cooldown:] if cooldown else []

    fresh = [loc for loc in locations if loc.get("id") not in recent]
    chosen = random.choice(fresh if fresh else locations)
    if record:
        used_list.append(chosen.get("id"))
        state[key] = used_list
        _save(state)
    return chosen


def record_location_usage(location: dict, is_saudi: bool):
    state = _load()
    key = "used_saudi_location_ids" if is_saudi else "used_intl_location_ids"
    state.setdefault(key, []).append(location.get("id"))
    _save(state)
