"""
RoutechAPIClient
================
Thin wrapper around Playwright's `APIRequestContext`, seeded from the
storage_state captured by the one-time UI login (see core/auth.py).

Why Playwright's request context instead of `requests`/`httpx`:
- It's created FROM the same storage_state as the UI session, so the
  session cookie + _csrf cookie are reused automatically -- no manual
  cookie-jar plumbing.
- It automatically absorbs any Set-Cookie headers returned by the API
  (useful since CSRF cookies can rotate).
- Same library as any UI steps we still need (e.g. captcha login), so the
  whole framework has one dependency instead of two.

IMPORTANT / things confirmed by live capture (2026-09-18 session):
- Auth = session cookie (`session`) + `_csrf` cookie, NOT a bearer token.
- The `_csrf` **cookie** value and the `_csrf` **form field** value submitted
  on POSTs are DIFFERENT strings (secret+token pattern, not double-submit).
  The form-field token must be scraped fresh from a rendered page
  (e.g. GET /bookings/add) before each state-changing POST.
- `/bookings/add` (create booking) and `/bookings/make_ppd_payment` are
  submitted as multipart/form-data, with several fields (`booking_detail`,
  `payment_array`) being a **JSON string**, not nested form fields.
- Response bodies for /bookings/add, /bookings/get_delivery_and_rate,
  /bookings/make_ppd_payment, /bookings/get_hs_codes were NOT captured in
  the recon session (DevTools buffer expired before we read them). The
  parsing in this client is written defensively (see `_safe_json`) and
  will need a small adjustment pass the first time it's actually run
  against the live app -- see handoff.md "Open Items".
"""
import json
import re
from typing import Optional

from playwright.sync_api import sync_playwright, APIRequestContext

from config import settings
from utils.html_parsing import extract_csrf_token, parse_pickup_locations, extract_status_badge


class RoutechAPIClient:
    def __init__(self, storage_state_path):
        self._playwright = sync_playwright().start()
        self.request: APIRequestContext = self._playwright.request.new_context(
            base_url=settings.BASE_URL,
            storage_state=str(storage_state_path),
            extra_http_headers={
                "x-requested-with": "XMLHttpRequest",
                "accept": "*/*",
            },
        )
        self._csrf_token: Optional[str] = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def close(self):
        self.request.dispose()
        self._playwright.stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _refresh_csrf_token(self) -> str:
        """
        Scrapes a fresh `_csrf` form-field token from a rendered page.
        Called once per client instance and re-called if a mutating POST
        comes back 403 (token likely rotated/expired).
        """
        resp = self.request.get("/bookings/add")
        self._check_ok(resp)
        html = resp.text()
        token = extract_csrf_token(html)
        if not token:
            raise RuntimeError(
                "Could not scrape _csrf token from /bookings/add response. "
                "Page markup may have changed -- inspect utils/html_parsing.extract_csrf_token."
            )
        self._csrf_token = token
        return token

    def csrf_token(self, force_refresh: bool = False) -> str:
        if force_refresh or not self._csrf_token:
            return self._refresh_csrf_token()
        return self._csrf_token

    @staticmethod
    def _safe_json(resp):
        """
        Parses JSON defensively and raises a clear error (with the raw body
        attached) if the shape isn't what we expect -- much easier to debug
        on first real run than a bare KeyError.
        """
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Expected JSON response from {resp.url}, got status={resp.status}, "
                f"body[:500]={resp.text()[:500]!r}"
            ) from exc

    @staticmethod
    def _check_ok(resp):
        """
        Playwright's APIResponse has no `.raise_for_status()` (that's a
        requests/httpx method) -- it exposes `.ok` (bool) and `.status`
        (int) instead. This is the equivalent: raises with a useful message
        (URL, status, response body) on any non-2xx response.
        """
        if not resp.ok:
            raise RuntimeError(
                f"Request to {resp.url} failed: status={resp.status} {resp.status_text}, "
                f"body[:500]={resp.text()[:500]!r}"
            )

    # ------------------------------------------------------------------
    # booking form / reference data
    # ------------------------------------------------------------------
    def get_booking_form(self, booking_type: str = "parcel", no_of_booking: int = 0) -> str:
        """
        POST /bookings/booking_form
        Returns the raw HTML fragment for the given booking type. Use
        utils.html_parsing.parse_pickup_locations() on the result to get
        structured saved-location data.
        """
        resp = self.request.post(
            "/bookings/booking_form",
            form={"no_of_booking": str(no_of_booking), "booking_type": booking_type},
        )
        self._check_ok(resp)
        return resp.text()

    def get_saved_locations(self, booking_type: str = "parcel") -> list[dict]:
        html = self.get_booking_form(booking_type=booking_type)
        return parse_pickup_locations(html)

    def get_hs_codes(self, item_name: str) -> list[dict]:
        """
        GET /bookings/get_hs_codes?search=X&item_name=X
        Response shape unconfirmed -- adjust once we see a real payload.
        Expected: a list of {code, description} or similar.
        """
        resp = self.request.get(
            "/bookings/get_hs_codes",
            params={"search": item_name, "item_name": item_name},
        )
        self._check_ok(resp)
        return self._safe_json(resp)

    def get_delivery_and_rate(self, payload: dict) -> dict:
        """
        POST /bookings/get_delivery_and_rate
        `payload` should match the form fields captured live, e.g.:
            {
                "type": "b2c",
                "pickup_city": ..., "dropoff_city": ...,
                "pickup_country": ..., "dropoff_country": ...,
                "pickup_country_code": ..., "dropoff_country_code": ...,
                "dropoff_postal_code": ..., "pickup_postal_code": ...,
                "pickup_state_code": ..., "dropoff_state_code": ...,
                "is_international": "1",
                "delivery_reg_same_type": "regular",
                "payment_type": "ppd",
                "shipment_content_type": "dry",
                "weight_per_kg": "",
                "package_dimension[0][length]": ...,
                "package_dimension[0][width]": ...,
                "package_dimension[0][height]": ...,
                "package_dimension[0][actual_weight]": ...,
                "package_dimension[0][weight]": ...,          # dimensional weight, server-ish computed client-side
                "package_dimension[0][higher_weight]": ...,
                "offer_code": "",
                "booking_type": "parcel",
                "is_palletized": "false",
            }
        Returns parsed JSON: expected to contain per-partner rate keys like
        `delivery_rate_ups`, `delivery_rate_fedex`, etc. (mirrors the field
        names seen in the final /bookings/add payload).
        """
        resp = self.request.post("/bookings/get_delivery_and_rate", form=payload)
        self._check_ok(resp)
        return self._safe_json(resp)

    # ------------------------------------------------------------------
    # booking creation
    # ------------------------------------------------------------------
    def create_booking(self, booking_detail: dict, is_warehouse: bool = False) -> dict:
        """
        POST /bookings/add  (multipart/form-data)

        `booking_detail` is a single booking's full dict (see
        data/booking_payloads.py for the field reference / builder). This
        method wraps it in the `[...]` list the server expects and JSON-
        encodes it, matching the exact captured request:

            booking_detail=[{...}]   (as a JSON *string* multipart field)

        Returns parsed JSON. Based on the payment call needing
        `booking_id`, `ppd_amount`, and `unique_id`, the response is
        expected to contain at least those keys -- CONFIRM on first run
        and adjust `create_booking`/`make_ppd_payment` glue if the actual
        key names differ.
        """
        token = self.csrf_token()
        multipart = {
            "is_warehouse": "true" if is_warehouse else "false",
            "_csrf": token,
            "booking_detail": json.dumps([booking_detail]),
            "delivery_partner_track_0": "1",
            "is_booking_confirm": "true",
        }
        resp = self.request.post("/bookings/add", multipart=multipart)
        if resp.status == 403:
            # csrf token likely stale -- refresh once and retry.
            token = self.csrf_token(force_refresh=True)
            multipart["_csrf"] = token
            resp = self.request.post("/bookings/add", multipart=multipart)
        self._check_ok(resp)
        return self._safe_json(resp)

    def make_ppd_payment(
        self,
        create_response: dict,
        is_wallet: bool = True,
        pay_online: bool = False,
    ) -> dict:
        """
        POST /bookings/make_ppd_payment  (multipart/form-data)

        CONFIRMED via a real captured create_booking response (2026-09-18):
        the response already contains a ready-made `payment_array`, e.g.:

            "payment_array": [{
                "booking_id": "...", "ppd_amount": 861.5295,
                "unique_id": "815536", "booking_type": "parcel",
                "wallet_amount": 14313.78
            }]

        ...plus a top-level `wallet_data` (clean float, the account's
        current wallet balance -- NOT the amount charged). We pass
        `payment_array` straight through rather than reassembling it by
        hand. `amount` (the actual charge) = payment_array[0]['ppd_amount'].
        `wallet_amount` (top-level form field) = `wallet_data`.

        NOTE: top-level `status` on the create_booking response is
        confirmed to read "error" even for a fully successful booking
        creation (no `message`/`req_data` present in that case) -- this is
        apparently how the server flags "booking created, payment not yet
        completed", not an API failure. See handoff.md.
        """
        payment_array = create_response.get("payment_array")
        if not payment_array:
            raise RuntimeError(
                "create_booking response has no 'payment_array' -- "
                f"full response: {create_response!r}"
            )
        entry = payment_array[0]
        wallet_amount = create_response.get("wallet_data", entry.get("wallet_amount"))
        amount = entry.get("ppd_amount")

        multipart = {
            "is_wallet": "true" if is_wallet else "false",
            "pay_online": "1" if pay_online else "0",
            "wallet_amount": str(wallet_amount),
            "amount": str(amount),
            "payment_array": json.dumps(payment_array),
            "payment_type": json.dumps(["wallet"] if is_wallet else ["online"]),
        }
        resp = self.request.post("/bookings/make_ppd_payment", multipart=multipart)
        self._check_ok(resp)
        return self._safe_json(resp)

    # ------------------------------------------------------------------
    # verification
    # ------------------------------------------------------------------
    def get_booking_details_html(self, booking_id: str) -> str:
        """
        GET /bookings/get_booking_details/:id
        Returns raw HTML (this endpoint is not JSON). Useful for asserting
        booking number / status / echoed fields post-creation.
        """
        resp = self.request.get(f"/bookings/get_booking_details/{booking_id}")
        self._check_ok(resp)
        return resp.text()

    def booking_number_from_details(self, booking_id: str) -> Optional[str]:
        """Convenience: pulls the bold booking-number string out of the details HTML."""
        html = self.get_booking_details_html(booking_id)
        match = re.search(r"<strong>(\d{5,})</strong>", html)
        return match.group(1) if match else None

    def booking_status_from_details(self, booking_id: str) -> Optional[str]:
        """Convenience: extracts the booking's status text (e.g. 'Shipment Submitted')."""
        html = self.get_booking_details_html(booking_id)
        return extract_status_badge(html)
