"""Write clearly-labelled SAMPLE data to public/data so the site can be previewed
before the first real refresh has run. Real runs overwrite every file.

    python pipeline/make_sample.py
"""
from __future__ import annotations

import json
import random

from shapely.geometry import Point, shape

import build
import layers
import transit
from common import CONFIG, OUT, write_json

# Approximate tram stops (sample only - real geometry comes from OpenStreetMap).
TRAM = [("Edinburgh Airport", -3.3616, 55.9480), ("Ingliston Park & Ride", -3.3503, 55.9407),
        ("Gogarburn", -3.3270, 55.9366), ("Edinburgh Gateway", -3.3203, 55.9407),
        ("Gyle Centre", -3.3063, 55.9372), ("Edinburgh Park Central", -3.3050, 55.9296),
        ("Bankhead", -3.2937, 55.9314), ("Saughton", -3.2650, 55.9366), ("Balgreen", -3.2485, 55.9409),
        ("Murrayfield", -3.2381, 55.9432), ("Haymarket", -3.2186, 55.9458), ("West End", -3.2095, 55.9491),
        ("Princes Street", -3.2000, 55.9513), ("St Andrew Square", -3.1925, 55.9546),
        ("Picardy Place", -3.1856, 55.9573), ("McDonald Road", -3.1814, 55.9620), ("Balfour Street", -3.1760, 55.9675),
        ("Foot of the Walk", -3.1735, 55.9705), ("Port of Leith", -3.1710, 55.9740),
        ("Ocean Terminal", -3.1760, 55.9800), ("Newhaven", -3.1915, 55.9810)]

DISTRICT = {"gillespie": "EH9", "boroughmuir": "EH10", "royal high": "EH4", "balerno": "EH14", "currie": "EH14",
            "trinity": "EH5", "craigmount": "EH12", "firrhill": "EH13", "portobello": "EH15", "queensferry": "EH30",
            "broughton": "EH3", "leith": "EH6", "liberton": "EH16", "gracemount": "EH17", "castlebrae": "EH16",
            "craigroyston": "EH4", "drummond": "EH7", "forrester": "EH12", "tynecastle": "EH11", "whec": "EH14"}
PREMIUM = {1: 1.55, 2: 1.5, 3: 1.35, 4: 1.2, 5: 1.2, 6: 1.25, 7: 1.1, 8: 1.05, 9: 1.1, 10: 1.0}
STREETS = ["Avenue", "Road", "Crescent", "Gardens", "Place", "Grove", "Terrace", "Drive", "Park", "Loan"]


def main() -> None:
    rnd = random.Random(42)
    catch = json.loads((CONFIG / "catchments_fallback.geojson").read_text())
    tops = json.loads((CONFIG / "top_schools.json").read_text())["schools"]
    fc = {"type": "FeatureCollection", "features": [
        layers._catchment_feature(f["geometry"], f["properties"]["school"], f["properties"]["sector"], tops)
        for f in catch["features"]]}
    write_json(OUT / "catchments.geojson", fc)
    write_json(OUT / "tram.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "line", "name": "Edinburgh Trams (approximate)"},
         "geometry": {"type": "LineString", "coordinates": [[x, y] for _, x, y in TRAM]}}] + [
        {"type": "Feature", "properties": {"kind": "stop", "name": n}, "geometry": {"type": "Point", "coordinates": [x, y]}}
        for n, x, y in TRAM]})
    write_json(OUT / "tram_proposed.geojson", layers.load_proposed_tram())
    write_json(OUT / "bus.geojson", {"type": "FeatureCollection", "features": []})
    write_json(OUT / "simd.geojson", {"type": "FeatureCollection", "features": []})
    top = json.loads((CONFIG / "top_schools.json").read_text())
    write_json(OUT / "top_schools.json", {k: v for k, v in top.items() if not k.startswith("_")})

    nd = [(shape(f["geometry"]), f["properties"]) for f in fc["features"] if f["properties"]["sector"] == "ND"]
    rc = [(shape(f["geometry"]), f["properties"]) for f in fc["features"] if f["properties"]["sector"] == "RC"]
    listings = []
    pid = 90000000
    while len(listings) < 220:
        lng, lat = rnd.uniform(-3.42, -3.02), rnd.uniform(55.87, 55.99)
        hit = next(((g, p) for g, p in nd if g.contains(Point(lng, lat))), None)
        if not hit:
            continue
        _, props = hit
        key = next((k for k in DISTRICT if k in props["school"].lower()), None)
        beds = rnd.choices([1, 2, 3, 4, 5, 6], [8, 25, 30, 22, 10, 5])[0]
        kind = rnd.choices(["flat", "terraced house", "semi-detached house", "detached house", "detached bungalow",
                            "detached villa"], [30, 18, 20, 18, 7, 7])[0]
        if kind == "flat":
            beds = min(beds, 3)
        detached = kind.startswith("detached")
        garage = detached and rnd.random() < 0.7 or (not detached and kind != "flat" and rnd.random() < 0.25)
        base = 95000 + beds * 72000 + (60000 if detached else 0) + (15000 if garage else 0)
        price = base * PREMIUM.get(props.get("top_rank"), 0.9) * rnd.uniform(0.85, 1.2)
        price = int(round(price / 5000) * 5000)
        pid += rnd.randint(1, 900)
        district = DISTRICT.get(key, "EH1")
        street = f"{rnd.randint(1, 80)} Sample {rnd.choice(STREETS)}"
        j = transit.journey(lat, lng, None, [], None)
        km = j["km"]
        j["pt_min"] = round(9 + km * 3.4)
        j["pt_how"] = "Sample estimate - real timetable routing appears after the first refresh"
        if j["pt_min"] < j["best_min"]:
            j["best_min"], j["best_how"] = j["pt_min"], j["pt_how"]
        rec = {"id": str(pid), "url": "https://espc.com/properties?locations=edinburgh",
               "title": f"{beds} bedroom {kind}", "address": f"{street}, Edinburgh", "postcode": f"{district} {rnd.randint(1, 9)}XX",
               "district": district, "lat": round(lat, 5), "lng": round(lng, 5), "price": price,
               "price_qualifier": rnd.choice(["Offers Over", "Offers Over", "Fixed Price"]), "bedrooms": beds,
               "property_type": kind, "detached": detached, "garage": garage, "catchment": props["school"],
               "first_seen": "2026-09-2" + str(rnd.randint(0, 8)), "travel": j,
               "price_history": [["2026-09-01", price]]}
        if props.get("top_rank"):
            rec["catchment_rank"] = rec["top_school_rank"] = props["top_rank"]
        rc_hit = next((p for g, p in rc if g.contains(Point(lng, lat))), None)
        if rc_hit:
            rec["catchment_rc"] = rc_hit["school"]
            if rc_hit.get("top_rank"):
                rec["catchment_rc_rank"] = rc_hit["top_rank"]
                rec["top_school_rank"] = min(rc_hit["top_rank"], rec.get("top_school_rank") or 99)
        if rnd.random() < 0.12:
            rec["price_history"] = [["2026-08-15", price + 15000], ["2026-09-12", price]]
        listings.append(rec)
    areas = build.area_stats(listings)
    by = {a["district"]: a for a in areas}
    for rec in listings:
        a = by[rec["district"]]
        rec["area"] = {"median": a["median"], "basis": "all", "vs_pct": round(100 * (rec["price"] - a["median"]) / a["median"])}
    write_json(OUT / "listings.json", listings)
    write_json(OUT / "areas.json", areas)
    write_json(OUT / "meta.json", {
        "sample": True, "generated_at": "2026-09-29T06:00+00:00", "listing_count": len(listings),
        "waverley": build.SETTINGS["waverley"], "errors": {},
        "sources": {"catchments": {"detail": "Bundled council catchments (c.2014)"}},
        "travel_assumptions": {"arrive_by": build.SETTINGS["travel"]["arrive_by"]}}, compact=False)
    print(f"Sample data written to {OUT} ({len(listings)} fake listings)")


if __name__ == "__main__":
    main()
