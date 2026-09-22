"""
Test-data pools and payload builders, encoding the business rules given
during the walkthrough:

  - For every booking, exactly one of {pickup, dropoff} must be a Saudi
    Arabia location; the other must be non-Saudi. Which side is Saudi
    should vary across bookings/tests (not always pickup, not always
    dropoff).
  - Insurance: always "No".
  - Shipment Invoice: always "Create Invoice" (never "Upload Invoice").
  - Item Name: random from ITEM_NAME_POOL.
  - Item Quantity: random from ITEM_QUANTITY_POOL.
  - HS Code: looked up using the SAME string as the item name (the site's
    autocomplete is then used to pick a real code -- see
    RoutechAPIClient.get_hs_codes).
  - Item Price: random from ITEM_PRICE_POOL.
  - Actual Weight: random int in [8, 16].
  - Length > Width > Height, chosen as one whole combo from
    DIMENSION_COMBO_POOL (never assembled from independently-random parts,
    to preserve the length > width > height ordering rule).
  - Receiver Mobile Number: 10 digits, starting with "96" (i.e. a Saudi
    mobile shape, regardless of which side is physically Saudi).

Field-name mapping confirmed by live capture of POST /bookings/add:
    stakeholder_name        -> Receiver Name
    country_code            -> Receiver's country code (e.g. "+966")
    stakeholders             -> Receiver Mobile Number
    sender_name / sender_country_code / sender_mobile_number -> Sender (auto-filled, just assert non-empty in tests)
"""
import random
from typing import Literal

ITEM_NAME_POOL = [
    "books", "furniture", "telephone", "monitor",
    "keyboard", "mouse", "fish", "sweatshirt",
]
ITEM_QUANTITY_POOL = [20, 100, 50, 60, 10, 200, 150]
ITEM_PRICE_POOL = [100, 200, 500, 1000, 699, 800, 700]

# Server-confirmed validation on stakeholder_name (Receiver Name):
# "Please enter a valid name using only letters and numbers. Special
# characters are not allowed" -- so NO hyphens/apostrophes here, even
# though they're common in real Saudi names (e.g. "Al-Saud").
RECEIVER_NAME_POOL = [
    "Ahmed Alsaud", "Fatimah Alqahtani", "Mohammed Alotaibi", "Noura Alharbi",
    "Khalid Alghamdi", "Sara Alshehri", "Faisal Alharthi", "Reem Aldosari",
]

# Each tuple is (length, width, height) with length > width > height, as
# specified -- picked as a whole combo, never mixed-and-matched.
DIMENSION_COMBO_POOL = [
    (14, 13, 11),
    (15, 12, 11),
    (13, 11, 9),
    (15, 11, 10),
    (13, 10, 8),
]

DELIVERY_PARTNER_RATE_FIELDS = [
    "delivery_rate_ups", "delivery_rate_dhl", "delivery_rate_aramex",
    "delivery_rate_fedex", "delivery_rate_fedex_priority", "delivery_rate_fedex_express",
    "delivery_rate_fedex_regional", "delivery_rate_fedex_connect_plus",
    "delivery_rate_fedex_priority_freight", "delivery_rate_fedex_regional_economy_freight",
    "delivery_rate_fedex_economy_freight", "delivery_rate_dhl_medical_express",
    "delivery_rate_dhl_express_worldwide", "delivery_rate_dhl_freight_worldwide",
    "delivery_rate_dhl_economy_select", "delivery_rate_dhl_express_easy",
    "delivery_rate_dhl_express_domestic", "delivery_rate_darb",
]


def random_actual_weight() -> int:
    return random.randint(8, 16)


def random_dimensions() -> tuple:
    return random.choice(DIMENSION_COMBO_POOL)


def random_receiver_mobile() -> str:
    """
    Starts with '96' (original walkthrough rule). Total length randomized
    between 9 and 10 digits per the user's explicit "be flexible with 9
    and 10 digit" instruction (2026-09-22) -- the server started rejecting
    our previous fixed-10-digit numbers with "must be 9 digits", but rather
    than commit to one length, we vary it to see what's actually accepted.
    """
    total_length = random.choice([9, 10])
    rest_length = total_length - 2  # "96" prefix takes 2
    rest = "".join(str(random.randint(0, 9)) for _ in range(rest_length))
    return f"96{rest}"


def random_item() -> dict:
    return {
        "item_name": random.choice(ITEM_NAME_POOL),
        "item_quantity": str(random.choice(ITEM_QUANTITY_POOL)),
        "item_price": str(random.choice(ITEM_PRICE_POOL)),
    }


def arrange_pickup_dropoff(saudi_location: dict, intl_location: dict, saudi_side: Literal["pickup", "dropoff"]):
    """
    Arranges an already-chosen Saudi location and non-Saudi location into
    (pickup, dropoff) order based on which side should be Saudi.

    NOTE: location SELECTION itself (which Saudi/non-Saudi location to use)
    is handled by data/diversity_tracker.py's next_fresh_location() --
    this function only arranges two already-picked locations. It used to
    also pick randomly itself (see git history / handoff.md), but that was
    superseded by the diversity tracker's cooldown-aware picking.
    """
    if saudi_side == "pickup":
        return saudi_location, intl_location  # pickup, dropoff
    return intl_location, saudi_location


def build_booking_detail(
    booking_type: str,
    pickup_location: dict,
    dropoff_location: dict,
    receiver_name: str,
    hs_code: str,
    delivery_rates: dict,
    delivery_partner: str = "ups",
    sender_name: str = "Neeraj Sharma",
    sender_country_code: str = "+966",
    sender_mobile_number: str = "800582001",  # confirmed real value shown on the site (2026-09-22) -- was "8005820010" (10 digits) until the server started requiring 9-digit numbers; user confirmed the site's own displayed value is this with the trailing 0 dropped
) -> dict:
    """
    Builds the exact `booking_detail[0]` dict shape captured live for both
    Parcel and Pallet (confirmed 2026-09-21: Pallet's request is identical
    to Parcel's except `booking_type` and the absence of
    `pallet_container_id`, which real capture showed is NOT pallet-specific
    despite the name -- included here only for booking_type=="parcel" to
    preserve exact fidelity per type without risking anything that already
    works). `pickup_location` / `dropoff_location` are dicts as returned by
    utils.html_parsing.parse_pickup_locations() (or an equivalent shape for
    a freshly-geocoded international address).

    `delivery_rates` should be the dict returned by
    RoutechAPIClient.get_delivery_and_rate() -- its per-partner rate keys
    are merged straight into the payload (mirrors what the UI does: it
    echoes back every partner's quote, not just the chosen one).

    NOTE: real capture also showed `sender_country_code` varying by pickup
    location in the live UI (e.g. "+61" for an Australia pickup) rather
    than a fixed "+966" -- but our automated tests already pass reliably
    with a hardcoded value across multiple country pairs, so this default
    is left as-is rather than adding a country->dial-code mapping for
    something that isn't blocking anything. See handoff.md.
    """
    item = random_item()
    actual_weight = random_actual_weight()
    length, width, height = random_dimensions()

    detail = {
        # -- pickup --
        "pickup_location": pickup_location.get("id", ""),
        "pickup_address": pickup_location.get("address", pickup_location.get("label", "")),
        "business_name": "",
        "pickup_latitude": str(pickup_location.get("latitude", "")),
        "pickup_longitude": str(pickup_location.get("longitude", "")),
        "pickup_city": pickup_location.get("city", ""),
        "pickup_state": pickup_location.get("state", ""),
        "pickup_country": pickup_location.get("country", ""),
        "pickup_country_code": pickup_location.get("country_code", ""),
        "pickup_postal_code": pickup_location.get("postal_code", ""),
        "pickup_additional_address": pickup_location.get("additional_address", ""),
        "type_partner": "b2c",
        "pickup_national_address": pickup_location.get("national_address", ""),
        # -- dropoff --
        "dropoff_address": dropoff_location.get("address", dropoff_location.get("label", "")),
        "is_international": "1",  # always cross-border under our Saudi-side rule
        "dropoff_latitude": str(dropoff_location.get("latitude", "")),
        "dropoff_longitude": str(dropoff_location.get("longitude", "")),
        "dropoff_city": dropoff_location.get("city", ""),
        "dropoff_state": dropoff_location.get("state", ""),
        "dropoff_country": dropoff_location.get("country", ""),
        "dropoff_country_code": dropoff_location.get("country_code", ""),
        "dropoff_postal_code": dropoff_location.get("postal_code", ""),
        "dropoff_additional_address": dropoff_location.get("additional_address", ""),
        "dropoff_national_address": dropoff_location.get("national_address", ""),
        "item_weight": "",
        "is_location_unknown": "0",
        # -- booking meta --
        "booking_type": booking_type,
        # -- receiver ("stakeholder") --
        "stakeholder_name": receiver_name,
        "country_code": "+966",
        "stakeholders": random_receiver_mobile(),
        # -- sender (auto-filled by account; just echoed back) --
        "sender_name": sender_name,
        "sender_country_code": sender_country_code,
        "sender_mobile_number": sender_mobile_number,
        "imported_order_id": "",
        # -- options (business rules) --
        "insurance": "0",       # always "No"
        "stackable": "1",       # default "Yes" (not overridden by any stated rule)
        "weight_per_kg": "",
        "payment_type": "ppd",
        "cod_amount": "",
        "schedule_type": "schedule_now",  # "On Demand"
        "scheduled_time": "",
        "currency_code": "SAR",
        "currency_value": "1",
        "delivery_type": "regular",
        "is_invoice_required": "0",  # "0" = Create Invoice (no upload needed)
        "shipping_method": "home",
        "shipment_content_type": "dry",
        "item_list_details": [
            {
                "item_name": item["item_name"],
                "item_quantity": item["item_quantity"],
                "item_detail": "",
                "hs_code": hs_code,
                "item_price": item["item_price"],
            }
        ],
        "quantity": "1",  # package quantity
        "is_offer_apply": "false",
        "package_dimensions": [
            {
                "actual_weight": str(actual_weight),
                "length": str(length),
                "width": str(width),
                "height": str(height),
            }
        ],
        "offer_code": "",
        "is_palletized": "false",
        "delivery_partners": delivery_partner,
    }

    if booking_type == "parcel":
        detail["pallet_container_id"] = ""

    # Merge in whatever per-partner rates the get_delivery_and_rate() call
    # returned (UI echoes all quoted rates back, not just the chosen one).
    for field in DELIVERY_PARTNER_RATE_FIELDS:
        detail[field] = str(delivery_rates.get(field, "")) if delivery_rates.get(field) not in (None, "") else ""

    return detail


def build_parcel_booking_detail(**kwargs) -> dict:
    return build_booking_detail(booking_type="parcel", **kwargs)


def build_pallet_booking_detail(**kwargs) -> dict:
    """
    Confirmed identical to Parcel's request shape (2026-09-21 live capture)
    except booking_type and no pallet_container_id -- see build_booking_detail().
    """
    return build_booking_detail(booking_type="pallet", **kwargs)
