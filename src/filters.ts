import { REJECTED } from "./saved";
import type { Listing } from "./types";

export type ColourBy = "price" | "time" | "vs";
export type SortBy = "price_asc" | "price_desc" | "time" | "newest" | "value" | "soonest" | "ppsqm";

/** The date something happens: auction day, or a closing date for offers. */
export const deadline = (l: Listing): string | undefined =>
  l.auction?.date ?? l.closing_date ?? l.also_auction?.[0]?.date;

export type HouseType = "detached" | "semi" | "terraced" | "townhouse" | "cottage" | "other";
export const HOUSE_TYPES: { id: HouseType; label: string }[] = [
  { id: "detached", label: "Detached" },
  { id: "semi", label: "Semi" },
  { id: "terraced", label: "Terraced" },
  { id: "townhouse", label: "Townhouse" },
  { id: "cottage", label: "Cottage / bungalow" },
  { id: "other", label: "Other" },
];

/** A home's type for the type chips, from ESPC's property type (or the detached flag when that's all there is). */
export function houseType(l: Listing): HouseType {
  const t = (l.property_type ?? "").toLowerCase();
  if (/semi/.test(t)) return "semi";
  if (/\bdetached\b/.test(t) || l.detached) return "detached";
  if (/terrace|mews/.test(t)) return "terraced";
  if (/town\s?house/.test(t)) return "townhouse";
  if (/cottage|bungalow/.test(t)) return "cottage";
  return "other";
}

/** Several choices in one filter value ("Edinburgh,Fife"); "" = any. */
export const listOf = (s: string): string[] => (s ? s.split(",").filter(Boolean) : []);
export const toggleIn = (s: string, v: string): string => {
  const xs = listOf(s);
  return (xs.includes(v) ? xs.filter((x) => x !== v) : [...xs, v]).join(",");
};

export interface Filters {
  minPrice: number;
  maxPrice: number;
  minBeds: number;
  maxBeds: number; // 0 = any
  minBaths: number; // 0 = any
  minArea: number; // m², 0 = any
  types: string; // comma list of HouseType, "" = any
  garage: boolean;
  maxMins: number; // 0 = any
  topSchool: boolean;
  school: string; // comma list, "" = any
  topPrimary: boolean;
  primary: string; // comma list of catchment primaries, "" = any
  region: string; // comma list, "" = any
  minSimd: number; // 0 = any
  q: string; // words that must all appear in the address/title/postcode
  newOnly: boolean;
  list: string; // show only homes on this saved list, "" = all
  hideRejected: boolean;
  saleType: "" | "private" | "auction";
  show: "homes" | "plots" | "all";
  motivated: boolean;
  needsWork: boolean;
  closingDate: boolean;
  reduced: boolean;
  colourBy: ColourBy;
  sort: SortBy;
  layers: Record<string, boolean>;
  simdDomain: string;
  allBus: boolean;
  basemap: "light" | "roads" | "satellite";
}

export const DEFAULTS: Filters = {
  minPrice: 0,
  maxPrice: 0,
  minBeds: 0,
  maxBeds: 0,
  minBaths: 0,
  minArea: 0,
  types: "",
  garage: false,
  maxMins: 0,
  topSchool: false,
  school: "",
  topPrimary: false,
  primary: "",
  region: "",
  minSimd: 0,
  q: "",
  newOnly: false,
  list: "",
  hideRejected: true,
  saleType: "",
  show: "homes",
  motivated: false,
  needsWork: false,
  closingDate: false,
  reduced: false,
  colourBy: "price",
  sort: "time",
  layers: { simd: false, tram: true, rail: true, tramProposed: true, bus: false, catchTop: true, catchAll: false, primary: false, areas: false },
  simdDomain: "decile",
  allBus: false,
  basemap: "light",
};

const NUM = ["minPrice", "maxPrice", "minBeds", "maxBeds", "minBaths", "minArea", "maxMins", "minSimd"] as const;
const BOOL = ["garage", "topSchool", "topPrimary", "newOnly", "hideRejected", "allBus", "motivated", "needsWork", "closingDate", "reduced"] as const;
const STR = ["show", "types", "school", "primary", "region", "q", "list", "saleType", "colourBy", "sort", "simdDomain", "basemap"] as const;
/** How the map looks rather than which homes show: not counted as active filters. */
const VIEW = new Set(["colourBy", "sort", "simdDomain", "basemap", "allBus", "show"]);

/** Filters live in the URL hash so a search can be bookmarked or shared. */
export function toHash(f: Filters): string {
  const p = new URLSearchParams();
  for (const k of NUM) if (f[k] !== DEFAULTS[k]) p.set(k, String(f[k]));
  for (const k of BOOL) if (f[k] !== DEFAULTS[k]) p.set(k, f[k] ? "1" : "0");
  for (const k of STR) if (f[k] !== DEFAULTS[k]) p.set(k, f[k]);
  const on = Object.entries(f.layers).filter(([, v]) => v).map(([k]) => k).sort().join(",");
  const def = Object.entries(DEFAULTS.layers).filter(([, v]) => v).map(([k]) => k).sort().join(",");
  if (on !== def) p.set("layers", on || "none");
  return p.toString();
}

export function fromHash(hash: string): Filters {
  const p = new URLSearchParams(hash.replace(/^#/, ""));
  const f: Filters = { ...DEFAULTS, layers: { ...DEFAULTS.layers } };
  for (const k of NUM) if (p.has(k)) f[k] = Number(p.get(k)) || 0;
  for (const k of BOOL) if (p.has(k)) f[k] = p.get(k) === "1";
  for (const k of STR) if (p.has(k)) (f as unknown as Record<string, string>)[k] = p.get(k)!;
  // Links saved before the type chips used "detached=1".
  if (p.get("detached") === "1" && !p.has("types")) f.types = "detached";
  if (p.has("layers")) {
    const on = new Set(p.get("layers")!.split(","));
    for (const k of Object.keys(f.layers)) f.layers[k] = on.has(k);
  }
  return f;
}

/** How many filters narrow the results (view settings like colour and sort don't count). */
export function activeCount(f: Filters): number {
  let n = 0;
  for (const k of [...NUM, ...BOOL, ...STR]) if (!VIEW.has(k) && f[k] !== DEFAULTS[k]) n++;
  return n;
}

const words = (q: string): string[] => q.toLowerCase().split(/[\s,]+/).filter(Boolean);

/** The saved lists a filter can refer to (filters.ts stays free of browser storage). */
export interface ListContext { list?: Set<string>; rejected?: Set<string> }

export function matches(l: Listing, f: Filters, ctx: ListContext = {}): boolean {
  if (f.list && ctx.list && !ctx.list.has(l.id)) return false;
  if (f.hideRejected && f.list !== REJECTED && ctx.rejected?.has(l.id)) return false;
  if (f.minPrice && l.price < f.minPrice) return false;
  if (f.maxPrice && l.price > f.maxPrice) return false;
  if (f.minBeds && (l.bedrooms ?? 0) < f.minBeds) return false;
  if (f.maxBeds && (l.bedrooms ?? 0) > f.maxBeds) return false;
  if (f.minBaths && (l.bathrooms ?? 0) < f.minBaths) return false;
  if (f.minArea && (l.floor_area_m2 ?? 0) < f.minArea) return false;
  if (f.types && !listOf(f.types).includes(houseType(l))) return false;
  if (f.garage && !l.garage) return false;
  if (f.maxMins && l.travel.best_min > f.maxMins) return false;
  if (f.topSchool && !l.top_school_rank) return false;
  if (f.school) {
    const want = listOf(f.school);
    if (!want.includes(l.catchment ?? "") && !want.includes(l.catchment_rc ?? "")) return false;
  }
  if (f.topPrimary && !l.top_primary) return false;
  if (f.primary) {
    const want = listOf(f.primary);
    if (!want.includes(l.primary ?? "") && !want.includes(l.primary_rc ?? "")) return false;
  }
  if (f.region && !listOf(f.region).includes(l.region ?? "")) return false;
  if (f.minSimd && (l.simd?.decile ?? 0) < f.minSimd) return false;
  if (f.q) {
    const hay = `${l.address ?? ""} ${l.title ?? ""} ${l.postcode ?? ""}`.toLowerCase();
    if (!words(f.q).every((w) => hay.includes(w))) return false;
  }
  if (f.newOnly && !isNew(l)) return false;
  if (f.show === "homes" && l.kind === "plot") return false;
  if (f.show === "plots" && l.kind !== "plot") return false;
  const atAuction = l.source === "auction" || !!l.also_auction?.length;
  if (f.saleType === "auction" && !atAuction) return false;
  if (f.saleType === "private" && l.source === "auction") return false;
  if (f.motivated && !l.flags?.includes("motivated")) return false;
  if (f.needsWork && !l.flags?.includes("needs_work")) return false;
  if (f.closingDate && !l.closing_date) return false;
  if (f.reduced && !isReduced(l)) return false;
  return true;
}

export const isNew = (l: Listing): boolean =>
  !!l.first_seen && Date.now() - new Date(l.first_seen).getTime() < 7 * 86_400_000;

export const isReduced = (l: Listing): boolean => {
  const h = l.price_history ?? [];
  return h.length > 1 && h[h.length - 1][1] < h[h.length - 2][1];
};

/** Asking price per m² of floor area, when the floor area is known. */
export const pricePerM2 = (l: Listing): number | undefined =>
  l.floor_area_m2 ? Math.round(l.price / l.floor_area_m2) : undefined;

export function sortListings(ls: Listing[], by: SortBy): Listing[] {
  const cmp: Record<SortBy, (a: Listing, b: Listing) => number> = {
    price_asc: (a, b) => a.price - b.price,
    price_desc: (a, b) => b.price - a.price,
    time: (a, b) => a.travel.best_min - b.travel.best_min,
    newest: (a, b) => (b.first_seen ?? "").localeCompare(a.first_seen ?? ""),
    soonest: (a, b) => (deadline(a) ?? "9999").localeCompare(deadline(b) ?? "9999"),
    value: (a, b) => (a.area?.vs_pct ?? 0) - (b.area?.vs_pct ?? 0),
    ppsqm: (a, b) => (pricePerM2(a) ?? 1e12) - (pricePerM2(b) ?? 1e12),
  };
  return [...ls].sort(cmp[by]);
}
