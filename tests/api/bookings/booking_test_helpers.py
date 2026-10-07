"""
Shared helpers for the per-booking-type E2E API tests (test_01_parcel_booking.py,
test_02_pallet_booking.py, ...). Extracted here once Pallet needed the same
logic Parcel already had, rather than duplicating per file.
"""
import json
import time
from pathlib import Path

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
    timeout: float = 30,
    interval: float = 2,
):
    """
    Polls booking_status_from_details() until a known terminal status
    (from either success_statuses or failure_statuses) is reached, or
    timeout elapses. Handles the apparent async delay between payment
    completing and the booking's status actually updating server-side.

    Timeout increased 15s -> 30s (2026-10-07): real accumulated report
    data (reports/booking_report.xlsx) shows the SAME delivery partner +
    country pair sometimes lands on "Shipment Submitted" and other times
    gets stuck on "Requested" -- NOT correlated with phone number digit
    count (ruled out: failures occurred on 9/10-digit countries, well
    within any claimed limit, while other runs with longer numbers
    succeeded fine). This looks like genuine server-side processing
    variance, not a data-shape issue -- giving it more time to settle is
    the safe fix. If "Requested" still shows up as a FINAL status even
    after 30s on a future run, that's evidence it's NOT just timing and
    needs separate investigation.
    """
    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        last_status = api_client.booking_status_from_details(booking_id)
        if last_status in success_statuses or last_status in failure_statuses:
            return last_status
        time.sleep(interval)
    return last_status


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
