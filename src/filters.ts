import type { Listing } from "./types";

export type ColourBy = "price" | "time" | "vs";
export type SortBy = "price_asc" | "price_desc" | "time" | "newest" | "value" | "soonest";

/** The date something happens: auction day, or a closing date for offers. */
export const deadline = (l: Listing): string | undefined =>
  l.auction?.date ?? l.closing_date ?? l.also_auction?.[0]?.date;

export interface Filters {
  minPrice: number;
  maxPrice: number;
  minBeds: number;
  detached: boolean;
  garage: boolean;
  maxMins: number; // 0 = any
  topSchool: boolean;
  school: string; // "" = any
  region: string; // "" = any
  minSimd: number; // 0 = any
  newOnly: boolean;
  saleType: "" | "private" | "auction";
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
  detached: false,
  garage: false,
  maxMins: 0,
  topSchool: false,
  school: "",
  region: "",
  minSimd: 0,
  newOnly: false,
  saleType: "",
  motivated: false,
  needsWork: false,
  closingDate: false,
  reduced: false,
  colourBy: "price",
  sort: "time",
  layers: { simd: false, tram: true, rail: true, tramProposed: true, bus: false, catchTop: true, catchAll: false, areas: false },
  simdDomain: "decile",
  allBus: false,
  basemap: "light",
};

const NUM = ["minPrice", "maxPrice", "minBeds", "maxMins", "minSimd"] as const;
const BOOL = ["detached", "garage", "topSchool", "newOnly", "allBus", "motivated", "needsWork", "closingDate", "reduced"] as const;
const STR = ["school", "region", "saleType", "colourBy", "sort", "simdDomain", "basemap"] as const;

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
  if (p.has("layers")) {
    const on = new Set(p.get("layers")!.split(","));
    for (const k of Object.keys(f.layers)) f.layers[k] = on.has(k);
  }
  return f;
}

export function matches(l: Listing, f: Filters): boolean {
  if (f.minPrice && l.price < f.minPrice) return false;
  if (f.maxPrice && l.price > f.maxPrice) return false;
  if (f.minBeds && (l.bedrooms ?? 0) < f.minBeds) return false;
  if (f.detached && !l.detached) return false;
  if (f.garage && !l.garage) return false;
  if (f.maxMins && l.travel.best_min > f.maxMins) return false;
  if (f.topSchool && !l.top_school_rank) return false;
  if (f.school && l.catchment !== f.school && l.catchment_rc !== f.school) return false;
  if (f.region && l.region !== f.region) return false;
  if (f.minSimd && (l.simd?.decile ?? 0) < f.minSimd) return false;
  if (f.newOnly && !isNew(l)) return false;
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

export function sortListings(ls: Listing[], by: SortBy): Listing[] {
  const cmp: Record<SortBy, (a: Listing, b: Listing) => number> = {
    price_asc: (a, b) => a.price - b.price,
    price_desc: (a, b) => b.price - a.price,
    time: (a, b) => a.travel.best_min - b.travel.best_min,
    newest: (a, b) => (b.first_seen ?? "").localeCompare(a.first_seen ?? ""),
    soonest: (a, b) => (deadline(a) ?? "9999").localeCompare(deadline(b) ?? "9999"),
    value: (a, b) => (a.area?.vs_pct ?? 0) - (b.area?.vs_pct ?? 0),
  };
  return [...ls].sort(cmp[by]);
}
