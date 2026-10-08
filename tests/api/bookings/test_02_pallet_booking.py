"""
E2E API test for Pallet booking creation (B2B account).

Confirmed via live recon (2026-09-21): Pallet's /bookings/add payload is
identical to Parcel's except `booking_type: "pallet"` (and no
`pallet_container_id`). Same endpoints throughout.

Pallet weight/dimensions (user rule, 2026-09-23/10-06): picked as ONE coupled
combo from PALLET_WEIGHT_DIMENSION_COMBOS (NOT length>width>height) -- see
data/booking_payloads.py.

Delivery partner rule (user-mandated 2026-10-07): the partner is chosen ONLY
from partners with a quoted amount for the route
(pick_route_with_available_partner). Selecting a partner with no quote gets
rejected or leaves the booking stuck on "Requested" forever. The rate quote
now uses the pallet's REAL weight/dimensions (it used to quote a 10 kg
parcel).

Assertions + step() trace lines are used at every stage (`pytest -s`).
See handoff.md for the confirmed API contract and open items.
"""
import random

import pytest

from data.booking_payloads import (
    build_pallet_booking_detail,
    RECEIVER_NAME_POOL,
    ITEM_NAME_POOL,
    PALLET_WEIGHT_DIMENSION_COMBOS,
)
from data.diversity_tracker import next_saudi_side
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
    pick_route_with_available_partner,
    step,
)


@pytest.mark.api
@pytest.mark.pallet
def test_create_pallet_booking(api_client, booking_report):
    """Creates exactly one Pallet booking via a route + partner that the server actually quotes."""
    step("START test_create_pallet_booking")

    # 1. saved locations
    all_locations = api_client.get_saved_locations(booking_type="pallet")
    assert all_locations, "No saved business locations returned -- check booking_form response/parsing."
    sa_locations = saudi_locations(all_locations)
    intl_locations = non_saudi_locations(all_locations)
    assert sa_locations, "No saved Saudi Arabia locations available on this account."
    assert intl_locations, "No saved non-Saudi locations available on this account."
    step("locations fetched", total=len(all_locations), saudi_count=len(sa_locations), intl_count=len(intl_locations))

    # 2. which side is Saudi
    saudi_side = next_saudi_side()
    assert saudi_side in ("pickup", "dropoff"), f"next_saudi_side() returned unexpected value: {saudi_side!r}"
    step("saudi_side decided", saudi_side=saudi_side)

    # 3. route + real-shipment quote + partner with a quoted amount
    route = pick_route_with_available_partner(
        api_client, "pallet", sa_locations, intl_locations, saudi_side
    )
    pickup, dropoff = route["pickup"], route["dropoff"]
    delivery_partner = route["delivery_partner"]
    delivery_rates = route["delivery_rates"]
    actual_weight, length, width, height = route["weight_and_dimensions"]

    # Pallet rule: weight+dimensions must be exactly one of the approved coupled combos.
    assert (actual_weight, length, width, height) in PALLET_WEIGHT_DIMENSION_COMBOS, (
        f"Pallet weight/dimensions {(actual_weight, length, width, height)} is not one of the "
        f"approved combos: {PALLET_WEIGHT_DIMENSION_COMBOS}"
    )

    receiver_name = random.choice(RECEIVER_NAME_POOL)

    # 4. HS code (same string as the item name, per the rule)
    item_name_for_lookup = random.choice(ITEM_NAME_POOL)
    hs_code = first_hs_code(api_client.get_hs_codes(item_name_for_lookup))
    assert hs_code, "first_hs_code() returned an empty value."
    step("hs_code resolved", item_name=item_name_for_lookup, hs_code=hs_code)

    # 5. build the booking
    booking_detail = build_pallet_booking_detail(
        pickup_location=pickup,
        dropoff_location=dropoff,
        receiver_name=receiver_name,
        hs_code=hs_code,
        delivery_rates=delivery_rates,
        delivery_partner=delivery_partner,
        weight_and_dimensions=route["weight_and_dimensions"],
    )
    assert_exactly_one_saudi_side(
        {"country_code": booking_detail["pickup_country_code"]},
        {"country_code": booking_detail["dropoff_country_code"]},
        context="booking_detail dict itself (pallet)",
    )
    assert booking_detail["stakeholders"], "Receiver mobile number (stakeholders) is empty."
    assert booking_detail["sender_mobile_number"], "Sender mobile number is empty."
    assert booking_detail["country_code"].startswith("+"), f"Receiver dial code looks wrong: {booking_detail['country_code']!r}"
    assert booking_detail["sender_country_code"].startswith("+"), f"Sender dial code looks wrong: {booking_detail['sender_country_code']!r}"
    chosen_rate = booking_detail[f"delivery_rate_{delivery_partner}"]
    assert chosen_rate and float(chosen_rate) > 0, (
        f"delivery_rate_{delivery_partner} should hold the quoted amount but is {chosen_rate!r}"
    )
    step(
        "booking_detail built",
        partner=delivery_partner,
        quoted_amount=chosen_rate,
        receiver_phone=f"{booking_detail['country_code']}{booking_detail['stakeholders']}",
        sender_phone=f"{booking_detail['sender_country_code']}{booking_detail['sender_mobile_number']}",
        weight=actual_weight,
        dims=(length, width, height),
    )
    dump_debug("pallet_booking_detail_payload", booking_detail)

    # 6. create + pay
    create_response = api_client.create_booking(booking_detail)
    dump_debug("create_pallet_booking_response", create_response)
    step("create_booking response received", top_level_status=create_response.get("status"))
    raise_if_error_status(create_response, context="create_booking (pallet)")
    booking_id = extract_booking_id(create_response)
    payment_array = create_response.get("payment_array")
    assert payment_array, (
        "create_booking response has no payment_array -- "
        "see .auth/debug_create_pallet_booking_response.json for the full payload."
    )
    ppd_amount = payment_array[0].get("ppd_amount")
    assert isinstance(ppd_amount, (int, float)) and ppd_amount > 0, (
        f"Booking {booking_id} was created with NO price (ppd_amount={ppd_amount!r}) -- the server could not "
        f"price partner {delivery_partner!r} for this route, which is what leaves bookings stuck on 'Requested'. "
        f"Not paying for it."
    )
    step("booking created", booking_id=booking_id, ppd_amount=ppd_amount)

    payment_response = api_client.make_ppd_payment(create_response, is_wallet=True)
    dump_debug("make_ppd_pallet_payment_response", payment_response)
    assert payment_response and payment_response.get("status") is True, (
        f"Payment did not report success: {payment_response!r}"
    )
    step("payment confirmed")

    # 7. verify
    booking_number = api_client.booking_number_from_details(booking_id)
    assert booking_number, "Booking number not found in get_booking_details response."
    step("booking_number resolved", booking_number=booking_number)
    details_html = api_client.get_booking_details_html(booking_id)
    assert "Pallet" in details_html

    status = wait_for_terminal_status(api_client, booking_id, SUCCESS_STATUSES, FAILURE_STATUSES)
    step("final status polled", status=status)

    # 8. report
    booking_report.add_row(
        booking_type=booking_type_label(
            booking_type="pallet",
            type_partner=booking_detail.get("type_partner", "b2c"),
        ),
        booking_number=booking_number,
        route=route_label(pickup.get("country", ""), dropoff.get("country", "")),
        delivery_partner=delivery_partner_label(booking_detail.get("delivery_partners", "")),
        status=status,
    )

    assert is_success_status(status), (
        f"Booking {booking_number} landed on status {status!r} after 60s, expected a success "
        f"status (Sent to Carrier / Shipment Submitted). Check .auth/debug_*.json for details."
    )
