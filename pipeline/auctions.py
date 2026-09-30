"""Property auction lots (Future Property Auctions, Auction House Scotland).

Each auction house publishes a catalogue page listing its lots. We read the catalogue,
follow each lot link, and parse the lot page with the same generic parser used for ESPC
(JSON-LD, embedded data, meta tags, text), then add what's specific to auctions: the
opening bid or guide price and the auction date. The same rules apply as for ESPC:
houses only, 2+ bedrooms, commutable postcode districts.

The markup of these sites could not be inspected when this was written, so lot links are
recognised by a per-source pattern in settings.json and the job log prints the links seen
on each catalogue page, so the patterns can be tuned from a run.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import espc
from common import DEBUG, SETTINGS, get, in_bbox, log, session

CFG = SETTINGS["auctions"]
PRICE_BASIS = re.compile(
    r"(opening bid|starting bid|guide price|guide|reserve price)\s*(?:of|is|from)?\s*[:\-]?\s*\*?\s*£\s?(\d{1,3}(?:,\d{3})+|\d{4,})",
    re.I)
MONTH_RX = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*"
DATE_WORDS = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+" + MONTH_RX + r",?\s+(\d{4})", re.I)
DATE_NUM = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d{2})\b")
AUCTION_CONTEXT = re.compile(r"auction|sale date|bidding|ends?\b", re.I)


def lot_id(url: str) -> str:
    """One id per lot, whatever page of the lot the link points at (details, offer form, ...):
    the auction house's own lot number when the URL carries one."""
    host = urlparse(url).netloc.replace("www.", "").split(".")[0]
    m = re.search(r"[?&](?:id|lotid|propertyid)=(\d+)", url, re.I) or re.search(r"/lots?/(\d+)", url, re.I)
    if m:
        return f"auc-{host}-{m.group(1)}"
    return "auc-" + hashlib.sha1(url.encode()).hexdigest()[:12]


# Lots that are neither a home nor a plot to build on (plots and land are kept, as their own kind).
NOT_A_HOME = re.compile(r"\b(?:lock-?up|garage site|ground rent|feu duty|retail unit|shop unit|office)\b", re.I)
# Page furniture the auction sites put around the address in titles.
TITLE_NOISE = re.compile(
    r"^(?:future\s+)?auction details\s*-\s*|^property for auction in scotland\s*-\s*|"
    r"\s*-?\s*(?:guide|opening bid|starting bid)\b.*$|\s*\|.*$", re.I)


def clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", title or "").strip()
    for _ in range(3):
        t = TITLE_NOISE.sub("", t).strip(" -")
    return t


def _dates(text: str) -> list[tuple[int, dt.date]]:
    """(position, date) for every date written like '10 Sep 2026' or '10/09/2026'."""
    found = []
    for m in DATE_WORDS.finditer(text):
        try:
            found.append((m.start(), dt.date(int(m.group(3)), espc.MONTHS[m.group(2)[:3].lower()], int(m.group(1)))))
        except (ValueError, KeyError):
            pass
    for m in DATE_NUM.finditer(text):
        try:
            found.append((m.start(), dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
        except ValueError:
            pass
    return sorted(found)


def auction_date(text: str, today: dt.date | None = None) -> str | None:
    """The auction date: the first upcoming date that follows the word 'auction' (or similar),
    else the first upcoming date on the page."""
    today = today or dt.date.today()
    upcoming = [(pos, d) for pos, d in _dates(text) if d >= today - dt.timedelta(days=1)]
    for pos, d in upcoming:
        if AUCTION_CONTEXT.search(text[max(0, pos - 80):pos]):
            return d.isoformat()
    return upcoming[0][1].isoformat() if upcoming else None


def price_and_basis(text: str) -> tuple[int | None, str | None]:
    m = PRICE_BASIS.search(text)
    if not m:
        return None, None
    basis = m.group(1).lower()
    basis = "Opening bid" if "opening" in basis or "starting" in basis else "Reserve" if "reserve" in basis else "Guide price"
    return int(m.group(2).replace(",", "")), basis


def lot_links(html: str, base: str, pattern: str) -> dict[str, str]:
    """Lot detail URLs on a catalogue page (same site, matching the source's pattern)."""
    rx = re.compile(pattern)
    host = urlparse(base).netloc
    out: dict[str, str] = {}
    for m in re.finditer(r"""href=["']([^"'#]+)["']""", html, re.I):
        url = urljoin(base, m.group(1).replace("&amp;", "&"))
        if urlparse(url).netloc.endswith(host.replace("www.", "")) and rx.search(url):
            out.setdefault(lot_id(url), url)
    return out


def card_hints(html: str, base: str, urls: dict[str, str]) -> dict[str, str]:
    """Text of each lot's catalogue card (address, price, 'three bedroom...'), when isolatable."""
    by_url = {u: i for i, u in urls.items()}
    soup = BeautifulSoup(html, "html.parser")
    hints: dict[str, str] = {}

    def lot_of(href: str) -> str | None:
        return by_url.get(urljoin(base, href.split("#")[0]))
    for a in soup.find_all("a", href=True):
        lid = lot_of(a["href"])
        if not lid or lid in hints:
            continue
        node = a
        for _ in range(6):
            node = node.parent
            if node is None or node.name in ("body", "html", "[document]"):
                break
            linked = {lot_of(x["href"]) for x in node.find_all("a", href=True)} - {None}
            if linked != {lid}:
                break
            text = node.get_text(" ", strip=True)
            if "£" in text and len(text) < 1200:
                hints[lid] = text
                break
    return hints


def prefilter(hint: str | None) -> str | None:
    """Reject from catalogue text alone: out-of-area postcode, a flat, or a 1-bed."""
    if not hint:
        return None
    pc = espc.POSTCODE.search(hint.upper())
    district = pc.group(1) if pc else None
    bm = espc.BEDS.search(hint)
    wm = espc.BEDS_WORD.search(hint)
    beds = int(bm.group(1)) if bm else espc.WORD_NUMS[wm.group(1).lower()] if wm else None
    if NOT_A_HOME.search(hint[:200]):
        return "not a home or plot"
    return espc.rejection(hint[:300], beds=beds, district=district, beds_required=False)


def parse_lot(html: str, url: str, house: str) -> dict:
    rec = espc.parse_property(html, url)
    for k in ("title", "address"):
        if rec.get(k):
            rec[k] = clean_title(rec[k])
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" "))
    price, basis = price_and_basis(text)
    rec.update({"id": lot_id(url), "url": url, "source": "auction"})
    if price:
        rec["price"] = price
    rec.pop("price_qualifier", None)
    rec["auction"] = {"house": house, "date": auction_date(text), "basis": basis or "Guide price"}
    # A lot page describes one property, so its body text is the description. Drop menus,
    # headers, footers and links first: auction sites have "Repossessions" in their navigation.
    for tag in soup(["nav", "header", "footer", "a", "form"]):
        tag.decompose()
    body = re.sub(r"\s+", " ", soup.get_text(" "))
    flags = [f for f, rx in (("motivated", espc.MOTIVATED), ("needs_work", espc.NEEDS_WORK)) if rx.search(body)]
    ps = espc.PLOT_SIZE.search(body)
    if ps and not rec.get("plot_acres"):
        acres = float(ps.group(1)) * (2.471 if ps.group(2).lower().startswith("h") else 1)
        if 0.01 <= acres <= 500:
            rec["plot_acres"] = round(acres, 2)
    if flags:
        rec["flags"] = flags
    # Type: the headline often is just the address, so also look at the start of the description.
    if not rec.get("property_type"):
        start = text[:1500]
        for w in espc.TYPE_WORDS:
            if re.search(rf"\b{re.escape(w)}\b", start, re.I):
                rec["property_type"] = w
                break
    address = rec.get("address") or rec.get("title") or ""
    if NOT_A_HOME.search(address):
        rec["not_a_home"] = True
    # At auction a lot addressed as a plot/site/land is land, even if its description mentions the
    # bedrooms of a house it has planning for (unlike ESPC's "Plot 13 - 4 bed house" new builds).
    if espc.is_plot(address):
        rec["kind"] = "plot"
    beds = f"{rec['bedrooms']} bed " if rec.get("bedrooms") else ""
    what = "Plot / land" if rec.get("kind") == "plot" else f"{beds}{rec.get('property_type') or 'property'}"
    rec["title"] = f"{what} at auction".strip()
    return rec


class Fetcher(espc.Fetcher):
    """Same polite fetcher as ESPC (robots.txt, spacing, back-off), per auction site."""

    def __init__(self, base_url: str):
        self.s = session(espc.CFG["user_agent"])
        saved = espc.CFG["base_url"]
        espc.CFG["base_url"] = base_url
        try:
            self.robots = espc.Robots(self.s)
        finally:
            espc.CFG["base_url"] = saved
        self.last = 0.0
        self.count = 0


def scrape(state: dict[str, dict]) -> dict[str, dict]:
    """Update and return `state` (lot id -> record) for all configured auction houses."""
    DEBUG.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    seen: set[str] = set()
    failed_sources = set()
    for src in CFG["sources"]:
        name = src["name"]
        base = "{0.scheme}://{0.netloc}".format(urlparse(src["start_urls"][0]))
        f = Fetcher(base)
        urls: dict[str, str] = {}
        hints: dict[str, str] = {}
        for start in src["start_urls"]:
            url, page, visited = start, 1, set()
            while url and url not in visited:
                visited.add(url)
                try:
                    html = f.html(url)
                except requests.RequestException as e:
                    log.error("%s: %s failed (%s)", name, url, e)
                    failed_sources.add(name)
                    break
                if not html:
                    break
                if page == 1:
                    slug = re.sub(r"\W+", "_", name)
                    (DEBUG / f"auction_{slug}_page_1.html").write_text(html)
                    sample = sorted({urljoin(url, h) for h in re.findall(r"""href=["']([^"'#]+)["']""", html)})[:60]
                    log.info("%s: links on first catalogue page (for tuning lot_link_regex): %s", name, sample)
                got = lot_links(html, url, src["lot_link_regex"])
                new = {k: v for k, v in got.items() if k not in urls}
                hints.update(card_hints(html, url, got) if got else {})
                log.info("%s page %d: %d lots (%d new)", name, page, len(got), len(new))
                urls.update(got)
                if not new:
                    break
                url = espc.next_page_url(html, url, page)
                page += 1
        pre = {i: prefilter(hints.get(i)) for i in urls}
        todo = [i for i in urls if not pre[i] and (i not in state or state[i].get("fetched", "") < today)]
        todo = todo[: CFG["max_lot_fetches_per_source"]]
        log.info("%s: %d lots, %d skipped from catalogue text, fetching %d", name, len(urls),
                 sum(1 for r in pre.values() if r), len(todo))
        for n, lid in enumerate(todo):
            try:
                html = f.html(urls[lid])
            except requests.RequestException as e:
                log.warning("%s lot %s failed: %s", name, urls[lid], e)
                continue
            if not html:
                continue
            if n < 2:
                (DEBUG / f"auction_lot_{lid}.html").write_text(html)
            rec = parse_lot(html, urls[lid], name)
            prev = state.get(lid, {})
            state[lid] = {**prev, **{k: v for k, v in rec.items() if v not in (None, "")},
                          "fetched": today, "first_seen": prev.get("first_seen", today)}
        for lid in urls:
            seen.add(lid)
            if pre.get(lid):
                state.pop(lid, None)
        time.sleep(1)
    for lid, rec in list(state.items()):
        if lid not in seen and rec.get("auction", {}).get("house") in failed_sources:
            continue  # catalogue unreachable this run: keep what we knew
        date = (rec.get("auction") or {}).get("date")
        why = None
        if lid not in seen:
            why = "no longer listed"
        elif date and date < today:
            why = "auction passed"
        elif rec.get("not_a_home"):
            why = "not a home or plot"
        else:
            if rec.get("kind") == "plot":
                d = rec.get("district")
                why = f"district {d}" if d and d not in espc.ALLOWED_DISTRICTS else None
            else:
                why = espc.rejection(rec.get("title", ""), rec.get("property_type", ""), rec.get("bedrooms"), rec.get("district"))
        rec["active"] = not why
        rec["excluded"] = why
        if why in ("no longer listed", "auction passed"):
            del state[lid]
    espc.geocode_postcodes([r for r in state.values() if r.get("active")])
    for rec in state.values():
        if rec.get("active") and rec.get("lat") is not None and not in_bbox(rec["lat"], rec["lng"]):
            rec["active"], rec["excluded"] = False, "outside map area"
    log.info("Auctions: %d lots kept", sum(1 for r in state.values() if r.get("active")))
    return state
