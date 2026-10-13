"""
Application bug report (reports/bug_report.xlsx).

Purpose (user, 2026-10-09): keep a running list of bugs in the APPLICATION
(server / app behaviour) found while building this suite -- NOT mistakes in
our own code -- so they can all be reported when the project finishes.

How it works
- One row per distinct bug, identified by a "Dedupe Key". Seeing the same bug
  again only bumps "Occurrences" and "Last Seen" (and refreshes the latest
  scenario / actual / evidence) -- it never creates a duplicate row.
- The file is edited IN PLACE, so anything the user changes by hand (Status,
  Severity, Notes, colours, extra columns on the right) is kept.
- A brand-new file is pre-filled with the bugs already observed
  (KNOWN_BUGS below). Each has evidence in the saved responses.
- The tests call log_bug() when they see server behaviour that looks wrong.
  Those rows start as "Suspected - verify in UI": a failure can still be OUR
  payload. The user (or the assistant, after checking) changes the Status to
  Confirmed / Not a bug.
- log_bug() NEVER raises: bug logging must not break a test run. If the file
  is open in Excel the entry is kept in memory and saved on the next log or
  at exit.

Severity: Critical / High / Medium / Low / Info.
Category: Bug / Inconsistency / Validation / Data / Performance / Info.
"""
import atexit
from datetime import datetime
from pathlib import Path
from typing import Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

BUG_REPORT_PATH = Path(__file__).resolve().parent.parent / "reports" / "bug_report.xlsx"

COLUMNS = [
    "Bug ID", "Title", "Category", "Severity", "Status", "Endpoint / Area", "Booking Type",
    "Delivery Partner", "Scenario / Route", "Expected", "Actual", "Evidence",
    "First Seen", "Last Seen", "Occurrences", "Notes", "Dedupe Key",
]
WIDTHS = [10, 48, 15, 10, 24, 26, 12, 18, 36, 42, 54, 50, 17, 17, 12, 44, 30]

CONFIRMED = "Confirmed"
SUSPECTED = "Suspected - verify in UI"
UNCONFIRMED = "Unconfirmed - retest pending"
NEEDS_DECISION = "Needs product decision"

_SEVERITY_FILL = {
    "Critical": "F4B6B6", "High": "F8CBAD", "Medium": "FFE699", "Low": "E2EFDA", "Info": "EDEDED",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Bugs already observed before this file existed (all have evidence in the
# saved responses / reports). Dates are when each was first seen.
# ---------------------------------------------------------------------------
KNOWN_BUGS = [
    dict(
        key="add_accepts_unquoted_partner",
        title="/bookings/add accepts a delivery partner that has no price and creates an unpriced booking that never progresses",
        category="Bug", severity="High", status=CONFIRMED,
        endpoint="POST /bookings/add",
        booking_type="pallet", partner="dhl",
        scenario="Pallet, Afghanistan -> Saudi Arabia, partner dhl. get_delivery_and_rate listed dhl under 'days' but not under 'amount' (no price).",
        expected="Reject the booking (e.g. 'select another delivery partner'), or never offer an unpriced partner.",
        actual="Booking created (booking 540161): ppd_amount null, goods_value null, base_goods_value null, tabby_payments [{}]. After payment it stays on status 'Requested' indefinitely and does not appear in the application's booking list. The same silent success happened for other partners without a quote.",
        evidence="booking 540161; reports/booking_report.xlsx row (DHL, Requested); .auth/debug_create_pallet_booking_response.json; .auth/debug_get_delivery_and_rate_pallet_response.json",
        first_seen="2026-10-07 15:47", last_seen="2026-10-07 15:47", occurrences=1,
        notes="Our test now only chooses partners that have a quoted amount, so it no longer triggers this. The server-side gap (no validation) remains.",
    ),
    dict(
        key="payment_ok_for_unpriced_booking",
        title="/bookings/make_ppd_payment reports success for a booking with no price",
        category="Bug", severity="High", status=CONFIRMED,
        endpoint="POST /bookings/make_ppd_payment",
        booking_type="pallet", partner="dhl",
        scenario="Paying booking 540161 (ppd_amount null) via wallet.",
        expected="Reject payment for a booking with no payable amount.",
        actual='Response {"status": true, "goods_value": null, "paymentArray": [{"ppd_amount": null, ...}]} - success reported for a null amount; booking stays "Requested".',
        evidence="booking 540161; .auth/debug_make_ppd_pallet_payment_response.json",
        first_seen="2026-10-07 15:47", last_seen="2026-10-07 15:47", occurrences=1,
        notes="Check whether the wallet was actually charged (it appeared unchanged).",
    ),
    dict(
        key="add_returns_status_error_on_success",
        title="/bookings/add returns \"status\": \"error\" for bookings that were created successfully",
        category="Inconsistency", severity="Medium", status=CONFIRMED,
        endpoint="POST /bookings/add",
        scenario="Any valid booking (parcel or pallet).",
        expected='A created booking returns a success status; "error" reserved for failures.',
        actual='The response carries "status": "error" together with booking_detail[0].booking_id and a full payment_array, and the booking is then payable. Real failures use the same field plus a "message" list, so clients must guess which "error" is real.',
        evidence=".auth/debug_create_booking_response.json (booking 6ab118cfc07d69eac7981d23 and every later successful create)",
        first_seen="2026-09-18 12:00", last_seen="2026-10-08 16:44", occurrences=38,
        notes="Occurrences = rows in booking_report.xlsx at the time this entry was created (every created booking).",
    ),
    dict(
        key="zero_quote|pallet|aramex",
        title="Rate quote returns payable_amount 0 for a partner as if it were a valid price",
        category="Bug", severity="Medium", status=CONFIRMED,
        endpoint="POST /bookings/get_delivery_and_rate",
        booking_type="pallet", partner="aramex",
        scenario="Pallet quote, Afghanistan -> Saudi Arabia: amount.aramex.payable_amount = 0 (with '3 Days').",
        expected="A real price, or aramex omitted from 'amount' when it cannot be priced.",
        actual="aramex returned with payable_amount 0, base_amount 0, base_price 0.",
        evidence=".auth/debug_get_delivery_and_rate_pallet_response.json (2026-10-07)",
        first_seen="2026-10-07 15:45", last_seen="2026-10-07 15:45", occurrences=1,
        notes="A zero is treated as 'no quote' by our partner selection.",
    ),
    dict(
        key="bad_location|Mauritius|Dubreuil",
        title="Saved location with latitude/longitude 0 is accepted into the account; booking it fails with an unhelpful error",
        category="Data", severity="Medium", status=CONFIRMED,
        endpoint="POST /bookings/add, saved locations",
        booking_type="parcel", partner="aramex",
        scenario="Parcel, Jeddah -> Dubreuil, Mauritius ('PH2M+26 Dubreuil, Mauritius'), partner aramex (quoted 1347.3). Saved location has dropoff_latitude '0', dropoff_longitude '0', empty postal code.",
        expected="Locations without real coordinates are rejected when saved, or booking explains which field is wrong.",
        actual='Booking rejected with {"param": "booking_detail_0_dropoff_address", "msg": "Missing parameters."} - no i18n "key", and it does not say which parameter is missing.',
        evidence=".auth/debug_create_booking_response.json (2026-10-08 16:44); .auth/debug_booking_detail_payload.json",
        first_seen="2026-10-08 16:44", last_seen="2026-10-08 16:44", occurrences=1,
        notes="Our tests now ignore saved locations with zero/missing coordinates.",
    ),
    dict(
        key="timeout|/bookings/add|pallet",
        title="POST /bookings/add does not respond within 30 seconds",
        category="Performance", severity="Medium", status=UNCONFIRMED,
        endpoint="POST /bookings/add",
        booking_type="pallet", partner="fedex_priority",
        scenario="Latest: pallet, Argentina -> Saudi Arabia, partner fedex_priority, 2026-10-07 (an earlier pallet run on about 2026-09-23 timed out the same way; partner/route not recorded).",
        expected="A response (success or validation error) well within 30 s.",
        actual="Playwright 'Request timed out after 30000ms' on POST /bookings/add (no response at all).",
        evidence="terminal output of the 2026-10-07 run",
        first_seen="2026-09-23 12:00", last_seen="2026-10-07 15:52", occurrences=2,
        notes="First-seen date approximate. Cause unconfirmed (possibly a carrier lookup). The suite now allows 90 s for this call.",
    ),
    dict(
        key="rate_days_without_amount",
        title="get_delivery_and_rate lists partners under 'days' that have no entry in 'amount'",
        category="Inconsistency", severity="Low", status=SUSPECTED,
        endpoint="POST /bookings/get_delivery_and_rate",
        scenario="Seen on most quotes, e.g. Saudi Arabia -> Ukraine parcel: days = dhl, ups, aramex, fedex; amount = ups, aramex only.",
        expected="Either every partner in 'days' also has a price, or the response says which are unavailable.",
        actual="Partners with delivery days but no price look selectable to an API client; selecting one is then rejected ('Please try again with another delivery partner or change the location', key booking.please_select_delivery_partners) or silently creates an unpriced booking (see add_accepts_unquoted_partner).",
        evidence=".auth/debug_get_delivery_and_rate_parcel_response.json; debug_create_booking_response.json (2026-10-07)",
        first_seen="2026-10-07 15:45", last_seen="2026-10-08 16:44", occurrences=1,
        notes="May be by design (the UI may only use 'amount'). Verify with the app owner.",
    ),
    dict(
        key="wallet_amount_html",
        title="API returns HTML markup and locale-formatted text in a data field (wallet_amount)",
        category="Inconsistency", severity="Low", status=CONFIRMED,
        endpoint="POST /bookings/add (response)",
        scenario="Any create-booking response.",
        expected="A numeric value (as in wallet_data).",
        actual='"wallet_amount": "<img width=\\"15px\\" height=\\"15px\\" src=\\"https://liveroutech.demoe2.com/frontend/images/sar_symbol.svg\\" alt=\\"img\\"/>1,87,546.78" - presentation HTML and Indian digit grouping inside an API field, while wallet_data holds the clean number.',
        evidence=".auth/debug_create_booking_response.json",
        first_seen="2026-09-18 12:00", last_seen="2026-10-08 16:44", occurrences=1,
    ),
    dict(
        key="name_hyphen_rejected",
        title="Receiver name rejects hyphens and apostrophes",
        category="Validation", severity="Low", status=NEEDS_DECISION,
        endpoint="POST /bookings/add (stakeholder_name)",
        scenario='Receiver name "Ahmed Al-Saud".',
        expected="Common real-world name characters (hyphen, apostrophe) accepted.",
        actual='Rejected: "Please enter a valid name using only letters and numbers. Special characters are not allowed, and the name must be within 200 characters." (key user.invalid_name).',
        evidence=".auth/debug_create_booking_response.json (2026-09-22)",
        first_seen="2026-09-22 12:00", last_seen="2026-09-22 12:00", occurrences=1,
        notes="May be intended. Confirm with product.",
    ),
    dict(
        key="error_echoes_request",
        title="Error responses echo the whole request, including the CSRF token and internal user id",
        category="Info", severity="Info", status=SUSPECTED,
        endpoint="POST /bookings/add (error response)",
        scenario="Any validation error.",
        expected="Only the error details.",
        actual="'req_data' repeats the full request: _csrf token, user_id, source_name, every booking field.",
        evidence=".auth/debug_create_booking_response.json",
        first_seen="2026-09-22 12:00", last_seen="2026-10-08 16:44", occurrences=1,
        notes="Low-risk hardening point (the data is returned to the same caller).",
    ),
    # Observed before the availability rule existed -> cannot yet separate "server problem" from "we chose a
    # partner with no quote". The suite now gates on quotes and tracks coverage per booking type, so a
    # retest will confirm or clear these.
    dict(
        key="stuck_after_payment|parcel|fedex_express",
        title="Parcel booked with Fedex Express stays on 'Requested' after payment",
        category="Bug", severity="High", status=UNCONFIRMED,
        endpoint="Booking lifecycle",
        booking_type="parcel", partner="fedex_express",
        scenario="Parcel, pickup Saudi Arabia (Saudi-Singapore/India/US style routes).",
        expected="Reaches 'Shipment Submitted' / 'Sent to Carrier'.",
        actual="Status 'Requested' when polled after payment.",
        evidence="reports/booking_report.xlsx (2 rows, Fedex Express, Requested)",
        first_seen="2026-09-21 17:00", last_seen="2026-10-02 12:00", occurrences=2,
        notes="At that time we did not check whether the partner had a quote on that route. Retest with the quote-gated suite.",
    ),
    dict(
        key="stuck_after_payment|parcel|fedex_priority",
        title="Parcel booked with Fedex Priority stays on 'Requested' after payment",
        category="Bug", severity="High", status=UNCONFIRMED,
        endpoint="Booking lifecycle",
        booking_type="parcel", partner="fedex_priority",
        scenario="Parcel, pickup Saudi Arabia (e.g. Saudi-France).",
        expected="Reaches 'Shipment Submitted' / 'Sent to Carrier'.",
        actual="Status 'Requested' when polled after payment.",
        evidence="reports/booking_report.xlsx (2 rows, Fedex Priority, Requested)",
        first_seen="2026-09-21 17:00", last_seen="2026-10-02 12:00", occurrences=2,
        notes="Quote availability was not checked at that time. Retest.",
    ),
    dict(
        key="stuck_after_payment|parcel|fedex_connect_plus",
        title="Parcel booked with Fedex Connect Plus stays on 'Requested' after payment",
        category="Bug", severity="High", status=UNCONFIRMED,
        endpoint="Booking lifecycle",
        booking_type="parcel", partner="fedex_connect_plus",
        scenario="Parcel, pickup Saudi Arabia.",
        expected="Reaches 'Shipment Submitted' / 'Sent to Carrier'.",
        actual="Status 'Requested' when polled after payment.",
        evidence="reports/booking_report.xlsx (1 row, Fedex Connect Plus, Requested)",
        first_seen="2026-09-21 17:00", last_seen="2026-10-02 12:00", occurrences=1,
        notes="Quote availability was not checked at that time. Retest.",
    ),
]


class BugReport:
    """Edits reports/bug_report.xlsx in place; one row per Dedupe Key."""

    def __init__(self, path: Path = None):
        self.path = Path(path or BUG_REPORT_PATH)
        self._pending = False
        self._session_keys = set()
        if self.path.exists():
            self.wb = load_workbook(self.path)
            self.ws = self.wb.active
            self._map_header()
        else:
            self.wb = Workbook()
            self.ws = self.wb.active
            self.ws.title = "Bugs"
            self._write_header()
            for bug in KNOWN_BUGS:
                self._insert(bug)
            self._pending = True
            self.save()

    # -- layout ------------------------------------------------------------
    def _write_header(self):
        self.ws.append(COLUMNS)
        for i, width in enumerate(WIDTHS, start=1):
            self.ws.column_dimensions[self.ws.cell(row=1, column=i).column_letter].width = width
        for cell in self.ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="305496")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        self.ws.freeze_panes = "C2"
        self.ws.auto_filter.ref = f"A1:{self.ws.cell(row=1, column=len(COLUMNS)).column_letter}1"
        self._map_header()

    def _map_header(self):
        self.col = {str(c.value): c.column for c in self.ws[1] if c.value}
        for name in COLUMNS:  # a column the user deleted/renamed is re-added on the right
            if name not in self.col:
                self.col[name] = self.ws.max_column + 1
                self.ws.cell(row=1, column=self.col[name], value=name)

    def _find_row(self, key: str) -> Optional[int]:
        kc = self.col["Dedupe Key"]
        for r in range(2, self.ws.max_row + 1):
            if self.ws.cell(row=r, column=kc).value == key:
                return r
        return None

    def _next_id(self) -> str:
        nums = []
        for r in range(2, self.ws.max_row + 1):
            v = str(self.ws.cell(row=r, column=self.col["Bug ID"]).value or "")
            if v.startswith("BUG-") and v[4:].isdigit():
                nums.append(int(v[4:]))
        return f"BUG-{(max(nums) + 1) if nums else 1:03d}"

    def _style_row(self, r: int, severity: str):
        for c in range(1, len(COLUMNS) + 1):
            self.ws.cell(row=r, column=c).alignment = Alignment(vertical="top", wrap_text=True)
        fill = _SEVERITY_FILL.get(severity)
        if fill:
            self.ws.cell(row=r, column=self.col["Severity"]).fill = PatternFill("solid", fgColor=fill)

    # -- writing -----------------------------------------------------------
    def _insert(self, bug: dict) -> str:
        r = self.ws.max_row + 1
        bug_id = self._next_id()
        values = {
            "Bug ID": bug_id, "Title": bug["title"], "Category": bug.get("category", "Bug"),
            "Severity": bug.get("severity", "Medium"), "Status": bug.get("status", SUSPECTED),
            "Endpoint / Area": bug.get("endpoint", ""), "Booking Type": bug.get("booking_type", ""),
            "Delivery Partner": bug.get("partner", ""), "Scenario / Route": bug.get("scenario", ""),
            "Expected": bug.get("expected", ""), "Actual": bug.get("actual", ""),
            "Evidence": bug.get("evidence", ""), "First Seen": bug.get("first_seen") or _now(),
            "Last Seen": bug.get("last_seen") or _now(), "Occurrences": bug.get("occurrences", 1),
            "Notes": bug.get("notes", ""), "Dedupe Key": bug["key"],
        }
        for name, value in values.items():
            self.ws.cell(row=r, column=self.col[name], value=value)
        self._style_row(r, values["Severity"])
        return bug_id

    def log(self, key: str, once_per_session: bool = False, **bug) -> str:
        """Add a bug, or bump an existing one (Occurrences/Last Seen + latest scenario/actual/evidence)."""
        bug["key"] = key
        row = self._find_row(key)
        if row is None:
            bug_id = self._insert(bug)
            self._session_keys.add(key)
        else:
            bug_id = self.ws.cell(row=row, column=self.col["Bug ID"]).value
            if not (once_per_session and key in self._session_keys):
                oc = self.ws.cell(row=row, column=self.col["Occurrences"])
                oc.value = int(oc.value or 0) + 1
                self.ws.cell(row=row, column=self.col["Last Seen"], value=_now())
            for field, name in (("scenario", "Scenario / Route"), ("actual", "Actual"), ("evidence", "Evidence")):
                if bug.get(field):
                    self.ws.cell(row=row, column=self.col[name], value=bug[field])
            self._session_keys.add(key)
        self._pending = True
        self.save()
        return bug_id

    def save(self):
        if not self._pending:
            return
        try:
            self.path.parent.mkdir(exist_ok=True)
            self.wb.save(self.path)
            self._pending = False
        except PermissionError:
            print(f"[bug-report] {self.path.name} is open in Excel - close it. Entries are kept and will be saved on the next log or at exit.")


_REPORT: Optional[BugReport] = None


def get_bug_report() -> BugReport:
    global _REPORT
    if _REPORT is None or Path(_REPORT.path) != Path(BUG_REPORT_PATH):
        _REPORT = BugReport(BUG_REPORT_PATH)
    return _REPORT


def log_bug(key: str, **bug) -> Optional[str]:
    """Module-level helper used by the tests. Never raises."""
    try:
        bug_id = get_bug_report().log(key, **bug)
        print(f"[bug-report] {bug_id}  {bug.get('title', key)[:80]}  ({bug.get('status', SUSPECTED)})")
        return bug_id
    except Exception as exc:  # noqa: BLE001 - logging must never break a test
        print(f"[bug-report] could not log {key!r}: {exc}")
        return None


@atexit.register
def _save_on_exit():
    if _REPORT is not None:
        try:
            _REPORT.save()
        except Exception:  # noqa: BLE001
            pass
