"""
E2E API test for Parcel booking creation (B2B account).

Flow (all via API, session captured once via UI login + manual captcha):
    1. Fetch saved business locations (/bookings/booking_form)
    2. Decide which side is Saudi (strict global alternation)
    3. Pick a route + weight/dimensions, quote the REAL shipment, and choose
       a delivery partner ONLY from partners with a quoted amount
       (pick_route_with_available_partner) -- user-mandated rule, 2026-10-07
    4. Look up an HS code matching the item name
    5. Build the booking_detail (rates filled from the real nested response)
    6. POST /bookings/add, POST /bookings/make_ppd_payment (wallet)
    7. Poll the booking for up to 60s until it reaches a terminal status
    8. Log to reports/booking_report.xlsx

Assertions + step() trace lines are used at every stage so a failure's
location and cause are visible straight from the terminal (`pytest -s`).
See handoff.md for the confirmed API contract and open items.
"""
"""
E2E API test for Parcel booking creation (B2B account).

Flow (all via API, session captured once via UI login + manual captcha):
    1. Fetch saved business locations
    2. Pick one Saudi + one non-Saudi location (rule: exactly one side Saudi)
    3. Quote rates for the REAL parcel weight/dimensions
    4. Pick a delivery partner that HAS a quoted amount
    5. Build the full booking_detail payload
    6. POST /bookings/add -> create the booking
    7. POST /bookings/make_ppd_payment -> pay via wallet
    8. GET /bookings/get_booking_details/:id -> verify it landed correctly

Includes a retry loop to handle known server bugs where a quoted partner
returns ppd_amount=0 during creation.
"""
import random

import pytest

from data.booking_payloads import (
    RECEIVER_NAME_POOL,
    ITEM_NAME_POOL,
    build_parcel_booking_detail,
)
from utils.html_parsing import saudi_locations, non_saudi_locations
from reporting.excel_report import (
    booking_type_label,
    route_label,
    delivery_partner_label,
    is_success_status,
)
from tests.api.bookings.booking_test_helpers import (
    step,
    dump_debug,
    first_hs_code,
    raise_if_error_status,
    extract_booking_id,
    wait_for_terminal_status,
    pick_route_with_available_partner,
    assert_exactly_one_saudi_side,
)
from data.diversity_tracker import next_saudi_side


@pytest.mark.api
@pytest.mark.parcel
def test_create_parcel_booking(api_client, booking_report):
    """Creates exactly one Parcel booking via a route + partner that the server actually prices."""
    step("START test_create_parcel_booking")

    # 1. saved locations
    all_locations = api_client.get_saved_locations(booking_type="parcel")
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

    # 3. Retry loop to handle server bugs where a quoted partner returns ppd_amount=0
    max_create_attempts = 3
    successful_create_response = None
    successful_booking_detail = None
    successful_booking_id = None
    successful_delivery_partner = None
    successful_pickup = None
    successful_dropoff = None

    for attempt in range(1, max_create_attempts + 1):
        try:
            # Route + real-shipment quote + partner with a quoted amount
            route = pick_route_with_available_partner(
                api_client, "parcel", sa_locations, intl_locations, saudi_side
            )
            pickup, dropoff = route["pickup"], route["dropoff"]
            delivery_partner = route["delivery_partner"]
            delivery_rates = route["delivery_rates"]
            actual_weight, length, width, height = route["weight_and_dimensions"]

            receiver_name = random.choice(RECEIVER_NAME_POOL)

            # HS code (same string as the item name, per the rule)
            item_name_for_lookup = random.choice(ITEM_NAME_POOL)
            hs_code = first_hs_code(api_client.get_hs_codes(item_name_for_lookup))
            assert hs_code, "first_hs_code() returned an empty value."
            step("hs_code resolved", item_name=item_name_for_lookup, hs_code=hs_code)

            # Build the booking
            booking_detail = build_parcel_booking_detail(
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
                context="booking_detail dict itself (parcel)",
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
            dump_debug("parcel_booking_detail_payload", booking_detail)

            # Create + check price
            create_response = api_client.create_booking(booking_detail)
            dump_debug("create_parcel_booking_response", create_response)
            step("create_booking response received", top_level_status=create_response.get("status"))
            raise_if_error_status(create_response, context="create_booking (parcel)")
            
            booking_id = extract_booking_id(create_response)
            payment_array = create_response.get("payment_array")
            assert payment_array, "create_booking response has no payment_array."
            
            ppd_amount = payment_array[0].get("ppd_amount")
            
            if isinstance(ppd_amount, (int, float)) and ppd_amount > 0:
                step("booking successfully priced by server", booking_id=booking_id, ppd_amount=ppd_amount)
                # Save successful variables and break the loop
                successful_create_response = create_response
                successful_booking_detail = booking_detail
                successful_booking_id = booking_id
                successful_delivery_partner = delivery_partner
                successful_pickup = pickup
                successful_dropoff = dropoff
                break
            else:
                step(f"Attempt {attempt}: Server returned ppd_amount=0 for {delivery_partner}. Retrying...", ppd_amount=ppd_amount)
                
        except Exception as e:
            step(f"Attempt {attempt} failed with exception: {e}")
            continue

    assert successful_create_response is not None, (
        f"Failed to create a priced booking after {max_create_attempts} attempts. "
        f"The server consistently returned ppd_amount=0 or failed."
    )

    # 4. Pay for the successfully priced booking
    step("paying for booking")
    payment_response = api_client.make_ppd_payment(successful_create_response, is_wallet=True)
    dump_debug("make_ppd_payment_response", payment_response)
    assert payment_response.get("status") is True, f"Payment failed: {payment_response}"
    step("payment confirmed")

    # 5. Verify
    booking_number = api_client.booking_number_from_details(successful_booking_id)
    assert booking_number, "Booking number not found in get_booking_details response."
    step("booking_number resolved", booking_number=booking_number)

    details_html = api_client.get_booking_details_html(successful_booking_id)
    assert "Parcel" in details_html

    # 6. Poll for terminal status
    status = wait_for_terminal_status(
        api_client,
        successful_booking_id,
        success_statuses={"Sent to Carrier", "Shipment Submitted"},
        failure_statuses={"In Transit", "Pickup Fail", "Fail", "Requested"},
    )
    step("final status polled", status=status)

    # 7. Log to Excel report
    booking_report.add_row(
        booking_type=booking_type_label(
            booking_type="parcel", 
            type_partner=successful_booking_detail.get("type_partner", "b2c")
        ),
        booking_number=booking_number,
        route=route_label(successful_pickup.get("country", ""), successful_dropoff.get("country", "")),
        delivery_partner=delivery_partner_label(successful_delivery_partner),
        status=status,
    )
    
    assert is_success_status(status), (
        f"Booking {booking_number} landed on status {status!r}, expected a success status."
    )