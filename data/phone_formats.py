"""
Country-aware mobile phone number formats: dial code + national
significant number length (digits dialed after the country code, no
leading 0), keyed by ISO alpha-2 country code (matches the `country_code`
field already present on our location dicts -- see utils/html_parsing.py).

Rule (given by the user, 2026-09-22): the receiver's (and, per a later
clarification, the sender's) mobile number length must match their actual
country's real format, or the server rejects it ("Invalid mobile number.
Mobile number must be N digits."). Examples given directly: India (+91)
10 digits, United States (+1) 10 digits, Saudi Arabia (+966) 9 digits,
Spain (+34) 9 digits, Singapore (+65) 8 digits, Hong Kong (+852) 8 digits.

NOTE (2026-09-22, live run): a non-Saudi booking's receiver number was
rejected with "must be 10 digits" even though our table said something
else for that country -- evidence the live server's ACTUAL validation
might be simpler than true per-country formats (e.g. "9 digits for Saudi,
10 digits for everything else"), not a full per-country lookup. This
table is kept as the best-effort per-country source of truth per the
user's instruction, but see handoff.md "Open Items" for the simpler
9-for-Saudi/10-for-everyone-else theory, which may need to take priority
if more countries keep coming back rejected.

This table is best-effort/not exhaustive -- extended with standard known
mobile-number lengths for countries likely to appear in the account's
saved locations (seen so far: France, Australia, Armenia, Ethiopia,
Bulgaria, Mauritius, India, USA, Saudi Arabia, United States) plus other
common countries. If a booking hits a NEW country not covered here and
the server rejects the phone number length, add it here rather than
guessing in the test -- the error message will say the required count.
"""
from typing import NamedTuple


class PhoneFormat(NamedTuple):
    dial_code: str
    digits: int


# Keyed by ISO alpha-2 country code.
PHONE_FORMAT_BY_ISO: dict[str, PhoneFormat] = {
    # Explicitly given by the user
    "IN": PhoneFormat("+91", 10),
    "US": PhoneFormat("+1", 10),
    "SA": PhoneFormat("+966", 9),
    "ES": PhoneFormat("+34", 9),
    "SG": PhoneFormat("+65", 8),
    "HK": PhoneFormat("+852", 8),
    # Countries already seen in this account's saved locations / bookings
    "FR": PhoneFormat("+33", 9),
    "AU": PhoneFormat("+61", 9),
    "AM": PhoneFormat("+374", 8),
    "ET": PhoneFormat("+251", 9),
    "BG": PhoneFormat("+359", 9),
    "MU": PhoneFormat("+230", 8),
    # Other common countries (best-effort, standard known lengths)
    "GB": PhoneFormat("+44", 10),
    "DE": PhoneFormat("+49", 10),
    "AE": PhoneFormat("+971", 9),
    "CN": PhoneFormat("+86", 11),
    "JP": PhoneFormat("+81", 10),
    "CA": PhoneFormat("+1", 10),
    "IT": PhoneFormat("+39", 10),
    "NL": PhoneFormat("+31", 9),
    "BR": PhoneFormat("+55", 11),
    "ZA": PhoneFormat("+27", 9),
    "PK": PhoneFormat("+92", 10),
    "BD": PhoneFormat("+880", 10),
    "PH": PhoneFormat("+63", 10),
    "ID": PhoneFormat("+62", 10),
    "KR": PhoneFormat("+82", 10),
    "MX": PhoneFormat("+52", 10),
    "TR": PhoneFormat("+90", 10),
    "EG": PhoneFormat("+20", 10),
    "NG": PhoneFormat("+234", 10),
    "KE": PhoneFormat("+254", 9),
    "RU": PhoneFormat("+7", 10),
    "CO": PhoneFormat("+57", 10),
    "AR": PhoneFormat("+54", 10),
    "JO": PhoneFormat("+962", 9),
    "KW": PhoneFormat("+965", 8),
    "QA": PhoneFormat("+974", 8),
    "BH": PhoneFormat("+973", 8),
    "OM": PhoneFormat("+968", 8),
    "LB": PhoneFormat("+961", 8),
}

# Used only if a country's ISO code isn't in the table above -- NOT
# guaranteed correct for that specific country, just a reasonable
# placeholder (9 digits is the most common mobile length worldwide). If
# this fires and the server rejects it, add the real entry above instead
# of relying on the fallback.
DEFAULT_PHONE_FORMAT = PhoneFormat("+1", 9)


def get_phone_format(iso_country_code: str) -> PhoneFormat:
    return PHONE_FORMAT_BY_ISO.get((iso_country_code or "").upper(), DEFAULT_PHONE_FORMAT)
