"""
Test-data pools and payload builders, encoding the business rules given
during the walkthrough:

  - For every booking, exactly one of {pickup, dropoff} must be a Saudi
    Arabia location; the other must be non-Saudi. Which side is Saudi
    strictly alternates booking to booking, globally, across all booking
    types -- see data/diversity_tracker.py.
  - Insurance: always "No".
  - Shipment Invoice: always "Create Invoice" (never "Upload Invoice").
  - Item Name: random from ITEM_NAME_POOL (except Envelope, which is fixed).
  - Item Quantity: random from ITEM_QUANTITY_POOL (except Envelope, which is fixed).
  - HS Code: looked up using the SAME string as the item name (except Envelope, which has none).
  - Item Price: random from ITEM_PRICE_POOL (except Envelope, which is fixed).
  - Actual Weight: random int in [8, 16] for Parcel; 1 or 2 for Envelope.
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
from datetime import datetime
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
# specified -- picked as a whole combo, never mixed-and-matched. PARCEL-ONLY
# (small items). For Pallet, see PALLET_WEIGHT_DIMENSION_COMBOS below.
DIMENSION_COMBO_POOL = [
    (10, 40, 35, 30),
    (11, 45, 35, 30),
    (12, 45, 40, 30),
    (13, 50, 40, 30),
    (14, 50, 45, 30),
    (15, 55, 45, 30),
]
ACTUAL_WEIGHT_RANGE = (10, 15)

# PALLET-ONLY rule, REPLACED 2026-09-23 (superseded the previous separate
# weight-range + length>width>height dimension pool -- that rule is now
# WRONG and must not be used). Real Pallet package dimensions are a flat,
# wide footprint with a SMALL height (like an actual shipping pallet),
# e.g. length==width==100 with height only 14 -- NOT length>width>height.
# Each (actual_weight, length, width, height) 4-tuple is used as ONE
# coupled combo -- weight and dimensions are NOT independently randomized
# for Pallet, unlike Parcel. Exact combos given directly by the user, with
# a real screenshot reference (Actual Weight 82, Length 100, Width 100,
# Height 14 was one of the on-screen examples):
PALLET_WEIGHT_DIMENSION_COMBOS = [
    (82, 100, 100, 14),
    (81, 110, 110, 14),
    (80, 122, 102, 14),
    (79, 120, 100, 15),
    (75, 120, 80, 14),
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


def extract_available_partners(delivery_rates: dict) -> list:
    """
    RULE (user-mandated 2026-10-07, backed by real response data): only
    partners that have a REAL quoted amount for this route may be selected.

    /bookings/get_delivery_and_rate returns a 'days' dict (every partner the
    route theoretically supports) and an 'amount' dict (only partners with
    an actual price). Choosing a partner that is in 'days' but missing from
    'amount' is either rejected outright ("Please try again with another
    delivery partner or change the location") or creates a booking that is
    stuck on status "Requested" forever and never shows in the application.

    A partner counts as available only if it is in 'amount' AND its
    payable_amount is a positive number. (A payable_amount of 0 was seen
    for aramex on one pallet route; a booking with no price, ppd_amount
    null, was the stuck one -- so 0 is treated as "no real quote".)
    """
    amounts = (delivery_rates or {}).get("amount", {}) or {}
    available = []
    for partner, info in amounts.items():
        payable = (info or {}).get("payable_amount")
        if isinstance(payable, (int, float)) and payable > 0:
            available.append(partner)
    return available


def volumetric_weight(length: int, width: int, height: int) -> float:
    """
    Dimensional weight as the app computes it: L*W*H / 5000, rounded UP to
    the nearest 0.5 (verified against real responses: 100x100x14 -> 28,
    120x100x15 -> 36, 15x12x11 -> 0.5).
    """
    import math
    return math.ceil((length * width * height / 5000) * 2) / 2


def build_rate_payload(
    booking_type: str,
    pickup: dict,
    dropoff: dict,
    actual_weight: int,
    length: int,
    width: int,
    height: int,
) -> dict:
    """
    Payload for /bookings/get_delivery_and_rate built from the REAL
    weight/dimensions of the booking about to be created.
    """
    if booking_type == "envelope":
        # Documents have no volumetric weight; higher_weight equals actual weight
        dimension_weight = 0
        higher_weight = actual_weight
    else:
        dimension_weight = volumetric_weight(length, width, height)
        higher_weight = max(actual_weight, dimension_weight)
    
    is_palletized = "true" if booking_type == "pallet" else "false"
    
    return {
        "type": "b2c",
        "pickup_city": pickup.get("city", ""),
        "dropoff_city": dropoff.get("city", ""),
        "pickup_country": pickup.get("country", ""),
        "dropoff_country": dropoff.get("country", ""),
        "pickup_country_code": pickup.get("country_code", ""),
        "dropoff_country_code": dropoff.get("country_code", ""),
        "dropoff_postal_code": dropoff.get("postal_code", ""),
        "pickup_postal_code": pickup.get("postal_code", ""),
        "pickup_state_code": pickup.get("state", ""),
        "dropoff_state_code": dropoff.get("state", ""),
        "is_international": "1",
        "delivery_reg_same_type": "regular",
        "payment_type": "ppd",
        "shipment_content_type": "dry",
        "weight_per_kg": "",
        "package_dimension[0][length]": str(length),
        "package_dimension[0][width]": str(width),
        "package_dimension[0][height]": str(height),
        "package_dimension[0][actual_weight]": str(actual_weight),
        "package_dimension[0][weight]": str(dimension_weight),
        "package_dimension[0][higher_weight]": str(higher_weight),
        "offer_code": "",
        "booking_type": booking_type,
        "is_palletized": is_palletized,
    }


def random_weight_and_dimensions(booking_type: str = "parcel") -> tuple:
    """
    Returns (actual_weight, length, width, height) as ONE combo.

    Parcel: weight and dimension-combo are independently randomized
    (weight from ACTUAL_WEIGHT_RANGE; dims as a whole combo from
    DIMENSION_COMBO_POOL, preserving length>width>height).

    Pallet: all four values picked together, whole, from
    PALLET_WEIGHT_DIMENSION_COMBOS.

    Envelope: weight is ALWAYS 1 or 2, dimensions are fixed at 1x1x1.
    """
    if booking_type == "pallet":
        return random.choice(PALLET_WEIGHT_DIMENSION_COMBOS)
    if booking_type == "envelope":
        return random.choice([1, 2]), 1, 1, 1
        
    actual_weight = random.randint(*ACTUAL_WEIGHT_RANGE)
    length, width, height = random.choice(DIMENSION_COMBO_POOL)
    return actual_weight, length, width, height


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
    weight_and_dimensions: tuple = None,
    item_name: str = None,
) -> dict:
    """
    Builds the exact `booking_detail[0]` dict shape captured live for
    Parcel, Pallet, and Envelope (Documents).
    """
    actual_weight, length, width, height = weight_and_dimensions or random_weight_and_dimensions(booking_type)
    receiver_dial_code, receiver_mobile = random_mobile_for_country(dropoff_location.get("country_code", ""))
    sender_dial_code, sender_mobile = random_mobile_for_country(pickup_location.get("country_code", ""))

    is_palletized = "true" if booking_type == "pallet" else "false"

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
        "is_international": "1",
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
        "insurance": "0",
        "stackable": "1",
        "weight_per_kg": "",
        "payment_type": "ppd",
        "cod_amount": "",
        "schedule_type": "schedule_now",
        "currency_code": "SAR",
        "currency_value": "1",
        "delivery_type": "regular",
        "is_invoice_required": "0",
        "shipping_method": "home",
        "shipment_content_type": "dry",
        "quantity": "1",
        "is_offer_apply": "false",
        "offer_code": "",
        "delivery_partners": delivery_partner,
    }

    # Envelope-specific rules (no HS code, fixed item, fixed dims, current time)
    if booking_type == "envelope":
        detail["scheduled_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        detail["item_list_details"] = [
            {
                "item_name": "Envelope Shipment",
                "item_quantity": "1",
                "item_detail": "",
                "item_price": "1"
            }
        ]
        detail["package_dimensions"] = [
            {
                "actual_weight": str(actual_weight),
                "length": "1",
                "width": "1",
                "height": "1"
            }
        ]
    else:
        # Parcel and Pallet rules
        detail["is_location_unknown"] = "0"
        detail["scheduled_time"] = ""
        
        item = random_item()
        if item_name:
            item["item_name"] = item_name
            
        detail["item_list_details"] = [
            {
                "item_name": item["item_name"],
                "item_quantity": item["item_quantity"],
                "item_detail": "",
                "hs_code": hs_code,
                "item_price": item["item_price"],
            }
        ]
        detail["package_dimensions"] = [
            {
                "actual_weight": str(actual_weight),
                "length": str(length),
                "width": str(width),
                "height": str(height),
            }
        ]
        
        if booking_type == "parcel":
            detail["pallet_container_id"] = ""
            
    # Only include is_palletized for Parcel and Pallet (unchecked for envelope)
    if booking_type != "envelope":
        detail["is_palletized"] = is_palletized

    # Fill delivery_rate_<partner> from the REAL nested response shape
    amounts = (delivery_rates or {}).get("amount", {}) or {}
    for field in DELIVERY_PARTNER_RATE_FIELDS:
        partner_key = field[len("delivery_rate_"):]
        payable = (amounts.get(partner_key) or {}).get("payable_amount")
        detail[field] = str(payable) if payable not in (None, "") else ""
        
    # Ensure the chosen partner's rate is present even if its name is not in DELIVERY_PARTNER_RATE_FIELDS
    chosen_key = f"delivery_rate_{delivery_partner}"
    chosen_payable = (amounts.get(delivery_partner) or {}).get("payable_amount")
    if chosen_key not in detail and chosen_payable not in (None, ""):
        detail[chosen_key] = str(chosen_payable)

    return detail


def build_parcel_booking_detail(**kwargs) -> dict:
    return build_booking_detail(booking_type="parcel", **kwargs)


def build_pallet_booking_detail(**kwargs) -> dict:
    """
    Confirmed identical to Parcel's request shape (2026-09-21 live capture)
    except booking_type and no pallet_container_id -- see build_booking_detail().
    """
    return build_booking_detail(booking_type="pallet", **kwargs)


def build_envelope_booking_detail(**kwargs) -> dict:
    """
    Documents/Envelope booking builder.
    Uses fixed item details, 1x1x1 dimensions, weight 1 or 2, and no HS code.
    """
    return build_booking_detail(booking_type="envelope", **kwargs)

#------------------------------------------------------------------------------
#-----------------------------------------------------------------------------
# =========================================================================
# LUGGAGE-SPECIFIC BUILDERS
# =========================================================================

LUGGAGE_TYPES = {
    "small-luggage": {"name": "Small Luggage", "weight": 15, "height": 56, "width": 38, "length": 28},
    "medium-luggage": {"name": "Medium Luggage", "weight": 20, "height": 66, "width": 46, "length": 28},
    "standard-luggage": {"name": "Standard Luggage", "weight": 22, "height": 76, "width": 30, "length": 48},
    "large-luggage": {"name": "Large Luggage", "weight": 25, "height": 81, "width": 51, "length": 30},
    "x-large-luggage": {"name": "X Large Luggage", "weight": 30, "height": 86, "width": 56, "length": 36},
    "oversize-luggage": {"name": "Oversize Luggage", "weight": 32, "height": 97, "width": 58, "length": 36},
}

def build_luggage_rate_payload(
    pickup: dict,
    dropoff: dict,
    luggage_list: dict,
) -> dict:
    return {
        "type": "b2c",
        "weight": "",
        "pickup_city": pickup.get("city", ""),
        "dropoff_city": dropoff.get("city", ""),
        "pickup_country": pickup.get("country", ""),
        "dropoff_country": dropoff.get("country", ""),
        "pickup_country_code": pickup.get("country_code", ""),
        "dropoff_country_code": dropoff.get("country_code", ""),
        "is_international": "1",
        "delivery_reg_same_type": "regular",
        "payment_type": "ppd",
        "shipment_content_type": "dry",
        "luggage_list": json.dumps(luggage_list, separators=(",", ":")),
        "dropoff_postal_code": dropoff.get("postal_code", ""),
        "pickup_postal_code": pickup.get("postal_code", ""),
        "pickup_state_code": pickup.get("state", ""),
        "dropoff_state_code": dropoff.get("state", ""),
        "luggage_type": "true",
        "offer_code": "",
        "is_offer_apply": "false",
    }

def build_luggage_booking_detail(
    pickup_location: dict,
    dropoff_location: dict,
    receiver_name: str,
    delivery_rates: dict,
    delivery_partner: str,
    luggage_id: str,
    luggage_slug: str,
    luggage_specs: dict,
    item_name: str,
    item_quantity: str,
    item_price: str,
    hs_code: str,
    sender_name: str = "Neeraj Sharma",
) -> dict:
    receiver_dial_code, receiver_mobile = random_mobile_for_country(dropoff_location.get("country_code", ""))
    sender_dial_code, sender_mobile = random_mobile_for_country(pickup_location.get("country_code", ""))

    luggage_list_obj = {
        luggage_slug: {
            "weight": str(luggage_specs["weight"]),
            "quantity": "1",
            "name": luggage_specs["name"],
            "weight_unit": "KG",
            "slug": luggage_slug,
            "height": str(luggage_specs["height"]),
            "width": str(luggage_specs["width"]),
            "length": str(luggage_specs["length"]),
        }
    }

    detail = {
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
        "luggage_type": "luggage",
        "luggage_list": json.dumps(luggage_list_obj, separators=(",", ":")),
        "type_partner": "b2c",
        "type": "b2c",
        "pickup_national_address": pickup_location.get("national_address", ""),
        "dropoff_address": dropoff_location.get("address", dropoff_location.get("label", "")),
        "dropoff_latitude": str(dropoff_location.get("latitude", "")),
        "dropoff_longitude": str(dropoff_location.get("longitude", "")),
        "dropoff_city": dropoff_location.get("city", ""),
        "dropoff_state": dropoff_location.get("state", ""),
        "dropoff_country": dropoff_location.get("country", ""),
        "dropoff_country_code": dropoff_location.get("country_code", ""),
        "dropoff_postal_code": dropoff_location.get("postal_code", ""),
        "dropoff_additional_address": dropoff_location.get("additional_address", ""),
        "item_weight": "",
        "is_international": "1",
        "dropoff_national_address": dropoff_location.get("national_address", ""),
        "collection_point_address": "",
        "collection_point_latitude": "",
        "collection_point_longitude": "",
        "collection_point_city": "",
        "collection_point_area": "",
        "collection_point_country": "",
        "booking_type": "luggage",
        "stakeholder_name": receiver_name,
        "country_code": receiver_dial_code,
        "stakeholders": receiver_mobile,
        "sender_name": sender_name,
        "sender_country_code": sender_dial_code,
        "sender_mobile_number": sender_mobile,
        "imported_order_id": "",
        "item_name": "Travel Luggage",
        "item_quantity": "1",
        "is_offer_apply": "false",
        "item_detail": "",
        "currency_code": "SAR",
        "currency_value": "1",
        "insurance": "0",
        "payment_type": "ppd",
        "cod_amount": "",
        "is_invoice_required": "0",
        "schedule_type": "schedule_now",
        "scheduled_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "item_list_details": {
            luggage_id: [
                {
                    f"{luggage_id}_0": [
                        {
                            "item_name": item_name,
                            "item_quantity": item_quantity,
                            "item_detail": "",
                            "hs_code": hs_code,
                            "item_price": item_price,
                            "luggage_name": luggage_specs["name"],
                        }
                    ]
                }
            ]
        },
        "delivery_type": "regular",
        "shipping_method": "home",
        "shipment_content_type": "dry",
        "offer_code": "",
        "delivery_partners": delivery_partner,
    }

    amounts = (delivery_rates or {}).get("amount", {}) or {}
    for field in DELIVERY_PARTNER_RATE_FIELDS:
        partner_key = field[len("delivery_rate_"):]
        payable = (amounts.get(partner_key) or {}).get("payable_amount")
        detail[field] = str(payable) if payable not in (None, "") else ""
        
    chosen_key = f"delivery_rate_{delivery_partner}"
    chosen_payable = (amounts.get(delivery_partner) or {}).get("payable_amount")
    if chosen_key not in detail and chosen_payable not in (None, ""):
        detail[chosen_key] = str(chosen_payable)

    return detail