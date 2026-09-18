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
    """10 digits total, starting with '96' (matches the walkthrough rule)."""
    rest = "".join(str(random.randint(0, 9)) for _ in range(8))
    return f"96{rest}"


def random_item() -> dict:
    return {
        "item_name": random.choice(ITEM_NAME_POOL),
        "item_quantity": str(random.choice(ITEM_QUANTITY_POOL)),
        "item_price": str(random.choice(ITEM_PRICE_POOL)),
    }


def pick_pickup_dropoff(saudi_locations: list, intl_locations: list, saudi_side: Literal["pickup", "dropoff"]):
    """
    Enforces the "exactly one side is Saudi" rule. Pass saudi_side="pickup"
    or "dropoff" explicitly from the test (e.g. alternate per test via
    pytest.mark.parametrize) rather than randomizing here, so test runs
    stay reproducible/explainable.
    """
    if not saudi_locations:
        raise ValueError("No saved Saudi Arabia locations available on this account.")
    if not intl_locations:
        raise ValueError("No saved non-Saudi locations available on this account.")

    saudi_loc = random.choice(saudi_locations)
    intl_loc = random.choice(intl_locations)

    if saudi_side == "pickup":
        return saudi_loc, intl_loc  # pickup, dropoff
    return intl_loc, saudi_loc


def build_parcel_booking_detail(
    pickup_location: dict,
    dropoff_location: dict,
    receiver_name: str,
    hs_code: str,
    delivery_rates: dict,
    delivery_partner: str = "ups",
    sender_name: str = "Neeraj Sharma",
    sender_country_code: str = "+966",
    sender_mobile_number: str = "8005820010",
) -> dict:
    """
    Builds the exact `booking_detail[0]` dict shape captured from the live
    POST /bookings/add request. `pickup_location` / `dropoff_location` are
    dicts as returned by utils.html_parsing.parse_pickup_locations() (or an
    equivalent shape for a freshly-geocoded international address).

    `delivery_rates` should be the dict returned by
    RoutechAPIClient.get_delivery_and_rate() -- its per-partner rate keys
    are merged straight into the payload (mirrors what the UI does: it
    echoes back every partner's quote, not just the chosen one).
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
        "booking_type": "parcel",
        "pallet_container_id": "",
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

    # Merge in whatever per-partner rates the get_delivery_and_rate() call
    # returned (UI echoes all quoted rates back, not just the chosen one).
    for field in DELIVERY_PARTNER_RATE_FIELDS:
        detail[field] = str(delivery_rates.get(field, "")) if delivery_rates.get(field) not in (None, "") else ""

    return detail
