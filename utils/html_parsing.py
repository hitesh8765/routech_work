"""
Small, dependency-light HTML helpers for parsing the server-rendered
fragments Routech returns (e.g. /bookings/booking_form).

We deliberately avoid a full HTML parser dependency where a regex is
reliable enough, but use BeautifulSoup where structure matters (the
pickup-location <select> has dozens of data-* attributes per <option>).
"""
import re
from typing import Optional

from bs4 import BeautifulSoup


def extract_csrf_token(html: str) -> Optional[str]:
    """
    Scrapes the hidden `_csrf` input's value from a rendered page.
    Routech's login/booking forms embed it as:
        <input type="hidden" name="_csrf" value="...">
    """
    soup = BeautifulSoup(html, "html.parser")
    field = soup.find("input", {"name": "_csrf"})
    if field and field.get("value"):
        return field["value"]

    # Fallback: some pages expose it as a meta tag instead.
    meta = soup.find("meta", {"name": "csrf-token"})
    if meta and meta.get("content"):
        return meta["content"]

    # Last-resort regex in case markup structure changes slightly.
    match = re.search(r'name=["\']_csrf["\']\s+value=["\']([^"\']+)["\']', html)
    return match.group(1) if match else None


def parse_pickup_locations(booking_form_html: str) -> list[dict]:
    """
    Parses the `#pickup_location_0` <select> returned by
    POST /bookings/booking_form into a list of saved business locations:

        {
            "id": "69b93763d50e7a681af945b5",
            "label": "Jeddah Park by Arabian Centres, Jeddah Saudi Arabia",
            "address": "JCZA8637، 8637 ...",
            "latitude": "21.5565224",
            "longitude": "39.1851878",
            "city": "Jeddah",
            "state": "Makkah Province",
            "country": "Saudi Arabia",
            "country_code": "SA",
            "postal_code": "23334",
            "additional_address": "",
            "national_address": "JCZA8637",
        }

    Skips the two placeholder options ("Select Business Location", "Add New Location").
    """
    soup = BeautifulSoup(booking_form_html, "html.parser")
    select = soup.find("select", {"id": "pickup_location_0"})
    if not select:
        return []

    locations = []
    for option in select.find_all("option"):
        value = option.get("value", "")
        if value in ("", "add_new"):
            continue
        locations.append(
            {
                "id": value,
                "label": option.get_text(strip=True),
                "address": option.get("data-location", ""),
                "latitude": option.get("data-latitude", ""),
                "longitude": option.get("data-longitude", ""),
                "city": option.get("data-city", ""),
                "state": option.get("data-state", ""),
                "country": option.get("data-country", ""),
                "country_code": option.get("data-country_code", ""),
                "postal_code": option.get("data-postal_code", ""),
                "additional_address": option.get("data-additional_address", ""),
                "national_address": option.get("data-national-address", ""),
            }
        )
    return locations


def saudi_locations(locations: list[dict]) -> list[dict]:
    """Filter helper for the 'one side of the booking must be Saudi' rule."""
    return [loc for loc in locations if loc.get("country_code", "").upper() == "SA"]


def non_saudi_locations(locations: list[dict]) -> list[dict]:
    return [loc for loc in locations if loc.get("country_code", "").upper() != "SA"]
