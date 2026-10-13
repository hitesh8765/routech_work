"""
Shared helpers for the per-booking-type E2E API tests (test_01_parcel_booking.py,
test_02_pallet_booking.py, ...). Extracted here once Pallet needed the same
logic Parcel already had, rather than duplicating per file.
"""
import json
import random
import time
from contextlib import contextmanager
from pathlib import Path

from data.booking_payloads import (
    ITEM_NAME_POOL,
    RECEIVER_NAME_POOL,
    arrange_pickup_dropoff,
    build_booking_detail,
    build_rate_payload,
    extract_available_partners,
    random_weight_and_dimensions,
)
from data.diversity_tracker import (
    commit_partner,
    format_partner_coverage,
    mark_location_bad,
    min_attempts_seen,
    next_fresh_location,
    partner_attempts,
    rank_partners,
    record_location_usage,
    record_partner_outcome,
    register_seen_partners,
)
from reporting.bug_report import SUSPECTED, log_bug
from reporting.excel_report import booking_type_label, delivery_partner_label, route_label
from utils.html_parsing import is_usable_location

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


def _route_text(pickup: dict, dropoff: dict) -> str:
    return f"{pickup.get('city')}, {pickup.get('country')} -> {dropoff.get('city')}, {dropoff.get('country')}"


def log_server_bug(kind: str, booking_type: str, partner: str = "", pickup: dict = None, dropoff: dict = None,
                   detail: str = "", evidence: str = ""):
    """
    Records server-side behaviour that looks wrong in reports/bug_report.xlsx
    (see reporting/bug_report.py). Rows start as "Suspected - verify in UI":
    a failure can still be OUR payload, so the user/assistant confirms it
    before it is reported to the app owner. Never raises.
    """
    route = _route_text(pickup, dropoff) if pickup and dropoff else ""
    kinds = {
        "unpriced_at_add": ("Partner with a quote is priced 0/null by /bookings/add", "Bug", "Medium", "POST /bookings/add",
                            "Partner quoted by get_delivery_and_rate gets a real price at /bookings/add."),
        "partner_rejected": ("Partner with a quote is rejected by /bookings/add", "Bug", "Medium", "POST /bookings/add",
                             "A partner that has an amount in the quote can be booked."),
        "stuck_after_payment": ("Priced and paid booking does not reach a success status", "Bug", "High", "Booking lifecycle",
                                "Reaches 'Shipment Submitted' / 'Sent to Carrier' after payment."),
        "payment_not_ok": ("Payment of a priced booking does not report success", "Bug", "Medium", "POST /bookings/make_ppd_payment",
                           "Wallet payment of a priced booking succeeds."),
        "zero_quote": ("Rate quote returns payable_amount 0 for a partner", "Bug", "Medium", "POST /bookings/get_delivery_and_rate",
                       "A real price, or the partner omitted from 'amount'."),
        "timeout": ("Request times out", "Performance", "Medium", "Booking flow",
                    "A response within the allowed time."),
        "bad_location": ("Saved location has invalid data and cannot be booked", "Data", "Medium", "Saved locations / POST /bookings/add",
                         "Only locations with usable data can be saved and booked."),
    }
    title, category, severity, endpoint, expected = kinds[kind]
    if kind == "timeout":
        endpoint = evidence or endpoint
        key = f"timeout|{endpoint}|{booking_type}"
    elif kind == "bad_location":
        key = f"bad_location|{dropoff.get('country') if dropoff else ''}|{dropoff.get('city') if dropoff else ''}"
    else:
        key = f"{kind if kind != 'unpriced_at_add' else 'quoted_but_unpriced_at_add'}|{booking_type}|{partner}"
        if kind == "partner_rejected":
            key = f"quoted_partner_rejected|{booking_type}|{partner}"
    log_bug(
        key,
        once_per_session=kind in ("zero_quote", "bad_location"),
        title=title, category=category, severity=severity, status=SUSPECTED, endpoint=endpoint,
        booking_type=booking_type, partner=partner, scenario=route, expected=expected,
        actual=detail, evidence=evidence if kind != "timeout" else detail[:200],
    )


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
    extract_available_partners() and the coverage-based pick in pick_route_with_available_partner(),
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
    choose_partner: bool = True,
    max_coverage_quotes: int = 3,
):
    """
    Picks pickup/dropoff + weight/dimensions, quotes the REAL shipment via
    get_delivery_and_rate, and chooses the delivery partner.

    RULES (user, 2026-10-07 and 2026-10-09):
      * ONLY partners with a real quoted amount (extract_available_partners)
        may be chosen -- all DHL and FedEx variants included.
      * The goal is to test EVERY available partner for EVERY booking type,
        so the partner chosen is the LEAST-TESTED one for this booking type
        among those quoted (diversity_tracker.rank_partners). A partner that
        was tried -- or skipped because it wouldn't price -- goes to the back
        of the line and comes round again later.
      * A given route only quotes some partners, so if the best candidate here
        has already been tested more than some partner seen for this type,
        up to `max_coverage_quotes` routes are quoted and the one offering the
        least-tested partner is used. (Routes shopped but not used do NOT
        consume the location cooldown.)
      * A route with no usable partner is replaced by another location pair
        (the server's own advice: "try another delivery partner or change
        the location"), up to max_attempts.

    choose_partner=False (probe mode): returns the first quoted route and
    every available partner without choosing or counting anything.

    Returns dict: pickup, dropoff, weight_and_dimensions, delivery_rates,
    available_partners, delivery_partner.
    """
    tried_ids = set()
    best = None
    quoted_routes = 0
    last_problem = "no attempt made"
    for attempt in range(1, max_attempts + 1):
        saudi_location = next_fresh_location(sa_locations, is_saudi=True, record=False, exclude_ids=tried_ids)
        assert (saudi_location.get("country_code") or "").upper() == "SA", (
            f"next_fresh_location(is_saudi=True) returned a non-Saudi location: {saudi_location!r}"
        )
        intl_location = next_fresh_location(intl_locations, is_saudi=False, record=False, exclude_ids=tried_ids)
        assert (intl_location.get("country_code") or "").upper() != "SA", (
            f"next_fresh_location(is_saudi=False) returned a Saudi location: {intl_location!r}"
        )
        tried_ids.update({saudi_location.get("id"), intl_location.get("id")})
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
        for zero_partner, info in (delivery_rates.get("amount") or {}).items():
            if (info or {}).get("payable_amount") == 0:
                log_server_bug("zero_quote", booking_type, zero_partner, pickup, dropoff,
                               detail="amount[%s].payable_amount = 0" % zero_partner,
                               evidence=f".auth/debug_get_delivery_and_rate_{booking_type}_response.json")

        if not available:
            last_problem = f"no partner has a quoted amount for {pickup.get('country')} -> {dropoff.get('country')}"
            step("route rejected, trying another location pair", reason=last_problem)
            continue

        register_seen_partners(booking_type, available)
        entry = {
            "pickup": pickup,
            "dropoff": dropoff,
            "saudi_location": saudi_location,
            "intl_location": intl_location,
            "weight_and_dimensions": weight_and_dimensions,
            "delivery_rates": delivery_rates,
            "available_partners": available,
        }
        if not choose_partner:
            record_location_usage(saudi_location, is_saudi=True)
            record_location_usage(intl_location, is_saudi=False)
            return {**entry, "delivery_partner": None}

        entry["candidate"] = rank_partners(booking_type, available)[0]
        entry["candidate_attempts"] = partner_attempts(booking_type, entry["candidate"])
        quoted_routes += 1
        if best is None or entry["candidate_attempts"] < best["candidate_attempts"]:
            best = entry
        if entry["candidate_attempts"] <= min_attempts_seen(booking_type):
            break  # this route offers a least-tested partner -- good enough
        if quoted_routes >= max_coverage_quotes:
            break
        step(
            "route has no least-tested partner, shopping for a better route",
            candidate=entry["candidate"],
            attempts=entry["candidate_attempts"],
        )

    if best is None:
        raise AssertionError(
            f"Could not find a route with a bookable delivery partner after {max_attempts} attempts "
            f"for {booking_type}. Last problem: {last_problem}"
        )

    record_location_usage(best["saudi_location"], is_saudi=True)
    record_location_usage(best["intl_location"], is_saudi=False)
    attempts_before = best["candidate_attempts"]
    delivery_partner = commit_partner(booking_type, best["candidate"])
    assert delivery_partner in best["available_partners"], (
        f"picked partner {delivery_partner!r} is not among partners with a quoted amount {best['available_partners']}"
    )
    step(
        "delivery_partner picked (quoted + least tested for this booking type)",
        delivery_partner=delivery_partner,
        times_tried_before=attempts_before,
        coverage=format_partner_coverage(booking_type),
    )
    return {
        "pickup": best["pickup"],
        "dropoff": best["dropoff"],
        "weight_and_dimensions": best["weight_and_dimensions"],
        "delivery_rates": best["delivery_rates"],
        "available_partners": best["available_partners"],
        "delivery_partner": delivery_partner,
    }


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


# ---------------------------------------------------------------------------
# Added 2026-10-08
# ---------------------------------------------------------------------------
def usable_locations(locations: list) -> list:
    """
    Drops saved locations with bad data (missing city/country code, or
    latitude/longitude missing or 0) BEFORE any is picked, and prints what
    was dropped. Evidence: a saved Mauritius location with lat/lng "0"
    made /bookings/add reject the booking ("Missing parameters." on
    dropoff_address) even with a healthy partner (aramex).
    """
    good = [loc for loc in locations if is_usable_location(loc)]
    dropped = [loc for loc in locations if not is_usable_location(loc)]
    for d in dropped:
        log_server_bug(
            "bad_location", "", pickup=None, dropoff=d,
            detail=f"id={d.get('id')} lat={d.get('latitude')!r} lng={d.get('longitude')!r} city={d.get('city')!r} country_code={d.get('country_code')!r}",
            evidence="saved location (booking_form option data-*)",
        )
    if dropped:
        step(
            "ignored saved locations with bad data",
            count=len(dropped),
            examples=[f"{d.get('city')}, {d.get('country')} (id={d.get('id')})" for d in dropped[:3]],
        )
    return good


# Server messages that mean "this location's data is bad" (as opposed to a
# partner/price problem).
_LOCATION_ERROR_MESSAGES = ("Missing parameters.", "Please enter valid location.")


def blacklist_bad_locations_from_error(response: dict, pickup: dict, dropoff: dict):
    """
    If /bookings/add rejected a booking because of an address, remember the
    offending saved location (diversity_state.json -> bad_location_ids) so
    later runs never pick it again. Does NOT hide the failure -- the caller
    still raises; the point is that the SAME bad record can't fail every
    future run. Only acts on the exact messages in _LOCATION_ERROR_MESSAGES.
    """
    if not isinstance(response, dict) or response.get("status") != "error":
        return
    for m in response.get("message", []) or []:
        if not isinstance(m, dict) or m.get("msg") not in _LOCATION_ERROR_MESSAGES:
            continue
        param = m.get("param", "")
        for side_name, loc in (("pickup_address", pickup), ("dropoff_address", dropoff)):
            if side_name in param:
                reason = f"{m.get('msg')} ({param})"
                mark_location_bad(loc.get("id"), reason)
                step(
                    "location blacklisted (will not be picked again)",
                    id=loc.get("id"),
                    city=loc.get("city"),
                    country=loc.get("country"),
                    reason=reason,
                )


@contextmanager
def log_failure_to_report(booking_report, booking_type: str, type_partner: str, pickup: dict, dropoff: dict, delivery_partner: str):
    """
    Until now a booking that failed before completing left NO row in the
    Excel report, so the combinations that fail (the whole point of this
    suite) were invisible there. Wrap the create/pay/poll steps in this: on
    any exception it adds a "FAILED: <reason>" row, then re-raises so the
    test still fails normally. Do NOT add the success row inside the same
    block after something that can fail -- put it last, nothing after it.
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - re-raised below
        text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        booking_report.add_row(
            booking_type=booking_type_label(booking_type=booking_type, type_partner=type_partner),
            booking_number="-",
            route=route_label(pickup.get("country", ""), dropoff.get("country", "")),
            delivery_partner=delivery_partner_label(delivery_partner),
            status=f"FAILED: {text[:110]}",
        )
        record_partner_outcome(booking_type, delivery_partner, False, f"FAILED: {text[:100]}")
        if "timed out" in text.lower() or "timed out" in str(exc).lower():
            log_server_bug("timeout", booking_type, delivery_partner, pickup, dropoff, detail=text[:200], evidence="payment/verification step")
        elif text.startswith("Payment did not report success"):
            log_server_bug("payment_not_ok", booking_type, delivery_partner, pickup, dropoff, detail=str(exc)[:300],
                           evidence=f".auth/debug_make_ppd_{booking_type}_payment_response.json")
        raise


def probe_partners(api_client, booking_type: str, saudi_side: str, sa_locations: list, intl_locations: list) -> list:
    """
    Settles "which partner works for which booking type + direction" with
    data instead of guesses. Quotes ONE route, then calls /bookings/add once
    per partner that has a quote -- identical payload apart from
    delivery_partners -- and records whether the server priced it
    (ppd_amount > 0), priced it at 0/null, or rejected it. NEVER pays.
    Side effect: each add creates an unpaid server-side booking record.
    """
    route = pick_route_with_available_partner(
        api_client, booking_type, sa_locations, intl_locations, saudi_side, choose_partner=False
    )
    pickup, dropoff = route["pickup"], route["dropoff"]
    item_name = random.choice(ITEM_NAME_POOL)
    hs_code = first_hs_code(api_client.get_hs_codes(item_name))
    receiver_name = random.choice(RECEIVER_NAME_POOL)
    results = []
    for partner in route["available_partners"]:
        detail = build_booking_detail(
            booking_type=booking_type,
            pickup_location=pickup,
            dropoff_location=dropoff,
            receiver_name=receiver_name,
            hs_code=hs_code,
            delivery_rates=route["delivery_rates"],
            delivery_partner=partner,
            weight_and_dimensions=route["weight_and_dimensions"],
            item_name=item_name,
        )
        quoted = detail.get(f"delivery_rate_{partner}")
        try:
            response = api_client.create_booking(detail)
        except Exception as exc:  # noqa: BLE001 - e.g. request timeout
            outcome, ppd = f"EXCEPTION: {str(exc).splitlines()[0][:80]}", None
        else:
            if isinstance(response, dict) and response.get("status") == "error" and (
                "message" in response or "req_data" in response
            ):
                msgs = "; ".join(
                    f"{m.get('param', '?')}: {m.get('msg')}" for m in (response.get("message") or []) if isinstance(m, dict)
                )
                outcome, ppd = f"REJECTED: {msgs[:100]}", None
            else:
                pay = (response.get("payment_array") or [{}])[0]
                ppd = pay.get("ppd_amount")
                outcome = "PRICED" if isinstance(ppd, (int, float)) and ppd > 0 else f"NO PRICE (ppd_amount={ppd!r})"
        results.append(
            {
                "type": booking_type,
                "direction": "outbound (pickup=Saudi)" if saudi_side == "pickup" else "inbound (dropoff=Saudi)",
                "route": route_label(pickup.get("country", ""), dropoff.get("country", "")),
                "partner": partner,
                "quoted_amount": quoted,
                "add_ppd_amount": ppd,
                "outcome": outcome,
                "sender_phone_digits": len(detail["sender_mobile_number"]),
                "receiver_phone_digits": len(detail["stakeholders"]),
                "weight_dims": route["weight_and_dimensions"],
            }
        )
        if outcome.startswith("NO PRICE"):
            log_server_bug("unpriced_at_add", booking_type, partner, pickup, dropoff, detail=f"{outcome} (quoted {quoted})", evidence="probe: reports/partner_probe.csv")
        elif outcome.startswith("REJECTED"):
            log_server_bug("partner_rejected", booking_type, partner, pickup, dropoff, detail=outcome, evidence="probe: reports/partner_probe.csv")
        elif "timed out" in outcome.lower():
            log_server_bug("timeout", booking_type, partner, pickup, dropoff, detail=outcome, evidence="POST /bookings/add")
        step("probe", partner=partner, quoted=quoted, outcome=outcome)
    return results


# ---------------------------------------------------------------------------
# Retry-with-visibility (2026-10-09) -- replaces the tests' inline retry loops
# ---------------------------------------------------------------------------
# Server keys that mean "this PARTNER or LOCATION does not work for this
# route" (the server itself says "try again with another delivery partner or
# change the location"). Everything else is a real error and must raise.
_RETRYABLE_ERROR_KEYS = {
    "booking.please_select_delivery_partners",
    "system.please_enter_valid_location",
}


def retryable_create_failure(response: dict):
    """
    Returns a short reason string when this /bookings/add outcome is a known
    "partner/route doesn't work" case that should be skipped and retried with
    another partner/location, or None when it should NOT be retried:
      * error response whose messages are ALL partner/location messages
      * success-shaped response priced at 0/null (ppd_amount) -- the server
        created the booking but couldn't price it; paying would leave it
        stuck on "Requested" forever
    Any other error (field validation, phone digits, etc.) returns None so
    the caller raises it WITH its real message -- it is never swallowed.
    """
    if not isinstance(response, dict):
        return None
    if response.get("status") == "error" and ("message" in response or "req_data" in response):
        msgs = [m for m in (response.get("message") or []) if isinstance(m, dict)]
        if msgs and all(
            m.get("key") in _RETRYABLE_ERROR_KEYS
            or (
                m.get("msg") in _LOCATION_ERROR_MESSAGES
                and ("pickup_address" in m.get("param", "") or "dropoff_address" in m.get("param", ""))
            )
            for m in msgs
        ):
            return "; ".join(f"{m.get('param', '?')}: {m.get('msg')}" for m in msgs)[:150]
        return None
    if not response.get("payment_array"):
        return None  # malformed success response: caller asserts on it
    ppd = response["payment_array"][0].get("ppd_amount")
    if not (isinstance(ppd, (int, float)) and ppd > 0):
        return f"no price at add (ppd_amount={ppd!r})"
    return None


def create_priced_booking(
    api_client,
    booking_report,
    booking_type: str,
    sa_locations: list,
    intl_locations: list,
    saudi_side: str,
    check_package,
    max_attempts: int = 3,
) -> dict:
    """
    Picks a route + partner (only partners with a quoted amount), builds the
    booking and calls /bookings/add until the server returns a PRICED
    booking, for up to max_attempts attempts.

    Why a retry at all: some partner/route combinations are quoted by
    get_delivery_and_rate but then priced at 0 (or rejected) by
    /bookings/add. An unpriced booking must never be paid for.

    What keeps this honest (the first inline version hid failures):
      * ONLY retryable_create_failure() cases are retried; every other
        error raises immediately with its real message.
      * Every skipped attempt is written to the Excel report as a
        "SKIPPED: <reason>" row (partner + route included), so a partner
        that keeps failing is visible instead of silently rotated past.
      * Each skipped attempt also blacklists a rejected location
        (blacklist_bad_locations_from_error).
      * After max_attempts the test FAILS and lists every skipped attempt.
    Note: every attempt creates an unpaid server-side booking record.

    `check_package(actual_weight, length, width, height)` is the booking
    type's own dimension/weight rule; it must assert.
    """
    skipped = []
    for attempt in range(1, max_attempts + 1):
        step(f"create attempt {attempt}/{max_attempts}")
        route = pick_route_with_available_partner(
            api_client, booking_type, sa_locations, intl_locations, saudi_side
        )
        pickup, dropoff = route["pickup"], route["dropoff"]
        delivery_partner = route["delivery_partner"]
        actual_weight, length, width, height = route["weight_and_dimensions"]
        check_package(actual_weight, length, width, height)

        item_name = random.choice(ITEM_NAME_POOL)
        hs_code = first_hs_code(api_client.get_hs_codes(item_name))
        assert hs_code, "first_hs_code() returned an empty value."
        step("hs_code resolved", item_name=item_name, hs_code=hs_code)

        booking_detail = build_booking_detail(
            booking_type=booking_type,
            pickup_location=pickup,
            dropoff_location=dropoff,
            receiver_name=random.choice(RECEIVER_NAME_POOL),
            hs_code=hs_code,
            delivery_rates=route["delivery_rates"],
            delivery_partner=delivery_partner,
            weight_and_dimensions=route["weight_and_dimensions"],
            item_name=item_name,
        )
        assert booking_detail["item_list_details"][0]["item_name"] == item_name, (
            "item name sent must be the one the HS code was looked up for"
        )
        assert_exactly_one_saudi_side(
            {"country_code": booking_detail["pickup_country_code"]},
            {"country_code": booking_detail["dropoff_country_code"]},
            context=f"booking_detail dict itself ({booking_type})",
        )
        assert booking_detail["stakeholders"], "Receiver mobile number (stakeholders) is empty."
        assert booking_detail["sender_mobile_number"], "Sender mobile number is empty."
        assert booking_detail["country_code"].startswith("+"), f"Receiver dial code looks wrong: {booking_detail['country_code']!r}"
        assert booking_detail["sender_country_code"].startswith("+"), f"Sender dial code looks wrong: {booking_detail['sender_country_code']!r}"
        expected_palletized = "true" if booking_type == "pallet" else "false"
        assert booking_detail["is_palletized"] == expected_palletized, (
            f"is_palletized must be {expected_palletized!r} for {booking_type}, got {booking_detail['is_palletized']!r}"
        )
        chosen_rate = booking_detail[f"delivery_rate_{delivery_partner}"]
        assert chosen_rate and float(chosen_rate) > 0, (
            f"delivery_rate_{delivery_partner} should hold the quoted amount but is {chosen_rate!r}"
        )
        step(
            "booking_detail built",
            partner=delivery_partner,
            quoted_amount=chosen_rate,
            is_palletized=booking_detail["is_palletized"],
            receiver_phone=f"{booking_detail['country_code']}{booking_detail['stakeholders']}",
            sender_phone=f"{booking_detail['sender_country_code']}{booking_detail['sender_mobile_number']}",
            weight=actual_weight,
            dims=(length, width, height),
        )
        dump_debug(f"{booking_type}_booking_detail_payload", booking_detail)

        try:
            create_response = api_client.create_booking(booking_detail)
        except Exception as exc:  # noqa: BLE001 - re-raised
            if "timed out" in str(exc).lower():
                log_server_bug("timeout", booking_type, delivery_partner, pickup, dropoff,
                               detail=str(exc).splitlines()[0][:200], evidence="POST /bookings/add")
            raise
        dump_debug(f"create_{booking_type}_booking_response", create_response)
        step("create_booking response received", top_level_status=create_response.get("status"))
        blacklist_bad_locations_from_error(create_response, pickup, dropoff)

        reason = retryable_create_failure(create_response)
        if reason is None:
            raise_if_error_status(create_response, context=f"create_booking ({booking_type})")
            booking_id = extract_booking_id(create_response)
            payment_array = create_response.get("payment_array")
            assert payment_array, "create_booking response has no payment_array."
            ppd_amount = payment_array[0].get("ppd_amount")
            assert isinstance(ppd_amount, (int, float)) and ppd_amount > 0, (
                f"Booking {booking_id} has no price (ppd_amount={ppd_amount!r})"
            )
            step("booking priced by server", booking_id=booking_id, ppd_amount=ppd_amount, attempt=attempt)
            return {
                "create_response": create_response,
                "booking_detail": booking_detail,
                "booking_id": booking_id,
                "ppd_amount": ppd_amount,
                "pickup": pickup,
                "dropoff": dropoff,
                "delivery_partner": delivery_partner,
                "skipped": skipped,
            }

        booking_report.add_row(
            booking_type=booking_type_label(booking_type=booking_type, type_partner=booking_detail.get("type_partner", "b2c")),
            booking_number="-",
            route=route_label(pickup.get("country", ""), dropoff.get("country", "")),
            delivery_partner=delivery_partner_label(delivery_partner),
            status=f"SKIPPED: {reason}",
        )
        record_partner_outcome(booking_type, delivery_partner, False, f"SKIPPED: {reason}")
        skipped_booking_id = ((create_response.get("booking_detail") or [{}])[0]).get("booking_id", "")
        log_server_bug(
            "unpriced_at_add" if reason.startswith("no price at add") else "partner_rejected",
            booking_type, delivery_partner, pickup, dropoff,
            detail=f"{reason} (partner had quoted amount {chosen_rate})",
            evidence=(f"booking_id {skipped_booking_id}; " if skipped_booking_id else "")
            + f".auth/debug_create_{booking_type}_booking_response.json",
        )
        skipped.append(f"{delivery_partner} on {pickup.get('country')}->{dropoff.get('country')}: {reason}")
        step("attempt skipped (not paid, will try another partner/route)", partner=delivery_partner, reason=reason)

    raise AssertionError(
        f"No priced {booking_type} booking after {max_attempts} attempts. Skipped:\n  - " + "\n  - ".join(skipped)
    )


def log_status_bug(booking_type: str, partner: str, pickup: dict, dropoff: dict, booking_number: str, status: str):
    """A booking that was priced AND paid but did not reach a success status within the wait."""
    log_server_bug(
        "stuck_after_payment", booking_type, partner, pickup, dropoff,
        detail=f"status {status!r} after payment and the full wait (booking {booking_number})",
        evidence=f"booking {booking_number}; reports/booking_report.xlsx",
    )
