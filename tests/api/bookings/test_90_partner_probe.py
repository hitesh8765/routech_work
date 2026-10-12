"""
Partner probe -- OPT-IN diagnostic, not part of the normal suite.

Run it with:   pytest -s -m probe

Question it answers with data instead of guesses: "which delivery partner
actually works for which booking type + direction?" Evidence that prompted
it (reports/booking_report.xlsx, 2026-10-08): Aramex 11/11 and UPS 11/11
(parcel, Saudi pickup only), FedEx variants 0/5 on those same parcels but
3/3 on pallets with Saudi dropoff, and a UPS pallet (Germany -> Saudi) that
the server priced at 0 even though get_delivery_and_rate quoted it. Because
each booking type always landed on the same Saudi side, type and direction
were tangled together in the history.

For each (booking type, Saudi side) it quotes ONE route, then calls
/bookings/add once per partner that has a quote (payload identical except
delivery_partners) and records PRICED / NO PRICE / REJECTED. It NEVER pays.
SIDE EFFECT: every add creates an unpaid server-side booking record, so
expect ~25 extra unpaid bookings per full probe run. It does not touch the
Saudi-side alternation or the partner rotation state.

Results are printed and appended to reports/partner_probe.csv.
"""
import csv
from datetime import datetime
from pathlib import Path

import pytest

from utils.html_parsing import saudi_locations, non_saudi_locations
from tests.api.bookings.booking_test_helpers import probe_partners, usable_locations, step

PROBE_CSV = Path(__file__).resolve().parent.parent.parent.parent / "reports" / "partner_probe.csv"
CSV_COLUMNS = [
    "probed_at", "type", "direction", "route", "partner", "quoted_amount",
    "add_ppd_amount", "outcome", "sender_phone_digits", "receiver_phone_digits", "weight_dims",
]


def _append_csv(rows: list):
    PROBE_CSV.parent.mkdir(exist_ok=True)
    new_file = not PROBE_CSV.exists()
    with PROBE_CSV.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if new_file:
            writer.writeheader()
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for row in rows:
            writer.writerow({"probed_at": stamp, **row})


@pytest.mark.api
@pytest.mark.probe
@pytest.mark.parametrize(
    "booking_type,saudi_side",
    [("parcel", "pickup"), ("parcel", "dropoff"), ("pallet", "pickup"), ("pallet", "dropoff")],
)
def test_partner_probe(api_client, booking_type, saudi_side):
    all_locations = usable_locations(api_client.get_saved_locations(booking_type=booking_type))
    sa_locations = saudi_locations(all_locations)
    intl_locations = non_saudi_locations(all_locations)
    assert sa_locations and intl_locations, "Need both Saudi and non-Saudi usable saved locations."

    results = probe_partners(api_client, booking_type, saudi_side, sa_locations, intl_locations)
    assert results, "No partner had a quote to probe."
    _append_csv(results)

    print(f"\n=== PROBE {booking_type} / Saudi {saudi_side} / {results[0]['route']} ===")
    for r in results:
        print(f"  {r['partner']:24s} quoted={str(r['quoted_amount']):>9s}  add_ppd={str(r['add_ppd_amount']):>9s}  {r['outcome']}")
    step("probe saved", csv=str(PROBE_CSV), rows=len(results))
