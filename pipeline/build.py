"""Daily refresh: fetch layers + listings, enrich, and write public/data/*.

    python pipeline/build.py                 # everything
    python pipeline/build.py --skip-espc     # layers + re-enrich existing listings
    python pipeline/build.py --only-layers

Each step is independent: if one source is down, the previous output for that
layer is kept and the failure is recorded in public/data/meta.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import statistics
import sys
import traceback
from collections import defaultdict

from shapely.geometry import Point
from shapely.strtree import STRtree

import espc
import layers
import transit
from common import OUT, SETTINGS, STATE, load_config, log, read_json, write_json
from layers import geometry_index


class Run:
    def __init__(self):
        self.meta = read_json(OUT / "meta.json", {}) or {}
        self.meta.pop("sample", None)
        self.meta.setdefault("sources", {})
        self.meta["errors"] = {}

    def step(self, name: str, fn, *a, **kw):
        log.info("== %s", name)
        try:
            res = fn(*a, **kw)
            self.meta["sources"].setdefault(name, {})["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
            return res
        except Exception as e:  # noqa: BLE001
            log.error("%s failed: %s", name, e)
            traceback.print_exc()
            self.meta["errors"][name] = str(e)[:500]
            return None


def refresh_layers(run: Run) -> None:
    simd = run.step("simd", layers.fetch_simd)
    if simd:
        run.meta["sources"]["simd"]["detail"] = simd.pop("source", "")
        write_json(OUT / "simd.geojson", simd)
    res = run.step("catchments", layers.fetch_catchments)
    if res:
        fc, src = res
        write_json(OUT / "catchments.geojson", fc)
        run.meta["sources"]["catchments"]["detail"] = src
    tb = run.step("osm_transit", layers.fetch_transit)
    if tb:
        write_json(OUT / "tram.geojson", tb[0])
        write_json(OUT / "bus.geojson", tb[1])
    write_json(OUT / "tram_proposed.geojson", layers.load_proposed_tram())
    top = load_config("top_schools.json")
    write_json(OUT / "top_schools.json", {k: v for k, v in top.items() if not k.startswith("_")})


REGION_OF = {d: name.split(":")[0].split(" (")[0]
             for name, ds in SETTINGS["espc"]["allowed_postcode_districts"].items() for d in ds}


def polygon_lookup(fc: dict | None):
    if not fc or not fc.get("features"):
        return lambda lat, lng: []
    shapes, props = geometry_index(fc)
    tree = STRtree(shapes)

    def find(lat: float, lng: float) -> list[dict]:
        p = Point(lng, lat)
        return [props[i] for i in tree.query(p, predicate="intersects")]
    return find


def area_stats(listings: list[dict]) -> list[dict]:
    by_d: dict[str, list[dict]] = defaultdict(list)
    for l in listings:
        if l.get("district") and l.get("price"):
            by_d[l["district"]].append(l)
    out = []
    for d, ls in by_d.items():
        prices = [l["price"] for l in ls]
        beds: dict[str, list[int]] = defaultdict(list)
        for l in ls:
            if l.get("bedrooms") is not None:
                beds[str(min(l["bedrooms"], 5))].append(l["price"])
        out.append({
            "district": d, "count": len(ls), "median": int(statistics.median(prices)),
            "mean": int(statistics.fmean(prices)),
            "by_beds": {b: {"count": len(p), "median": int(statistics.median(p))} for b, p in sorted(beds.items())},
            "lat": round(statistics.fmean(l["lat"] for l in ls), 5),
            "lng": round(statistics.fmean(l["lng"] for l in ls), 5),
        })
    out.sort(key=lambda a: (a["district"][:2], int("".join(c for c in a["district"][2:] if c.isdigit()) or 0)))
    return out


def enrich(listings: list[dict], net, profiles, previous: dict | None = None) -> list[dict]:
    simd_at = polygon_lookup(read_json(OUT / "simd.geojson"))
    catch_at = polygon_lookup(read_json(OUT / "catchments.geojson"))
    index = transit.StopIndex(net) if net else None
    out = []
    for l in listings:
        if l.get("lat") is None or not l.get("price"):
            continue
        rec = {k: l.get(k) for k in (
            "id", "url", "title", "address", "postcode", "district", "lat", "lng", "price", "price_qualifier",
            "bedrooms", "property_type", "detached", "garage", "image", "first_seen", "price_history",
            "approx_location")}
        rec.pop("top_school_rank", None)
        rec["lat"], rec["lng"] = round(rec["lat"], 5), round(rec["lng"], 5)
        rec["region"] = REGION_OF.get(rec.get("district") or "")
        z = simd_at(l["lat"], l["lng"])
        if z:
            rec["simd"] = {k: v for k, v in z[0].items() if k != "la"}
        for c in catch_at(l["lat"], l["lng"]):
            key = "catchment_rc" if c["sector"] == "RC" else "catchment"
            rec[key] = c["school"]
            if c.get("top_rank"):
                rec[f"{key}_rank"] = c["top_rank"]
                rec["top_school_rank"] = min(c["top_rank"], rec.get("top_school_rank") or 99)
        rec["travel"] = transit.journey(l["lat"], l["lng"], net, profiles, index)
        old = (previous or {}).get(l["id"])
        if net is None and old and "pt_min" in old:
            # Timetable unavailable this run: keep the last bus/tram estimate for this home.
            for k in ("pt_min", "pt_how"):
                rec["travel"][k] = old[k]
            if old["pt_min"] < rec["travel"]["best_min"]:
                rec["travel"]["best_min"], rec["travel"]["best_how"] = old["pt_min"], old["pt_how"]
        out.append({k: v for k, v in rec.items() if v not in (None, "", [], False) or k in ("detached", "garage")})
    areas = area_stats(out)
    lookup = {a["district"]: a for a in areas}
    for rec in out:
        a = lookup.get(rec.get("district"))
        if not a:
            continue
        same = a["by_beds"].get(str(min(rec.get("bedrooms") or 0, 5)))
        base, basis = (same["median"], f"{min(rec['bedrooms'], 5)}-bed") if same and same["count"] >= 3 and rec.get("bedrooms") else (a["median"], "all")
        rec["area"] = {"median": base, "basis": basis, "vs_pct": round(100 * (rec["price"] - base) / base)}
    write_json(OUT / "areas.json", areas)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-layers", action="store_true")
    ap.add_argument("--skip-espc", action="store_true")
    ap.add_argument("--skip-transit", action="store_true")
    ap.add_argument("--only-layers", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run = Run()
    if not args.skip_layers:
        refresh_layers(run)
    if args.only_layers:
        write_meta(run, None)
        return 0

    net = profiles = None
    if not args.skip_transit:
        net = run.step("gtfs", lambda: transit.load_network(transit.download_gtfs()))
        if net:
            profiles = transit.build_profiles(net)
            run.meta["sources"]["gtfs"]["service_date"] = net.service_date

    state_path = STATE / "listings_state.json"
    state = read_json(state_path, {}) or {}
    if not args.skip_espc:
        new_state = run.step("espc", espc.scrape, state)
        if new_state is not None:
            state = new_state
            write_json(state_path, state, compact=False)
    if not state:
        # Nothing scraped yet (first run failed): keep whatever listings.json holds (e.g. sample data).
        run.meta["sample"] = (read_json(OUT / "meta.json", {}) or {}).get("sample", False)
        write_meta(run, None)
        return 1 if "espc" in run.meta["errors"] else 0
    active = [r for r in state.values() if r.get("active")]
    previous = {l["id"]: l.get("travel") for l in read_json(OUT / "listings.json", []) or []}
    listings = enrich(active, net, profiles, previous)
    write_json(OUT / "listings.json", listings)
    write_meta(run, listings)
    log.info("Wrote %d listings", len(listings))
    # Fail the job (so you get an email) if listings could not be refreshed at all.
    return 1 if "espc" in run.meta["errors"] else 0


def write_meta(run: Run, listings: list[dict] | None) -> None:
    run.meta["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    run.meta["waverley"] = SETTINGS["waverley"]
    run.meta["travel_assumptions"] = {
        "arrive_by": SETTINGS["travel"]["arrive_by"], "walk_kmh": SETTINGS["travel"]["walk_speed_kmh"],
        "cycle_kmh": SETTINGS["travel"]["cycle_speed_kmh"]}
    if listings is not None:
        run.meta["listing_count"] = len(listings)
    write_json(OUT / "meta.json", run.meta, compact=False)


if __name__ == "__main__":
    sys.exit(main())
