import datetime as dt
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build  # noqa: E402
import espc  # noqa: E402
import layers  # noqa: E402
import transit  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
WAV = (55.95196, -3.18992)


# ------------------------------------------------------------------ GTFS / travel time

def _gtfs(tmp_path: Path) -> Path:
    """A tiny network: bus 22 from Craigleith (4 stops) into Princes St by Waverley,
    every 10 minutes; a feeder bus 99 joins it at stop B."""
    stops = [
        ("A", "Craigleith", 55.9590, -3.2350),
        ("B", "Comely Bank", 55.9565, -3.2200),
        ("C", "Queensferry St", 55.9515, -3.2090),
        ("W", "Waverley Bridge", 55.9510, -3.1915),   # ~200 m from Waverley
        ("F", "Fettes", 55.9640, -3.2150),
    ]
    trips, st = [], []
    for k, start in enumerate(range(7 * 3600, 9 * 3600 + 1, 600)):
        tid = f"t22_{k}"
        trips.append(("r22", "wk", tid))
        for seq, (sid, off) in enumerate([("A", 0), ("B", 240), ("C", 600), ("W", 900)]):
            t = start + off
            st.append((tid, seq, sid, t))
    for k, start in enumerate(range(7 * 3600, 9 * 3600 + 1, 1200)):
        tid = f"t99_{k}"
        trips.append(("r99", "wk", tid))
        st += [(tid, 0, "F", start), (tid, 1, "B", start + 300)]
    fmt = lambda s: f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"  # noqa: E731
    files = {
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n" + "".join(f"{a},{b},{c},{d}\n" for a, b, c, d in stops),
        "routes.txt": "route_id,route_short_name,route_type\nr22,22,3\nr99,99,3\n",
        "trips.txt": "route_id,service_id,trip_id\n" + "".join(f"{r},{s},{t}\n" for r, s, t in trips),
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        "wk,1,1,1,1,1,0,0,20200101,20991231\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                          + "".join(f"{t},{fmt(x)},{fmt(x)},{s},{q}\n" for t, q, s, x in st),
    }
    p = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(p, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)
    return p


def test_service_date_is_midweek(tmp_path):
    with zipfile.ZipFile(_gtfs(tmp_path)) as z:
        day, svc = transit.pick_service_date(z, dt.date(2026, 9, 28))  # a Monday
    assert day.weekday() in (1, 2, 3) and svc == {"wk"}


def test_travel_time_direct_and_with_transfer(tmp_path):
    net = transit.load_network(_gtfs(tmp_path), dt.date(2026, 9, 28))
    profiles = transit.build_profiles(net)
    idx = transit.StopIndex(net)
    # Right next to stop A: ride is 15 min + ~3 min walk at Waverley end.
    j = transit.journey(55.9591, -3.2351, net, profiles, idx)
    assert 16 <= j["pt_min"] <= 22, j
    assert "bus 22" in j["pt_how"] and "Craigleith" in j["pt_how"]
    assert j["best_min"] <= j["walk_min"]
    # At Fettes: bus 99 to B (5 min) then change onto the 22 (11 min + walk).
    j2 = transit.journey(55.9641, -3.2151, net, profiles, idx)
    assert "pt_min" in j2 and j2["pt_min"] < j2["walk_min"], j2
    # Right by Waverley: walking wins and no silly bus trip is suggested.
    j3 = transit.journey(55.9525, -3.1905, net, profiles, idx)
    assert j3["best_how"] == "Walk" and j3["best_min"] <= 3


def test_rail_option_near_station():
    j = transit.journey(55.9340, -3.0740, None, [], None)  # by Musselburgh station
    assert j["rail_min"] < j["walk_min"] and "Musselburgh" in j["rail_how"]


# ------------------------------------------------------------------ ESPC parsing

def test_extract_property_urls():
    html = (FIX / "espc_search.html").read_text()
    urls = espc.extract_property_urls(html, "https://espc.com/properties?locations=edinburgh")
    assert set(urls) == {"36111111", "36222222", "36333333"}
    assert urls["36111111"].startswith("https://espc.com/property/")


def test_next_page():
    html = (FIX / "espc_search.html").read_text()
    assert espc.next_page_url(html, "https://espc.com/properties?locations=edinburgh", 1) == \
        "https://espc.com/properties?locations=edinburgh&page=2"


def test_parse_jsonld_property():
    rec = espc.parse_property((FIX / "espc_property_jsonld.html").read_text(),
                              "https://espc.com/property/12-barnton-avenue-edinburgh-eh4-6aa/36111111")
    assert rec["id"] == "36111111"
    assert rec["price"] == 545000 and rec["price_qualifier"] == "Offers Over"
    assert rec["bedrooms"] == 4
    assert rec["detached"] is True and rec["garage"] is True
    assert rec["postcode"] == "EH4 6AA" and rec["district"] == "EH4"
    assert abs(rec["lat"] - 55.9625) < 1e-6
    assert rec["bathrooms"] == 2 and rec["floor_area_m2"] == 168


def test_parse_app_state_property():
    rec = espc.parse_property((FIX / "espc_property_nextdata.html").read_text(),
                              "https://espc.com/property/flat-3-5-marchmont-road-edinburgh-eh9-1hb/36222222")
    assert rec["price"] == 295000 and rec["bedrooms"] == 2
    assert rec["detached"] is False and rec["garage"] is False
    assert rec["lat"] and rec["district"] == "EH9"
    assert rec["bathrooms"] == 1 and rec["floor_area_m2"] == 68


def test_parse_text_only_semi_detached():
    rec = espc.parse_property((FIX / "espc_property_text.html").read_text(),
                              "https://espc.com/property/7-oak-lane-edinburgh-eh12-5xx/36333333")
    assert rec["price"] == 410000 and rec["price_qualifier"] == "Fixed Price"
    assert rec["bedrooms"] == 3
    assert rec["detached"] is False       # semi-detached must not count
    assert rec["garage"] is False         # "no garage" in the description
    assert rec["postcode"] == "EH12 5XX" and "lat" in rec and rec["lat"] > 55
    assert rec["bathrooms"] == 2                        # the listing's own, not the "similar homes" one
    assert rec["floor_area_m2"] == 100                  # 1,076 sq ft -> 100 m²
    assert rec["epc"] == {"band": "C"}                  # "EPC rating C." in the page text


def test_epc_band_from_jsonld_and_text():
    rec = espc.parse_property((FIX / "espc_property_jsonld.html").read_text(),
                              "https://espc.com/property/12-barnton-avenue-edinburgh-eh4-6aa/36111111")
    assert rec["epc"] == {"band": "D"}                  # JSON-LD, not the council tax band G
    for txt, band in (("EPC rating C", "C"), ("EPC: Band E", "E"), ("EPC Rating b", None),
                      ("EPC A", "A"), ("Ask for the EPC a copy", None), ("EPCs available", None)):
        m = espc.EPC_TEXT.search(txt)
        assert (m.group(1) if m else None) == band, txt


# ------------------------------------------------------------------ layers / enrichment

def test_esri_polygon_conversion_keeps_hole():
    outer = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]         # clockwise
    hole = [[2, 2], [8, 2], [8, 8], [2, 8], [2, 2]]              # anticlockwise
    g = layers.esri_to_geojson_geometry({"rings": [outer, hole]})
    assert g["type"] == "Polygon" and len(g["coordinates"]) == 2


def test_top_school_matching_on_fallback_names():
    fc = json.loads((layers.CONFIG / "catchments_fallback.geojson").read_text())
    tops = json.loads((layers.CONFIG / "top_schools.json").read_text())["schools"]
    matched = {}
    for f in fc["features"]:
        t = layers._match_top(f["properties"]["school"], tops, f["properties"]["sector"])
        if t:
            matched[t["rank"]] = f["properties"]["school"]
    assert set(matched) == set(range(1, 11))
    assert "Thomas" in matched[4]                     # the one RC school in the ESPC top 10
    # An ND school name must not pick up an RC ranking and vice versa.
    assert layers._match_top("Trinity Academy", tops, "RC") is None


def test_area_stats_and_polygon_lookup():
    ls = [{"district": "EH4", "price": p, "bedrooms": 3, "lat": 55.96, "lng": -3.28} for p in (300000, 400000, 500000)]
    a = build.area_stats(ls)[0]
    assert a["median"] == 400000 and a["by_beds"]["3"]["count"] == 3
    fc = json.loads((layers.CONFIG / "catchments_fallback.geojson").read_text())
    find = build.polygon_lookup(fc)
    schools = {c["school"] for c in find(55.9330, -3.2090)}   # Bruntsfield Links area
    assert any("Gillespie" in s or "Boroughmuir" in s for s in schools), schools


def test_fetch_simd_field_detection(monkeypatch):
    sq = {"type": "Polygon", "coordinates": [[[-3.2, 55.9], [-3.2, 55.91], [-3.19, 55.91], [-3.2, 55.9]]]}
    rows = [{"type": "Feature", "geometry": sq, "properties": {
        "DataZone": "S01008600", "Name": "Morningside - 03", "LAName": "City of Edinburgh",
        "Rankv2": 6800, "IncRankv2": 20, "CrimeRank": 3500, "HouseRank": 700}}]
    monkeypatch.setattr(layers, "arcgis_query", lambda *a, **k: rows)
    monkeypatch.setattr(layers, "_simd_candidates", lambda s: iter([("u", "ua")]))
    p = layers.fetch_simd()["features"][0]["properties"]
    assert p["dz"] == "S01008600" and p["decile"] == 10
    assert p["income"] == 1 and p["crime"] == 6 and p["housing"] == 2


def test_fetch_transit_parsing(monkeypatch):
    way = lambda pts: {"type": "way", "role": "", "geometry": [{"lat": a, "lon": b} for a, b in pts]}  # noqa: E731
    payload = {"elements": [
        {"type": "relation", "tags": {"route": "tram", "name": "Tram"},
         "members": [way([(55.95, -3.36), (55.946, -3.22)]), way([(55.946, -3.22), (55.98, -3.19)])]},
        {"type": "relation", "tags": {"route": "bus", "ref": "22", "name": "Lothian 22: Ocean Terminal => Gyle Centre"},
         "members": [way([(55.98, -3.17), (55.95, -3.2)])]},
        {"type": "relation", "tags": {"route": "bus", "ref": "113", "name": "Lothian 113"},
         "members": [way([(55.93, -3.0), (55.95, -3.2)])]},
        {"type": "node", "lat": 55.9546, "lon": -3.1925, "tags": {"name": "St Andrew Square"}},
    ]}
    monkeypatch.setattr(layers, "_overpass", lambda q: payload)
    tram, bus, rail = layers.fetch_transit()
    assert tram["features"][0]["geometry"]["type"] == "LineString"   # the two ways merge
    assert any(f["properties"].get("name") == "St Andrew Square" for f in tram["features"])
    refs = [f["properties"]["ref"] for f in bus["features"]]
    assert refs == ["22", "113"]
    b22 = bus["features"][0]["properties"]
    assert b22["main"] is True and b22["name"] == "Ocean Terminal ↔ Gyle Centre"


def test_enrich_end_to_end(tmp_path, monkeypatch):
    """Parsed listing -> enriched record with catchment, area comparison and travel."""
    monkeypatch.setattr(build, "OUT", tmp_path)
    (tmp_path / "catchments.geojson").write_text((layers.CONFIG / "catchments_fallback.geojson").read_text())
    base = {"district": "EH10", "bedrooms": 3, "detached": False, "garage": False, "active": True}
    ls = [{**base, "id": str(i), "url": "u", "price": p, "lat": 55.9330, "lng": -3.2090}
          for i, p in enumerate((300000, 350000, 400000, 450000))]
    ls.append({**base, "id": "nogeo", "url": "u", "price": 1, "lat": None, "lng": None})
    prev = {"0": {"pt_min": 9, "pt_how": "Walk 2 min to X, then bus 23"}}
    out = build.enrich(ls, None, None, prev)
    assert [r["id"] for r in out] == ["0", "1", "2", "3"]
    r0 = out[0]
    assert r0["catchment"] and r0["area"]["median"] == 375000 and r0["area"]["basis"] == "3-bed"
    assert r0["travel"]["pt_min"] == 9 and r0["travel"]["best_min"] == 9     # reused previous PT time
    assert (tmp_path / "areas.json").exists()


def test_simd_falls_back_to_next_source(monkeypatch):
    sq = {"type": "Polygon", "coordinates": [[[-3.2, 55.9], [-3.2, 55.91], [-3.19, 55.91], [-3.2, 55.9]]]}
    good = [{"type": "Feature", "geometry": sq, "properties": {
        "DataZone": "S01008600", "SIMD2020v2_Income_Domain_Rank": 50, "SIMD2020v2_Rank": 3000, "Total_population": 800}}]

    def query(s, url, extra=None):
        if url == "blocked":
            raise RuntimeError("403 Client Error: Forbidden")
        return good
    monkeypatch.setattr(layers, "arcgis_query", query)
    monkeypatch.setattr(layers, "_simd_candidates", lambda s: iter([("blocked", "a"), ("mirror", "b")]))
    fc = layers.fetch_simd()
    p = fc["features"][0]["properties"]
    assert fc["source"] == "mirror"
    assert p["rank"] == 3000 and p["decile"] == 5    # overall rank, not the income-domain rank
    assert p["income"] == 1


# ------------------------------------------------------------------ what we keep

def test_slug_district():
    u = "https://espc.com/property/2-lilybank-lane-ratho-station-newbridge-eh28-8aw/36392542"
    assert espc.slug_district(u) == "EH28"
    assert espc.slug_district("https://espc.com/property/12-some-road-dalgety-bay-ky11-9ab/36000001") == "KY11"
    assert espc.slug_district("https://espc.com/property/no-postcode-here/36000002") is None


def test_rejection_rules():
    r = espc.rejection
    assert r("3 bed semi-detached house for sale in Corstorphine", "semi-detached", 3, "EH12") is None
    assert r("4 bed detached bungalow for sale in Dalgety Bay", "detached", 4, "KY11") is None
    assert r("2 bed first floor flat for sale in Leith", "flat", 2, "EH6") == "flat"
    assert r("3 bed maisonette flat for sale", "flat", 3, "EH4") == "flat"
    assert r("2 bed duplex for sale", "duplex", 2, "EH7") == "flat"
    assert r("1 bed retirement property for sale", "", 1, "EH10") == "flat"
    assert r("1 bed terraced house for sale", "terraced", 1, "EH6") == "1 bedroom"
    assert r("3 bed detached house for sale in Bathgate", "detached", 3, "EH48") == "district EH48"
    assert r("Plot for sale", "", None, "EH26") is None            # plots are kept (as their own kind)
    assert r("Plot for sale", "", None, "EH48") == "district EH48"
    assert r("Detached house for sale", "", None, "EH4") == "bedrooms unknown"
    assert r("3 bed house to rent", "house", 3, "EH4") == "rental"


def test_card_hints_and_prefilter():
    html = """<ul>
      <li><a href="/property/1-a-road-edinburgh-eh4-1aa/36000011"><img></a>
          <h3>3 bed detached house for sale in Barnton</h3><a href="/property/1-a-road-edinburgh-eh4-1aa/36000011">View</a></li>
      <li><a href="/property/2-b-street-edinburgh-eh6-2bb/36000012">2 bed ground floor flat for sale in Leith</a></li>
      <li><a href="/property/3-c-lane-bathgate-eh48-3cc/36000013">4 bed detached house for sale in Bathgate</a></li>
      <li><a href="/property/4-d-view-musselburgh-eh21-4dd/36000014">1 bed terraced house for sale</a></li>
    </ul>"""
    urls = espc.extract_property_urls(html, "https://espc.com/properties")
    hints = espc.extract_card_hints(html)
    assert "Barnton" in hints["36000011"] and "Leith" not in hints["36000011"]
    got = {pid: espc.prefilter(u, hints.get(pid)) for pid, u in urls.items()}
    assert got == {"36000011": None, "36000012": "flat", "36000013": "district EH48", "36000014": "1 bedroom"}


def test_scrape_keeps_only_wanted_houses(monkeypatch, tmp_path):
    monkeypatch.setattr(espc, "DEBUG", tmp_path)
    search = """<a href="/property/1-a-road-edinburgh-eh4-1aa/36000011">3 bed detached house for sale</a>
                <a href="/property/9-z-road-edinburgh-eh5-9zz/36000019">house</a>"""
    pages = {"36000011": "<title>3 bed detached house for sale in Barnton</title><h1>1 A Road, Edinburgh EH4 1AA</h1>"
                         "<div>Offers over £500,000</div><meta property='place:location:latitude' content='55.96'>"
                         "<meta property='place:location:longitude' content='-3.28'>",
             "36000019": "<title>2 bed upper flat for sale in Trinity</title><h1>9 Z Road, Edinburgh EH5 9ZZ</h1>"
                         "<div>Offers over £250,000</div>"}

    class FakeFetcher:
        count = 0
        robots = type("R", (), {"sitemaps": []})()
        def html(self, url):
            self.count += 1
            if "/property/" in url:
                return pages[espc.listing_id(url)]
            return search if "page=" not in url else ""
    monkeypatch.setattr(espc, "Fetcher", FakeFetcher)
    monkeypatch.setattr(espc, "geocode_postcodes", lambda ls: None)
    monkeypatch.setitem(espc.CFG, "search_urls", ["https://espc.com/properties?locations=edinburgh"])
    old_flat = {"title": "2 bed first floor flat for sale", "property_type": "flat", "bedrooms": 2,
                "district": "EH6", "active": True, "fetched": "2099-01-01"}
    state = espc.scrape({"36000001": old_flat})
    assert state["36000011"]["active"] is True
    assert "36000019" in state and state["36000019"]["active"] is False and state["36000019"]["excluded"] == "flat"
    assert state["36000001"]["active"] is False     # no longer listed -> inactive, kept for history


def test_cut_short_search_does_not_mark_listings_sold(monkeypatch, tmp_path):
    monkeypatch.setattr(espc, "DEBUG", tmp_path)

    class FlakyFetcher:
        count = 0
        robots = type("R", (), {"sitemaps": []})()
        def html(self, url):
            raise espc.requests.ConnectionError("Connection reset by peer")
    monkeypatch.setattr(espc, "Fetcher", FlakyFetcher)
    monkeypatch.setattr(espc, "geocode_postcodes", lambda ls: None)
    monkeypatch.setitem(espc.CFG, "search_urls", ["https://espc.com/properties?locations=edinburgh"])
    monkeypatch.setitem(espc.CFG, "use_sitemap_fallback", False)
    house = {"title": "3 bed detached house for sale", "property_type": "detached", "bedrooms": 3,
             "district": "EH4", "active": True}
    flat = {"title": "2 bed top floor flat for sale", "property_type": "flat", "bedrooms": 2,
            "district": "EH6", "active": True}
    # Nothing found at all -> the run fails and the caller keeps the previous state untouched.
    import pytest
    with pytest.raises(RuntimeError):
        espc.scrape({"1": dict(house)})
    # Partly found (a search cut short): the unseen house stays listed, the unseen flat is still dropped.
    found = {"36000011": "https://espc.com/property/x-eh4-1aa/36000011"}
    monkeypatch.setattr(espc, "discover", lambda f, hints=None: (found, False))

    class OneHouse:
        count = 0
        def html(self, url):
            return "<title>4 bed detached house for sale</title><h1>X EH4 1AA</h1><div>Offers over £600,000</div>"
    monkeypatch.setattr(espc, "Fetcher", OneHouse)
    state = espc.scrape({"1": dict(house), "2": dict(flat)})
    assert state["1"]["active"] is True and state["36000011"]["active"] is True
    assert "2" not in state or state["2"]["active"] is False


def test_recognised_location():
    assert espc.recognised_location("<title>Properties for Sale in Edinburgh | ESPC</title>")
    assert espc.recognised_location("<title>Properties for Sale in Dalgety Bay | ESPC</title>")
    assert not espc.recognised_location("<title>Properties for Sale | ESPC</title>")
    assert espc.recognised_location("<html>no title</html>")   # don't block on missing titles


def test_excluded_flats_are_not_refetched(monkeypatch, tmp_path):
    monkeypatch.setattr(espc, "DEBUG", tmp_path)
    fetched = []
    found = {"36000021": "https://espc.com/property/a-eh6-1aa/36000021",
             "36000022": "https://espc.com/property/b-eh4-1bb/36000022"}
    monkeypatch.setattr(espc, "discover", lambda f, hints=None: (found, True))
    monkeypatch.setattr(espc, "geocode_postcodes", lambda ls: None)

    class Recorder:
        count = 0
        def html(self, url):
            fetched.append(espc.listing_id(url))
            return "<title>4 bed detached house for sale</title><h1>B EH4 1BB</h1><div>Offers over £700,000</div>"
    monkeypatch.setattr(espc, "Fetcher", Recorder)
    old = "2000-01-01"   # long stale
    state = {"36000021": {"title": "2 bed flat for sale", "excluded": "flat", "fetched": old, "active": False},
             "36000022": {"title": "4 bed detached house for sale", "bedrooms": 4, "district": "EH4",
                          "fetched": old, "active": True}}
    espc.scrape(state)
    assert fetched == ["36000022"]


def test_agent_search_is_not_skipped_as_unrecognised_location(monkeypatch, tmp_path):
    monkeypatch.setattr(espc, "DEBUG", tmp_path)
    page = "<title>Properties for Sale | ESPC</title><a href='/property/1-a-road-edinburgh-eh4-1aa/36000011'>x</a>"

    class F:
        count = 0
        def html(self, url):
            return page if "p=2" not in url and "page=2" not in url else ""
    monkeypatch.setitem(espc.CFG, "search_urls", ["https://espc.com/properties?orgid=1560"])
    urls, complete = espc.discover(F())
    assert "36000011" in urls


def _square(lng: float, lat: float, d: float = 0.01) -> dict:
    return {"type": "Polygon", "coordinates": [[[lng, lat], [lng + d, lat], [lng + d, lat + d], [lng, lat + d], [lng, lat]]]}


def test_primary_catchments_read_council_quirks(monkeypatch):
    """Outlying 'X' patches and Canaan Lane's split-by-stage areas resolve to real schools with scores."""
    nd = [
        {"type": "Feature", "geometry": _square(-3.2, 55.93), "properties": {
            "SCHOOL_NAM": "Bruntsfield Primary School", "EST_NAME": "Bruntsfield Primary School"}},
        {"type": "Feature", "geometry": _square(-3.3, 55.93), "properties": {
            "SCHOOL_NAM": "X Castleview Primary School",
            "EST_NAME": "Properties in this area have Castleview Primary as their catchment school"}},
        {"type": "Feature", "geometry": _square(-3.25, 55.93), "properties": {
            "SCHOOL_NAM": "Canaan Lane PS Old JGPS", "EST_NAME": "For P1-P4 Pupils Only - Canaan Lane Primary School"}},
        {"type": "Feature", "geometry": _square(-3.25, 55.93), "properties": {
            "SCHOOL_NAM": "Canaan Lane PS Old JGPS", "EST_NAME": "For P5-P7 Pupils Only - James Gillespie's Primary School"}},
    ]
    rc = [{"type": "Feature", "geometry": _square(-3.25, 55.93), "properties": {
        "SCHOOL_NAM": "St Mary's (Edinburgh) RC Primary", "EST_NAME": "St Mary's RC Primary School"}}]
    monkeypatch.setattr(layers, "arcgis_query", lambda s, url, extra=None: rc if url.endswith("/5/query") else nd)
    fc = layers.fetch_primary_catchments()
    props = [f["properties"] for f in fc["features"]]
    assert props[0] == {"school": "Bruntsfield Primary", "sector": "ND", "score": 95.0, "top": True}
    assert props[1]["school"] == "Castleview Primary" and props[1]["score"]
    assert (props[2]["school"], props[2]["stages"]) == ("Canaan Lane Primary", "P1-P4")
    assert (props[3]["school"], props[3]["stages"]) == ("James Gillespie's Primary", "P5-P7")
    assert props[4]["school"] == "St Mary's (Edinburgh) RC Primary" and props[4]["sector"] == "RC" and "score" in props[4]

    look = build.polygon_lookup(fc)
    split = build.primary_fields(look(55.935, -3.245))
    assert split["primary"] == "Canaan Lane Primary" and split["primary_rc"].startswith("St Mary's")
    assert split["primary_note"] == "P1–P4 only; P5–P7 at James Gillespie's Primary"
    assert build.primary_fields(look(55.935, -3.195)) == {"primary": "Bruntsfield Primary", "primary_score": 95.0, "top_primary": True}
    assert build.primary_fields(look(50.0, 0.0)) == {}


def test_failed_area_search_only_keeps_its_own_districts(monkeypatch, tmp_path):
    """A Fife search that errors keeps unseen Fife homes listed, but sold Edinburgh homes still drop off."""
    monkeypatch.setattr(espc, "DEBUG", tmp_path)
    page = ("<title>Properties for Sale in Edinburgh | ESPC</title>"
            "<a href='/property/4-a-road-edinburgh-eh4-1aa/36000011'>x</a>")

    class F:
        count = 0
        robots = type("R", (), {"sitemaps": []})()
        def html(self, url):
            if "fife" in url:
                raise espc.requests.HTTPError("500 Server Error")
            if "/property/" in url:
                return "<title>4 bed detached house for sale</title><h1>A Road EH4 1AA</h1><div>Offers over £600,000</div>"
            return page if "page=" not in url else ""
    monkeypatch.setattr(espc, "Fetcher", F)
    monkeypatch.setattr(espc, "geocode_postcodes", lambda ls: None)
    monkeypatch.setitem(espc.CFG, "search_urls", [
        "https://espc.com/properties?locations=edinburgh",
        {"url": "https://espc.com/properties?locations=kinross-and-west-fife", "districts": ["KY3", "KY11"]}])
    urls, complete = espc.discover(F())
    assert "36000011" in urls and complete == frozenset({"KY3", "KY11"})

    house = {"title": "3 bed detached house for sale", "property_type": "detached", "bedrooms": 3, "active": True}
    state = espc.scrape({"1": {**house, "district": "KY11"}, "2": {**house, "district": "EH10"}})
    assert state["1"]["active"] is True                                   # Fife search failed: unknown, kept
    assert state["2"]["active"] is False and state["2"]["excluded"] == "no longer listed"
    assert state["36000011"]["active"] is True


def test_area_searches_all_fine_is_complete(monkeypatch, tmp_path):
    monkeypatch.setattr(espc, "DEBUG", tmp_path)

    class F:
        count = 0
        def html(self, url):
            return "" if "page=" in url else ("<title>Properties for Sale in Kinross &amp; West Fife | ESPC</title>"
                                              "<a href='/property/1-b-st-inverkeithing-ky11-1aa/36000012'>x</a>")
    monkeypatch.setitem(espc.CFG, "search_urls", [{"url": "https://espc.com/properties?locations=kinross-and-west-fife",
                                                    "districts": ["KY11"]}])
    urls, complete = espc.discover(F())
    assert complete is True and "36000012" in urls
