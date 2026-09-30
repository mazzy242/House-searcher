"""Find the same home listed more than once, and keep ESPC as the master record.

Two listings are the same home when they share a postcode and the same house number(s) on
the same street (or, for named houses without a number, the same house name). House numbers
are compared exactly: "70" and "70a" are different homes. A lot that covers several homes
("70 and 70a Kingston Avenue") only *partly* overlaps a listing for "70": both are kept and
cross-referenced rather than merged.

  ESPC + ESPC (same home re-listed)  -> keep the newest listing, carry over price history, "Re-listed"
  ESPC + auction (same home)         -> keep ESPC; the auction is attached to it ("Also at auction")
  ESPC + auction (lot covers more)   -> keep both, each pointing at the other
  auction + auction                  -> keep the earliest auction, the other attached to it
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from common import log

STOP = {"edinburgh", "the", "and", "flat", "at", "of", "city", "midlothian", "lothian", "east", "west",
        "scotland", "united", "kingdom", "uk"}
NOISE = re.compile(r"^(?:future\s+)?auction details\s*-\s*|^property for auction in scotland\s*-\s*|"
                   r"\s*-?\s*(?:guide price|guide|opening bid|starting bid)\b.*$", re.I | re.S)
POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.I)


@dataclass(frozen=True)
class Key:
    postcode: str
    numbers: frozenset   # {"70"}, {"70", "70a"}, {"plot13"}; empty for named houses
    words: frozenset     # street / house-name words


def key(address: str, postcode: str) -> Key:
    addr = NOISE.sub("", address or "")
    addr = POSTCODE.sub("", addr)
    parts = []
    for p in addr.lower().split(","):
        p = re.sub(r"\bplot\s+(\d+)", r"plot\1", p)
        p = re.sub(r"\b\d+/\d+\b", " ", p)  # "2/1" is a flat position, not a house number
        if p.strip():
            parts.append(p.strip())
    num_rx = r"\b(plot\d+|\d+[a-z]?)\b"
    numbered = [i for i, p in enumerate(parts) if any(n.lstrip("0") for n in re.findall(num_rx, p))]
    if numbered:
        # The street is the part holding the number ("2 Main Street"), not the town after it.
        i = numbered[0]
        seg = parts[i]
        if not re.sub(num_rx, "", seg).strip(" -&and") and i + 1 < len(parts):
            seg += " " + parts[i + 1]  # "70, Kingston Avenue"
        if i > 0:
            seg = parts[i - 1] + " " + seg  # a house name before the number: "The Coach House, 70 ..."
    else:
        seg = " ".join(parts[:2])
    # "2-4", "2 & 4", "70 and 70a" -> each number separately.
    numbers = frozenset(n for n in re.findall(num_rx, seg) if n.lstrip("0"))
    street = parts[numbered[0]] if numbered else seg
    words = frozenset(w for w in re.findall(r"[a-z]+", re.sub(num_rx, " ", street))
                      if w not in STOP and len(w) > 1)
    return Key(re.sub(r"\s+", " ", (postcode or "").upper().strip()), numbers, words)


def relation(a: dict, b: dict) -> str | None:
    """'same', 'partial' (one lot contains the other home among others) or None."""
    ka, kb = key(a.get("address", ""), a.get("postcode", "")), key(b.get("address", ""), b.get("postcode", ""))
    if not ka.postcode or ka.postcode != kb.postcode:
        return None
    if ka.numbers and kb.numbers:
        if ka.words and kb.words and not (ka.words & kb.words):
            return None  # same postcode, same number, different street
        if ka.numbers == kb.numbers:
            return "same"
        return "partial" if ka.numbers & kb.numbers else None
    if not ka.numbers and not kb.numbers and ka.words and kb.words:
        # Named houses ("Rose Cottage, Main Street"). New-build house types ("The Fulton, Edgelaw
        # View") look alike but are separate listings, so ESPC listings are never merged on names alone.
        if a.get("source") != "auction" and b.get("source") != "auction":
            return None
        overlap = len(ka.words & kb.words)
        beds_ok = not a.get("bedrooms") or not b.get("bedrooms") or a["bedrooms"] == b["bedrooms"]
        if overlap >= max(2, min(len(ka.words), len(kb.words)) - 1) and beds_ok:
            return "same"
    return None


def _auction_ref(r: dict) -> dict:
    return {"house": r["auction"]["house"], "date": r["auction"].get("date"), "basis": r["auction"].get("basis"),
            "price": r["price"], "url": r["url"], "address": r.get("address", "")}


def _espc_ref(r: dict) -> dict:
    return {"id": r["id"], "address": r.get("address", ""), "price": r["price"], "url": r["url"]}


def _newer(a: dict, b: dict) -> bool:
    try:
        return int(a["id"]) > int(b["id"])  # ESPC ids increase over time
    except ValueError:
        return a.get("first_seen", "") > b.get("first_seen", "")


def dedupe(listings: list[dict]) -> tuple[list[dict], list[str]]:
    """Return (listings with duplicates resolved, a log line per decision)."""
    by_pc: dict[str, list[dict]] = {}
    for r in listings:
        by_pc.setdefault(key(r.get("address", ""), r.get("postcode", "")).postcode, []).append(r)
    dropped: set[str] = set()
    notes: list[str] = []
    for pc, group in by_pc.items():
        if not pc or len(group) < 2:
            continue
        # ESPC first, so ESPC listings are always the ones that survive.
        group.sort(key=lambda r: (r.get("source") == "auction", r["id"]))
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a["id"] in dropped or b["id"] in dropped:
                    continue
                rel = relation(a, b)
                if not rel:
                    continue
                a_auc, b_auc = a.get("source") == "auction", b.get("source") == "auction"
                if not a_auc and not b_auc and rel == "same":
                    keep, lose = (a, b) if _newer(a, b) else (b, a)
                    hist = sorted({tuple(x) for x in (keep.get("price_history") or []) + (lose.get("price_history") or [])})
                    keep["price_history"] = [list(x) for x in hist]
                    keep["first_seen"] = min(filter(None, [keep.get("first_seen"), lose.get("first_seen")]), default=None)
                    keep["relisted"] = True
                    dropped.add(lose["id"])
                    notes.append(f"re-listed: ESPC {lose['id']} merged into {keep['id']} ({keep.get('address')})")
                elif not a_auc and b_auc and rel == "same":
                    a.setdefault("also_auction", []).append(_auction_ref(b))
                    dropped.add(b["id"])
                    notes.append(f"auction lot {b['id']} is ESPC {a['id']} ({a.get('address')}): shown on the ESPC listing")
                elif not a_auc and b_auc:  # partial: the lot covers this home and others
                    a.setdefault("in_auction_lot", []).append(_auction_ref(b))
                    b.setdefault("overlaps_espc", []).append(_espc_ref(a))
                    notes.append(f"auction lot {b['id']} ({b.get('address')}) includes ESPC {a['id']} ({a.get('address')}): both kept")
                elif a_auc and b_auc and rel == "same":
                    keep, lose = sorted((a, b), key=lambda r: r["auction"].get("date") or "9999")
                    keep.setdefault("also_auction", []).append(_auction_ref(lose))
                    dropped.add(lose["id"])
                    notes.append(f"auction lot {lose['id']} is the same home as {keep['id']}: merged")
    for n in notes:
        log.info("dedupe: %s", n)
    return [r for r in listings if r["id"] not in dropped], notes
