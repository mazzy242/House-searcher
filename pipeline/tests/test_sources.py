"""Seller flags on ESPC listings, auction lots, and EPC matching."""
import datetime as dt
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auctions  # noqa: E402
import build  # noqa: E402
import epc  # noqa: E402
import espc  # noqa: E402

TODAY = dt.date(2026, 9, 29)


# ------------------------------------------------------------------ ESPC seller flags

def _page(desc: str, extra: str = "") -> str:
    return (f"<title>3 bed detached house for sale in Colinton</title><h1>5 A Road, Edinburgh EH13 0AA</h1>"
            f"<meta name='description' content='{desc}'><div>Offers over £450,000</div>{extra}")


def test_motivated_and_needs_work_flags():
    rec = espc.parse_property(_page("Offered on behalf of the heritable creditor. The property requires full "
                                    "modernisation throughout and is suited to cash buyers."), "https://espc.com/property/x/36000001")
    assert rec["flags"] == ["motivated", "needs_work"]
    rec = espc.parse_property(_page("An executry sale offering scope for updating."), "https://espc.com/property/x/36000002")
    assert rec["flags"] == ["motivated", "needs_work"]
    rec = espc.parse_property(_page("Immaculately presented family home."), "https://espc.com/property/x/36000003")
    assert "flags" not in rec


def test_closing_date_parsing():
    assert espc.closing_date("Closing date: Friday 10th October at 12 noon", TODAY) == "2026-10-10"
    assert espc.closing_date("A closing date has been set for 3 Nov 2026", TODAY) == "2026-11-03"
    assert espc.closing_date("Closing date 14/10/2026 at 12pm", TODAY) == "2026-10-14"
    assert espc.closing_date("Closing date 8th January", dt.date(2026, 12, 20)) == "2027-01-08"
    assert espc.closing_date("No closing date set", TODAY) is None


def test_word_bedrooms():
    html = "<title>Detached villa</title><h1>1 B Road EH4 1AA</h1><p>A spacious three bedroom detached villa.</p><div>£500,000</div>"
    assert espc.parse_property(html, "https://espc.com/property/x/36000004")["bedrooms"] == 3


# ------------------------------------------------------------------ auctions

CATALOGUE = """<html><body>
<div class="lot"><a href="lot_details.asp?id=5001">Lot 12</a>
  <p>14 Craiglockhart Road, Edinburgh EH14 1AA - Three bedroom semi-detached house. Opening bid £210,000</p></div>
<div class="lot"><a href="lot_details.asp?id=5002">Lot 13</a>
  <p>Flat 1, 1 East Pilton Farm Crescent, Edinburgh EH5 2AA - two bedroom flat. Opening bid £87,000</p></div>
<div class="lot"><a href="lot_details.asp?id=5003">Lot 14</a>
  <p>3 Main Street, Bathgate EH48 1AA - four bedroom detached house. Opening bid £150,000</p></div>
<a href="catalogue_viewall_auction.asp?id=10651">All lots</a>
</body></html>"""

LOT = """<html><head><title>14 Craiglockhart Road, Edinburgh, EH14 1AA | Future Property Auctions</title></head><body>
<nav><a href="/repossessions.asp">Repossessions</a> <a href="/sell.asp">Sell to cash buyers</a></nav>
<h1>14 Craiglockhart Road, Edinburgh, EH14 1AA</h1>
<p>Timed online auction: 8th October 2026, 10am - 3pm</p>
<p>Opening Bid: £210,000*</p>
<p>A three bedroom semi-detached house requiring modernisation, sold on behalf of the heritable creditor.</p>
<img src="https://maps.googleapis.com/maps/api/staticmap?center=55.9260,-3.2380&zoom=15">
<p>Previous auction 12/05/2025</p>
</body></html>"""

BASE = "https://www.futurepropertyauctions.co.uk/catalogue_area.asp?search=Edinburgh"
FPA_RX = "(?i)^(?!.*catalogue).*(\\.asp\\?(?:[^\"']*&)?(?:id|lotid|propertyid|pid)=\\d+|/lots?/\\d+|/property/\\d+)"


def test_lot_links_and_prefilter():
    links = auctions.lot_links(CATALOGUE, BASE, FPA_RX)
    assert sorted(links.values()) == [f"https://www.futurepropertyauctions.co.uk/lot_details.asp?id={i}" for i in (5001, 5002, 5003)]
    hints = auctions.card_hints(CATALOGUE, BASE, links)
    reasons = {links[i].rsplit("=", 1)[1]: auctions.prefilter(hints.get(i)) for i in links}
    assert reasons == {"5001": None, "5002": "flat", "5003": "district EH48"}


def test_parse_lot():
    rec = auctions.parse_lot(LOT, "https://www.futurepropertyauctions.co.uk/lot_details.asp?id=5001", "Future Property Auctions")
    assert rec["source"] == "auction" and rec["id"].startswith("auc-")
    assert rec["price"] == 210000 and rec["auction"]["basis"] == "Opening bid"
    assert rec["auction"]["date"] == "2026-10-08" or rec["auction"]["date"] >= dt.date.today().isoformat()
    assert rec["bedrooms"] == 3 and rec["district"] == "EH14"
    assert set(rec["flags"]) == {"motivated", "needs_work"}
    assert espc.rejection(rec["title"], rec.get("property_type", ""), rec["bedrooms"], rec["district"]) is None
    # The site's navigation ("Repossessions") must not flag an ordinary lot.
    plain = LOT.replace("requiring modernisation, sold on behalf of the heritable creditor", "in good order")
    assert "flags" not in auctions.parse_lot(plain, "https://x/lot_details.asp?id=9", "FPA")


def test_auction_date_prefers_auction_context():
    text = "Listed 01/09/2026. Guide £100,000. Online auction ends 22nd October 2026 at 3pm."
    assert auctions.auction_date(text, TODAY) == "2026-10-22"
    assert auctions.price_and_basis("Guide Price £145,000+") == (145000, "Guide price")


def test_auctions_scrape_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr(auctions, "DEBUG", tmp_path)
    monkeypatch.setattr(espc, "geocode_postcodes", lambda ls: None)
    monkeypatch.setitem(auctions.CFG, "sources", [{"name": "FPA", "start_urls": [BASE], "lot_link_regex": FPA_RX}])
    fetched = []

    class FakeFetcher:
        count = 0
        def __init__(self, base):
            pass
        def html(self, url):
            fetched.append(url)
            return CATALOGUE if "catalogue_area" in url else LOT
    monkeypatch.setattr(auctions, "Fetcher", FakeFetcher)
    monkeypatch.setattr(auctions.time, "sleep", lambda s: None)
    stale = {"auc-old": {"auction": {"house": "FPA", "date": "2026-01-01"}, "active": True}}
    state = auctions.scrape(stale)
    kept = [r for r in state.values() if r.get("active")]
    assert len(kept) == 1 and kept[0]["price"] == 210000
    assert "auc-old" not in state                              # no longer listed
    assert sum("lot_details" in u for u in fetched) == 1       # flat + Bathgate skipped from the catalogue


# ------------------------------------------------------------------ EPC

def _epc_zip(path: Path) -> Path:
    rows = [
        "OSG_REFERENCE_NUMBER,ADDRESS1,ADDRESS2,POST_TOWN,POSTCODE,CURRENT_ENERGY_RATING,TOTAL_FLOOR_AREA,LODGEMENT_DATE",
        "Reference,Address line 1,Address line 2,Town,Postcode,Current rating,Floor area,Date",  # 2nd header row
        "1,46 Woodhall Terrace,,EDINBURGH,EH14 5BR,D,131.5,2019-03-01",
        "2,46 Woodhall Terrace,,EDINBURGH,EH14 5BR,C,134.0,2024-06-12",   # newer certificate wins
        "3,48 Woodhall Terrace,,EDINBURGH,EH14 5BR,E,99,2020-01-01",
        "4,Rose Cottage,Main Street,RATHO,EH28 8RT,F,88,2021-02-02",
        "5,1 Somewhere,,GLASGOW,G1 1AA,B,70,2022-01-01",                  # outside our districts
    ]
    p = path / "epc.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("D_EPC_data_2026Q2.csv", "\n".join(rows))
    return p


def test_epc_index_and_match(tmp_path):
    index = epc.build_index(_epc_zip(tmp_path), {"EH14", "EH28"})
    assert set(index) == {"EH14 5BR", "EH28 8RT"}
    m = epc.match("46 Woodhall Terrace, Edinburgh, EH14 5BR", "EH14 5BR", index)
    assert m == {"band": "C", "floor_area_m2": 134, "date": "2024-06-12"}
    assert epc.match("Rose Cottage, Main Street, Ratho, EH28 8RT", "EH28 8RT", index)["band"] == "F"
    assert epc.match("12 Woodhall Terrace, Edinburgh", "EH14 5BR", index) is None      # no guessing
    assert epc.match("46 Woodhall Terrace", "EH99 9ZZ", index) is None


def test_enrich_fills_floor_area_from_epc(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "OUT", tmp_path)
    index = epc.build_index(_epc_zip(tmp_path), {"EH14"})
    base = {"url": "u", "district": "EH14", "postcode": "EH14 5BR", "bedrooms": 3, "detached": True,
            "garage": False, "lat": 55.91, "lng": -3.28}
    ls = [{**base, "id": "1", "price": 400000, "address": "46 Woodhall Terrace, Edinburgh"},
          {**base, "id": "2", "price": 500000, "address": "48 Woodhall Terrace, Edinburgh", "floor_area_m2": 120},
          {**base, "id": "3", "price": 90000, "address": "Lot", "source": "auction",
           "auction": {"house": "FPA", "date": "2026-10-08", "basis": "Opening bid"}}]
    out = {r["id"]: r for r in build.enrich(ls, None, None, None, index)}
    assert out["1"]["floor_area_m2"] == 134 and out["1"]["floor_area_source"] == "EPC" and out["1"]["epc"]["band"] == "C"
    assert out["2"]["floor_area_m2"] == 120 and "floor_area_source" not in out["2"]    # ESPC's own figure kept
    assert out["3"]["auction"]["basis"] == "Opening bid"
    areas = {a["district"]: a for a in __import__("json").loads((tmp_path / "areas.json").read_text())}
    assert areas["EH14"]["count"] == 2                                                  # auction lot not in averages


def test_one_id_per_lot_and_land_is_dropped():
    a = auctions.lot_id("https://www.futurepropertyauctions.co.uk/property_details.asp?id=14511302")
    b = auctions.lot_id("https://www.futurepropertyauctions.co.uk/offer_form.asp?id=14511302")
    assert a == b == "auc-futurepropertyauctions-14511302"
    assert auctions.lot_id("https://www.auctionhouse.co.uk/scotland/auction/lot/152411") == "auc-auctionhouse-152411"
    assert auctions.clean_title("Auction Details - 70 and 70a Kingston Avenue, Edinburgh -   GUIDE PRICE  £0") == \
        "70 and 70a Kingston Avenue, Edinburgh"
    assert auctions.prefilter("Plot 1 at Station Road, Broxburn EH52 5QR - Guide £112,000") is None      # plot: kept
    assert auctions.prefilter("Plot 6 Hartside Wood, Lauder TD2 6AA - Guide £4,000") == "district TD2"
    assert auctions.prefilter("Lock-up garage, Dalkeith EH22 1AA - Guide £9,000") == "not a home or plot"
    assert auctions.prefilter("12 Elm Row EH21 7AA - three bedroom detached house with garage. Guide £250,000") is None


def test_plots_are_their_own_kind():
    assert espc.is_plot("Plot for sale in Balerno")
    assert espc.is_plot("Building plot for sale in Gullane")
    assert espc.is_plot("3 Harelaw, Development with Full Planning, Danderhall", beds=4)
    # A new-build home advertised with its plot number is a home, not a plot.
    assert not espc.is_plot("4 bed detached house for sale in South Queensferry", beds=4)
    assert not espc.is_plot("3 bed detached house for sale in Colinton")
    html = ("<title>Plot for sale in Balerno</title><h1>Plot 2, Mansfield Road, Balerno EH14 7JZ</h1>"
            "<meta name='description' content='A building plot of approximately 0.35 acres with outline planning consent.'>"
            "<div>Offers over £180,000</div>")
    rec = espc.parse_property(html, "https://espc.com/property/plot-2-mansfield-road-balerno-eh14-7jz/36400099")
    assert rec["plot_acres"] == 0.35 and "bedrooms" not in rec
    assert espc.rejection(rec["title"], rec.get("property_type", ""), None, rec["district"]) is None
    lot_html = LOT.replace("14 Craiglockhart Road", "Plot 1 at Craiglockhart Road").replace(
        "A three bedroom semi-detached house requiring modernisation, sold on behalf of the heritable creditor.",
        "A building plot of 0.2 hectares with planning permission for a detached house.")
    lot = auctions.parse_lot(lot_html, "https://x/property_details.asp?id=77", "FPA")
    assert lot["kind"] == "plot" and lot["title"] == "Plot / land at auction" and lot["plot_acres"] == 0.49


def test_auction_plot_with_planned_bedrooms_is_a_plot():
    html = LOT.replace("14 Craiglockhart Road", "Plot 2 Station Road Development Plot").replace(
        "A three bedroom semi-detached house requiring modernisation, sold on behalf of the heritable creditor.",
        "Planning consent for a three bedroom detached house.")
    rec = auctions.parse_lot(html, "https://x/property_details.asp?id=88", "FPA")
    assert rec["kind"] == "plot" and rec["title"] == "Plot / land at auction"
