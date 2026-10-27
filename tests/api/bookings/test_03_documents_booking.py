"""
E2E API test for Documents (Envelope) booking creation (B2B account).

Flow (all via API, session captured once via UI login + manual captcha):
    1. Fetch saved business locations
    2. Decide which side is Saudi (strict global alternation)
    3. create_priced_booking(): pick a route, quote the REAL shipment, choose a
       delivery partner that has a quoted amount, build the booking, call
       /bookings/add. (Skips HS code lookup for envelope).
    4. POST /bookings/make_ppd_payment (wallet)
    5. Poll the booking for up to 60s until it reaches a terminal status
    6. Log to reports/booking_report.xlsx

Documents rule: weight is ALWAYS 1 or 2, dimensions are fixed at 1x1x1, 
item is fixed to "Envelope Shipment", and no HS code is required.
"""
import pytest

from tests.api.bookings.booking_test_helpers import (
    create_priced_booking,
    log_failure_to_report,
    step,
    wait_for_terminal_status,
)
from utils.html_parsing import saudi_locations, non_saudi_locations
from reporting.excel_report import (
    booking_type_label,
    route_label,
    delivery_partner_label,
    SUCCESS_STATUSES,
    FAILURE_STATUSES,
)
from data.diversity_tracker import next_saudi_side


@pytest.mark.api
@pytest.mark.documents
def test_create_documents_booking(api_client, booking_report):
    """Creates exactly one Documents (Envelope) booking, priced by the server."""
    step("START test_create_documents_booking")

    # 1. saved locations
    all_locations = api_client.get_saved_locations(booking_type="envelope")
    assert all_locations, "No saved business locations returned -- check booking_form response/parsing."
    sa_locations = saudi_locations(all_locations)
    intl_locations = non_saudi_locations(all_locations)
    assert sa_locations, "No usable saved Saudi Arabia locations available on this account."
    assert intl_locations, "No usable saved non-Saudi locations available on this account."
    step("locations fetched", total=len(all_locations), saudi_count=len(sa_locations), intl_count=len(intl_locations))

    # 2. which side is Saudi
    saudi_side = next_saudi_side()
    assert saudi_side in ("pickup", "dropoff"), f"next_saudi_side() returned unexpected value: {saudi_side!r}"
    step("saudi_side decided", saudi_side=saudi_side)

    # 3. priced booking (route + partner + add, with visible retries)
    def check_envelope_package(actual_weight, length, width, height):
        # Documents rule: actual weight 1 or 2, dimensions fixed at 1x1x1
        assert actual_weight in [1, 2], f"Envelope actual_weight must be 1 or 2, got {actual_weight}"
        assert (length, width, height) == (1, 1, 1), f"Envelope dimensions must be (1, 1, 1), got {(length, width, height)}"

    booked = create_priced_booking(
        api_client, booking_report, "envelope", sa_locations, intl_locations, saudi_side, check_envelope_package
    )
    booking_id = booked["booking_id"]
    booking_detail = booked["booking_detail"]
    pickup, dropoff = booked["pickup"], booked["dropoff"]
    delivery_partner = booked["delivery_partner"]

    # 4-6. pay, verify, poll, report -- any failure here is also logged as a FAILED row
    with log_failure_to_report(
        booking_report, "documents", booking_detail.get("type_partner", "b2c"), pickup, dropoff, delivery_partner
    ):
        payment_response = api_client.make_ppd_payment(booked["create_response"], is_wallet=True)
        dump_debug("make_ppd_envelope_payment_response", payment_response)
        assert payment_response and payment_response.get("status") is True, (
            f"Payment did not report success: {payment_response!r}"
        )
        step("payment confirmed")

        booking_number = api_client.booking_number_from_details(booking_id)
        assert booking_number, "Booking number not found in get_booking_details response."
        step("booking_number resolved", booking_number=booking_number)
        
        details_html = api_client.get_booking_details_html(booking_id)
        assert "Envelope" in details_html or "Documents" in details_html

        status = wait_for_terminal_status(api_client, booking_id, SUCCESS_STATUSES, FAILURE_STATUSES)
        step("final status polled", status=status)

        booking_report.add_row(
            booking_type=booking_type_label(
                booking_type="documents",
                type_partner=booking_detail.get("type_partner", "b2c"),
            ),
            booking_number=booking_number,
            route=route_label(pickup.get("country", ""), dropoff.get("country", "")),
            delivery_partner=delivery_partner_label(booking_detail.get("delivery_partners", "")),
            status=status,
        )

    assert status in SUCCESS_STATUSES, (
        f"Booking {booking_number} landed on status {status!r} after 60s, expected a success "
        f"status (Sent to Carrier / Shipment Submitted). Check .auth/debug_*.json for details."
    )