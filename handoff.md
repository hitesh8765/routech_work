# Routech Automation — Project Handoff

Read this file top to bottom before touching the code. It captures the
full context, decisions, and rules established during the walkthrough so
any AI/dev picking this up can continue without re-deriving everything.

---

## 1. What this is

Hybrid **UI + API** test automation framework for **Routech**, a B2B/C2C
shipping/booking demo application.

- **Site:** `https://liveroutech.demoe2.com`
- **Stack:** Python + Playwright + pytest (chosen: Playwright's
  `APIRequestContext` for API calls, NOT `requests`/`httpx` — see §4).
- **Strategy:** Login is UI-only (captcha can't be automated), performed
  **once**, session is captured and reused. All booking creation and
  everything downstream is done via **direct API calls**, not UI clicking.

## 2. Application structure

- Login page (`/login`) has two account types via radio button:
  - **B2B** ("Login as business") — `neeraj@mailinator.com` / `123456`
  - **C2C** ("Login as individual") — `bharti@mailinator.com` / `Test@123`
  - Login has a Google reCAPTCHA — **must be solved by a human**.
- After login → `/dashboard`.
- Left nav: Dashboard, Bookings, Bulk Booking, Notifications, Sub-Account,
  Manifest List, Reports, Wallet, Offers, Membership Plans, Marketplaces.
- `/bookings` — lists all bookings (card per booking: number, receiver,
  delivery partner, time, pickup/dropoff, status). Click a card for tabs:
  Order Details / Partner Detail / Documents / Customer Info / Item List.
- `/bookings/add` — choose booking type: **Parcel, Pallet, Luggage,
  Documents** (all 4 to be automated; only **Parcel** built so far).

## 3. Business rules (apply to ALL booking types unless told otherwise)

1. **Location rule:** for every booking, exactly one of
   {Pickup Location, Dropoff Address} must be in **Saudi Arabia**; the
   other must be non-Saudi. Which side is Saudi should vary across
   bookings (don't always put Saudi on the same side).
2. **Insurance:** always select **No**.
3. **Shipment Invoice:** always **Create Invoice** (never "Upload Invoice").
4. **Item Name:** random from a fixed pool (books, furniture, telephone,
   monitor, keyboard, mouse, fish, sweatshirt).
5. **Item Quantity:** random from {20, 100, 50, 60, 10, 200, 150}.
6. **HS Code:** look up using the **same string as the item name**, then
   pick the top/first suggestion from the site's autocomplete.
7. **Item Price:** random from {100, 200, 500, 1000, 699, 800, 700}.
8. **Actual Weight:** random int in [8, 16] (commonly 10, 15, 12).
9. **Dimensions:** Length > Width > Height, picked as a **whole combo**
   (not independently randomized) from:
   `(14,13,11), (15,12,11), (13,11,9), (15,11,10), (13,10,8)`.
10. **Receiver Mobile Number:** 10 digits, starts with `96________`.
11. **Sender Name / Sender Mobile:** auto-filled by the account — just
    assert non-empty, never fill manually.
12. **Delivery Partner:** any one (test uses UPS).
13. **Payment:** always **Pay By Wallet**.

These rules were given specifically for **Parcel**; confirm with the user
whether they also apply verbatim to Pallet/Luggage/Documents before
assuming so (structurally likely similar, but not yet confirmed).

## 4. Auth mechanism (confirmed via live DevTools capture)

- **Session cookie based**, NOT JWT/bearer token.
- Login: `POST /login/business` (or `/login/individual` for C2C),
  `application/x-www-form-urlencoded`, body includes:
  `user_type`, `_csrf`, `country_code`, `user_name`, `password`,
  `g-recaptcha-response`.
- **Login page control IDs (confirmed via live DOM inspection):**
  - `#user_type_business` / `#user_type_customer` — the two radio inputs
    (note: "customer" in the DOM = C2C/"individual" in the UI copy).
  - `#login-business-btn-id` — the Submit control for **both** forms
    (misleadingly named, but shared). **It's an `<a href="javascript:void(0)">`,
    not a `<button>`** — same JS-click-driven pattern as the booking-type
    tiles (§4 conversation log item 4). `get_by_role("button", name="Submit")`
    silently matches nothing and hangs until timeout; use
    `page.locator("#login-business-btn-id")` instead. This cost a full
    debug cycle — site-wide takeaway: **assume interactive controls here
    are `<a>` tags with JS handlers, not semantic `<button>`s, and prefer
    ID/class selectors over role-based locators** unless you've confirmed
    the actual tag first.
- On success, server sets:
  - `session` cookie (httpOnly, signed, Express `express-session`)
  - `_csrf` cookie
- **Session cookie expiry observed: ~1 hour** (`Expires` header showed
  exactly 1hr after issue). This contradicts an earlier assumption that it
  might not expire — it does. Framework re-logs-in automatically once the
  cached session exceeds `ROUTECH_SESSION_MAX_AGE` (see `config/settings.py`,
  default 55 min to be safe).
- **Important CSRF subtlety:** the `_csrf` **cookie** value and the `_csrf`
  **form field** value submitted on POSTs are **different strings** (this
  is a secret+token CSRF pattern, not simple double-submit). The form
  token must be scraped fresh from a rendered page's hidden
  `<input name="_csrf" value="...">` before each state-changing POST.
  `RoutechAPIClient._refresh_csrf_token()` does this via `GET /bookings/add`.
- All authenticated calls (UI or API) just need the `session` + `_csrf`
  cookies attached — no `Authorization` header of any kind.
- Chose **Playwright's `APIRequestContext`** (via
  `playwright.request.new_context(storage_state=...)`) over `requests`
  so the UI-login session's cookies transfer automatically with zero
  manual cookie-jar code, and any rotated cookies from API responses are
  absorbed automatically too.

## 5. Captured API endpoints (Parcel booking flow)

All under `https://liveroutech.demoe2.com`, all require the session+csrf
cookies from step 4.

| Endpoint | Method | Content-Type | Purpose |
|---|---|---|---|
| `/login/business` | POST | form-urlencoded | Login (B2B) |
| `/login/individual` | POST | form-urlencoded | Login (C2C) — endpoint name inferred, not yet captured live |
| `/bookings/booking_form` | POST | form-urlencoded (`no_of_booking`, `booking_type`) | Returns HTML fragment with the pickup-location `<select>` — every saved business location is embedded as an `<option>` with `data-*` attrs (lat/lng/city/state/country/postal/national_address). **No AJAX call happens per-selection** — it's all pre-loaded client-side data. |
| `/bookings/load_map` | POST | — | Fires when the "Add New Location" map modal opens (for locations NOT already saved). Not yet exercised by the API client — see Open Items. |
| `/bookings/get_hs_codes?search=X&item_name=X` | GET | — | HS code autocomplete. Response shape **not confirmed** (see Open Items). |
| `/bookings/get_delivery_and_rate` | POST | form-urlencoded | Triggered by "Calculate Weight". Takes pickup/dropoff city/country/postal/state, package dimensions, etc. Returns delivery partner quotes. Response shape **not confirmed**. |
| `/bookings/add` | POST | **multipart/form-data** | **Creates the booking.** Fields: `is_warehouse`, `_csrf`, `booking_detail` (JSON-encoded **string**, an array with one object — see §6 for full field list), `delivery_partner_track_0`, `is_booking_confirm`. Response shape **not confirmed**, but must contain enough to drive the payment call (booking id, amount owed, a unique id). |
| `/bookings/make_ppd_payment` | POST | **multipart/form-data** | Pays for the booking. Fields: `is_wallet`, `pay_online`, `wallet_amount`, `amount`, `payment_array` (JSON string with `booking_id`, `ppd_amount`, `unique_id`, `booking_type`, `wallet_amount`), `payment_type` (JSON array string, e.g. `["wallet"]`). Tabby-specific fields exist in the captured payload but are believed optional/omittable when not using Tabby. |
| `/bookings/get_booking_details/:id` | GET | — | Returns an **HTML fragment** (not JSON) with full booking details incl. booking number, status, fare breakdown. Good for post-creation assertions. Confirmed working — booking number `767028` was successfully retrieved this way. |
| `/bookings` (as POST) | POST | — | Appears to be a DataTables-style server-side listing call (pagination/search) for the bookings list page. Not yet reverse-engineered or used by the client. |
| `/notifications/get_header_notifications_counter` | POST | — | Just the bell-icon counter; irrelevant to booking flows, ignore. |

## 6. `booking_detail[0]` field reference (Parcel)

Captured verbatim from a real successful submission. Field → meaning:

```
pickup_location            saved location's id (from booking_form options), or "" for a fresh map-picked location
pickup_address              full address string
business_name               (seen empty in capture; purpose unconfirmed)
pickup_latitude / longitude
pickup_city / state / country / country_code / postal_code / additional_address / national_address
type_partner                 always "b2c" in captures (even for what should be b2b?? — copied as-is, not yet understood, flag if it causes issues)
dropoff_address
is_international             "1" whenever the shipment crosses a border (which, under our Saudi-side rule, is always)
dropoff_latitude / longitude
dropoff_city / state / country / country_code / postal_code / additional_address / national_address
item_weight                  (left empty in captures — distinct from package_dimensions weight)
is_location_unknown          "0" (maps to the "Location Unknown" checkbox)
booking_type                  "parcel" / "pallet" / "luggage" / "documents"
pallet_container_id           (pallet-specific, empty for parcel)
stakeholder_name              *** THIS IS RECEIVER NAME *** (not obviously named!)
country_code                  Receiver's phone country code, e.g. "+966"
stakeholders                  *** THIS IS RECEIVER MOBILE NUMBER ***
sender_name / sender_country_code / sender_mobile_number   auto-filled by account
imported_order_id             Order Id field
insurance                     "0" = No, "1" = Yes  -> always "0" per business rule
stackable                     "1" = Yes (default; no rule overrides this)
weight_per_kg                 (left empty in captures)
payment_type                  "ppd" or "cod"
cod_amount                    empty unless payment_type=cod
schedule_type                 "schedule_now" (On Demand) or presumably a "schedule_later" variant
scheduled_time                empty unless scheduled
currency_code                  "SAR"
currency_value                  "1"
delivery_type                  "regular" or presumably "same_day"
is_invoice_required            "0" observed when "Create Invoice" was selected (i.e. this flag is really "needs file upload", not "has invoice")
shipping_method                 "home" (default, not yet explored — may relate to home vs. drop-point pickup)
shipment_content_type           "dry" (default, not yet explored — likely has other values for other content types)
item_list_details               array of {item_name, item_quantity, item_detail, hs_code, item_price}
quantity                        PACKAGE quantity (top-level "1", separate from item_quantity)
is_offer_apply                  "false"
package_dimensions              array of {actual_weight, length, width, height}
offer_code                       ""
is_palletized                    "false"
delivery_rate_ups / _dhl / _aramex / _fedex / _fedex_priority / _fedex_express /
_fedex_regional / _fedex_connect_plus / _fedex_priority_freight /
_fedex_regional_economy_freight / _fedex_economy_freight /
_dhl_medical_express / _dhl_express_worldwide / _dhl_freight_worldwide /
_dhl_economy_select / _dhl_express_easy / _dhl_express_domestic /
delivery_rate_darb              all the quoted rates from get_delivery_and_rate, echoed back (not just the chosen partner's)
delivery_partners                the CHOSEN partner's key, e.g. "ups"
```

## 7. Framework structure

```
routech-automation/
├── config/settings.py       # base URL, credentials (env-driven), timeouts, storage_state paths
├── core/auth.py             # UI login w/ manual captcha pause; storage_state persistence + freshness check
├── api/client.py            # RoutechAPIClient — all endpoint methods, CSRF handling
├── utils/html_parsing.py    # scrape CSRF token + saved-location options from server-rendered HTML
├── data/booking_payloads.py # business-rule-encoded random data pools + booking_detail builder
├── tests/api/bookings/test_parcel_booking.py   # first working E2E test
├── conftest.py               # b2b_session / c2c_session (session-scoped login) + api_client fixtures
├── pytest.ini
├── requirements.txt
└── .env.example
```

### How a test run works
1. First test needing `api_client` triggers the session-scoped `b2b_session`
   fixture → `core.auth.ensure_logged_in("b2b")`.
2. If no fresh cached session exists (`.auth/b2b_state.json` + its
   `.meta.json` sidecar, within `ROUTECH_SESSION_MAX_AGE`), it launches a
   **visible** (non-headless) browser, fills credentials, and **pauses on
   `input()`** for a human to solve the captcha, then submits and waits
   for `/dashboard`, then saves `storage_state`.
3. Every test gets its own `RoutechAPIClient` built from that same
   storage_state — pure API calls from here on, no browser needed.
4. Session auto-refreshes (re-runs UI login) once it's older than
   `ROUTECH_SESSION_MAX_AGE` (default 55 min, real cookie lifetime ~60 min).

## 8. Open items / things to fix on first real run

The framework was built from a **live DevTools capture**, but several
**response bodies expired from Chrome's network buffer** before they were
read (captured request payloads, not responses, for these):

- `POST /bookings/add` response shape — needed to know the real key for
  `booking_id`, and the amount/unique_id fields needed by the payment call.
  `_extract_booking_id()` / `_extract_payment_fields()` in
  `test_parcel_booking.py` guess common key names and will need adjusting.
- `POST /bookings/get_delivery_and_rate` response shape — needed to know
  the real per-partner rate key names (assumed to mirror the
  `delivery_rate_*` field names used in the final submit, but unconfirmed).
- `POST /bookings/make_ppd_payment` response shape — used only for a
  smoke assertion right now (`is not None`), not a hard schema check yet.
- `GET /bookings/get_hs_codes` response shape — `_first_hs_code()` guesses
  a `{code: ...}`-ish list shape.

**Fix strategy:** run `test_create_parcel_booking` once, `print()` (or
debugger) each raw response the first time it's hit, then tighten the
extraction helpers and `RoutechAPIClient` docstrings to match reality.
This should be a quick pass, not a redesign — the request side is solid.

**Confirmed since (live run, first real test execution):**
- `/bookings/add` **error** response shape:
  `{'status': 'error', 'message': [{'param', 'msg', 'key'}, ...], 'req_data': {...full echoed request, WITH a server-generated booking_id already nested inside req_data.booking_detail[0].booking_id...}}`.
  Strong signal the **success** shape nests `booking_id` the same way
  (`result.booking_detail[0].booking_id`) — extraction code now checks
  both the flat level and this nested path.
- `stakeholder_name` (Receiver Name) validation: **letters and numbers
  only, no special characters** — a name like "Ahmed Al-Saud" (hyphen)
  is rejected with `user.invalid_name`. `RECEIVER_NAME_POOL` in
  `data/booking_payloads.py` was fixed accordingly (no hyphens/apostrophes).
- `get_hs_codes` confirmed shape: `{'status': 'success', 'result': [{'id': '<hs code>', ...}, ...]}`.

**Open hypothesis (unconfirmed) — Saudi dropoff using a saved location fails:**
When `saudi_side="dropoff"`, reusing one of the account's own **saved
pickup-location** entries verbatim as the dropoff produced:
`dropoff_address: "Please enter valid location."` — while the exact same
approach with a non-Saudi (India) dropoff succeeded. Working theory: the
server may reject a dropoff address that exactly matches one of the
account's own registered pickup/business locations (can't ship to your
own address?). NOT yet confirmed — needs either (a) a live recon of a
real UI booking with an international pickup + Saudi dropoff to diff the
payload, or (b) trying a Saudi dropoff address that ISN'T one of the
saved pickup locations (e.g. via the `/bookings/load_map` new-location
flow instead of reusing `get_saved_locations()` for both sides). Until
resolved, treat `saudi_side="pickup"` as the reliably-working path.

**Not yet built:**
- `/bookings/load_map` flow (fresh/international location via Google Maps
  pin-drop) — current tests only use the account's already-saved locations
  for both the Saudi and non-Saudi side, which satisfies the business rule
  without needing to reverse-engineer Google Maps geocoding calls. Revisit
  if we ever need a genuinely new (not-yet-saved) address in a test.
- C2C flow — endpoint name assumed (`/login/individual`), not yet
  confirmed live.
- Pallet / Luggage / Documents booking types — only Parcel is done.
- Bulk Booking, Manifest List, Sub-Account, Wallet, Offers — untouched.

## 9. Environment notes

- Copy `.env.example` → `.env` and fill in (or rely on the defaults, which
  match the demo credentials already given).
- `ROUTECH_UI_HEADLESS=0` is required for the captcha step to be solvable
  by a human — do not flip to headless unless a captcha-bypass exists for
  this demo instance.
- `pip install -r requirements.txt && playwright install chromium` before
  first run.
- Run: `pytest -m parcel` for just the parcel suite, or `pytest` for
  everything.

## 10. Conversation log summary (chronological)

1. Established ground rules: walkthrough first, build later, stack is
   Playwright+Python+pytest, tools available: playwright, fetch-api,
   filesystem, chrome-devtools.
2. Walked the B2B login screen, credentials for B2B/C2C, captcha-pause
   requirement.
3. Walked Dashboard → Bookings → Add Booking → 4 booking types.
4. Inspected DOM: booking-type tiles are `<a data-box-type="parcel">` with
   `href="javascript:void(0)"` — **JS-click-driven, no real navigation
   link**; confirmed later that a genuine trusted click (or `el.click()`)
   is required — a raw CDP `Input.dispatchMouseEvent`-based click
   (chrome-devtools MCP `click` tool) did **not** reliably trigger it in
   this recon session; `evaluate_script` calling `.click()` directly did.
   Flag this if Playwright's own `.click()` ever seems to no-op on this
   element — may need a JS-dispatched fallback.
5. Walked the full Parcel booking form field-by-field with screenshots
   (pickup location combobox + "Add New Location" map modal, dropoff
   address, receiver/sender, insurance, invoice type, item details,
   package detail, delivery partners, confirm checkbox, packaging
   guidelines popup, payment methods popup, thank-you page, bookings list
   reflecting the new entry) — all captured into the business rules in §3.
6. User set the business rules in §3 explicitly (Saudi-side alternation,
   insurance=No, Create Invoice, random pools for item/qty/price,
   weight/dimension rules, receiver mobile format).
7. First live recon session (chrome-devtools, me driving solo): logged in,
   confirmed session-cookie auth (§4), started a Parcel booking, discovered
   the `/bookings/booking_form` endpoint pre-loads all saved locations
   client-side (no per-selection AJAX), extracted the full form field-name
   map via `evaluate_script` (§6 field list came from here).
8. Second live session (user driving UI manually, me watching Network
   tab): user completed a full real Parcel booking end-to-end. I captured
   the full endpoint sequence (§5) and every request payload, but response
   bodies for several calls had already aged out of DevTools' buffer by
   the time I read them (see §8 Open Items) — lesson for next recon
   session: read response bodies **immediately** after each call, not
   after the whole flow finishes.
9. User asked to scaffold the framework now, defer Pallet/Luggage/
   Documents walkthroughs to later. This file + the code in this repo is
   the result of that request.

## 11. For the next session

If the user says "now build automation" again for a **new** booking type
(Pallet/Luggage/Documents), or asks to fix the Open Items above:
- Re-read this whole file first.
- For Open Items: the fastest path is running the existing test once
  against the live site and reading real responses, NOT another full
  DevTools recon session.
- For new booking types: same recon pattern as §5 (watch Network tab
  while user performs the booking manually), then extend
  `data/booking_payloads.py` with a new builder + `api/client.py` if any
  new endpoints appear, following the exact same structure as the Parcel
  implementation.
