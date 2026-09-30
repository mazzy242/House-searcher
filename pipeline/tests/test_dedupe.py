import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dedupe  # noqa: E402


def espc(id_, address, postcode, price=400000, beds=3, **kw):
    return {"id": id_, "address": address, "postcode": postcode, "price": price, "bedrooms": beds,
            "url": f"https://espc.com/property/x/{id_}", **kw}


def lot(id_, address, postcode, price=300000, beds=3, date="2026-10-08", house="FPA"):
    return {"id": id_, "address": address, "postcode": postcode, "price": price, "bedrooms": beds,
            "source": "auction", "url": f"https://auction/{id_}", "auction": {"house": house, "date": date, "basis": "Guide price"}}


def ids(out):
    return sorted(r["id"] for r in out)


def test_house_numbers_are_exact():
    k70 = dedupe.key("70 Kingston Avenue, Edinburgh", "EH16 5SW")
    k70a = dedupe.key("70a Kingston Avenue, Edinburgh", "EH16 5SW")
    assert k70.numbers == {"70"} and k70a.numbers == {"70a"}
    assert dedupe.relation(espc("1", "70 Kingston Avenue", "EH16 5SW"), espc("2", "70a Kingston Avenue", "EH16 5SW")) is None


def test_lot_covering_two_homes_only_partly_overlaps():
    """ESPC '70' and an auction lot '70 and 70a' are different sales: keep both, cross-referenced."""
    e = espc("36376882", "The Coach House, 70 Kingston Avenue, Edinburgh", "EH16 5SW", 495000, 4)
    a = lot("auc-fpa-1", "Auction Details - 70 and 70a Kingston Avenue, Edinburgh - GUIDE PRICE £0", "EH16 5SW", 864000, 5)
    out, notes = dedupe.dedupe([e, a])
    assert ids(out) == ["36376882", "auc-fpa-1"]
    assert e["in_auction_lot"][0]["price"] == 864000 and a["overlaps_espc"][0]["id"] == "36376882"
    assert "both kept" in notes[0]


def test_auction_of_the_same_home_is_folded_into_espc():
    e = espc("36400001", "14 Craiglockhart Road, Edinburgh", "EH14 1AA", 350000)
    a = lot("auc-fpa-2", "14 Craiglockhart Road, Edinburgh, EH14 1AA", "EH14 1AA", 210000)
    out, _ = dedupe.dedupe([a, e])            # order doesn't matter: ESPC always leads
    assert ids(out) == ["36400001"]
    assert out[0]["also_auction"][0]["house"] == "FPA" and out[0]["price"] == 350000


def test_relisted_espc_home_keeps_newest_listing_and_history():
    old = espc("36300000", "5 Oak Lane, Edinburgh", "EH12 5XX", 420000, first_seen="2026-08-01",
               price_history=[["2026-08-01", 450000], ["2026-08-20", 420000]])
    new = espc("36410000", "5 Oak Lane, Edinburgh", "EH12 5XX", 410000, first_seen="2026-09-25",
               price_history=[["2026-09-25", 410000]])
    out, _ = dedupe.dedupe([old, new])
    assert ids(out) == ["36410000"]
    r = out[0]
    assert r["relisted"] and r["first_seen"] == "2026-08-01" and [p for _, p in r["price_history"]] == [450000, 420000, 410000]


def test_new_build_house_types_are_not_merged():
    """ESPC shows several plots/house types at one development with the same postcode."""
    a = espc("1", "The Fulton Edgelaw View, Edinburgh", "EH17 8SD", 342000)
    b = espc("2", "Fulton Edgelaw View, Edinburgh", "EH17 8SD", 315000)
    c = espc("3", "Plot 13 The Hopetoun Forthview, South Queensferry", "EH30 9NE")
    d = espc("4", "Plot 10 Dalmeny Ferrymuir Gait, South Queensferry", "EH30 9NE")
    e = espc("5", "13 Ferrymuir Gait, South Queensferry", "EH30 9NE")
    out, notes = dedupe.dedupe([a, b, c, d, e])
    assert len(out) == 5 and not notes


def test_same_number_different_street_same_postcode():
    a = espc("1", "2 Main Street, Ratho", "EH28 8RT")
    b = lot("auc-x", "2 Station Road, Ratho", "EH28 8RT")
    assert dedupe.relation(a, b) is None


def test_named_house_matches_across_sources_only():
    e = espc("1", "Rose Cottage, Main Street, Ratho", "EH28 8RT", beds=3)
    a = lot("auc-y", "Rose Cottage, Main Street, Ratho", "EH28 8RT", beds=3)
    out, _ = dedupe.dedupe([e, a])
    assert ids(out) == ["1"] and out[0]["also_auction"]


def test_same_lot_at_two_auction_houses_keeps_earliest():
    a = lot("auc-a", "9 Elm Row, Musselburgh", "EH21 7AA", date="2026-10-22", house="AHS")
    b = lot("auc-b", "9 Elm Row, Musselburgh", "EH21 7AA", date="2026-10-08", house="FPA")
    out, _ = dedupe.dedupe([a, b])
    assert ids(out) == ["auc-b"] and out[0]["also_auction"][0]["house"] == "AHS"
