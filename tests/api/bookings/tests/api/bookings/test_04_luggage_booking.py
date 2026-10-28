"""
E2E API test for Luggage booking creation (B2B account).

Flow (all via API, session captured once via UI login + manual captcha):
    1. Fetch saved business locations via /luggage/booking_form
    2. Decide which side is Saudi (strict global alternation)
    3. Pick a luggage type, fetch its metadata (_id) via /luggage/luggage_details
    4. Quote rates via /luggage/get_luggage_delivery_and_rate
    5. Lookup HS code via /luggage/get_hs_codes
    6. Build the unique Luggage booking_detail payload
    7. POST /luggage/add -> create the booking
    8. POST /bookings/make_ppd_payment -> pay via wallet
    9. Poll for terminal status and log to Excel report
"""
import pytest
import random

from tests.api.bookings.booking_test_helpers import (
    log_failure_to_report,
    step,
    wait_for_terminal_status,
    dump_debug,
    first_hs_code,
    raise_if_error_status,
)
from utils.html_parsing import saudi_locations, non_saudi_locations
from reporting.excel_report import (
    booking_type_label,
    route_label,
    delivery_partner_label,
    SUCCESS_STATUSES,
    FAILURE_STATUSES,
)
from data.diversity_tracker import next_saudi_side, next_fresh_location, next_delivery_partner
from data.booking_payloads import (
    RECEIVER_NAME_POOL,
    ITEM_NAME_POOL,
    ITEM_QUANTITY_POOL,
    ITEM_PRICE_POOL,
    LUGGAGE_TYPES,
    build_luggage_rate_payload,
    build_luggage_booking_detail,
)


@pytest.mark.api
@pytest.mark.luggage
def test_create_luggage_booking(api_client, booking_report):
    """Creates exactly one Luggage booking, priced by the server."""
    step("START test_create_luggage_booking")

    # 1. saved locations
    all_locations = api_client.get_saved_locations(booking_type="luggage")
    assert all_locations, "No saved business locations returned -- check luggage booking_form response/parsing."
    sa_locations = saudi_locations(all_locations)
    intl_locations = non_saudi_locations(all_locations)
    assert sa_locations, "No usable saved Saudi Arabia locations available on this account."
    assert intl_locations, "No usable saved non-Saudi locations available on this account."
    step("locations fetched", total=len(all_locations), saudi_count=len(sa_locations), intl_count=len(intl_locations))

    # 2. which side is Saudi
    saudi_side = next_saudi_side()
    assert saudi_side in ("pickup", "dropoff"), f"next_saudi_side() returned unexpected value: {saudi_side!r}"
    step("saudi_side decided", saudi_side=saudi_side)

    # 3. Route and Luggage selection
    saudi_location = next_fresh_location(sa_locations, is_saudi=True)
    intl_location = next_fresh_location(intl_locations, is_saudi=False)
    
    if saudi_side == "pickup":
        pickup, dropoff = saudi_location, intl_location
    else:
        pickup, dropoff = intl_location, saudi_location
    step("route selected", pickup=f"{pickup.get('city')}, {pickup.get('country')}", dropoff=f"{dropoff.get('city')}, {dropoff.get('country')}")

    luggage_slug = random.choice(list(LUGGAGE_TYPES.keys()))
    luggage_specs = LUGGAGE_TYPES[luggage_slug]
    luggage_list_obj = {
        luggage_slug: {
            "weight": str(luggage_specs["weight"]),
            "quantity": "1",
            "name": luggage_specs["name"],
            "weight_unit": "KG",
            "slug": luggage_slug,
            "height": str(luggage_specs["height"]),
            "width": str(luggage_specs["width"]),
            "length": str(luggage_specs["length"]),
        }
    }
    step("luggage selected", slug=luggage_slug, specs=luggage_specs)

    # 4. Get luggage details to fetch _id
    luggage_details_resp = api_client.get_luggage_details(luggage_list_obj)
    assert luggage_details_resp.get("status") == "success", f"Failed to get luggage details: {luggage_details_resp}"
    luggage_meta = luggage_details_resp["result"][0]
    luggage_id = luggage_meta["_id"]
    step("luggage details fetched", luggage_id=luggage_id)

    # 5. Rate quote
    rate_payload = build_luggage_rate_payload(pickup, dropoff, luggage_list_obj)
    delivery_rates = api_client.get_luggage_delivery_and_rate(rate_payload)
    dump_debug("get_luggage_delivery_and_rate_response", delivery_rates)
    assert delivery_rates.get("status") == "success", f"Rate quote failed: {delivery_rates}"
    
    amounts = (delivery_rates or {}).get("amount", {}) or {}
    available_partners = [k for k, v in amounts.items() if isinstance(v.get("payable_amount"), (int, float)) and v["payable_amount"] > 0]
    assert available_partners, "No partners with a valid payable_amount > 0"
    step("rates quoted", partners_with_amount=available_partners)

    delivery_partner = next_delivery_partner(available_partners=available_partners)
    step("delivery_partner picked", delivery_partner=delivery_partner)

    # 6. HS Code lookup
    item_name = random.choice(ITEM_NAME_POOL)
    hs_code_resp = api_client.get_luggage_hs_codes(item_name)
    hs_code = first_hs_code(hs_code_resp)
    assert hs_code, "first_hs_code() returned an empty value."
    step("hs_code resolved", item_name=item_name, hs_code=hs_code)

    # 7. Build booking detail
    receiver_name = random.choice(RECEIVER_NAME_POOL)
    item_quantity = str(random.choice(ITEM_QUANTITY_POOL))
    item_price = str(random.choice(ITEM_PRICE_POOL))
    
    booking_detail = build_luggage_booking_detail(
        pickup_location=pickup,
        dropoff_location=dropoff,
        receiver_name=receiver_name,
        delivery_rates=delivery_rates,
        delivery_partner=delivery_partner,
        luggage_id=luggage_id,
        luggage_slug=luggage_slug,
        luggage_specs=luggage_specs,
        item_name=item_name,
        item_quantity=item_quantity,
        item_price=item_price,
        hs_code=hs_code,
    )
    step("booking_detail built", partner=delivery_partner)
    dump_debug("luggage_booking_detail_payload", booking_detail)

    # 8. Create booking
    create_response = api_client.create_luggage_booking(booking_detail)
    dump_debug("create_luggage_booking_response", create_response)
    step("create_booking response received", top_level_status=create_response.get("status"))
    raise_if_error_status(create_response, context="create_booking (luggage)")

    payment_array = create_response.get("payment_array")
    assert payment_array, "create_booking response has no payment_array."
    ppd_amount = payment_array[0].get("ppd_amount")
    assert isinstance(ppd_amount, (int, float)) and ppd_amount > 0, f"Booking unpriced: ppd_amount={ppd_amount}"
    step("booking successfully priced by server", ppd_amount=ppd_amount)

    booking_id = payment_array[0].get("booking_id")

    # 9. Pay, verify, poll, report
    with log_failure_to_report(
        booking_report, "luggage", booking_detail.get("type_partner", "b2c"), pickup, dropoff, delivery_partner
    ):
        payment_response = api_client.make_ppd_payment(create_response, is_wallet=True)
        dump_debug("make_ppd_luggage_payment_response", payment_response)
        assert payment_response and payment_response.get("status") is True, f"Payment failed: {payment_response}"
        step("payment confirmed")

        booking_number = api_client.booking_number_from_details(booking_id)
        assert booking_number, "Booking number not found in get_booking_details response."
        step("booking_number resolved", booking_number=booking_number)

        details_html = api_client.get_booking_details_html(booking_id)
        assert "Luggage" in details_html

        status = wait_for_terminal_status(api_client, booking_id, SUCCESS_STATUSES, FAILURE_STATUSES)
        step("final status polled", status=status)

        booking_report.add_row(
            booking_type=booking_type_label(booking_type="luggage", type_partner=booking_detail.get("type_partner", "b2c")),
            booking_number=booking_number,
            route=route_label(pickup.get("country", ""), dropoff.get("country", "")),
            delivery_partner=delivery_partner_label(delivery_partner),
            status=status,
        )

    assert status in SUCCESS_STATUSES, (
        f"Booking {booking_number} landed on status {status!r} after 60s, expected a success "
        f"status (Sent to Carrier / Shipment Submitted). Check .auth/debug_*.json for details."
    )