"""Energy Performance Certificates: floor area and EPC band for each house.

The Scottish Government publishes every domestic EPC in Scotland, with full address,
floor area and energy rating, as a quarterly zip on statistics.gov.scot (Open Government
Licence). We download it (cached for 30 days), keep only certificates in our postcode
districts, and match listings by postcode + house number or house name.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urljoin

from common import CACHE, SETTINGS, get, log, session

CFG = SETTINGS["epc"]
ZIP_PATH = CACHE / "epc.zip"
INDEX_PATH = CACHE / "epc_index.json"
BANDS = set("ABCDEFG")


def find_download_url(s) -> str:
    """The domestic EPC zip linked from the dataset page (its file name changes each quarter)."""
    if CFG.get("download_url"):
        return CFG["download_url"]
    page = CFG["dataset_page"]
    html = get(s, page, timeout=60).text
    links = [urljoin(page, h) for h in re.findall(r"""href=["']([^"']+)["']""", html)]
    zips = [u for u in links if re.search(r"\.zip(\?|$)", u, re.I)]
    domestic = [u for u in zips if not re.search(r"non[-_ ]?dom", u, re.I)]
    log.info("EPC: zip links on dataset page: %s", zips[:10])
    if not domestic:
        raise RuntimeError(f"no domestic EPC zip link found on {page}")
    return domestic[0]


def download() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    fresh = ZIP_PATH.exists() and (dt.datetime.now().timestamp() - ZIP_PATH.stat().st_mtime) < 30 * 86400
    if fresh:
        return ZIP_PATH
    s = session()
    url = find_download_url(s)
    log.info("EPC: downloading %s", url)
    with get(s, url, stream=True, timeout=900) as r:
        r.raise_for_status()
        tmp = ZIP_PATH.with_suffix(".part")
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
        tmp.replace(ZIP_PATH)
    INDEX_PATH.unlink(missing_ok=True)
    return ZIP_PATH


def _col(header: list[str], *patterns: str) -> int | None:
    for pat in patterns:
        for i, h in enumerate(header):
            if re.search(pat, h, re.I):
                return i
    return None


def build_index(zip_path: Path, districts: set[str]) -> dict[str, list]:
    """postcode -> [[address, floor_area_m2, band, date], ...] for our districts only."""
    index: dict[str, list] = {}
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.lower().endswith(".csv"):
                continue
            with z.open(name) as fh:
                rows = csv.reader(io.TextIOWrapper(fh, encoding="utf-8-sig", errors="replace"))
                header = None
                for row in rows:
                    if header is None:
                        if any(c.strip().upper() == "POSTCODE" for c in row):
                            header = row
                            cols = {
                                "pc": _col(header, r"^postcode$"),
                                "a1": _col(header, r"^address_?1$", r"^address$"),
                                "a2": _col(header, r"^address_?2$"),
                                "area": _col(header, r"total_?floor_?area", r"floor.?area"),
                                "band": _col(header, r"^current_?energy_?(efficiency_?)?(rating|band)$", r"current.*(rating|band)"),
                                "date": _col(header, r"lodgement_?date", r"inspection_?date", r"date"),
                            }
                            log.info("EPC %s columns: %s", name, {k: header[v] if v is not None else None for k, v in cols.items()})
                        continue
                    try:
                        pc = re.sub(r"\s+", " ", row[cols["pc"]].strip().upper())
                    except (IndexError, TypeError):
                        continue
                    if pc.split(" ")[0] not in districts:
                        continue
                    get_ = lambda k: row[cols[k]].strip() if cols[k] is not None and cols[k] < len(row) else ""  # noqa: E731
                    try:
                        area = float(get_("area"))
                    except ValueError:
                        area = None
                    band = get_("band").upper()[:1]
                    index.setdefault(pc, []).append([
                        f"{get_('a1')} {get_('a2')}".strip(),
                        round(area) if area and 15 <= area <= 2000 else None,
                        band if band in BANDS else None,
                        get_("date")[:10],
                    ])
    return index


def load_index(districts: set[str]) -> dict[str, list]:
    zip_path = download()
    if INDEX_PATH.exists() and INDEX_PATH.stat().st_mtime >= zip_path.stat().st_mtime:
        return json.loads(INDEX_PATH.read_text())
    index = build_index(zip_path, districts)
    INDEX_PATH.write_text(json.dumps(index))
    log.info("EPC: %d certificates across %d postcodes", sum(map(len, index.values())), len(index))
    return index


def _norm(s: str) -> str:
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\b(road)\b", "rd", s)
    s = re.sub(r"\b(street)\b", "st", s)
    s = re.sub(r"\b(avenue)\b", "ave", s)
    return re.sub(r"[^a-z0-9/ ]+", " ", s).split()


def _key(address: str) -> tuple[str | None, list[str]]:
    """(house number or None, words of the first address line) - 'Flat 2, 14 X Road' -> ('14', [...])."""
    first = address.split(",")[0]
    words = _norm(first)
    nums = [w for w in words if re.fullmatch(r"\d+[a-z]?", w)]
    return (nums[-1] if nums else None), [w for w in words if not re.fullmatch(r"\d+[a-z]?", w)]


def match(address: str, postcode: str, index: dict[str, list]) -> dict | None:
    """Most recent certificate for this address, or None when the match isn't clear."""
    certs = index.get(re.sub(r"\s+", " ", (postcode or "").upper().strip()))
    if not certs or not address:
        return None
    num, words = _key(address)
    street = set(words)
    best = []
    for addr, area, band, date in certs:
        c_num, c_words = _key(addr)
        overlap = len(street & set(c_words))
        if num and c_num == num and (overlap or not street):
            best.append((date, area, band))
        elif not num and street and overlap >= max(1, len(street) - 1):  # named house: "Rose Cottage"
            best.append((date, area, band))
    if not best:
        return None
    date, area, band = max(best)
    return {k: v for k, v in {"band": band, "floor_area_m2": area, "date": date}.items() if v}
