"""
E2E API test for Parcel booking creation (B2B account).

Flow (all via API, session captured once via UI login + manual captcha):
    1. Fetch saved business locations; ignore ones with bad data (lat/lng 0)
    2. Decide which side is Saudi (strict global alternation)
    3. create_priced_booking(): pick a route, quote the REAL shipment, choose a
       delivery partner that has a quoted amount, build the booking, call
       /bookings/add. If the server will not price that partner/route (price 0,
       or its "try another partner / change the location" rejection) the
       attempt is skipped, written to the report as SKIPPED, and retried (max 3).
       Any other error raises immediately with its real message.
    4. POST /bookings/make_ppd_payment (wallet) -- only for a PRICED booking
    5. Poll the booking for up to 60s until it reaches a terminal status
    6. Log to reports/booking_report.xlsx (a FAILED row if anything after the
       booking was created goes wrong)

Parcel dimension/weight rule: actual weight 8-16, length > width > height (restored -- it had been dropped).
Assertions + step() trace lines are used at every stage (`pytest -s`).
See handoff.md for the confirmed API contract and open items.
"""
import pytest

# (no payload imports needed: create_priced_booking() builds the booking)
from data.diversity_tracker import next_saudi_side, record_partner_outcome
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
    wait_for_terminal_status,
    create_priced_booking,
    usable_locations,
    log_failure_to_report,
    log_status_bug,
    step,
)


@pytest.mark.api
@pytest.mark.parcel
def test_create_parcel_booking(api_client, booking_report):
    """Creates exactly one Parcel booking, priced by the server, via a partner that has a quote."""
    step("START test_create_parcel_booking")

    # 1. saved locations (bad-data ones are ignored)
    all_locations = usable_locations(api_client.get_saved_locations(booking_type="parcel"))
    assert all_locations, "No usable saved business locations returned -- check booking_form response/parsing."
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
    def check_package(actual_weight, length, width, height):
        # Parcel rule: actual weight 8-16, length > width > height.
        assert 8 <= actual_weight <= 16, f"Parcel actual_weight out of range: {actual_weight} (expected 8-16)"
        assert length > width > height, f"Parcel dimensions must satisfy length>width>height, got {(length, width, height)}"

    booked = create_priced_booking(
        api_client, booking_report, "parcel", sa_locations, intl_locations, saudi_side, check_package
    )
    booking_id = booked["booking_id"]
    booking_detail = booked["booking_detail"]
    pickup, dropoff = booked["pickup"], booked["dropoff"]
    delivery_partner = booked["delivery_partner"]

    # 4-6. pay, verify, poll, report -- any failure here is also logged as a FAILED row
    with log_failure_to_report(
        booking_report, "parcel", booking_detail.get("type_partner", "b2c"), pickup, dropoff, delivery_partner
    ):
        payment_response = api_client.make_ppd_payment(booked["create_response"], is_wallet=True)
        dump_debug("make_ppd_parcel_payment_response", payment_response)
        assert payment_response and payment_response.get("status") is True, (
            f"Payment did not report success: {payment_response!r}"
        )
        step("payment confirmed")

        booking_number = api_client.booking_number_from_details(booking_id)
        assert booking_number, "Booking number not found in get_booking_details response."
        step("booking_number resolved", booking_number=booking_number)
        details_html = api_client.get_booking_details_html(booking_id)
        assert "Parcel" in details_html

        status = wait_for_terminal_status(api_client, booking_id, SUCCESS_STATUSES, FAILURE_STATUSES)
        step("final status polled", status=status)
        record_partner_outcome("parcel", delivery_partner, is_success_status(status), status)

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

    if not is_success_status(status):
        log_status_bug("parcel", delivery_partner, pickup, dropoff, booking_number, status)
    assert is_success_status(status), (
        f"Booking {booking_number} landed on status {status!r} after 60s, expected a success "
        f"status (Sent to Carrier / Shipment Submitted). Check .auth/debug_*.json for details."
    )
