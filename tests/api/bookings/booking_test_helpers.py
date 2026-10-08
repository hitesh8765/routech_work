"""
Shared helpers for the per-booking-type E2E API tests (test_01_parcel_booking.py,
test_02_pallet_booking.py, ...). Extracted here once Pallet needed the same
logic Parcel already had, rather than duplicating per file.
"""
import json
import time
from pathlib import Path

from data.booking_payloads import (
    arrange_pickup_dropoff,
    build_rate_payload,
    extract_available_partners,
    random_weight_and_dimensions,
)
from data.diversity_tracker import next_delivery_partner, next_fresh_location

DEBUG_DIR = Path(__file__).resolve().parent.parent.parent.parent / ".auth"
DEBUG_DIR.mkdir(exist_ok=True)


def step(label: str, **details):
    """
    Prints a single-line progress marker to the terminal (visible with
    `pytest -s`), so the full flow is traceable live without digging
    through debug JSON files after the fact. Added 2026-09-23 per the
    user's "I want to know at what point it is failing" request -- call
    this at every major milestone in a test, not just at the end.
    """
    extra = " ".join(f"{k}={v!r}" for k, v in details.items())
    print(f"[step] {label}" + (f" -- {extra}" if extra else ""))


def dump_debug(name: str, data) -> Path:
    """
    Writes the full raw response to a JSON file so we can inspect it
    without fighting pytest's/PowerShell's terminal truncation. Called
    unconditionally after key calls (cheap, and means the artifact is
    already there the moment something looks wrong).
    """
    path = DEBUG_DIR / f"debug_{name}.json"
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return path


def first_hs_code(hs_code_response) -> str:
    """
    Extraction of a usable HS code string from
    RoutechAPIClient.get_hs_codes()'s response.

    Confirmed real shape (2026-09-18 live run):
        {'status': 'success', 'result': [{'id': '...', ...}, ...]}
    """
    if isinstance(hs_code_response, dict):
        results = (
            hs_code_response.get("result")
            or hs_code_response.get("results")
            or hs_code_response.get("data")
        )
        if results is not None:
            return first_hs_code(results)
    if isinstance(hs_code_response, list) and hs_code_response:
        first = hs_code_response[0]
        if isinstance(first, dict):
            for key in ("hs_code", "code", "value", "id"):
                if key in first:
                    return str(first[key])
            raise AssertionError(
                f"First HS code result has none of the expected keys. "
                f"Full item: {first!r} -- add the correct key to first_hs_code()."
            )
        return str(first)
    raise AssertionError(
        f"Could not extract an HS code from response: {hs_code_response!r} "
        "-- inspect the real shape and fix first_hs_code()."
    )


def raise_if_error_status(response: dict, context: str):
    """
    Confirmed error shape from a real /bookings/add validation failure:
        {'status': 'error', 'message': [{'param', 'msg', 'key'}, ...], 'req_data': {...}}

    IMPORTANT: a bare `status == "error"` is NOT sufficient on its own --
    a real successful create_booking response was observed that ALSO had
    top-level 'status' unrelated to this check. Only fire when the
    'message' and/or 'req_data' keys -- unique to the confirmed error
    shape -- are also present.
    """
    if not isinstance(response, dict) or response.get("status") != "error":
        return
    if "message" not in response and "req_data" not in response:
        return  # 'status': 'error' here means something else; not our validation-error shape

    messages = response.get("message", [])
    details = "; ".join(
        f"{m.get('param', '?')}: {m.get('msg', m)}" if isinstance(m, dict) else str(m)
        for m in messages
    ) or "(no message details -- dump the full response to inspect)"
    raise AssertionError(f"{context} returned an error status -- {details}")


def extract_booking_id(create_response: dict) -> str:
    """
    Confirmed reliable path (2026-09-18 live run):
        create_response['booking_detail'][0]['booking_id']
    """
    booking_detail = create_response.get("booking_detail")
    if isinstance(booking_detail, list) and booking_detail:
        booking_id = booking_detail[0].get("booking_id")
        if booking_id:
            return str(booking_id)
    raise AssertionError(
        f"Could not find booking_id in create_booking response: {create_response!r} "
        "-- inspect the real shape and fix extract_booking_id()."
    )


def wait_for_terminal_status(
    api_client,
    booking_id: str,
    success_statuses: set,
    failure_statuses: set,
    timeout: float = 60,
    interval: float = 3,
):
    """
    Waits up to `timeout` seconds (60 by user request, 2026-10-07) for the
    booking to reach a known terminal status. Every poll is a FRESH GET of
    /bookings/get_booking_details/:id -- the equivalent of refreshing the
    page -- and each status seen is printed so a slow booking is visible
    live in the terminal.

    "Requested" is NOT terminal. Root cause of bookings stuck there
    forever (confirmed 2026-10-07): a delivery partner with no quoted
    amount for the route was selected. That is now prevented up front by
    extract_available_partners() / next_delivery_partner(available_partners),
    so a booking still stuck here after 60s is a real, unexpected problem.
    """
    deadline = time.time() + timeout
    last_status = None
    polls = 0
    while time.time() < deadline:
        polls += 1
        last_status = api_client.booking_status_from_details(booking_id)
        print(f"[poll {polls}] booking {booking_id} status={last_status!r}")
        if last_status in success_statuses or last_status in failure_statuses:
            return last_status
        time.sleep(interval)
    return last_status


def pick_route_with_available_partner(
    api_client,
    booking_type: str,
    sa_locations: list,
    intl_locations: list,
    saudi_side: str,
    max_attempts: int = 6,
):
    """
    Picks pickup/dropoff + weight/dimensions, quotes the REAL shipment via
    get_delivery_and_rate, and chooses a delivery partner ONLY from partners
    with a real quoted amount for that route (user-mandated rule,
    2026-10-07). If the route has no usable partner, picks a different
    location pair and tries again (the server's own advice: "Please try
    again with another delivery partner or change the location") -- up to
    max_attempts -- instead of blindly submitting a booking that gets
    rejected or stuck on "Requested".

    Returns dict: pickup, dropoff, weight_and_dimensions, delivery_rates,
    available_partners, delivery_partner.
    """
    last_problem = "no attempt made"
    for attempt in range(1, max_attempts + 1):
        saudi_location = next_fresh_location(sa_locations, is_saudi=True)
        assert (saudi_location.get("country_code") or "").upper() == "SA", (
            f"next_fresh_location(is_saudi=True) returned a non-Saudi location: {saudi_location!r}"
        )
        intl_location = next_fresh_location(intl_locations, is_saudi=False)
        assert (intl_location.get("country_code") or "").upper() != "SA", (
            f"next_fresh_location(is_saudi=False) returned a Saudi location: {intl_location!r}"
        )
        pickup, dropoff = arrange_pickup_dropoff(saudi_location, intl_location, saudi_side=saudi_side)
        assert_exactly_one_saudi_side(pickup, dropoff, context=f"route attempt {attempt} ({booking_type})")
        step(
            f"route attempt {attempt}",
            pickup=f"{pickup.get('city')}, {pickup.get('country')}",
            dropoff=f"{dropoff.get('city')}, {dropoff.get('country')}",
        )

        weight_and_dimensions = random_weight_and_dimensions(booking_type)
        actual_weight, length, width, height = weight_and_dimensions
        rate_payload = build_rate_payload(booking_type, pickup, dropoff, actual_weight, length, width, height)
        delivery_rates = api_client.get_delivery_and_rate(rate_payload)
        dump_debug(f"get_delivery_and_rate_{booking_type}_response", delivery_rates)

        assert delivery_rates.get("status") == "success", (
            f"get_delivery_and_rate did not return status 'success': {delivery_rates!r}"
        )
        quoted_days = sorted((delivery_rates.get("days") or {}).keys())
        available = extract_available_partners(delivery_rates)
        step("rates quoted", partners_in_days=quoted_days, partners_with_amount=available)

        if not available:
            last_problem = f"no partner has a quoted amount for {pickup.get('country')} -> {dropoff.get('country')}"
            step("route rejected, trying another location pair", reason=last_problem)
            continue

        try:
            delivery_partner = next_delivery_partner(available_partners=available)
        except ValueError as exc:
            last_problem = str(exc)
            step("route rejected, trying another location pair", reason=last_problem)
            continue

        assert delivery_partner in available, (
            f"picked partner {delivery_partner!r} is not among partners with a quoted amount {available}"
        )
        step("delivery_partner picked (has quoted amount)", delivery_partner=delivery_partner)
        return {
            "pickup": pickup,
            "dropoff": dropoff,
            "weight_and_dimensions": weight_and_dimensions,
            "delivery_rates": delivery_rates,
            "available_partners": available,
            "delivery_partner": delivery_partner,
        }

    raise AssertionError(
        f"Could not find a route with a bookable delivery partner after {max_attempts} attempts "
        f"for {booking_type}. Last problem: {last_problem}"
    )


def assert_exactly_one_saudi_side(pickup: dict, dropoff: dict, context: str = ""):
    """
    Defensive assertion (added 2026-09-22 after a real Pallet booking was
    rejected server-side with "There should be saudi arabia country either
    in pickup or dropoff address", despite our selection logic appearing
    correct on review): fails FAST, before even calling the API, if our
    own pickup/dropoff dicts don't have exactly one Saudi side. This
    narrows down whether a future occurrence is a bug in OUR location
    selection (this assertion fires) or something else entirely -- e.g. a
    payload-construction issue, or a genuine server-side quirk (this
    assertion passes here, but the server still rejects it -- in which
    case the full request payload from .auth/debug_*.json is needed).
    """
    pickup_is_sa = (pickup.get("country_code") or "").upper() == "SA"
    dropoff_is_sa = (dropoff.get("country_code") or "").upper() == "SA"
    assert pickup_is_sa != dropoff_is_sa, (  # exactly one, not both/neither
        f"{context}: expected exactly ONE of pickup/dropoff to be Saudi Arabia, got "
        f"pickup country_code={pickup.get('country_code')!r} (id={pickup.get('id')!r}), "
        f"dropoff country_code={dropoff.get('country_code')!r} (id={dropoff.get('id')!r})"
    )
