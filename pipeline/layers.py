"""Static-ish map layers: SIMD 2020, school catchments, tram and bus routes."""
from __future__ import annotations

import json
import math
import re
from typing import Any

from shapely.geometry import LineString, MultiLineString, Polygon, mapping, shape
from shapely.ops import linemerge, orient, unary_union

from common import CONFIG, SETTINGS, get, load_config, log, round_coords, session

SIMD_DATAZONES = 6976  # SIMD 2020 ranks run 1 (most deprived) .. 6976


def _bbox_params() -> dict:
    b = SETTINGS["bbox"]
    return {
        "geometry": f"{b['west']},{b['south']},{b['east']},{b['north']}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
    }


def esri_to_geojson_geometry(g: dict) -> dict | None:
    """Convert an Esri JSON polygon (rings) to a GeoJSON geometry."""
    rings = g.get("rings")
    if not rings:
        return None
    shells, holes = [], []
    for ring in rings:
        if len(ring) < 4:
            continue
        poly = Polygon(ring)
        # Esri: outer rings are clockwise (negative signed area in shapely terms).
        (holes if poly.exterior.is_ccw else shells).append(poly)
    polys = []
    for shell in shells:
        inner = [h.exterior.coords for h in holes if shell.contains(h.representative_point())]
        polys.append(Polygon(shell.exterior.coords, inner))
    if not polys:  # all rings the "wrong" way round: treat them as shells
        polys = [Polygon(h.exterior.coords) for h in holes]
    geom = unary_union(polys) if len(polys) > 1 else polys[0]
    return mapping(orient(geom) if geom.geom_type == "Polygon" else geom)


def arcgis_query(s, layer_query_url: str, extra: dict | None = None, page: int = 1000) -> list[dict]:
    """Page through an ArcGIS REST layer query, returning GeoJSON features."""
    feats: list[dict] = []
    offset = 0
    fmt = "geojson"
    while True:
        params = {
            "where": "1=1", "outFields": "*", "outSR": 4326, "f": fmt,
            "returnGeometry": "true", "resultOffset": offset, "resultRecordCount": page,
            "maxAllowableOffset": 0.00003, "geometryPrecision": 5,
        }
        params.update(extra or {})
        r = get(s, layer_query_url, params=params)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            if fmt == "geojson":  # older servers: fall back to Esri JSON
                fmt = "json"
                continue
            raise RuntimeError(f"ArcGIS error from {layer_query_url}: {data['error']}")
        if fmt == "geojson":
            batch = data.get("features", [])
            exceeded = data.get("exceededTransferLimit") or data.get("properties", {}).get("exceededTransferLimit")
        else:
            batch = [{"type": "Feature", "properties": f.get("attributes", {}),
                      "geometry": esri_to_geojson_geometry(f.get("geometry") or {})}
                     for f in data.get("features", [])]
            exceeded = data.get("exceededTransferLimit")
        feats.extend(f for f in batch if f.get("geometry"))
        if not batch or not (exceeded or len(batch) >= page):
            break
        offset += len(batch)
    return feats


def _find_key(props: dict, *patterns: str) -> str | None:
    for pat in patterns:
        rx = re.compile(pat, re.I)
        for k in props:
            if rx.search(k):
                return k
    return None


def _decile(rank: Any) -> int | None:
    try:
        r = float(rank)
    except (TypeError, ValueError):
        return None
    if r <= 0:
        return None
    return min(10, max(1, math.ceil(r / (SIMD_DATAZONES / 10))))


SIMD_DOMAINS = {
    "income": r"^inc.*rank", "employment": r"^emp.*rank", "health": r"^hl?th.*rank",
    "education": r"^edu.*rank", "access": r"^g?acc.*rank", "crime": r"^crime.*rank",
    "housing": r"^hous.*rank",
}


def fetch_simd() -> dict:
    s = session()
    feats = arcgis_query(s, SETTINGS["sources"]["simd_query_url"], _bbox_params())
    if not feats:
        raise RuntimeError("SIMD query returned no features")
    sample = feats[0]["properties"]
    log.info("SIMD fields: %s", sorted(sample))
    k_dz = _find_key(sample, r"^data_?zone$", r"^dz(_?code)?$", r"datazone", r"^dz")
    k_name = _find_key(sample, r"^(dz_?)?name$", r"name")
    k_rank = _find_key(sample, r"^rank(v2)?$", r"^simd.*rank", r"^rank")
    k_la = _find_key(sample, r"^la_?name$", r"council", r"^laname")
    domain_keys = {d: _find_key(sample, p) for d, p in SIMD_DOMAINS.items()}
    if not k_rank:
        raise RuntimeError(f"Could not find the overall SIMD rank field in {sorted(sample)}")
    out = []
    for f in feats:
        p = f["properties"]
        props = {
            "dz": p.get(k_dz) if k_dz else None,
            "name": p.get(k_name) if k_name else None,
            "la": p.get(k_la) if k_la else None,
            "rank": int(p[k_rank]) if p.get(k_rank) not in (None, "") else None,
        }
        props["decile"] = _decile(props["rank"])
        for d, k in domain_keys.items():
            if k:
                props[d] = _decile(p.get(k))
        out.append({"type": "Feature", "properties": props,
                    "geometry": {**f["geometry"], "coordinates": round_coords(f["geometry"]["coordinates"])}})
    log.info("SIMD: %d data zones", len(out))
    return {"type": "FeatureCollection", "features": out}


def _discover_catchment_layer(s, title: str) -> str | None:
    """Find a council catchment FeatureServer layer via the public ArcGIS Online search."""
    r = get(s, "https://www.arcgis.com/sharing/rest/search",
            params={"q": f'title:"{title}" type:"Feature Service"', "f": "json", "num": 25})
    if not r.ok:
        return None
    results = r.json().get("results", [])
    want = title.casefold()
    ranked = sorted(
        (it for it in results if it.get("url")),
        key=lambda it: (it.get("title", "").casefold() != want,
                        "edinburgh" not in json.dumps(it).casefold()))
    for it in ranked:
        if "edinburgh" not in json.dumps(it).casefold():
            continue
        url = it["url"].rstrip("/")
        if re.search(r"/\d+$", url):
            return url
        info = get(s, url, params={"f": "json"})
        layers = info.json().get("layers", []) if info.ok else []
        if len(layers) == 1:
            return f"{url}/{layers[0]['id']}"
        for layer in layers:
            if "secondary" in layer.get("name", "").casefold():
                return f"{url}/{layer['id']}"
        if layers:
            return f"{url}/{layers[0]['id']}"
    return None


def _match_top(school: str, tops: list[dict], sector: str | None = None) -> dict | None:
    name = school.casefold().replace("’", "'")
    for t in tops:
        if t["match"] in name and (sector is None or t.get("sector", "ND") == sector):
            return t
    return None


def fetch_catchments() -> tuple[dict, str]:
    """Return (FeatureCollection, source description)."""
    s = session()
    src = SETTINGS["sources"]
    tops = load_config("top_schools.json")["schools"]
    feats: list[dict] = []
    used: list[str] = []
    for sector, label in (("non_denominational", "ND"), ("roman_catholic", "RC")):
        url = src["catchment_feature_layers"].get(sector) or ""
        try:
            if not url:
                url = _discover_catchment_layer(s, src["catchment_search_titles"][sector]) or ""
            if not url:
                raise RuntimeError("layer not found")
            raw = arcgis_query(s, url.rstrip("/") + "/query")
            if not raw:
                raise RuntimeError("no features")
            k_school = _find_key(raw[0]["properties"], r"school", r"^name$", r"name")
            for f in raw:
                school = str(f["properties"].get(k_school) or "Unknown")
                feats.append(_catchment_feature(f["geometry"], school, label, tops))
            used.append(f"{label}: {url}")
            log.info("Catchments %s: %d from %s", label, len(raw), url)
        except Exception as e:  # noqa: BLE001 - fall back to the bundled copy below
            log.warning("Catchments %s live fetch failed (%s)", label, e)
    if not any(f["properties"]["sector"] == "ND" for f in feats):
        fallback = json.loads((CONFIG / "catchments_fallback.geojson").read_text())
        feats = [_catchment_feature(f["geometry"], f["properties"]["school"], f["properties"]["sector"], tops)
                 for f in fallback["features"]]
        used = ["Bundled City of Edinburgh Council catchments (c.2014) - live fetch failed"]
    return {"type": "FeatureCollection", "features": feats}, "; ".join(used)


def _catchment_feature(geometry: dict, school: str, sector: str, tops: list[dict]) -> dict:
    top = _match_top(school, tops, sector)
    return {"type": "Feature",
            "properties": {"school": _tidy_school(school), "sector": sector,
                           "top_rank": top["rank"] if top else None},
            "geometry": {**geometry, "coordinates": round_coords(geometry["coordinates"])}}


def _tidy_school(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    return name.title() if name.isupper() else name


# ---------------------------------------------------------------- OpenStreetMap

def _overpass(query: str) -> dict:
    s = session()
    r = s.post(SETTINGS["sources"]["overpass_url"], data={"data": query}, timeout=300)
    r.raise_for_status()
    return r.json()


def _relation_lines(rel: dict) -> list[list[list[float]]]:
    lines = []
    for m in rel.get("members", []):
        if m.get("type") == "way" and m.get("geometry") and m.get("role", "") in ("", "forward", "backward", "main"):
            lines.append([[p["lon"], p["lat"]] for p in m["geometry"]])
    return lines


def _merged(lines: list[list[list[float]]], tolerance: float = 0.00003) -> dict | None:
    if not lines:
        return None
    merged = linemerge(MultiLineString([LineString(l) for l in lines if len(l) > 1]))
    merged = merged.simplify(tolerance, preserve_topology=False)
    return {**mapping(merged), "coordinates": round_coords(mapping(merged)["coordinates"])}


def fetch_transit() -> tuple[dict, dict]:
    """Return (tram FeatureCollection incl. stops, bus FeatureCollection)."""
    b = SETTINGS["bbox"]
    bb = f"{b['south']},{b['west']},{b['north']},{b['east']}"
    op = SETTINGS["bus"]["operator_regex"]
    q = f"""[out:json][timeout:240];
(
  relation["route"="tram"]({bb});
  relation["route"="bus"]["operator"~"{op}"]({bb});
);
out geom;
node["railway"="tram_stop"]({bb});
out;"""
    data = _overpass(q)
    tram_lines: list = []
    bus: dict[str, dict] = {}
    stops = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "node":
            stops.append({"type": "Feature", "properties": {"kind": "stop", "name": tags.get("name", "")},
                          "geometry": {"type": "Point", "coordinates": [round(el["lon"], 5), round(el["lat"], 5)]}})
        elif tags.get("route") == "tram":
            tram_lines += _relation_lines(el)
        elif tags.get("route") == "bus":
            ref = tags.get("ref") or tags.get("name", "?")
            entry = bus.setdefault(ref, {"lines": [], "name": tags.get("name", ""), "colour": tags.get("colour")})
            entry["lines"] += _relation_lines(el)
    tram_feats = []
    g = _merged(tram_lines)
    if g:
        tram_feats.append({"type": "Feature", "properties": {"kind": "line", "name": "Edinburgh Trams"}, "geometry": g})
    tram_feats += stops
    main = set(SETTINGS["bus"]["main_routes"])
    bus_feats = []
    for ref, e in bus.items():
        g = _merged(e["lines"], 0.00006)
        if g:
            bus_feats.append({"type": "Feature", "geometry": g, "properties": {
                "ref": ref, "name": _route_label(ref, e["name"]), "colour": e["colour"], "main": ref in main}})
    bus_feats.sort(key=lambda f: _route_sort_key(f["properties"]["ref"]))
    log.info("OSM: tram %d features, bus %d routes", len(tram_feats), len(bus_feats))
    if not tram_lines:
        raise RuntimeError("Overpass returned no tram route")
    return ({"type": "FeatureCollection", "features": tram_feats},
            {"type": "FeatureCollection", "features": bus_feats})


def _route_label(ref: str, name: str) -> str:
    # OSM names look like "Lothian 22: Ocean Terminal => Gyle Centre"; keep the endpoints.
    m = re.search(r":\s*(.+)$", name)
    return m.group(1).replace("=>", "↔") if m else name


def _route_sort_key(ref: str):
    m = re.match(r"(\d+)(.*)", ref)
    return (0, int(m.group(1)), m.group(2)) if m else (1, 0, ref)


def load_proposed_tram() -> dict:
    fc = json.loads((CONFIG / "tram_proposed.geojson").read_text())
    fc.pop("_comment", None)
    return fc


def geometry_index(fc: dict):
    """(shapes, props) for point-in-polygon lookups."""
    return [shape(f["geometry"]) for f in fc["features"]], [f["properties"] for f in fc["features"]]
