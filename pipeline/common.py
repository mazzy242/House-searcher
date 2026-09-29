"""Shared helpers: paths, config, HTTP, geometry and JSON output."""
from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "pipeline"
CONFIG = PIPELINE / "config"
OUT = ROOT / "public" / "data"
STATE = ROOT / "state"          # committed: listing history kept between runs
CACHE = PIPELINE / ".cache"     # not committed: downloads (GTFS etc.)
DEBUG = PIPELINE / "debug"      # not committed: uploaded as a workflow artifact

log = logging.getLogger("house-searcher")


def load_config(name: str) -> Any:
    return json.loads((CONFIG / name).read_text())


SETTINGS = load_config("settings.json")


def session(user_agent: str | None = None) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = user_agent or SETTINGS["espc"]["user_agent"]
    s.headers["Accept-Language"] = "en-GB,en;q=0.9"
    return s


def get(s: requests.Session, url: str, *, retries: int = 3, **kw) -> requests.Response:
    """GET with a small retry/backoff on network errors and 429/5xx."""
    kw.setdefault("timeout", 60)
    for attempt in range(retries):
        try:
            r = s.get(url, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            return r
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def walk_seconds(metres: float) -> float:
    t = SETTINGS["travel"]
    return metres * t["walk_detour_factor"] / (t["walk_speed_kmh"] / 3.6)


def in_bbox(lat: float, lng: float) -> bool:
    b = SETTINGS["bbox"]
    return b["south"] <= lat <= b["north"] and b["west"] <= lng <= b["east"]


def write_json(path: Path, data: Any, *, compact: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, separators=(",", ":") if compact else None,
                      indent=None if compact else 1, ensure_ascii=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def round_coords(geom: Any, ndigits: int = 5) -> Any:
    """Round nested coordinate arrays to keep GeoJSON small (5dp is about 1 m)."""
    if isinstance(geom, (list, tuple)):
        if geom and isinstance(geom[0], (int, float)):
            return [round(v, ndigits) for v in geom]
        return [round_coords(g, ndigits) for g in geom]
    return geom
