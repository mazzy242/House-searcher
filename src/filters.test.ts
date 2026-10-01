import { describe, expect, it } from "vitest";
import { DEFAULTS, type Filters, activeCount, fromHash, houseType, matches, sortListings, toHash, toggleIn } from "./filters";
import type { Listing } from "./types";

const home = (over: Partial<Listing> = {}): Listing => ({
  id: "1", url: "https://espc.com/1", lat: 55.95, lng: -3.19, price: 400_000, detached: false, garage: false,
  travel: { best_min: 20, best_how: "bus", walk_min: 60, cycle_min: 20, km: 5 },
  address: "12 Comiston Road, Edinburgh", postcode: "EH10 6AA", region: "Edinburgh", bedrooms: 3, ...over,
});
const f = (over: Partial<Filters> = {}): Filters => ({ ...DEFAULTS, layers: { ...DEFAULTS.layers }, ...over });

describe("houseType", () => {
  it("reads ESPC's property type, semi before detached", () => {
    expect(houseType(home({ property_type: "semi-detached" }))).toBe("semi");
    expect(houseType(home({ property_type: "detached" }))).toBe("detached");
    expect(houseType(home({ property_type: "terraced" }))).toBe("terraced");
    expect(houseType(home({ property_type: "townhouse" }))).toBe("townhouse");
    expect(houseType(home({ property_type: "bungalow" }))).toBe("cottage");
    expect(houseType(home({ property_type: "house" }))).toBe("other");
  });
  it("falls back to the detached flag", () => {
    expect(houseType(home({ property_type: "cottage", detached: true }))).toBe("detached");
  });
});

describe("matches", () => {
  it("filters by type (any of several)", () => {
    const semi = home({ property_type: "semi-detached" });
    expect(matches(semi, f({ types: "detached" }))).toBe(false);
    expect(matches(semi, f({ types: "detached,semi" }))).toBe(true);
  });
  it("bed, bath and floor-area limits; missing figures fail a set limit", () => {
    expect(matches(home({ bedrooms: 5 }), f({ maxBeds: 4 }))).toBe(false);
    expect(matches(home({ bedrooms: 4 }), f({ maxBeds: 4 }))).toBe(true);
    expect(matches(home({ bathrooms: 1 }), f({ minBaths: 2 }))).toBe(false);
    expect(matches(home({ bathrooms: 2 }), f({ minBaths: 2 }))).toBe(true);
    expect(matches(home(), f({ minArea: 100 }))).toBe(false);
    expect(matches(home({ floor_area_m2: 120 }), f({ minArea: 100 }))).toBe(true);
  });
  it("text search needs every word, any case, across address/title/postcode", () => {
    expect(matches(home(), f({ q: "comiston eh10" }))).toBe(true);
    expect(matches(home(), f({ q: "Comiston Leith" }))).toBe(false);
  });
  it("several areas and schools", () => {
    expect(matches(home({ region: "Midlothian" }), f({ region: "Edinburgh,Midlothian" }))).toBe(true);
    expect(matches(home({ region: "Fife" }), f({ region: "Edinburgh,Midlothian" }))).toBe(false);
    const l = home({ catchment: "Firrhill High", catchment_rc: "St Thomas of Aquin's RC High" });
    expect(matches(l, f({ school: "Boroughmuir High,Firrhill High" }))).toBe(true);
    expect(matches(l, f({ school: "St Thomas of Aquin's RC High" }))).toBe(true);
    expect(matches(l, f({ school: "Boroughmuir High" }))).toBe(false);
  });
});

describe("hash", () => {
  it("round-trips the new filters", () => {
    const x = f({ types: "detached,semi", maxBeds: 4, minArea: 120, q: "morningside", region: "Edinburgh,Fife" });
    expect(fromHash(toHash(x))).toEqual(x);
  });
  it("reads links saved before the type chips", () => {
    expect(fromHash("#detached=1&maxPrice=500000").types).toBe("detached");
    expect(fromHash("#region=Edinburgh").region).toBe("Edinburgh");
  });
});

it("toggleIn adds and removes", () => {
  expect(toggleIn("", "a")).toBe("a");
  expect(toggleIn("a,b", "a")).toBe("b");
});

it("activeCount ignores view settings", () => {
  expect(activeCount(f({ colourBy: "time", sort: "newest" }))).toBe(0);
  expect(activeCount(f({ maxPrice: 500_000, types: "detached" }))).toBe(2);
});

it("sorts by price per m², unknown floor areas last", () => {
  const ls = [home({ id: "a" }), home({ id: "b", floor_area_m2: 100 }), home({ id: "c", price: 300_000, floor_area_m2: 100 })];
  expect(sortListings(ls, "ppsqm").map((l) => l.id)).toEqual(["c", "b", "a"]);
});

describe("saved lists", () => {
  it("shows only the chosen list, and hides rejected homes unless asked", () => {
    const l = home();
    expect(matches(l, f({ list: "Shortlist" }), { list: new Set(["2"]) })).toBe(false);
    expect(matches(l, f({ list: "Shortlist" }), { list: new Set(["1"]) })).toBe(true);
    expect(matches(l, f(), { rejected: new Set(["1"]) })).toBe(false);
    expect(matches(l, f({ hideRejected: false }), { rejected: new Set(["1"]) })).toBe(true);
    expect(matches(l, f({ list: "Rejected" }), { list: new Set(["1"]), rejected: new Set(["1"]) })).toBe(true);
  });
});

it("filters by catchment primary and top primaries", () => {
  const l = home({ primary: "Bruntsfield Primary", primary_rc: "St Peter's RC Primary", top_primary: true });
  expect(matches(l, f({ primary: "Sciennes Primary,Bruntsfield Primary" }))).toBe(true);
  expect(matches(l, f({ primary: "St Peter's RC Primary" }))).toBe(true);
  expect(matches(l, f({ primary: "Sciennes Primary" }))).toBe(false);
  expect(matches(l, f({ topPrimary: true }))).toBe(true);
  expect(matches(home(), f({ topPrimary: true }))).toBe(false);
});
