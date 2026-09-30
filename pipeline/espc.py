"""ESPC listings scraper (personal use, once a day, polite).

ESPC has no public API and its markup can change, so this is deliberately
defensive and layered:

1. Search results pages (settings.json -> espc.search_urls), following
   pagination, collect property URLs.  If none are found it falls back to the
   property sitemap(s) advertised in robots.txt.
2. Each *new* property page (and any not refreshed for N days) is fetched and
   parsed from, in order of trust: JSON-LD, embedded app-state JSON, meta tags,
   then plain-text heuristics.
3. robots.txt is honoured and requests are spaced out.

If a run finds zero listings it raises, and the raw HTML is saved to
pipeline/debug/ (uploaded by the workflow) so the parser can be fixed.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter
import re
import time
import urllib.robotparser
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from common import DEBUG, SETTINGS, get, in_bbox, log, session

CFG = SETTINGS["espc"]
PROPERTY_PATH = re.compile(r"/property/[a-z0-9][a-z0-9\-/]*?(\d{5,})/?(?=[\"'?#\s]|$)", re.I)
POSTCODE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b")
PRICE = re.compile(
    r"(?P<q>offers over|offers around|offers in the region of|offers in excess of|fixed price|"
    r"oieo|oiro|o/o|price on application|from)?\s*£\s?(?P<n>\d{1,3}(?:,\d{3})+|\d{5,})", re.I)
BEDS = re.compile(r"\b(\d{1,2})\s*(?:-|\s)?\s*(?:bed(?:room)?s?)\b", re.I)
DETACHED = re.compile(r"(?<!semi)(?<!semi-)(?<!semi )\bdetached\b", re.I)
NOT_GARAGE = re.compile(r"\b(no|without)\s+(a\s+)?garag", re.I)
GARAGE = re.compile(r"\bgarag(e|es|ing)\b", re.I)
BATHS = re.compile(r"\b(\d{1,2})\s*(?:-|\s)?\s*bath(?:room)?s?\b", re.I)
BATHS_LABEL = re.compile(r"\bbathrooms\s*:?\s*(\d{1,2})\b(?!\s*(?:\.\d|x|m\b))", re.I)
AREA_UNIT = r"(m²|m2|sq\.?\s?m(?:etres|eters)?\b|square\s+met(?:re|er)s|sq\.?\s?ft\b|square\s+f(?:ee|oo)t|ft²)"
AREA_NUM = r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d{2,5})(?:\.\d+)?"  # 112, 1,076, 98.5
AREA_LABELLED = re.compile(r"(?:floor\s*area|internal\s*area|total\s*area|living\s*area|floor\s*space|size)"
                           r"[^\d]{0,40}?" + AREA_NUM + r"\s*" + AREA_UNIT, re.I)
AREA_ANY = re.compile(AREA_NUM + r"\s*" + AREA_UNIT, re.I)
PARSER_VERSION = 3  # bump when the parser learns new fields, so kept houses are re-read once

WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
BEDS_WORD = re.compile(r"\b(one|two|three|four|five|six|seven|eight)[\s-]+bed(?:room)?s?\b", re.I)

# Signs the seller wants a quick or unconditional sale (lender, executor, trustee...).
MOTIVATED = re.compile(
    r"heritable creditors?|repossess\w*|lender in possession|executry|executors? of|on behalf of the executor|"
    r"trustees? in sequestration|sequestrat\w*|\breceivers?\b|power of sale|sold as seen|"
    r"cash buyers? only|suited to cash buyers|priced for (?:a )?quick sale", re.I)
NEEDS_WORK = re.compile(
    r"(?:require|requires|requiring|in need of|would benefit from|scope for)\s+(?:some\s+|full\s+|complete\s+|general\s+|"
    r"extensive\s+|a programme of\s+)?(?:modernisation|modernization|refurbishment|renovation|upgrading|updating|repair)|"
    r"renovation project|refurbishment project|restoration project|project property|development opportunity|"
    r"doer[- ]upper|in need of (?:some )?(?:tlc|work)", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
CLOSING = re.compile(
    r"closing date[^.\d]{0,40}?(?:(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*,?"
    r"(?:\s+(\d{4}))?|(\d{1,2})[/.](\d{1,2})[/.](\d{2,4}))", re.I)


def closing_date(text: str, today: dt.date | None = None) -> str | None:
    """ISO date of a set closing date ("Closing date: Friday 10th October at 12 noon"), if any."""
    today = today or dt.date.today()
    m = CLOSING.search(text)
    if not m:
        return None
    try:
        if m.group(1):
            day, month = int(m.group(1)), MONTHS[m.group(2)[:3].lower()]
            year = int(m.group(3)) if m.group(3) else today.year
            d = dt.date(year, month, day)
            if not m.group(3) and d < today - dt.timedelta(days=60):
                d = dt.date(year + 1, month, day)  # "10th January" seen in December
        else:
            year = int(m.group(6))
            d = dt.date(year + 2000 if year < 100 else year, int(m.group(5)), int(m.group(4)))
    except (ValueError, KeyError):
        return None
    return d.isoformat()

TYPE_WORDS = ["semi-detached", "detached", "terraced", "end-terrace", "mid-terrace", "villa",
              "bungalow", "cottage", "flat", "apartment", "maisonette", "townhouse", "house", "duplex"]


class Robots:
    def __init__(self, s):
        self.rp = urllib.robotparser.RobotFileParser()
        self.sitemaps: list[str] = []
        try:
            r = get(s, urljoin(CFG["base_url"], "/robots.txt"), timeout=30)
            lines = r.text.splitlines() if r.ok else []
        except Exception:  # noqa: BLE001
            lines = []
        self.rp.parse(lines)
        self.sitemaps = [l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("sitemap:")]

    def allowed(self, url: str) -> bool:
        return self.rp.can_fetch(CFG["user_agent"], url)


class Fetcher:
    def __init__(self):
        self.s = session(CFG["user_agent"])
        self.robots = Robots(self.s)
        self.last = 0.0
        self.count = 0

    def html(self, url: str) -> str | None:
        if not self.robots.allowed(url):
            log.warning("robots.txt disallows %s - skipping", url)
            return None
        wait = CFG["request_delay_seconds"] - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        self.count += 1
        for pause in (60, 180, None):  # ESPC occasionally resets connections: back off, then give up
            try:
                r = get(self.s, url, timeout=45)
                break
            except requests.RequestException as e:
                if pause is None:
                    raise
                log.warning("ESPC request failed (%s); pausing %ds", e, pause)
                time.sleep(pause)
        if r.status_code == 404 or r.status_code == 410:
            return None
        r.raise_for_status()
        return r.text


def listing_id(url: str) -> str | None:
    m = PROPERTY_PATH.search(urlparse(url).path)
    return m.group(1) if m else None


def extract_property_urls(html: str, base: str) -> dict[str, str]:
    """Map ESPC id -> absolute property URL, from hrefs and any embedded JSON."""
    found: dict[str, str] = {}
    html = html.replace("\\/", "/")  # JSON-escaped URLs in embedded state
    for m in re.finditer(r"""(?:href=|"url"\s*:\s*|"link"\s*:\s*)["']([^"']*/property/[^"']+)["']""", html, re.I):
        url = urljoin(base, m.group(1))
        pid = listing_id(url)
        if pid:
            found.setdefault(pid, url.split("#")[0])
    return found


# ---------------------------------------------------------------- what we keep

EXCLUDE_TYPES = re.compile(CFG["exclude_types_regex"], re.I)
ALLOWED_DISTRICTS = {d for ds in CFG["allowed_postcode_districts"].values() for d in ds}
ALLOWED_AREAS = {re.match(r"[A-Z]+", d).group(0) for d in ALLOWED_DISTRICTS}
SLUG_POSTCODE = re.compile(r"-([a-z]{1,2}\d[a-z\d]?)-(\d[a-z]{2})(?:/|$)", re.I)


def slug_district(url: str) -> str | None:
    """ESPC property URLs end with the postcode: /property/2-lilybank-lane-...-eh28-8aw/36392542."""
    path = urlparse(url).path.rstrip("/")
    path = path[: path.rfind("/")] if re.search(r"/\d{5,}$", path) else path
    m = SLUG_POSTCODE.search(path + "/")
    return m.group(1).upper() if m else None


# Building plots and land for sale (to build on). A new-build *house* advertised as "Plot 13, The
# Hopetoun" is a home: its title says "4 bed detached house for sale", so it isn't caught here.
PLOT_WORDS = re.compile(
    r"\b(?:building plots?|plots? of land|plots?|land|site|development (?:site|opportunity|plot|with)|"
    r"planning (?:permission|consent)|self[- ]build|woodland|paddock)\b", re.I)
PLOT_FOR_SALE = re.compile(
    r"\b(?:plots?|land|site)\s+for sale|building plot|plots? of land|with (?:full |outline |detailed )?planning|self[- ]build",
    re.I)
PLOT_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(acres?|hectares?|ha)\b", re.I)


def is_plot(text: str, beds: int | None = None) -> bool:
    """Land or a building plot rather than a home. With a bedroom count it must say so outright
    (a plot "with planning for a 4 bed house" is still a plot)."""
    return bool(PLOT_WORDS.search(text)) and (beds is None or bool(PLOT_FOR_SALE.search(text)))


def rejection(title: str = "", ptype: str = "", beds: int | None = None, district: str | None = None,
              beds_required: bool = True) -> str | None:
    """Why a listing isn't wanted (None = keep): houses for sale, 2+ bedrooms, commutable districts.
    Plots and land are kept too (as their own kind); only the district rule applies to them."""
    text = f"{title} {ptype}"
    if re.search(r"\bto (rent|let)\b", text, re.I):
        return "rental"
    if is_plot(text, beds):
        return f"district {district}" if district and district not in ALLOWED_DISTRICTS else None
    if EXCLUDE_TYPES.search(text):
        return "flat"
    if beds is None and beds_required:
        return "bedrooms unknown"
    if beds is not None and beds < CFG["min_bedrooms"]:
        return f"{beds} bedroom"
    if district and district not in ALLOWED_DISTRICTS:
        return f"district {district}"
    return None


def extract_card_hints(html: str) -> dict[str, str]:
    """ESPC id -> the text of its results card ("3 bed semi-detached house for sale in ..."), when
    the card can be isolated. Lets us skip flats and 1-beds without fetching their pages."""
    soup = BeautifulSoup(html, "html.parser")
    hints: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        pid = listing_id(a["href"])
        if not pid or pid in hints:
            continue
        node = a
        for _ in range(6):
            node = node.parent
            if node is None or node.name in ("body", "html", "[document]"):
                break
            ids = {listing_id(x["href"]) for x in node.find_all("a", href=True)} - {None}
            if ids != {pid}:
                break  # this container holds other listings too
            text = node.get_text(" ", strip=True)
            if BEDS.search(text) and len(text) < 800:
                hints[pid] = text
                break
    return hints


def prefilter(url: str, hint: str | None) -> str | None:
    """Rejection reason decidable from the results page alone, else None (fetch the details)."""
    district = slug_district(url)
    if district and district not in ALLOWED_DISTRICTS:
        return f"district {district}"
    if hint:
        bm = BEDS.search(hint)
        return rejection(hint, beds=int(bm.group(1)) if bm else None, beds_required=False)
    return None


def page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def recognised_location(html: str) -> bool:
    """ESPC titles a location search 'Properties for Sale in Edinburgh | ESPC'. Without the 'in <place>'
    it has fallen back to a broader search, which we don't want to page through."""
    title = page_title(html)
    return not title or bool(re.search(r"\bfor sale in \w", title, re.I))


def next_page_url(html: str, current: str, page: int) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    link = soup.find("link", rel="next") or soup.find("a", rel="next")
    if link and link.get("href"):
        return urljoin(current, link["href"])
    for a in soup.find_all("a", href=True):
        label = (a.get("aria-label") or a.get_text(" ", strip=True)).lower()
        if label in ("next", "next page", "›", "»", ">") and a["href"] not in ("#", ""):
            return urljoin(current, a["href"])
    return None


def discover(f: Fetcher, hints: dict[str, str] | None = None) -> tuple[dict[str, str], bool]:
    """(id -> URL for every listing on the search pages, whether every search ran to the end)."""
    urls: dict[str, str] = {}
    hints = {} if hints is None else hints
    complete = True
    for n_search, start in enumerate(CFG["search_urls"], 1):
        url, page, seen, empty = start, 1, set(), 0
        while url and url not in seen:
            seen.add(url)
            try:
                html = f.html(url)
            except requests.RequestException as e:
                log.error("%s: page %d failed (%s) - moving on; listings not seen keep their status", start, page, e)
                complete = False
                break
            if not html:
                break
            got = extract_property_urls(html, url)
            hints.update(extract_card_hints(html))
            if page == 1:
                (DEBUG / f"search_{n_search}_page_1.html").write_text(html)
                if "orgid=" not in start and not recognised_location(html):
                    log.warning("%s: ESPC doesn't seem to recognise this location (page title %r) - skipping",
                                start, page_title(html))
                    break
            new = {k: v for k, v in got.items() if k not in urls}
            log.info("%s page %d: %d property links (%d new)", start.split("?")[-1], page, len(got), len(new))
            urls.update(got)
            if not new:
                break
            # Guard against a location ESPC doesn't recognise (and so returns all of Scotland):
            # give up on this search after several pages with nothing in our postcode areas.
            areas = {re.match(r"[A-Z]+", d).group(0) for d in map(slug_district, got.values()) if d}
            empty = 0 if (not areas or areas & ALLOWED_AREAS) else empty + 1
            if empty >= CFG["stop_after_empty_pages"]:
                log.warning("%s: %d pages with no listings in %s - stopping this search", start, empty, sorted(ALLOWED_AREAS))
                break
            url = next_page_url(html, url, page)
            if not url:  # no explicit link: try the conventional ?page=N
                sep = "&" if "?" in start else "?"
                url = f"{start}{sep}page={page + 1}"
            page += 1
    if not urls and CFG["use_sitemap_fallback"]:
        urls = discover_from_sitemaps(f)
    return urls, complete


def discover_from_sitemaps(f: Fetcher) -> dict[str, str]:
    urls: dict[str, str] = {}
    queue = list(f.robots.sitemaps) or [urljoin(CFG["base_url"], "/sitemap.xml")]
    seen = set()
    while queue and len(seen) < 50:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        xml = f.html(sm)
        if not xml:
            continue
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
            if loc.endswith(".xml") and ("propert" in loc or "sitemap" in loc):
                queue.append(loc)
            elif (pid := listing_id(loc)):
                urls[pid] = loc
    log.info("Sitemaps: %d property URLs", len(urls))
    return urls


# ------------------------------------------------------------------ parsing

def _walk(obj: Any) -> Iterable[dict]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _first(d: dict, *keys: str) -> Any:
    lower = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = lower.get(k.lower())
        if v not in (None, "", [], {}):
            return v
    return None


def _first_num(d: dict, *keys: str) -> float | None:
    lower = {k.lower(): v for k, v in d.items()}
    for k in keys:
        n = _num(lower.get(k.lower()))
        if n is not None:
            return n
    return None


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d[\d,]*\.?\d*", v)
        if m:
            try:
                return float(m.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


def _area(value: Any, unit: str = "") -> int | None:
    """Floor area in whole m² from a number/string/{value, unitCode} and a unit hint; None if implausible."""
    if isinstance(value, dict):
        unit = str(value.get("unitCode") or value.get("unitText") or value.get("unit") or unit)
        value = value.get("value") if value.get("value") is not None else value.get("amount")
    n = _num(str(value).replace(",", "")) if value is not None else None
    if n is None:
        return None
    if re.search(r"ft|feet|FTK", unit or "", re.I):
        n *= 0.092903
    return int(round(n)) if 25 <= n <= 2000 else None


def _json_blobs(soup: BeautifulSoup, html: str) -> tuple[list[Any], list[Any]]:
    ld, app = [], []
    for sc in soup.find_all("script"):
        text = sc.string or sc.get_text() or ""
        typ = (sc.get("type") or "").lower()
        if "ld+json" in typ:
            try:
                ld.append(json.loads(text))
            except json.JSONDecodeError:
                pass
        elif typ == "application/json" or sc.get("id") in ("__NEXT_DATA__", "__NUXT_DATA__"):
            try:
                app.append(json.loads(text))
            except json.JSONDecodeError:
                pass
        else:
            for m in re.finditer(r"window\.__[A-Z_]+__\s*=\s*(\{.*?\})\s*;?\s*$", text, re.S | re.M):
                try:
                    app.append(json.loads(m.group(1)))
                except json.JSONDecodeError:
                    pass
    return ld, app


def parse_property(html: str, url: str) -> dict:
    """Best-effort extraction of one ESPC property page."""
    soup = BeautifulSoup(html, "html.parser")
    ld, app = _json_blobs(soup, html)
    out: dict[str, Any] = {"id": listing_id(url), "url": url}

    # 1) JSON-LD (schema.org)
    for d in _walk(ld):
        geo = d.get("geo") if isinstance(d.get("geo"), dict) else None
        if geo and out.get("lat") is None:
            out["lat"], out["lng"] = _num(geo.get("latitude")), _num(geo.get("longitude"))
        addr = d.get("address")
        if isinstance(addr, dict) and not out.get("address"):
            parts = [addr.get(k) for k in ("streetAddress", "addressLocality", "postalCode")]
            out["address"] = ", ".join(p for p in parts if p)
            if addr.get("postalCode"):
                out["postcode"] = addr["postalCode"]
        elif isinstance(addr, str) and not out.get("address"):
            out["address"] = addr
        if out.get("price") is None:
            offers = d.get("offers")
            for o in (offers if isinstance(offers, list) else [offers] if isinstance(offers, dict) else []):
                if _num(o.get("price")):
                    out["price"] = int(_num(o["price"]))
        if out.get("bathrooms") is None:
            b = _first_num(d, "numberOfBathroomsTotal", "numberOfBathrooms", "numberOfFullBathrooms")
            if b is not None and 0 < b < 15:
                out["bathrooms"] = int(b)
        if out.get("floor_area_m2") is None and d.get("floorSize") is not None:
            out["floor_area_m2"] = _area(d["floorSize"])
        beds = _first(d, "numberOfBedrooms", "numberOfRooms")
        if beds is not None and out.get("bedrooms") is None and _num(beds):
            out["bedrooms"] = int(_num(beds))
        if not out.get("image"):
            img = d.get("image")
            img = img[0] if isinstance(img, list) and img else img
            if isinstance(img, dict):
                img = img.get("url")
            if isinstance(img, str):
                out["image"] = img
        if not out.get("title") and isinstance(d.get("name"), str):
            out["title"] = d["name"]
        if not out.get("description") and isinstance(d.get("description"), str):
            out["description"] = d["description"]

    # 2) Embedded app state: look for property-like objects.
    for d in _walk(app):
        la, ln = _first_num(d, "latitude", "lat"), _first_num(d, "longitude", "lng", "lon")
        if out.get("lat") is None and la and ln and 50 < la < 60:
            out["lat"], out["lng"] = la, ln
        if out.get("bedrooms") is None:
            b = _first_num(d, "bedrooms", "beds", "numberOfBedrooms", "bedroomCount")
            if b is not None and 0 <= b < 20:
                out["bedrooms"] = int(b)
        if out.get("bathrooms") is None:
            b = _first_num(d, "bathrooms", "baths", "numberOfBathrooms", "bathroomCount")
            if b is not None and 0 < b < 15:
                out["bathrooms"] = int(b)
        if out.get("floor_area_m2") is None:
            for k, v in d.items():
                if re.search(r"floor.?area|internal.?area|floor.?size|sq(uare)?.?(m|metres|meters|ft|feet)$", k, re.I):
                    unit = "ft" if re.search(r"ft|feet", k, re.I) else ""
                    out["floor_area_m2"] = _area(v, unit)
                    if out["floor_area_m2"]:
                        break
        if out.get("price") is None:
            p = _first_num(d, "price", "askingPrice", "priceValue", "displayPrice")
            if p and p > 10000:
                out["price"] = int(p)
        if not out.get("property_type"):
            t = _first(d, "propertyType", "propertyTypeName", "type", "style")
            if isinstance(t, str) and any(w in t.lower() for w in TYPE_WORDS):
                out["property_type"] = t
        if not out.get("features"):
            feats = _first(d, "features", "keyFeatures", "bullets")
            if isinstance(feats, list) and feats and all(isinstance(x, str) for x in feats):
                out["features"] = feats
        if not out.get("description"):
            desc = _first(d, "description", "summary", "fullDescription")
            if isinstance(desc, str) and len(desc) > 80:
                out["description"] = desc

    # 3) Meta tags and data attributes
    def meta(prop: str) -> str | None:
        el = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return el.get("content") if el else None
    out.setdefault("title", meta("og:title") or (soup.title.get_text(strip=True) if soup.title else ""))
    if not out.get("image"):
        out["image"] = meta("og:image")
    if not out.get("description"):
        out["description"] = meta("og:description") or meta("description") or ""
    if out.get("lat") is None:
        la, ln = meta("place:location:latitude"), meta("place:location:longitude")
        if la and ln:
            out["lat"], out["lng"] = _num(la), _num(ln)
    if out.get("lat") is None:
        el = soup.find(attrs={"data-lat": True}) or soup.find(attrs={"data-latitude": True})
        if el:
            out["lat"] = _num(el.get("data-lat") or el.get("data-latitude"))
            out["lng"] = _num(el.get("data-lng") or el.get("data-lon") or el.get("data-longitude"))
    if out.get("lat") is None:  # static map / map links: ...center=55.9,-3.2 or ?q=55.9,-3.2 or @55.9,-3.2
        m = re.search(r"(?:center=|q=|ll=|@)(5[5-6]\.\d{3,}),\s*(-[23]\.\d{3,})", html)
        if m:
            out["lat"], out["lng"] = float(m.group(1)), float(m.group(2))

    # 4) Text heuristics
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" "))
    head = f"{out.get('title', '')} {out.get('property_type', '')}"
    feats_text = " ".join(out.get("features") or [])

    pm = PRICE.search(head) or PRICE.search(text)
    if pm:
        if out.get("price") is None:
            out["price"] = int(pm.group("n").replace(",", ""))
        if pm.group("q"):
            out["price_qualifier"] = pm.group("q").title()
    if out.get("bedrooms") is None:
        bm = BEDS.search(head) or BEDS.search(text)
        if bm:
            out["bedrooms"] = int(bm.group(1))
        else:
            wm = BEDS_WORD.search(head) or BEDS_WORD.search(text)
            if wm:
                out["bedrooms"] = WORD_NUMS[wm.group(1).lower()]
    if not out.get("postcode"):
        pc = POSTCODE.search(out.get("address") or "") or POSTCODE.search(head) or POSTCODE.search(text)
        if pc:
            out["postcode"] = f"{pc.group(1)} {pc.group(2)}"
    if not out.get("address"):
        h1 = soup.find("h1")
        out["address"] = h1.get_text(" ", strip=True) if h1 else out.get("title", "")
    if not out.get("property_type"):
        for w in TYPE_WORDS:
            if re.search(rf"\b{re.escape(w)}\b", head, re.I):
                out["property_type"] = w
                break
    # Detached: from the title/type/features; only fall back to the opening of the
    # description when the headline doesn't already say what kind of home it is.
    type_text = f"{head} {feats_text}"
    desc_open = (out.get("description") or "")[:300]
    out["detached"] = bool(DETACHED.search(type_text) or (
        not re.search(r"semi|terrace|flat|apartment|maisonette", type_text, re.I) and DETACHED.search(desc_open)))
    desc = out.get("description") or ""
    # Bathrooms: "2 bathrooms" in the headline/features/description, then the page's own
    # "3 bedrooms 2 bathrooms" summary or a "Bathrooms: 2" label.
    if out.get("bathrooms") is None:
        for src in (f"{head} {feats_text} {desc}", text):
            m = BATHS.search(src) or BATHS_LABEL.search(src)
            if m and 0 < int(m.group(1)) < 15:
                out["bathrooms"] = int(m.group(1))
                break
    # Floor area: a labelled figure ("Floor area: 112 m²", often from the Home Report) first,
    # then any area figure in the description/features, then anywhere on the page.
    if not out.get("floor_area_m2"):
        for rx, src in ((AREA_LABELLED, f"{feats_text} {desc} {text}"), (AREA_ANY, f"{feats_text} {desc}"), (AREA_ANY, text)):
            for m in rx.finditer(src):
                a = _area(m.group(1), m.group(2))
                if a:
                    out["floor_area_m2"] = a
                    break
            if out.get("floor_area_m2"):
                break
    if not out.get("floor_area_m2"):
        out.pop("floor_area_m2", None)
    # Seller situation and sale stage, from the listing's own words (not site-wide boilerplate).
    own = f"{head} {feats_text} {desc}"
    flags = []
    if MOTIVATED.search(own):
        flags.append("motivated")
    if NEEDS_WORK.search(own):
        flags.append("needs_work")
    if flags:
        out["flags"] = flags
    cd = closing_date(f"{own} {text}")
    if cd:
        out["closing_date"] = cd
    ps = PLOT_SIZE.search(own)
    if ps:
        acres = float(ps.group(1)) * (2.471 if ps.group(2).lower().startswith("h") else 1)
        if 0.01 <= acres <= 500:
            out["plot_acres"] = round(acres, 2)
    out["plot_parsed"] = True
    out["parser_version"] = PARSER_VERSION
    garage_text = f"{head} {feats_text} {out.get('description') or ''}"
    out["garage"] = bool(GARAGE.search(garage_text)) and not NOT_GARAGE.search(garage_text)
    if out.get("postcode"):
        out["postcode"] = out["postcode"].upper()
        out["district"] = out["postcode"].split()[0]
    for k in ("description", "features"):
        out.pop(k, None)
    out["title"] = (out.get("title") or "").split("|")[0].strip()
    return out


def geocode_postcodes(listings: list[dict]) -> None:
    """Fill missing coordinates from postcodes.io (free, no key)."""
    need = sorted({l["postcode"] for l in listings if l.get("lat") is None and l.get("postcode")})
    if not need:
        return
    s = session()
    found: dict[str, tuple[float, float]] = {}
    for i in range(0, len(need), 100):
        r = s.post(SETTINGS["sources"]["postcodes_io"], json={"postcodes": need[i:i + 100]}, timeout=60)
        if not r.ok:
            continue
        for item in r.json().get("result", []):
            res = item.get("result")
            if res:
                found[item["query"]] = (res["latitude"], res["longitude"])
    for l in listings:
        if l.get("lat") is None and l.get("postcode") in found:
            l["lat"], l["lng"] = found[l["postcode"]]
            l["approx_location"] = True
    log.info("Geocoded %d postcodes", len(found))


def scrape(state: dict[str, dict]) -> dict[str, dict]:
    """Update and return `state` (id -> listing record)."""
    DEBUG.mkdir(parents=True, exist_ok=True)
    f = Fetcher()
    today = dt.date.today().isoformat()
    hints: dict[str, str] = {}
    urls, complete = discover(f, hints)
    if not urls:
        raise RuntimeError("ESPC: no property links found on search pages or sitemaps - see pipeline/debug/")
    # Drop flats, 1-beds and far-away districts using only the results page (URL postcode + card text).
    pre = {pid: prefilter(u, hints.get(pid)) for pid, u in urls.items()}
    reasons = Counter(r.split()[0] if r.startswith("district") else r for r in pre.values() if r)
    log.info("ESPC: %d listings found; skipped from results page: %s", len(urls), dict(reasons))
    stale_before = (dt.date.today() - dt.timedelta(days=CFG["refetch_details_after_days"])).isoformat()
    # A flat stays a flat: listings already excluded for their type/size/place aren't re-fetched.
    permanent = ("flat", "rental", "district")
    def needs_fetch(pid: str) -> bool:
        rec = state.get(pid)
        if rec is None:
            return True
        why = rec.get("excluded") or ""
        if why.startswith(permanent) or re.match(r"\d+ bedroom$", why):
            return False
        if why == "bedrooms unknown" and not rec.get("plot_parsed"):
            return True  # possibly a plot: read it again for the plot size
        return rec.get("fetched", "") < stale_before or rec.get("parser_version", 1) < PARSER_VERSION
    todo = [pid for pid in urls if not pre[pid] and needs_fetch(pid)]
    todo = todo[:CFG["max_detail_fetches_per_run"]]
    log.info("ESPC: fetching %d detail pages", len(todo))
    parsed_ok = 0
    missing_logged = 0
    for n, pid in enumerate(todo, 1):
        try:
            html = f.html(urls[pid])
        except Exception as e:  # noqa: BLE001
            log.warning("detail %s failed: %s", pid, e)
            continue
        if not html:
            continue
        if n <= 3:
            (DEBUG / f"property_{pid}.html").write_text(html)
        rec = parse_property(html, urls[pid])
        if missing_logged < 3 and not rec.get("floor_area_m2") and not rec.get("excluded"):
            # Show where the page mentions these, so the parser can be tuned from the job log.
            flat_text = re.sub(r"<[^>]+>", " ", html)
            snippets = [re.sub(r"\s+", " ", flat_text[max(0, m.start() - 80): m.end() + 80])
                        for m in re.finditer(r"bathroom|m²|sq\.? ?ft|floor area|EPC", flat_text, re.I)][:6]
            log.info("  no floor area on %s; context: %s", pid, " | ".join(snippets)[:1200])
            missing_logged += 1
        prev = state.get(pid, {})
        history = prev.get("price_history", [])
        if rec.get("price") and (not history or history[-1][1] != rec["price"]):
            history.append([today, rec["price"]])
        state[pid] = {**prev, **{k: v for k, v in rec.items() if v not in (None, "")},
                      "price_history": history, "fetched": today,
                      "first_seen": prev.get("first_seen", today)}
        if rec.get("price") and (rec.get("lat") or rec.get("postcode")):
            parsed_ok += 1
        if n % 50 == 0:
            log.info("  ... %d/%d", n, len(todo))
    got = [state[p] for p in todo if p in state and state[p].get("fetched") == today]
    if got:
        log.info("ESPC: of %d pages read, bathrooms found on %d, floor area on %d", len(got),
                 sum(1 for r in got if r.get("bathrooms")), sum(1 for r in got if r.get("floor_area_m2")))
    if todo and parsed_ok == 0:
        raise RuntimeError("ESPC: fetched detail pages but could not parse price/location from any - see pipeline/debug/")
    excluded: Counter = Counter()
    for pid, rec in state.items():
        if pid in urls:
            why = pre.get(pid)
        elif not complete and rec.get("active"):
            why = None  # a search was cut short, so we can't tell whether this one has sold
        else:
            why = "no longer listed"
        if not why:
            why = rejection(rec.get("title", ""), rec.get("property_type", ""), rec.get("bedrooms"), rec.get("district"))
        if is_plot(f"{rec.get('title', '')} {rec.get('property_type', '')}", rec.get("bedrooms")):
            rec["kind"] = "plot"
        else:
            rec.pop("kind", None)
        rec["active"] = not why
        if why:
            rec["excluded"] = why
            excluded[why.split()[0] if why.startswith("district") else why] += 1
        else:
            rec.pop("excluded", None)
        if pid in urls:
            rec["last_seen"] = today
    geocode_postcodes([r for r in state.values() if r["active"]])
    for rec in state.values():
        if rec["active"] and rec.get("lat") is not None and not in_bbox(rec["lat"], rec["lng"]):
            rec["active"], rec["excluded"] = False, "outside map area"
            excluded["outside map area"] += 1
    # Keep the state file small: forget excluded listings (they're re-checked if they change).
    for pid in [p for p, r in state.items() if r.get("excluded") and r["excluded"] != "no longer listed" and p not in urls]:
        del state[pid]
    log.info("ESPC: %d requests, %d houses kept, excluded: %s", f.count,
             sum(r["active"] for r in state.values()), dict(excluded))
    return state
