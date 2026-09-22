"""
E2E API test for Pallet booking creation (B2B account).

Confirmed via live recon (2026-09-21, user drove the UI manually while
this framework watched DevTools): Pallet's /bookings/add payload is
IDENTICAL to Parcel's except `booking_type: "pallet"` (and the absence of
`pallet_container_id`, which turned out not to be pallet-specific despite
the name -- see handoff.md). Same booking_form / get_hs_codes /
get_delivery_and_rate / make_ppd_payment / get_booking_details endpoints,
same field names throughout.

Diversity rules (given by the user, refined 2026-09-22) -- these apply
PER BOOKING, not per booking type:
    - Saudi side (pickup/dropoff) must strictly ALTERNATE from whatever
      the previous booking (of ANY type) used -- data/diversity_tracker.py
      next_saudi_side().
    - Pickup/dropoff locations must respect reuse cooldowns (~7 other
      Saudi locations / ~17 other non-Saudi locations before a repeat) --
      next_fresh_location().
    - Delivery partner round-robins through every partner (DHL excluded)
      before any repeat -- next_delivery_partner().

Flow is otherwise identical to test_parcel_booking.py -- see that file's
docstring for the step-by-step breakdown, and handoff.md for the full
confirmed API contract / open items.
"""
import random

import pytest

from data.booking_payloads import (
    arrange_pickup_dropoff,
    build_pallet_booking_detail,
    RECEIVER_NAME_POOL,
    ITEM_NAME_POOL,
)
from data.diversity_tracker import next_saudi_side, next_fresh_location, next_delivery_partner
from utils.html_parsing import saudi_locations, non_saudi_locations
from reporting.excel_report import booking_type_label, route_label, delivery_partner_label, is_success_status
from tests.api.bookings.booking_test_helpers import (
    dump_debug,
    first_hs_code,
    raise_if_error_status,
    extract_booking_id,
)


@pytest.mark.api
@pytest.mark.pallet
def test_create_pallet_booking(api_client, booking_report):
    """
    Creates one Pallet booking, driven entirely by the diversity tracker:
    Saudi side alternates globally from the last booking of any type, the
    specific pickup/dropoff locations respect their reuse cooldowns, and
    the delivery partner is the next one in the rotation (DHL excluded).
    """

    # 1. saved locations, picked with per-booking reuse cooldowns applied
    all_locations = api_client.get_saved_locations(booking_type="pallet")
    assert all_locations, "No saved business locations returned -- check booking_form response/parsing."

    sa_locations = saudi_locations(all_locations)
    intl_locations = non_saudi_locations(all_locations)
    assert sa_locations, "No saved Saudi Arabia locations available on this account."
    assert intl_locations, "No saved non-Saudi locations available on this account."

    saudi_side = next_saudi_side()
    saudi_location = next_fresh_location(sa_locations, is_saudi=True)
    intl_location = next_fresh_location(intl_locations, is_saudi=False)
    pickup, dropoff = arrange_pickup_dropoff(saudi_location, intl_location, saudi_side=saudi_side)

    delivery_partner = next_delivery_partner()
    receiver_name = random.choice(RECEIVER_NAME_POOL)

    # 2. HS code lookup (uses same string as item name per the rule)
    item_name_for_lookup = random.choice(ITEM_NAME_POOL)
    hs_code_response = api_client.get_hs_codes(item_name_for_lookup)
    hs_code = first_hs_code(hs_code_response)

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
        "booking_type": "pallet",
        "is_palletized": "false",
    }
    delivery_rates = api_client.get_delivery_and_rate(rate_payload)

    # 4. build + create booking
    booking_detail = build_pallet_booking_detail(
        pickup_location=pickup,
        dropoff_location=dropoff,
        receiver_name=receiver_name,
        hs_code=hs_code,
        delivery_rates=delivery_rates,
        delivery_partner=delivery_partner,
    )
    create_response = api_client.create_booking(booking_detail)
    dump_debug("create_pallet_booking_response", create_response)
    raise_if_error_status(create_response, context="create_booking (pallet)")
    booking_id = extract_booking_id(create_response)
    assert create_response.get("payment_array"), (
        "create_booking response has no payment_array -- "
        "see .auth/debug_create_pallet_booking_response.json for the full payload."
    )

    # 5. pay via wallet
    payment_response = api_client.make_ppd_payment(create_response, is_wallet=True)
    dump_debug("make_ppd_pallet_payment_response", payment_response)
    # Confirmed real shape (2026-09-21 live run): {"status": true, "online": ..., "wallet": ..., "paymentArray": [...], ...}
    assert payment_response and payment_response.get("status") is True, (
        f"Payment did not report success: {payment_response!r}"
    )

    # 6. verify
    booking_number = api_client.booking_number_from_details(booking_id)
    assert booking_number, "Booking number not found in get_booking_details response."

    details_html = api_client.get_booking_details_html(booking_id)
    assert "Pallet" in details_html

    # 7. log to the Excel report (location/partner/side usage already
    #    recorded atomically by the next_* tracker calls above)
    status = api_client.booking_status_from_details(booking_id)
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
        f"Booking {booking_number} landed on status {status!r}, expected a success "
        f"status (Sent to Carrier / Shipment Submitted). Check .auth/debug_*.json for details."
    )
