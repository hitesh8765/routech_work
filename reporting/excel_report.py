"""
Excel test-run report, matching the format from the user's sample:

    B2C Parcel (Web)
    Booking Number-509477
    Italy-Saudi
    DHL
    Sent to Carrier

...rendered as proper Excel COLUMNS (one row per booking) rather than the
block-per-booking plain-text layout, since that's far more usable inside
Excel (sortable/filterable). Columns: Booking Type, Booking Number, Route,
Delivery Partner, Status, Created At.

If the block layout is actually preferred, this is the one place to
change -- see BookingReport.save().
"""
from datetime import datetime
from pathlib import Path
from typing import Optional

from openpyxl import Workbook

REPORT_PATH = Path(__file__).resolve().parent.parent / "reports" / "booking_report.xlsx"

COLUMNS = ["Booking Type", "Booking Number", "Route", "Delivery Partner", "Status", "Created At"]
COLUMN_WIDTHS = [22, 16, 20, 22, 18, 20]

# Display-name overrides confirmed from the user's sample report -- the
# app's own reporting abbreviates "Saudi Arabia" to "Saudi" in the route
# column. Extend this if more abbreviations surface.
COUNTRY_DISPLAY_OVERRIDES = {
    "Saudi Arabia": "Saudi",
}

# Status semantics confirmed directly by the user (2026-09-18):
#   Success (booking went through fine): "Sent to Carrier", "Shipment Submitted"
#   Failure (something went wrong post-creation): "In Transit", "Pickup Fail", "Fail"
# NOTE "In Transit" being a *failure* here is specific to right-after-creation
# checks in this test suite -- obviously a real shipment later in its life
# legitimately being "In Transit" is normal. This classification only makes
# sense as an immediate post-creation sanity check.
SUCCESS_STATUSES = {"Sent to Carrier", "Shipment Submitted"}
FAILURE_STATUSES = {"In Transit", "Pickup Fail", "Fail"}


def is_success_status(status: Optional[str]) -> bool:
    return status in SUCCESS_STATUSES


DELIVERY_PARTNER_LABELS = {
    "ups": "UPS",
    "dhl": "DHL",
    "aramex": "Aramex",
    "fedex": "Fedex",
    "fedex_priority": "Fedex Priority",
    "fedex_express": "Fedex Express",
    "fedex_regional": "Fedex Regional",
    "fedex_connect_plus": "Fedex Connect Plus",
    "fedex_priority_freight": "Fedex Priority Freight",
    "fedex_regional_economy_freight": "Fedex Regional Economy Freight",
    "fedex_economy_freight": "Fedex Economy Freight",
    "dhl_medical_express": "DHL Medical Express",
    "dhl_express_worldwide": "DHL Express Worldwide",
    "dhl_freight_worldwide": "DHL Freight Worldwide",
    "dhl_economy_select": "DHL Economy Select",
    "dhl_express_easy": "DHL Express Easy",
    "dhl_express_domestic": "DHL Express Domestic",
    "darb": "Darb",
}


def country_label(country: str) -> str:
    return COUNTRY_DISPLAY_OVERRIDES.get(country, country)


def delivery_partner_label(key: str) -> str:
    return DELIVERY_PARTNER_LABELS.get(key, key.replace("_", " ").title())


def booking_type_label(booking_type: str, type_partner: str = "b2c", channel: str = "Web") -> str:
    """e.g. "B2C Parcel (Web)" -- matches the sample report's format."""
    return f"{type_partner.upper()} {booking_type.capitalize()} ({channel})"


def route_label(pickup_country: str, dropoff_country: str) -> str:
    return f"{country_label(pickup_country)}-{country_label(dropoff_country)}"


class BookingReport:
    """
    Accumulates one row per created booking, for the CURRENT test session
    only. Per the user's explicit instruction (2026-09-22): every full
    `pytest` run starts a FRESH report -- it does NOT load/append to a
    prior run's file. (Contrast with data/diversity_tracker.py's rotation
    state, which DOES persist across runs -- those are separate concerns.)
    """

    def __init__(self, path: Path = REPORT_PATH):
        self.path = path
        self._rows: list = []

    def add_row(
        self,
        booking_type: str,
        booking_number: str,
        route: str,
        delivery_partner: str,
        status: Optional[str],
    ):
        self._rows.append(
            [
                booking_type,
                booking_number,
                route,
                delivery_partner,
                status or "Unknown",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ]
        )

    def save(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "Bookings"
        ws.append(COLUMNS)
        for row in self._rows:
            ws.append(row)
        for i, width in enumerate(COLUMN_WIDTHS, start=1):
            ws.column_dimensions[chr(64 + i)].width = width
        self.path.parent.mkdir(exist_ok=True)
        wb.save(self.path)
        print(f"\n[report] Booking report saved -> {self.path} ({len(self._rows)} rows)\n")
