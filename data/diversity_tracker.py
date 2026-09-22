"""
Cross-booking diversity/rotation rules, given directly by the user
(2026-09-21 and refined 2026-09-22). These rules apply PER BOOKING, not
per booking type -- e.g. Pallet's booking and the next Luggage booking
must differ from each other, not just "differ within their own type".

- Every booking must use a genuinely different pickup/dropoff LOCATION
  than recent bookings (not just a different country) -- prefer
  well-known/popular places where the account has such saved locations.
  (Limitation: we can't algorithmically judge "popularity" of a saved
  location -- this picks randomly among not-recently-used ones. See
  handoff.md if the user wants genuinely new, popular-city locations
  added via the /bookings/load_map geocoding flow instead of only
  choosing among the account's existing saved locations.)
- A non-Saudi location may only repeat after ~15-20 OTHER distinct
  non-Saudi locations have been used since (cooldown here: 17).
- A Saudi location may only repeat after ~6-8 OTHER distinct Saudi
  locations have been used since (cooldown here: 7).
- Exactly one side (pickup or dropoff) must be Saudi -- mandatory, enforced
  elsewhere (arrange_pickup_dropoff in booking_payloads.py / the calling test).
- Which side is Saudi must STRICTLY ALTERNATE booking to booking (not
  random): if booking N had Saudi as pickup, booking N+1 must have Saudi
  as dropoff -- globally, across ALL bookings regardless of type.
- A different delivery partner must be used on every single booking (not
  just per booking type) -- round-robins through every partner before
  any repeat.
- DHL (plain "DHL" AND every dhl_* variant) is intentionally EXCLUDED from
  the rotation for now -- kept in the codebase/labels for future use, but
  never selected, until the user says otherwise.

All state persists to reports/diversity_state.json so it survives across
separate `pytest` runs/sessions. This is DIFFERENT from
reporting/excel_report.py's BookingReport, which the user confirmed
starts FRESH every run -- don't conflate the two.

Each "next_*" function is atomic: it both picks AND records the choice in
one call, so tests don't need a separate "remember to record" step (safer
against partial test failures leaving state inconsistent).
"""
import json
import random
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent.parent / "reports" / "diversity_state.json"

SAUDI_LOCATION_COOLDOWN = 7    # within the given 6-8 range
INTL_LOCATION_COOLDOWN = 17    # within the given 15-20 range

# Full delivery-partner rotation. DHL (plain + every dhl_* variant)
# deliberately excluded per the user's explicit instruction -- keep the
# labels/mapping in reporting/excel_report.py for when it's needed, just
# don't pick from it here until told otherwise.
DELIVERY_PARTNER_ROTATION = [
    "ups", "aramex", "fedex", "fedex_priority", "fedex_express",
    "fedex_regional", "fedex_connect_plus", "fedex_priority_freight",
    "fedex_regional_economy_freight", "fedex_economy_freight", "darb",
]

# Backfilled from real bookings already created before this stricter
# per-booking tracker existed:
#   Parcel run 1: saudi_side=pickup,  partner=ups
#   Parcel run 2: saudi_side=dropoff, partner=ups
#   Pallet run:   saudi_side=pickup,  partner=aramex
# -> last Saudi side used = "pickup" (so the next booking should flip to
#    "dropoff"); partners already used = ups (idx 0), aramex (idx 1), so
#    the next fresh pick should start from idx 2 (fedex).
_DEFAULT_STATE = {
    "used_saudi_location_ids": [],
    "used_intl_location_ids": [],
    "last_saudi_side": "pickup",
    "partner_rotation_index": 2,
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


def next_saudi_side() -> str:
    """Strictly alternates which side is Saudi, booking to booking, globally. Atomic (picks + records)."""
    state = _load()
    last = state.get("last_saudi_side", "dropoff")
    new_side = "dropoff" if last == "pickup" else "pickup"
    state["last_saudi_side"] = new_side
    _save(state)
    return new_side


def set_last_saudi_side(side: str):
    """
    For tests whose direction is fixed externally (e.g. Parcel's own
    pytest.mark.parametrize covers both directions every run regardless of
    the global rotation) -- updates the persisted 'last side used' to
    match reality, so the NEXT booking's next_saudi_side() call still
    alternates correctly from true history.
    """
    state = _load()
    state["last_saudi_side"] = side
    _save(state)


def next_delivery_partner() -> str:
    """Round-robins through DELIVERY_PARTNER_ROTATION (DHL excluded). Atomic (picks + records)."""
    state = _load()
    idx = state.get("partner_rotation_index", 0) % len(DELIVERY_PARTNER_ROTATION)
    partner = DELIVERY_PARTNER_ROTATION[idx]
    state["partner_rotation_index"] = (idx + 1) % len(DELIVERY_PARTNER_ROTATION)
    _save(state)
    return partner


def next_fresh_location(locations: list[dict], is_saudi: bool) -> dict:
    """
    Picks a location whose id hasn't been used within the relevant cooldown
    window (Saudi: ~7 other locations since; non-Saudi: ~17). Falls back to
    picking from the full pool if every available location is still within
    cooldown (e.g. the account doesn't have enough distinct saved locations
    yet to satisfy the full cooldown). Atomic (picks + records).
    """
    state = _load()
    key = "used_saudi_location_ids" if is_saudi else "used_intl_location_ids"
    cooldown = SAUDI_LOCATION_COOLDOWN if is_saudi else INTL_LOCATION_COOLDOWN
    used_list = state.get(key, [])
    recent = used_list[-cooldown:] if cooldown else []

    fresh = [loc for loc in locations if loc.get("id") not in recent]
    pool = fresh if fresh else locations
    chosen = random.choice(pool)

    used_list.append(chosen.get("id"))
    state[key] = used_list
    _save(state)
    return chosen
