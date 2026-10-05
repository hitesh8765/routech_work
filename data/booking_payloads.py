"""
Test-data pools and payload builders, encoding the business rules given
during the walkthrough:

  - For every booking, exactly one of {pickup, dropoff} must be a Saudi
    Arabia location; the other must be non-Saudi. Which side is Saudi
    strictly alternates booking to booking, globally, across all booking
    types -- see data/diversity_tracker.py.
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
  - Receiver Mobile Number AND Sender Mobile Number: digit count must
    match the ACTUAL country they belong to (receiver -> dropoff country,
    sender -> pickup country), confirmed by the user (2026-09-22) and
    extended to cover the sender too in a later clarification -- see
    data/phone_formats.py. Was previously a fixed/flexible-length scheme;
    superseded.

Field-name mapping confirmed by live capture of POST /bookings/add:
    stakeholder_name        -> Receiver Name
    country_code            -> Receiver's phone country code (now dynamic, matches dropoff country)
    stakeholders             -> Receiver Mobile Number (now dynamic length, matches dropoff country)
    sender_name / sender_country_code / sender_mobile_number -> Sender
        (sender_name stays fixed -- real account holder name; sender
        phone/country code now dynamic, matches PICKUP country -- real
        live recon showed the actual UI does this too, e.g. "+61" for an
        Australia pickup, not a fixed Saudi default)
"""
import random
from typing import Literal

from data.phone_formats import get_phone_format

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
# PARCEL-ONLY (small items). For Pallet, see PALLET_DIMENSION_COMBO_POOL
# below -- pallets are much bigger/heavier, per the user's explicit rule
# (2026-09-23).
DIMENSION_COMBO_POOL = [
    (14, 13, 11),
    (15, 12, 11),
    (13, 11, 9),
    (15, 11, 10),
    (13, 10, 8),
]
ACTUAL_WEIGHT_RANGE = (8, 16)

# PALLET-ONLY: actual weight always 75-83; length > width > height, picked
# as a whole combo from a small fixed pool (reference combo given by the
# user: 80/75/68), rather than independently randomizing each dimension.
PALLET_DIMENSION_COMBO_POOL = [
    (80, 75, 68),
    (82, 74, 70),
    (83, 76, 69),
    (81, 73, 67),
    (84, 77, 71),
]
PALLET_ACTUAL_WEIGHT_RANGE = (75, 83)

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


def random_actual_weight(booking_type: str = "parcel") -> int:
    if booking_type == "pallet":
        return random.randint(*PALLET_ACTUAL_WEIGHT_RANGE)
    return random.randint(*ACTUAL_WEIGHT_RANGE)


def random_dimensions(booking_type: str = "parcel") -> tuple:
    if booking_type == "pallet":
        return random.choice(PALLET_DIMENSION_COMBO_POOL)
    return random.choice(DIMENSION_COMBO_POOL)


def random_mobile_for_country(iso_country_code: str) -> tuple:
    """
    Returns (dial_code, local_number) matching the given country's real
    phone format (digit count) -- see data/phone_formats.py. Used for
    BOTH the receiver (matched to dropoff country) and sender (matched to
    pickup country) per the user's explicit instructions (2026-09-22).
    Avoids a leading 0 so the number looks like a plausible real mobile.
    """
    fmt = get_phone_format(iso_country_code)
    digits = [str(random.randint(1, 9))] + [str(random.randint(0, 9)) for _ in range(fmt.digits - 1)]
    return fmt.dial_code, "".join(digits)


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
    this function only arranges two already-picked locations.
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

    Receiver AND sender phone number/country-code are both generated to
    match their respective location's real country format (receiver <-
    dropoff, sender <- pickup) -- see random_mobile_for_country() and
    data/phone_formats.py. sender_name stays fixed (the real account
    holder's name, confirmed via live capture).
    """
    item = random_item()
    actual_weight = random_actual_weight(booking_type)
    length, width, height = random_dimensions(booking_type)

    # Receiver is physically at the dropoff location; sender at pickup.
    # Phone format (dial code + digit count) should match each side's own
    # country -- see data/phone_formats.py.
    receiver_dial_code, receiver_mobile = random_mobile_for_country(dropoff_location.get("country_code", ""))
    sender_dial_code, sender_mobile = random_mobile_for_country(pickup_location.get("country_code", ""))

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
        "country_code": receiver_dial_code,
        "stakeholders": receiver_mobile,
        # -- sender --
        "sender_name": sender_name,
        "sender_country_code": sender_dial_code,
        "sender_mobile_number": sender_mobile,
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
