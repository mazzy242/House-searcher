"""Public-transport travel time to Waverley from a GTFS timetable.

Reverse connection scan: for a target arrival time T, scan every timetabled hop
(stop A -> stop B on one trip) from latest to earliest departure and work out,
for every stop, the latest time you can be standing there and still reach
Waverley by T. Doing that for a handful of T values (08:30..09:10) and averaging
gives a typical weekday-morning door-to-door time for any address:

    walk to a nearby stop + ride(s) + transfers + walk from the stop to Waverley

Rail is not in the bus GTFS feed, so trains are estimated from
config/rail_stations.json (walk + half the peak interval + minutes on the train).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from common import CACHE, SETTINGS, get, haversine_m, in_bbox, load_config, log, session, walk_seconds

NEG = float("-inf")


def hms(t: str) -> int:
    h, m, *s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + (int(s[0]) if s else 0)


@dataclass
class Network:
    stop_ids: list[str]
    names: list[str]
    lat: list[float]
    lng: list[float]
    # (dep, arr, from_idx, to_idx, trip_idx), sorted by dep descending
    connections: list[tuple[int, int, int, int, int]]
    trip_route: list[str]
    footpaths: list[list[tuple[int, float]]] = field(default_factory=list)
    service_date: str = ""


def download_gtfs(url: str | None = None) -> Path:
    url = url or SETTINGS["sources"]["gtfs_url"]
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "gtfs.zip"
    if path.exists() and (dt.datetime.now().timestamp() - path.stat().st_mtime) < 6 * 86400:
        log.info("GTFS: using cached %s", path)
        return path
    log.info("GTFS: downloading %s", url)
    with get(session(), url, stream=True, timeout=600) as r:
        r.raise_for_status()
        tmp = path.with_suffix(".part")
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
        tmp.replace(path)
    return path


def _rows(z: zipfile.ZipFile, name: str):
    try:
        with z.open(name) as fh:
            yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
    except KeyError:
        return


def _active_services(z: zipfile.ZipFile, day: dt.date) -> set[str]:
    ymd = day.strftime("%Y%m%d")
    weekday = day.strftime("%A").lower()
    active = {r["service_id"] for r in _rows(z, "calendar.txt")
              if r.get(weekday) == "1" and r["start_date"] <= ymd <= r["end_date"]}
    for r in _rows(z, "calendar_dates.txt"):
        if r["date"] == ymd:
            (active.add if r["exception_type"] == "1" else active.discard)(r["service_id"])
    return active


def pick_service_date(z: zipfile.ZipFile, today: dt.date | None = None) -> tuple[dt.date, set[str]]:
    """Next Tuesday-Thursday (avoiding Mondays/Fridays) with the most services running."""
    today = today or dt.date.today()
    best: tuple[int, dt.date, set[str]] | None = None
    for i in range(1, 15):
        d = today + dt.timedelta(days=i)
        if d.weekday() not in (1, 2, 3):
            continue
        svc = _active_services(z, d)
        if best is None or len(svc) > best[0]:
            best = (len(svc), d, svc)
    if not best or not best[0]:
        raise RuntimeError("GTFS: no services run on any upcoming weekday - is the feed current?")
    return best[1], best[2]


def load_network(gtfs_path: Path, today: dt.date | None = None) -> Network:
    cfg = SETTINGS["travel"]
    arrive = [hms(t) for t in cfg["arrive_by"]]
    window = (min(arrive) - 120 * 60, max(arrive))
    with zipfile.ZipFile(gtfs_path) as z:
        day, services = pick_service_date(z, today)
        stop_idx: dict[str, int] = {}
        ids, names, lats, lngs = [], [], [], []
        for r in _rows(z, "stops.txt"):
            try:
                la, ln = float(r["stop_lat"]), float(r["stop_lon"])
            except (TypeError, ValueError):
                continue
            if in_bbox(la, ln):
                stop_idx[r["stop_id"]] = len(ids)
                ids.append(r["stop_id"]); names.append(r.get("stop_name", "")); lats.append(la); lngs.append(ln)
        route_name = {}
        for r in _rows(z, "routes.txt"):
            short = r.get("route_short_name") or r.get("route_long_name") or r["route_id"]
            route_name[r["route_id"]] = "Tram" if r.get("route_type") == "0" else short
        trip_idx: dict[str, int] = {}
        trip_route: list[str] = []
        for r in _rows(z, "trips.txt"):
            if r["service_id"] in services:
                trip_idx[r["trip_id"]] = len(trip_route)
                trip_route.append(route_name.get(r["route_id"], "?"))
        # stop_times can be tens of millions of rows for all of Scotland: stream it and
        # keep only in-bbox stops of running trips inside the time window.
        per_trip: dict[int, list[tuple[int, int, int, int]]] = {}
        for r in _rows(z, "stop_times.txt"):
            t = trip_idx.get(r["trip_id"])
            s = stop_idx.get(r["stop_id"])
            if t is None or s is None:
                continue
            a, d = r.get("arrival_time") or r.get("departure_time"), r.get("departure_time") or r.get("arrival_time")
            if not a:
                continue
            at, dt_ = hms(a), hms(d)
            if dt_ < window[0] - 3600 or at > window[1]:
                continue
            per_trip.setdefault(t, []).append((int(r["stop_sequence"]), at, dt_, s))
    conns = []
    for t, seq in per_trip.items():
        seq.sort()
        for (_, _, dep, u), (_, arr, _, v) in zip(seq, seq[1:]):
            if u != v and arr >= dep and window[0] <= dep <= window[1]:
                conns.append((dep, arr, u, v, t))
    conns.sort(key=lambda c: (-c[0], -c[1]))
    net = Network(ids, names, lats, lngs, conns, trip_route, service_date=day.isoformat())
    net.footpaths = _footpaths(net, cfg["transfer_walk_m"])
    log.info("GTFS: %s, %d stops, %d trips, %d connections", day, len(ids), len(per_trip), len(conns))
    return net


def _footpaths(net: Network, max_m: float) -> list[list[tuple[int, float]]]:
    # Grid bucket so this stays fast for a few thousand stops.
    cell = 0.005
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (la, ln) in enumerate(zip(net.lat, net.lng)):
        grid.setdefault((int(la / cell), int(ln / cell)), []).append(i)
    out: list[list[tuple[int, float]]] = [[] for _ in net.stop_ids]
    for i, (la, ln) in enumerate(zip(net.lat, net.lng)):
        gi, gj = int(la / cell), int(ln / cell)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for j in grid.get((gi + di, gj + dj), ()):
                    if j != i:
                        d = haversine_m(la, ln, net.lat[j], net.lng[j])
                        if d <= max_m:
                            out[i].append((j, walk_seconds(d)))
    return out


@dataclass
class Profile:
    """For one target arrival time: latest time to be at each stop, when you'd actually arrive, and how."""
    target: int
    latest: list[float]
    arrival: list[float]
    route: list[str]
    board_stop: list[int]


def reverse_scan(net: Network, target: int) -> Profile:
    cfg = SETTINGS["travel"]
    buf = cfg["transfer_buffer_s"]
    w = SETTINGS["waverley"]
    n = len(net.stop_ids)
    latest = [NEG] * n
    arrival = [NEG] * n
    route = [""] * n
    board = [-1] * n
    to_station: dict[int, float] = {}
    for i in range(n):
        d = haversine_m(net.lat[i], net.lng[i], w["lat"], w["lng"])
        if d <= cfg["destination_radius_m"]:
            to_station[i] = walk_seconds(d)
    trip_arr: dict[int, float] = {}
    for dep, arr, u, v, t in net.connections:
        if dep > target:
            continue
        reach = trip_arr.get(t)
        if reach is None:
            if v in to_station and arr + to_station[v] <= target:
                reach = arr + to_station[v]
            elif latest[v] >= arr + buf:
                reach = arrival[v]
            else:
                continue
            trip_arr[t] = reach
        if dep > latest[u]:
            latest[u], arrival[u], route[u], board[u] = dep, reach, net.trip_route[t], u
            for x, walk in net.footpaths[u]:
                cand = dep - walk
                if cand > latest[x]:
                    latest[x], arrival[x], route[x], board[x] = cand, reach, net.trip_route[t], u
    return Profile(target, latest, arrival, route, board)


def build_profiles(net: Network) -> list[Profile]:
    return [reverse_scan(net, hms(t)) for t in SETTINGS["travel"]["arrive_by"]]


class StopIndex:
    def __init__(self, net: Network, cell: float = 0.01):
        self.net, self.cell = net, cell
        self.grid: dict[tuple[int, int], list[int]] = {}
        for i, (la, ln) in enumerate(zip(net.lat, net.lng)):
            self.grid.setdefault((int(la / cell), int(ln / cell)), []).append(i)

    def near(self, lat: float, lng: float, max_m: float) -> list[tuple[int, float]]:
        gi, gj = int(lat / self.cell), int(lng / self.cell)
        r = int(max_m / 600) + 1
        out = []
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                for j in self.grid.get((gi + di, gj + dj), ()):
                    d = haversine_m(lat, lng, self.net.lat[j], self.net.lng[j])
                    if d <= max_m:
                        out.append((j, d))
        return out


def journey(lat: float, lng: float, net: Network | None, profiles: list[Profile],
            index: StopIndex | None) -> dict:
    """Door-to-Waverley estimate for one address. Minutes, rounded."""
    cfg = SETTINGS["travel"]
    w = SETTINGS["waverley"]
    direct_m = haversine_m(lat, lng, w["lat"], w["lng"])
    walk_min = walk_seconds(direct_m) / 60
    result: dict = {
        "walk_min": round(walk_min),
        "cycle_min": round(direct_m * cfg["walk_detour_factor"] / (cfg["cycle_speed_kmh"] / 3.6) / 60),
        "km": round(direct_m / 1000, 1),
    }
    options: list[tuple[float, str]] = [(walk_min, "Walk")]
    # Bus / tram
    if net and profiles and index:
        near = index.near(lat, lng, cfg["max_access_walk_m"])
        times, best_how = [], None
        for p in profiles:
            best = None
            for s, d in near:
                if p.latest[s] == NEG:
                    continue
                leave = p.latest[s] - walk_seconds(d)
                if best is None or leave > best[0]:
                    best = (leave, p.arrival[s] - leave, s, d, p.route[s], p.board_stop[s])
            if best:
                times.append(best[1] / 60)
                best_how = best_how or best
        if times:
            pt = sum(times) / len(times)
            _, _, s, d, rte, b = best_how
            stop = net.names[b] if b >= 0 else net.names[s]
            how = f"Walk {round(walk_seconds(d) / 60)} min to {stop}, then {'tram' if rte == 'Tram' else 'bus ' + rte}"
            result["pt_min"] = round(pt)
            result["pt_how"] = how
            options.append((pt, how))
    # Rail
    best_rail = None
    for st in load_config("rail_stations.json")["stations"]:
        d = haversine_m(lat, lng, st["lat"], st["lng"])
        if d > 2500:
            continue
        mins = walk_seconds(d) / 60 + 30 / st["trains_per_hour"] + st["minutes"]
        if best_rail is None or mins < best_rail[0]:
            best_rail = (mins, f"Walk {round(walk_seconds(d) / 60)} min to {st['name']} station, then train")
    if best_rail:
        result["rail_min"] = round(best_rail[0])
        result["rail_how"] = best_rail[1]
        options.append(best_rail)
    fastest = min(options, key=lambda o: o[0])
    result["best_min"] = round(fastest[0])
    result["best_how"] = fastest[1]
    return result
