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
read (captured request payloads, not responses, for these). Status as of
the first real live test runs (2026-09-18):

- ~~`POST /bookings/add` response shape~~ **CONFIRMED** — see §4 above.
  `_extract_booking_id()` now uses the confirmed `booking_detail[0].booking_id`
  path; `make_ppd_payment()` now consumes the response's own `payment_array`
  directly instead of reassembling fields by hand.
- ~~`GET /bookings/get_hs_codes` response shape~~ **CONFIRMED**:
  `{'status': 'success', 'result': [{'id': '<hs code>', ...}, ...]}`.
- `POST /bookings/get_delivery_and_rate` response shape — **still
  unconfirmed**. Needed to know the real per-partner rate key names
  (assumed to mirror the `delivery_rate_*` field names used in the final
  submit, but this is still a guess). Notably, the confirmed
  `create_booking` payload showed every `delivery_rate_*` field submitted
  as an **empty string** even for a successful booking — so this data may
  not actually matter much for a happy-path test; worth confirming but
  lower priority now.
- `POST /bookings/make_ppd_payment` response shape — **still unconfirmed**.
  Currently only smoke-asserted (`is not None`) in the test. Next thing to
  pin down once the payment call itself is confirmed to succeed end-to-end.

**Fix strategy:** run `test_create_parcel_booking` once, `print()` (or
debugger) each raw response the first time it's hit, then tighten the
extraction helpers and `RoutechAPIClient` docstrings to match reality.
This should be a quick pass, not a redesign — the request side is solid.

**Confirmed since (live run, first real test execution):**
- **`/bookings/add` SUCCESS response shape (fully confirmed via a real
  successful booking, 2026-09-18):** flat JSON (no `result`/`data`
  wrapper), including:
  - `booking_detail[0].booking_id` — the reliable booking id path (also
    present, pre-generated, even on validation-error responses).
  - **`payment_array`** — a server-pre-built list ready to pass straight
    into `/bookings/make_ppd_payment`'s `payment_array` field, e.g.
    `[{"booking_id", "ppd_amount", "unique_id", "booking_type", "wallet_amount"}]`.
    **No need to reassemble this by hand** — `RoutechAPIClient.make_ppd_payment()`
    now takes the whole `create_booking()` response and reuses this array
    directly.
  - `wallet_data` — clean float, the account's *current total wallet
    balance* (NOT the amount owed for this booking). This is what the
    payment call's top-level `wallet_amount` form field should be.
  - The amount actually charged for the booking is
    `payment_array[0].ppd_amount` (maps to the payment call's `amount`
    field).
  - **Top-level `status` can read `"error"` even on a fully successful
    booking creation** (booking_id, full booking_detail, payment_array
    all present) — this apparently just means "booking created, payment
    not yet completed", not an API failure. The ONLY reliable error
    signal is `status == "error"` **combined with** a `message` and/or
    `req_data` key being present (those two only appear on genuine
    validation failures). `_raise_if_error_status()` in the test file
    encodes this distinction.
  - Other fields present: `goods_value`, `base_goods_value`,
    `vat_percentage`, `insurance_amount`, `platform_fees`, `vat_amount`,
    `fuel_surcharge_amount`, `fuel_surcharge_details`, `tabby_payments`
    (Tabby installment plan details, irrelevant to our wallet-payment path),
    `offer_details`, `isDomesticBooking`.
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

**Saudi dropoff using a saved location — RESOLVED (2026-09-18):** earlier
suspected as a possible "can't ship to your own saved pickup address"
rule (a `saudi_side="dropoff"` run produced `dropoff_address: "Please
enter valid location."`), but that error was reported *alongside* a
`stakeholder_name` validation error in the same response. Once
`RECEIVER_NAME_POOL` was fixed (see above), a full `pytest -m parcel -s`
run passed **both** `pickup` and `dropoff` cases end-to-end — reusing a
saved pickup location as the dropoff address is confirmed working for
both directions. (Still not 100% certain whether the location error was
a red herring caused by the multi-error response, or a real transient
issue — if "please enter valid location" ever resurfaces on its own,
revisit this.)

**🎉 First fully working E2E Parcel booking flow confirmed (2026-09-18):**
`pytest -m parcel -s` — 2 passed. Full chain verified live: one-time UI
login w/ captcha → cached session reuse → saved locations → HS code
lookup → delivery rate quote → booking creation → wallet payment →
booking-details verification. Both Saudi-pickup and Saudi-dropoff
directions pass.

**Booking status semantics — CONFIRMED by user directly (2026-09-18):**
- **Success** (booking went through fine): `"Sent to Carrier"`, `"Shipment Submitted"`
- **Failure** (something went wrong post-creation): `"In Transit"`, `"Pickup Fail"`, `"Fail"`
- Note: "In Transit" being a *failure* only makes sense as an immediate
  post-creation sanity check — obviously a real shipment legitimately
  being "In Transit" later in its life is normal. Don't reuse this
  classification for anything other than "did this booking we JUST
  created land correctly".
- Encoded in `reporting/excel_report.py` as `SUCCESS_STATUSES` /
  `FAILURE_STATUSES` / `is_success_status()`; `test_parcel_booking.py`
  now asserts on this after every booking, not just logs it.
- `extract_status_badge()` in `utils/html_parsing.py` (badge/status CSS
  class detection + known-string fallback) was confirmed working
  correctly on the first real run — no fix needed there.

**Reporting: `reporting/excel_report.py` (added 2026-09-18):** every test
now logs a row to `reports/booking_report.xlsx` after verification —
columns: Booking Type, Booking Number, Route, Delivery Partner, Status,
Created At — matching a sample report format the user provided. Re-running
the suite appends to the same file (loads existing rows first) rather than
overwriting. Wired in via a session-scoped `booking_report` fixture in
`conftest.py` (saved once at session end, so a mid-run failure doesn't
lose earlier rows). `country_label()` abbreviates "Saudi Arabia" → "Saudi"
in the Route column specifically, confirmed from the user's sample.
`booking_type_label()` builds e.g. `"B2C Parcel (Web)"` from
`type_partner` + `booking_type` (channel hardcoded to "Web" since we only
automate the web/API path). Reuse these helpers for Pallet/Luggage/
Documents rather than duplicating the labeling logic per booking type.

**Pallet booking type — CONFIRMED working (2026-09-21):** live recon
session (user drove the real UI manually, framework watched DevTools) then
`tests/api/bookings/test_pallet_booking.py` built and passing. Key finding:
Pallet's `/bookings/add` payload is **structurally identical to Parcel's**
— same field names throughout, same endpoints
(`booking_form`/`get_hs_codes`/`get_delivery_and_rate`/`make_ppd_payment`/
`get_booking_details`), only difference is `booking_type: "pallet"` and the
absence of `pallet_container_id` (that field turned out NOT to be
pallet-specific despite the name — real capture confirms Pallet bookings
don't send it at all; harmless either way since it's just an extra empty
field). `data/booking_payloads.py` was refactored: `build_booking_detail()`
is now the shared implementation, with `build_parcel_booking_detail()` and
`build_pallet_booking_detail()` as thin per-type wrappers — reuse this
pattern for Luggage/Documents rather than writing a new builder from
scratch, unless recon reveals real structural differences.

Also observed live: `sender_country_code` varies by pickup location in the
real UI (e.g. `"+61"` for an Australia pickup) rather than the `"+966"` we
hardcode — but our tests already pass reliably with the hardcoded value
across multiple country pairs, so this was deliberately left as-is (not
worth the complexity of a country→dial-code mapping for something that
isn't blocking anything). `get_delivery_and_rate` was also observed to
fire twice in one real booking and to be accompanied by 1-3
`POST /bookings/get_item_weights` calls — neither appears in the actual
`/bookings/add` payload, so neither was implemented; they look like
supplementary/informational calls, not required for booking creation.

**Diversity rules — SUPERSEDED/refined by the user (2026-09-22), stricter
than the original 2026-09-21 version:** the rules now apply PER BOOKING,
not per booking type. Confirmed directly by the user:
- Every booking must use a genuinely different pickup/dropoff **location**
  (not just a different country) than recent bookings — prefer well-known/
  popular places (illustrative example given: Tokyo). We can't
  algorithmically judge "popularity" of a saved location, so this picks
  randomly among not-recently-used ones from the account's existing saved
  locations — **if the user wants genuinely new popular-city locations
  added (not just cycling the account's existing saved list), that needs
  the `/bookings/load_map` geocoding flow implemented — currently not
  built, see below.**
- Non-Saudi location reuse cooldown: ~15-20 other distinct non-Saudi
  locations used first (implemented as 17).
- Saudi location reuse cooldown: ~6-8 other distinct Saudi locations used
  first (implemented as 7).
- Saudi side (pickup/dropoff) must **strictly alternate**, globally, across
  ALL bookings regardless of type — NOT random. If booking N had Saudi as
  pickup, booking N+1 (whatever type) must have Saudi as dropoff.
- A different delivery partner every single booking (not just per type) —
  round-robin through every partner before any repeat.
- **DHL (plain "DHL" AND every `dhl_*` variant) is intentionally EXCLUDED
  from selection for now** — kept in the codebase/labels
  (`reporting/excel_report.py`'s `DELIVERY_PARTNER_LABELS`) for future use,
  just never picked, until the user says otherwise.

Implemented in `data/diversity_tracker.py` (rewritten 2026-09-22,
superseding the original country/partner-only version), persisted to
`reports/diversity_state.json` (survives across separate `pytest` runs).
Three atomic functions (each both picks AND records in one call, so tests
can't forget to record and leave state inconsistent):
- `next_saudi_side()` — strict global alternation.
- `next_fresh_location(locations, is_saudi)` — cooldown-aware pick.
- `next_delivery_partner()` — round-robin (DHL excluded).
Plus `set_last_saudi_side(side)`, used only by Parcel's test since its own
`pytest.mark.parametrize` covers both directions every run by design
(valuable regression coverage) rather than reading the global alternator —
it calls this after each parametrized run so the global alternation stays
in sync with reality for whichever booking type runs next.

`data/booking_payloads.py`'s old `pick_pickup_dropoff()` (which picked
randomly itself) was renamed to `arrange_pickup_dropoff()` and now only
arranges two ALREADY-picked locations into (pickup, dropoff) order —
selection itself is the tracker's job now.

State was backfilled with real history predating this tracker (Parcel x2 +
Pallet x1): `last_saudi_side: "pickup"` (Pallet's actual last run), and
`partner_rotation_index: 2` (skipping past `ups` and `aramex`, which
Parcel/Pallet already used, so the next fresh pick starts at `fedex`).

**Excel report persistence — SUPERSEDED, final version (2026-09-22):**
the user initially asked for a fresh report every run, then changed this
later the same day: `reporting/excel_report.py`'s `BookingReport` now
**appends** to the existing `reports/booking_report.xlsx` across separate
`pytest` sessions again (loads prior rows on init, same as the very first
version), but now tags every row with a `"Run ID"` column (format
`Run_YYYYMMDD_HHMMSS`, one shared value per session via
`generate_run_id()`) so individual runs can be told apart within the one
growing file. This is DIFFERENT from the diversity tracker's state, which
also persists across runs but is a separate file/concern — don't conflate
the two.

**Test execution order fixed (2026-09-22):** pytest collects test files
alphabetically by default, so `test_pallet_booking.py` was running BEFORE
`test_parcel_booking.py` ("pallet" < "parcel" alphabetically) — breaking
the intended Parcel→Pallet→Luggage→Documents sequence and, combined with
Parcel's old parametrize, caused two consecutive bookings with the same
Saudi side. Fixed by renaming with numeric prefixes:
`test_01_parcel_booking.py`, `test_02_pallet_booking.py`. **Any new
booking-type test file must follow this numbering** (`test_03_luggage_booking.py`,
`test_04_documents_booking.py`) to keep the sequence correct — don't go
back to unprefixed names.

**Mobile number validation changed mid-project (discovered 2026-09-22):**
the exact same `sender_mobile_number` ("8005820010", 10 digits) and
`stakeholders`/receiver numbers (10 digits starting with "96") that
succeeded in every earlier booking (§5-8 above) started being uniformly
rejected with `"Invalid mobile number. Mobile number must be 9 digits."`
on BOTH fields, consistently across repeated runs (ruled out as flaky —
same error 3/3 times). Nothing in our code changed for these fields
between the working runs and this — points to the demo site's own
validation rule changing server-side. Fixed per user's direct
confirmation (2026-09-22):
- `sender_mobile_number` default in `build_booking_detail()` changed from
  `"8005820010"` to `"800582001"` (9 digits) — user checked the live
  Add Booking → Parcel form and confirmed this is the field's real
  pre-filled value with the trailing 0 dropped. Still hardcoded, not
  scraped — if it changes again, re-check the live form rather than
  re-guessing.
- `random_receiver_mobile()` — **SUPERSEDED again, 2026-09-22 (second
  correction):** the 9-or-10-flexible approach was itself replaced almost
  immediately — the real rule is the digit count must match the
  **receiver's actual country**, not a fixed/flexible length. User gave
  explicit examples: India (+91) 10 digits, United States (+1) 10 digits,
  Saudi Arabia (+966) 9 digits, Spain (+34) 9 digits, Singapore (+65) 8
  digits, Hong Kong (+852) 8 digits. Implemented as
  `data/phone_formats.py` (`PHONE_FORMAT_BY_ISO`, keyed by the ISO
  alpha-2 codes already on our location dicts) +
  `random_mobile_for_country()` in `booking_payloads.py`, which replaced
  `random_receiver_mobile()` entirely. The receiver's phone is generated
  using the **dropoff location's** country (receiver is physically at
  dropoff, regardless of which side is Saudi) — both the `country_code`
  field (now a real dial code, e.g. `"+91"`, not hardcoded `"+966"`) and
  the `stakeholders` digit count now vary per booking accordingly. The
  table covers countries already seen in this account's saved locations
  plus ~25 other common ones, with a documented best-effort fallback (9
  digits) for anything not in the table — **if a new country triggers a
  digit-count rejection, add it to `PHONE_FORMAT_BY_ISO` rather than
  guessing; the error message states the required count.**

**`make_ppd_payment` response shape — CONFIRMED (2026-09-21 live run):**
```json
{"status": true, "online": 0, "wallet": <float>, "paymentArray": [...], "is_wallet": true, "goods_value": <float>}
```
Note `status` is a **boolean** here (`true`), unlike `create_booking`'s
string-based status convention — don't conflate the two. Both Parcel and
Pallet tests now assert `payment_response.get("status") is True` instead
of the old weak `is not None` check.

**Parcel's parametrize REMOVED (2026-09-22, user correction):** Parcel used
to `pytest.mark.parametrize` over both `saudi_side` values, producing 2
bookings every run regardless of what ran before it — which broke the
GLOBAL strict alternation (e.g. Pallet used Saudi-as-pickup, then Parcel's
first parametrized case ALSO used Saudi-as-pickup, back to back, instead
of flipping). Fixed: Parcel now produces exactly ONE booking per run, side
decided by `next_saudi_side()` — identical pattern to every other booking
type now. `set_last_saudi_side()` still exists in `diversity_tracker.py`
but is currently unused by any test (kept in case a dedicated
both-directions regression test is wanted later — ask the user before
reintroducing it, don't assume). **Rule going forward: every
booking-type test produces exactly ONE booking per full suite run.**

**Status-check timing fix (2026-09-22):** a booking checked immediately
after payment completion once showed status `"Requested"` — not in either
confirmed status set. Working theory: async delay between payment
completing and the booking actually processing into a terminal status
server-side, and we were checking too fast (single immediate check).
Fixed with `wait_for_terminal_status()` in `booking_test_helpers.py` —
polls up to 15s (2s interval) until a known success/failure status is
reached. **NOT YET RE-CONFIRMED** as the actual root cause — if
`"Requested"` still shows up as the final status after the full timeout on
a future run, this needs separate investigation (ask the user to check
that specific booking number on the live site) rather than assuming the
polling fix resolved it.

**Every new booking-type test must call `next_saudi_side()`,
`next_fresh_location()` for both sides, `next_delivery_partner()`, and
`wait_for_terminal_status()` for the final status check** — see
`test_02_pallet_booking.py` for the pattern to copy.

**Not yet built:**
- `/bookings/load_map` flow (fresh/international location via Google Maps
  pin-drop) — current tests only use the account's already-saved locations
  for both the Saudi and non-Saudi side, which satisfies the business rule
  without needing to reverse-engineer Google Maps geocoding calls. **The
  user's "popular place like Tokyo" instruction (2026-09-22) may want this
  built for genuinely new locations rather than only cycling the account's
  existing saved list — ask the user if this is wanted before building it,
  it's a meaningfully bigger feature (Google Places integration).**
- C2C flow — endpoint name assumed (`/login/individual`), not yet
  confirmed live.
- Luggage / Documents booking types — Parcel and Pallet are done; these
  two remain. Given how identical Parcel/Pallet turned out to be, try a
  live recon first (same pattern as Pallet's) before assuming — Luggage in
  particular might have genuinely different fields (dimensions per piece,
  luggage count) worth confirming rather than assuming.
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

## Addendum (2026-09-22, continued session) -- append this section to handoff.md

**Sender phone now also country-matched (user clarification):** extending
the receiver-phone fix, the user clarified the SAME rule applies to the
sender too: "whichever country u take in pickup and dropoff adjust the
phone number accordingly ... set according to the country we are taking
in pickup and dropoff location." Implemented: `sender_country_code` /
`sender_mobile_number` are now generated via `random_mobile_for_country()`
matched to the **pickup** location's country (receiver stays matched to
**dropoff**, as before). This also matches what real live recon already
showed (Pallet's Australia-pickup booking had `sender_country_code: "+61"`
in the actual captured UI request, not a fixed Saudi default) --
`build_booking_detail()`'s `sender_country_code`/`sender_mobile_number`
parameters were removed (no longer meaningful as overridable defaults
since they're always computed now); `sender_name` stays the only
remaining override param (fixed account-holder name).

**Open question, NOT YET RESOLVED — per-country phone table vs. simpler
rule:** a live run after the above fixes showed the RECEIVER (`stakeholders`)
rejected with "must be 10 digits" for some non-Saudi dropoff country. This
contradicts our per-country table's entry for whichever country was
actually used. Two live data points so far:
  - Dropoff = Saudi Arabia → server said "must be 9 digits" (matches our SA entry)
  - Dropoff = (some other country, not yet confirmed which) → server said "must be 10 digits"
This is consistent with a MUCH simpler theory: **the server may just
require 9 digits for Saudi numbers and 10 digits for every other country,
full stop** -- not genuine fine-grained per-country formats like the
Spain=9/Singapore=8/HK=8 examples the user gave (those may be accurate
real-world telecom standards but not what THIS demo server's validation
actually checks). **Next step: get the specific failing country from
`.auth/debug_create_booking_response.json` on the next run, and ask the
user whether to collapse `data/phone_formats.py` down to just
"9 for SA, 10 for everyone else"** rather than maintaining the full
per-country table, if more non-Saudi countries keep coming back expecting
10 regardless of what our table says for them specifically. Don't
unilaterally simplify the table without more evidence first -- only one
data point so far beyond Saudi.

**New bug found — Pallet booking rejected with "There should be saudi
arabia country either in pickup or dropoff address" (neither side was
Saudi per the server), despite our selection logic (`next_fresh_location`,
`arrange_pickup_dropoff`) appearing correct on close code review — no
obvious bug found by inspection alone. Added extensive defensive
assertions throughout BOTH test files (`assert_exactly_one_saudi_side()`
in `booking_test_helpers.py`, called right after `arrange_pickup_dropoff`
AND again on the final built `booking_detail` dict, plus sanity checks on
`next_saudi_side()`'s return value, `next_fresh_location()`'s
Saudi/non-Saudi correctness, delivery partner never being DHL, and
non-empty phone/HS-code fields) per the user's explicit instruction to
"always use assertions wherever possible so we can identify the issue
fast." **NOT YET RE-CONFIRMED** — next run will show definitively whether
the bug is in our own code (one of these new assertions will fire,
pinpointing exactly where) or something else entirely (all assertions
pass here, but the server still rejects it — in which case share
`.auth/debug_create_pallet_booking_response.json` for the real payload).

**Session note:** the sandbox environment hosting this framework's working
copy was reset between conversation turns (lost all files). All files were
reconstructed from conversation history/memory and re-verified
(compile-checked + smoke-tested) before being resent. No functional
regressions expected, but if anything seems to have reverted to an older
behavior unexpectedly, flag it — that would be a reconstruction slip to
diff against the files the user already has saved locally.

## 13. Session addendum (2026-09-23, continued) — Pallet dimension rule finalized, repo sync process

**CRITICAL PROCESS FIX: this repo (https://github.com/hitesh8765/routech_work)
is now the assistant's source of truth for current file state.** Several
fixes sent in earlier turns never actually landed in the user's working
copy (confirmed by `git clone`-ing the repo directly and diffing against
what should have been there) -- including the first version of the Pallet
dimension rule below, which was itself then superseded before it was ever
even applied. **Going forward: `git clone` the repo FIRST whenever
behavior seems inconsistent with the code, or before making further
changes to a file that was recently sent** -- don't assume the user's
local copy matches the last message's attachments. `.auth/` (debug JSON
dumps) is gitignored and still needs to be shared directly by the user;
`reports/diversity_state.json` and `reports/booking_report.xlsx` ARE
tracked and genuinely useful for checking real accumulated results without
asking.

**Pallet dimension/weight rule — FINAL version (2026-09-23), replaces
BOTH the original Parcel-style rule AND a first Pallet-specific attempt
that was itself wrong:**

- An earlier attempt used a separate weight-range (75-83) + a
  length>width>height dimension pool, mirroring Parcel's structure just
  scaled up. **This was wrong** -- real Pallet packages are a flat, wide
  footprint with a SMALL height (like an actual shipping pallet), not a
  scaled-up box.
- **Correct rule, given directly with a real screenshot reference**
  (showing Actual Weight 82, Length 100, Width 100, Height 14 as one
  on-screen example): weight and dimensions are **coupled together as ONE
  combo**, not independently randomized, and do **NOT** follow
  length>width>height. The exact 5 approved combos
  (`actual_weight, length, width, height`):
  ```
  (82, 100, 100, 14)
  (81, 110, 110, 14)
  (80, 122, 102, 14)
  (79, 120, 100, 15)
  (75, 120, 80, 14)
  ```
- Implemented as `PALLET_WEIGHT_DIMENSION_COMBOS` in
  `data/booking_payloads.py` -- a list of 4-tuples, picked whole via
  `random.choice()`. The old `PALLET_DIMENSION_COMBO_POOL` and
  `PALLET_ACTUAL_WEIGHT_RANGE` constants were REMOVED entirely (not just
  deprecated) -- don't resurrect them.
- `random_actual_weight()` / `random_dimensions()` (the two separate
  functions) were replaced by a single `random_weight_and_dimensions(booking_type)`
  that returns `(actual_weight, length, width, height)` together. Parcel's
  behavior is unchanged (weight and dimension-combo still independently
  randomized, length>width>height still preserved) -- only Pallet's path
  changed.
- `test_02_pallet_booking.py`'s sanity assertion now checks the full
  `(actual_weight, length, width, height)` tuple is a member of
  `PALLET_WEIGHT_DIMENSION_COMBOS`, replacing the old
  separate-range-plus-ordering checks.
- **If Luggage/Documents need their own weight/dimension rules, check
  with the user whether they're Parcel-style (independent weight +
  ordered dimensions) or Pallet-style (coupled combos) before assuming
  either pattern** -- don't default to copying Parcel's structure again
  without asking, since that was the mistake the first time for Pallet.

**Other fixes landed this same session (also now actually in this repo,
confirmed via direct edits against the cloned copy rather than
reconstruction from memory):**
- `DEFAULT_PHONE_FORMAT` fallback: 9 -> 10 digits (`data/phone_formats.py`).
- Delivery partner rotation: RESTORED to the full list (the earlier
  narrowing to just `ups`/`aramex` was based on incomplete evidence --
  the accumulated `reports/booking_report.xlsx` showed several fedex
  variants actually succeeding most of the time), and DHL added per
  direct user instruction ("start using DHL delivery partner as well") --
  only plain `"dhl"`, not the `dhl_*` sub-variants. See
  `data/diversity_tracker.py`.
- Step-by-step progress printing added: `step()` helper in
  `booking_test_helpers.py`, called at every major milestone in both test
  files (locations fetched, side decided, each location picked, partner
  picked, HS code resolved, rates fetched, booking built, booking created,
  payment confirmed, final status) -- visible with `pytest -s`, so a
  failure's exact location is obvious from the terminal transcript alone
  without needing a debug-file round-trip. **Any new booking-type test
  should include the same step() calls at the same milestones.**
- `BookingReport.save()` now catches `PermissionError` (file locked,
  almost always because it's open in Excel) and raises a clear message
  instead of a raw zipfile traceback.

## 14. Session addendum (2026-10-07) — FedEx digit-cap theory REJECTED with evidence

A third-party analysis (pasted in by the user, apparently from another AI
tool) proposed that FedEx enforces a strict 10-digit phone number limit
regardless of country, and suggested capping `random_mobile_for_country()`
at 10 digits whenever the delivery partner starts with "fedex". **This was
checked against the actual accumulated `reports/booking_report.xlsx` data
and REJECTED** -- the evidence contradicts it:
- The failures (status "Requested", not transitioning to a terminal state)
  occurred on **France (9 digits)** and **United States (10 digits)** --
  both already within the claimed 10-digit cap.
- The SAME delivery partners (Fedex Express, Fedex Priority) succeeded
  cleanly in OTHER runs with no digit-count correlation.
- If a hard 10-digit FedEx cap were real, failures should correlate with
  countries needing MORE than 10 digits (China=11, Brazil=11) -- no such
  correlation is visible in the data.

**Do not apply a FedEx-specific phone digit cap without first seeing a
real server error message that explicitly says so.** The "Requested"
stuck-status issue looks more like genuine server-side processing
variance than a data-shape problem. Applied instead:
- `wait_for_terminal_status()`'s timeout increased 15s -> 30s (safe,
  evidence-supported fix for the "Requested" timing issue specifically).
- Both test files now dump the outgoing `booking_detail` payload itself
  (`dump_debug("booking_detail_payload", ...)` /
  `dump_debug("pallet_booking_detail_payload", ...)`) right before
  `create_booking()` is called, independent of what the response echoes
  back -- useful for diagnosing payload-shape issues going forward.

**Lesson for future sessions: when the user pastes in analysis or a
proposed fix from another source (another AI, a teammate, etc.), check it
against real accumulated data (the report / diversity state / debug
files) before applying it, rather than trusting the stated reasoning at
face value** -- it can sound plausible and still be wrong, as happened
here. The two "top_level_status='error'" failures in the screenshots that
prompted this (distinct from the "Requested" timing issue) are still
UNRESOLVED -- the real validation message was cut off in the shared
screenshots; still need `.auth/debug_create_booking_response.json` /
`.auth/debug_create_pallet_booking_response.json` from an actual failing
run to diagnose properly.

## 15. Session addendum (2026-10-07, continued) — Freight FedEx variants identified as the real cause, with evidence

User shared the actual `.auth/debug_create_booking_response.json` /
`debug_create_pallet_booking_response.json` files from a real failing
run, resolving the open question from §14.

**Real server error messages (not truncated this time):**
- Parcel (`fedex_priority_freight`, Saudi→Spain route):
  `{"param": "booking_detail_0_delivery_partners", "msg": "Please try again with another delivery partner or change the location.", "key": "booking.please_select_delivery_partners"}`
- Pallet (`fedex_regional_economy_freight`, Niger→Saudi route):
  `{"param": "booking_detail_0_dropoff_address", "msg": "Please enter valid location.", "key": "system.please_enter_valid_location"}`

**Root cause identified with real evidence:** both failures used one of
the three "_freight" suffixed FedEx variants
(`fedex_priority_freight`, `fedex_regional_economy_freight`,
`fedex_economy_freight`). Cross-checking `reports/booking_report.xlsx`:
the STANDARD fedex variants (`fedex`, `fedex_priority`, `fedex_express`,
`fedex_connect_plus`) have multiple confirmed "Shipment Submitted"
successes; **none of the three freight variants have ever succeeded, in
any run.** These are freight/bulk-cargo services, plausibly with
different route/weight eligibility than standard express/priority parcel
services (or simply not offered at all on this demo account for arbitrary
routes). Removed from `DELIVERY_PARTNER_ROTATION` in
`data/diversity_tracker.py` (kept as a comment for reference, not deleted
outright, in case a future need arises to test them deliberately against
a much larger/heavier shipment). `"darb"` is left in the rotation despite
having no confirmed success OR failure yet — watch for it.

**This also retroactively confirms §14's rejection of the "FedEx has a
hard 10-digit phone cap" theory was correct** — the real cause was
specific-partner-unavailability, nothing to do with phone number length.
The user's own instinct ("i think it fail in while booking in fedex")
was directionally right, just needed narrowing to the freight-specific
sub-variants rather than FedEx broadly.

**Not yet investigated, flagged for later if it recurs:** the Pallet
failure's pickup location (Niger, a country not seen in any prior
booking) had an EMPTY `pickup_postal_code` in the submitted payload. This
may be a data-quality issue specific to that one saved location (possibly
why the server's generic validator attributed the error to
`dropoff_address` despite pickup being the side with missing data) rather
than a real problem. Since the freight-partner fix addresses the
STRONGER, dual-failure-confirmed pattern, this is left as a secondary
watch-item rather than acted on now — if "please enter valid location"
recurs on a NON-freight-partner booking, revisit this specific location's
data quality (check Niger's postal_code attribute in a fresh
`/bookings/booking_form` fetch).

## 16. Session addendum (2026-10-07, final) — ROOT CAUSE of rejected / stuck bookings found and fixed

**`get_delivery_and_rate` real response shape — CONFIRMED (long-standing open item, now closed):**
```json
{"status": "success",
 "days":   {"dhl": "3 Days", "ups": "5 - 7 Days", "aramex": "5 - 7 Days", "fedex": "3 Days"},
 "amount": {"ups":    {"payable_amount": 916.46, "vat_amount": 0, "base_amount": 916.46, "base_price": 916.46, "is_offer_applied": false, "offer_details": {}},
            "aramex": {"payable_amount": 1803.45, ...}},
 "offer_details": ""}
```
`days` lists every partner the route theoretically supports; `amount` lists
only the partners with a REAL price. **A partner is bookable only if it is
in `amount`.** Evidence from real runs: Saudi→Ukraine parcel quoted days for
dhl/ups/aramex/fedex but amount only for ups/aramex; we picked `fedex` and
the server answered `booking.please_select_delivery_partners` ("Please try
again with another delivery partner or change the location"). Afghanistan→
Saudi pallet had `dhl` in days but not amount; we picked `dhl`, the booking
was created with `ppd_amount: null`, `goods_value: null`, an empty
`tabby_payments`, and sat on status "Requested" forever (and never appears
in the application).

**This supersedes the earlier theories** (FedEx phone-digit cap, "freight"
variants being invalid, per-country phone lengths causing the instability):
the instability was blind partner selection. Freight variants were not
inherently invalid — they simply had no quote on those routes.

**RULE (user-mandated, treat as permanent): only select a delivery partner
that has a quoted amount for that specific route.** Implemented:
- `extract_available_partners()` (data/booking_payloads.py): partner must be
  in `amount` with `payable_amount > 0`. (Decision to flag: aramex showed
  `payable_amount: 0` on one pallet route and is treated as NOT available —
  a booking with no price is exactly what gets stuck. If the user says a 0
  quote is valid, relax to "key present".)
- `next_delivery_partner(available_partners)` (data/diversity_tracker.py):
  now REQUIRES the available list; keeps round-robin order but skips
  unavailable partners; raises ValueError if none usable.
- `pick_route_with_available_partner()` (booking_test_helpers.py): picks the
  route, quotes it, selects the partner; if the route has no usable partner
  it picks a DIFFERENT location pair and retries (6 attempts), then fails
  with a clear message. Both tests use it.
- Defensive assertions: chosen partner is in the available list; its
  `delivery_rate_<partner>` field is > 0; after `/bookings/add`,
  `payment_array[0].ppd_amount` must be a positive number — if it is null
  the test fails BEFORE paying, instead of paying for a stuck booking.

**Other bugs found while reading the real response, fixed:**
1. Rates were never actually submitted: the old code looked for flat
   `delivery_rate_*` keys in the response that never existed, so every
   booking silently sent blank rates. Now mapped from
   `amount[<partner>].payable_amount` into `delivery_rate_<partner>`.
2. The rate quote always used a fixed 10 kg / 15x12x11 parcel, even for
   Pallet. Availability depends on weight/size, so it was being checked
   against the wrong shipment. Weight/dimensions are now chosen FIRST and
   the quote uses them (`build_rate_payload()`); the same tuple is passed
   to the builder (`weight_and_dimensions=`) so quote and booking match.
3. Volumetric weight = L*W*H/5000 rounded UP to the nearest 0.5 (verified
   against server values: 100x100x14→28, 120x100x15→36, 15x12x11→0.5);
   `higher_weight = max(actual, volumetric)`.

**Status polling:** `wait_for_terminal_status()` now waits up to 60s
(interval 3s), re-fetching the booking details on every poll (equivalent to
refreshing the page) and printing each poll. "Requested" is not terminal; a
booking still on it after 60s is a real problem now that stuck-by-design
bookings are prevented up front.

**Timeouts:** `/bookings/add` and `/bookings/make_ppd_payment` now use a 90s
Playwright timeout (a pallet `add` hit the 30s default once, 2026-10-07,
route Argentina→Saudi, partner fedex_priority; cause unconfirmed — may be the
same unavailable-partner problem, since a stuck carrier lookup is plausible).
If it still times out with an available partner, investigate separately.

**Debug dump filenames changed:** rate responses are now
`.auth/debug_get_delivery_and_rate_parcel_response.json` and
`.auth/debug_get_delivery_and_rate_pallet_response.json` (one per booking
type, written on every route attempt).

**Notes / open items:**
- Partners seen with real quotes so far: ups, aramex, fedex, fedex_priority,
  fedex_connect_plus. `dhl` has appeared only in `days` so far, so it will
  rarely be chosen until a route actually quotes it — expected, not a bug.
- The "freight" FedEx variants (removed from rotation in §15) are now safe to
  re-add because availability gating protects against them; left out until
  the user asks, since no route has quoted them yet.
- Diversity cooldown state is consumed on each route attempt, including
  rejected ones — harmless, but the Saudi/intl location cooldown lists will
  advance faster when retries happen.
- Still unresolved: the earlier Niger-pickup case with an empty
  `pickup_postal_code` (§15) — revisit only if "please enter valid location"
  recurs with a partner that HAS a quote.
