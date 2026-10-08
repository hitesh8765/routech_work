"""
Cross-booking diversity/rotation rules, given directly by the user
(2026-09-21 and refined 2026-09-22). These rules apply PER BOOKING, not
per booking type -- e.g. Pallet's booking and the next Luggage booking
must differ from each other, not just "differ within their own type".

- Every booking must use a genuinely different pickup/dropoff LOCATION
  than recent bookings (not just a different country) -- prefer
  well-known/popular places where the account has such saved locations.
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
reporting/excel_report.py's BookingReport state, which is a separate
concern -- don't conflate the two.

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

# RESTORED (2026-09-23): was briefly narrowed to just ["ups", "aramex"]
# over a suspicion that fedex_* variants weren't valid selections. Checking
# the actual accumulated reports/booking_report.xlsx disproved that --
# "Fedex", "Fedex Express", "Fedex Priority", and "Fedex Connect Plus" all
# show real "Shipment Submitted" successes across multiple runs. The
# narrowing was the wrong fix; reverted. The "Requested"/intermittent
# failures are NOT specifically tied to which partner was chosen.
#
# DHL added (2026-09-23) per direct user instruction: "start using DHL
# delivery partner as well" -- supersedes the earlier "keep but don't use"
# instruction. Only plain "dhl" added, not the dhl_* sub-variants
# (DHL Medical Express etc.) -- those remain unconfirmed/unused until the
# user says otherwise, same caution as the fedex sub-variants originally.
#
# FREIGHT variants removed (2026-10-07) -- REAL server error evidence,
# not a guess: "fedex_priority_freight" was rejected with
# booking.please_select_delivery_partners / "Please try again with
# another delivery partner or change the location." (Saudi-Spain route),
# and "fedex_regional_economy_freight" was rejected with
# system.please_enter_valid_location (Niger-Saudi route) in the SAME test
# run. Unlike the standard fedex_* variants above (which have multiple
# CONFIRMED successes in the report), NONE of the three "_freight" suffixed
# variants (fedex_priority_freight, fedex_regional_economy_freight,
# fedex_economy_freight) have ever succeeded. These are freight/bulk-cargo
# services, likely with different route/weight eligibility than standard
# express/priority parcel services -- plausibly not offered at all for
# arbitrary routes on this demo account. Removed from rotation; "darb" is
# left in despite having no confirmed success OR failure yet (no evidence
# against it specifically) -- watch for it in future runs.
DELIVERY_PARTNER_ROTATION = [
    "ups", "aramex", "dhl", "fedex", "fedex_priority", "fedex_express",
    "fedex_regional", "fedex_connect_plus", "darb",
]
# Freight variants kept here for reference, NOT in the active rotation --
# do not re-add without confirming the server actually accepts them for
# some route (e.g. a much heavier/larger shipment than our current weight
# ranges, if "freight" implies a minimum weight threshold):
#   "fedex_priority_freight", "fedex_regional_economy_freight",
#   "fedex_economy_freight"

# Backfilled from real bookings already created before this stricter
# per-booking tracker existed. Last Saudi side used = "pickup" (so the
# next booking should flip to "dropoff"); partners already used = ups,
# aramex, so the next fresh pick starts from idx 2 (fedex).
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
    For tests whose direction is fixed externally -- updates the
    persisted 'last side used' to match reality, so the NEXT booking's
    next_saudi_side() call still alternates correctly from true history.
    Currently unused by any test (Parcel's parametrize was removed
    2026-09-22), kept in case a dedicated both-directions regression test
    is wanted later.
    """
    state = _load()
    state["last_saudi_side"] = side
    _save(state)


def next_delivery_partner(available_partners: list = None) -> str:
    """
    Round-robins through DELIVERY_PARTNER_ROTATION, but ONLY among partners
    that are actually bookable for this route.

    RULE (user-mandated 2026-10-07): `available_partners` must be the
    partners with a real quoted amount from get_delivery_and_rate (see
    data/booking_payloads.py extract_available_partners). Picking a partner
    without a quote is rejected by the server or leaves the booking stuck on
    "Requested" forever. Rotation order is kept: starting from the saved
    index, the first rotation entry that is available is used, and the index
    moves just past it so the next booking tries the next partner.

    Raises ValueError if none of the rotation partners is available for the
    route -- the caller should pick a different location pair, not guess.
    Atomic (picks + records).
    """
    state = _load()
    idx = state.get("partner_rotation_index", 0) % len(DELIVERY_PARTNER_ROTATION)

    if available_partners is None:
        raise ValueError(
            "next_delivery_partner() now requires available_partners (the partners "
            "with a quoted amount for this route) -- picking blindly caused rejected "
            "and stuck 'Requested' bookings."
        )

    available = {p.lower() for p in available_partners}
    for offset in range(len(DELIVERY_PARTNER_ROTATION)):
        candidate_idx = (idx + offset) % len(DELIVERY_PARTNER_ROTATION)
        candidate = DELIVERY_PARTNER_ROTATION[candidate_idx]
        if candidate.lower() in available:
            state["partner_rotation_index"] = (candidate_idx + 1) % len(DELIVERY_PARTNER_ROTATION)
            _save(state)
            return candidate

    raise ValueError(
        f"None of the rotation partners {DELIVERY_PARTNER_ROTATION} has a quote for this route "
        f"(available with a quote: {sorted(available)})."
    )


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
