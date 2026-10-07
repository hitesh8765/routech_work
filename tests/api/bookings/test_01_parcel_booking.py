"""
E2E API test for Parcel booking creation (B2B account).

Flow (all via API, session captured once via UI login + manual captcha):
    1. Fetch saved business locations (GET via /bookings/booking_form)
    2. Pick one Saudi + one non-Saudi location, respecting per-booking
       reuse cooldowns (data/diversity_tracker.py: next_fresh_location)
    3. Look up an HS code matching our random item name
    4. Get delivery partner rates for our random weight/dimensions
    5. Build the full booking_detail payload, using the next delivery
       partner in the rotation (DHL excluded -- see diversity_tracker.py)
    6. POST /bookings/add -> create the booking
    7. POST /bookings/make_ppd_payment -> pay via wallet
    8. GET /bookings/get_booking_details/:id -> verify it landed correctly
    9. Log to reports/booking_report.xlsx (appends across runs, tagged
       with a Run ID)

Per the user's explicit instruction (2026-09-22): assertions are used
liberally throughout, including BEFORE hitting the API where possible, so
any failure's root cause is immediately obvious from the assertion message
rather than needing a round-trip through a server error.

See handoff.md for the full confirmed API contract and any open items.
"""
import random

import pytest

from data.booking_payloads import (
    arrange_pickup_dropoff,
    build_parcel_booking_detail,
    RECEIVER_NAME_POOL,
    ITEM_NAME_POOL,
)
from data.diversity_tracker import next_saudi_side, next_fresh_location, next_delivery_partner
from utils.html_parsing import saudi_locations, non_saudi_locations
from reporting.excel_report import (
    booking_type_label,
    route_label,
    delivery_partner_label,
    is_success_status,
    SUCCESS_STATUSES,
    FAILURE_STATUSES,
)
from tests.api.bookings.booking_test_helpers import (
    dump_debug,
    first_hs_code,
    raise_if_error_status,
    extract_booking_id,
    wait_for_terminal_status,
    assert_exactly_one_saudi_side,
    step,
)


@pytest.mark.api
@pytest.mark.parcel
def test_create_parcel_booking(api_client, booking_report):
    """Creates exactly one Parcel booking, side/location/partner all driven by the global diversity tracker."""
    step("START test_create_parcel_booking")

    # 1. saved locations, picked with per-booking reuse cooldowns applied
    all_locations = api_client.get_saved_locations(booking_type="parcel")
    assert all_locations, "No saved business locations returned -- check booking_form response/parsing."

    sa_locations = saudi_locations(all_locations)
    intl_locations = non_saudi_locations(all_locations)
    assert sa_locations, "No saved Saudi Arabia locations available on this account."
    assert intl_locations, "No saved non-Saudi locations available on this account."
    step("locations fetched", total=len(all_locations), saudi_count=len(sa_locations), intl_count=len(intl_locations))

    saudi_side = next_saudi_side()
    assert saudi_side in ("pickup", "dropoff"), f"next_saudi_side() returned unexpected value: {saudi_side!r}"
    step("saudi_side decided", saudi_side=saudi_side)

    saudi_location = next_fresh_location(sa_locations, is_saudi=True)
    assert (saudi_location.get("country_code") or "").upper() == "SA", (
        f"next_fresh_location(is_saudi=True) returned a non-Saudi location: {saudi_location!r}"
    )
    step("saudi_location picked", id=saudi_location.get("id"), city=saudi_location.get("city"), country=saudi_location.get("country"))

    intl_location = next_fresh_location(intl_locations, is_saudi=False)
    assert (intl_location.get("country_code") or "").upper() != "SA", (
        f"next_fresh_location(is_saudi=False) returned a Saudi location: {intl_location!r}"
    )
    step("intl_location picked", id=intl_location.get("id"), city=intl_location.get("city"), country=intl_location.get("country"))

    pickup, dropoff = arrange_pickup_dropoff(saudi_location, intl_location, saudi_side=saudi_side)
    assert_exactly_one_saudi_side(pickup, dropoff, context="after arrange_pickup_dropoff (parcel)")
    step("pickup/dropoff arranged", pickup_country=pickup.get("country"), dropoff_country=dropoff.get("country"))

    delivery_partner = next_delivery_partner()
    assert delivery_partner, "next_delivery_partner() returned an empty value."
    step("delivery_partner picked", delivery_partner=delivery_partner)

    receiver_name = random.choice(RECEIVER_NAME_POOL)

    # 2. HS code lookup (uses same string as item name per the rule -- but
    #    we don't know the item name until build time, so do a lightweight
    #    two-pass: pick item name pool value first via a throwaway call).
    item_name_for_lookup = random.choice(ITEM_NAME_POOL)
    hs_code_response = api_client.get_hs_codes(item_name_for_lookup)
    hs_code = first_hs_code(hs_code_response)
    assert hs_code, "first_hs_code() returned an empty value."
    step("hs_code resolved", item_name=item_name_for_lookup, hs_code=hs_code)

    # 3. delivery rate quote
    rate_payload = {
        "type": "b2c",
        "pickup_city": pickup.get("city", ""),
        "dropoff_city": dropoff.get("city", ""),
        "pickup_country": pickup.get("country", ""),
        "dropoff_country": dropoff.get("country", ""),
        "pickup_country_code": pickup.get("country_code", ""),
        "dropoff_country_code": dropoff.get("country_code", ""),
        "dropoff_postal_code": dropoff.get("postal_code", ""),
        "pickup_postal_code": pickup.get("postal_code", ""),
        "pickup_state_code": pickup.get("state", ""),
        "dropoff_state_code": dropoff.get("state", ""),
        "is_international": "1",
        "delivery_reg_same_type": "regular",
        "payment_type": "ppd",
        "shipment_content_type": "dry",
        "weight_per_kg": "",
        "package_dimension[0][length]": "15",
        "package_dimension[0][width]": "12",
        "package_dimension[0][height]": "11",
        "package_dimension[0][actual_weight]": "10",
        "package_dimension[0][weight]": "0.4",
        "package_dimension[0][higher_weight]": "10",
        "offer_code": "",
        "booking_type": "parcel",
        "is_palletized": "false",
    }
    delivery_rates = api_client.get_delivery_and_rate(rate_payload)
    dump_debug("get_delivery_and_rate_response", delivery_rates)
    step("delivery_rates fetched")

    # 4. build + create booking
    booking_detail = build_parcel_booking_detail(
        pickup_location=pickup,
        dropoff_location=dropoff,
        receiver_name=receiver_name,
        hs_code=hs_code,
        delivery_rates=delivery_rates,
        delivery_partner=delivery_partner,
    )
    # Sanity-check the FINAL built dict too, not just the inputs -- catches
    # any bug introduced inside build_booking_detail's own field mapping.
    assert_exactly_one_saudi_side(
        {"country_code": booking_detail["pickup_country_code"]},
        {"country_code": booking_detail["dropoff_country_code"]},
        context="booking_detail dict itself (parcel)",
    )
    assert booking_detail["stakeholders"], "Receiver mobile number (stakeholders) is empty."
    assert booking_detail["sender_mobile_number"], "Sender mobile number is empty."
    assert booking_detail["country_code"].startswith("+"), f"Receiver dial code looks wrong: {booking_detail['country_code']!r}"
    assert booking_detail["sender_country_code"].startswith("+"), f"Sender dial code looks wrong: {booking_detail['sender_country_code']!r}"

    # Parcel dimension/weight rule: actual weight 8-16, length > width > height.
    pkg = booking_detail["package_dimensions"][0]
    actual_weight, length, width, height = int(pkg["actual_weight"]), int(pkg["length"]), int(pkg["width"]), int(pkg["height"])
    assert 8 <= actual_weight <= 16, f"Parcel actual_weight out of range: {actual_weight} (expected 8-16)"
    assert length > width > height, f"Parcel dimensions must satisfy length>width>height, got {(length, width, height)}"
    step(
        "booking_detail built",
        receiver_phone=f"{booking_detail['country_code']}{booking_detail['stakeholders']}",
        sender_phone=f"{booking_detail['sender_country_code']}{booking_detail['sender_mobile_number']}",
        weight=actual_weight,
        dims=(length, width, height),
    )
    dump_debug("booking_detail_payload", booking_detail)

    create_response = api_client.create_booking(booking_detail)
    dump_debug("create_booking_response", create_response)
    step("create_booking response received", top_level_status=create_response.get("status"))
    raise_if_error_status(create_response, context="create_booking")
    booking_id = extract_booking_id(create_response)
    assert create_response.get("payment_array"), (
        "create_booking response has no payment_array -- "
        "see .auth/debug_create_booking_response.json for the full payload."
    )
    step("booking created", booking_id=booking_id)

    # 5. pay via wallet (server already built the exact payment_array we need)
    payment_response = api_client.make_ppd_payment(create_response, is_wallet=True)
    dump_debug("make_ppd_payment_response", payment_response)
    # Confirmed real shape (2026-09-21 live run): {"status": true, "online": ..., "wallet": ..., "paymentArray": [...], ...}
    assert payment_response and payment_response.get("status") is True, (
        f"Payment did not report success: {payment_response!r}"
    )
    step("payment confirmed")

    # 6. verify
    booking_number = api_client.booking_number_from_details(booking_id)
    assert booking_number, "Booking number not found in get_booking_details response."
    step("booking_number resolved", booking_number=booking_number)

    details_html = api_client.get_booking_details_html(booking_id)
    assert "Parcel" in details_html

    # 7. log to the Excel report (location/partner/side usage already
    #    recorded atomically by the next_* tracker calls above)
    status = wait_for_terminal_status(api_client, booking_id, SUCCESS_STATUSES, FAILURE_STATUSES)
    step("final status polled", status=status)
    booking_report.add_row(
        booking_type=booking_type_label(
            booking_type="parcel",
            type_partner=booking_detail.get("type_partner", "b2c"),
        ),
        booking_number=booking_number,
        route=route_label(pickup.get("country", ""), dropoff.get("country", "")),
        delivery_partner=delivery_partner_label(booking_detail.get("delivery_partners", "")),
        status=status,
    )

    assert is_success_status(status), (
        f"Booking {booking_number} landed on status {status!r}, expected a success "
        f"status (Sent to Carrier / Shipment Submitted). Check .auth/debug_*.json for details."
    )
