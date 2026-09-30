export interface Travel {
  best_min: number;
  best_how: string;
  pt_min?: number;
  pt_how?: string;
  rail_min?: number;
  rail_how?: string;
  walk_min: number;
  cycle_min: number;
  km: number;
}

export interface Simd {
  dz?: string;
  name?: string;
  rank?: number;
  decile?: number;
  income?: number;
  employment?: number;
  health?: number;
  education?: number;
  access?: number;
  crime?: number;
  housing?: number;
}

export interface Listing {
  id: string;
  url: string;
  title?: string;
  address?: string;
  postcode?: string;
  district?: string;
  region?: string;
  lat: number;
  lng: number;
  price: number;
  price_qualifier?: string;
  bedrooms?: number;
  bathrooms?: number;
  floor_area_m2?: number;
  property_type?: string;
  detached: boolean;
  garage: boolean;
  image?: string;
  first_seen?: string;
  price_history?: [string, number][];
  approx_location?: boolean;
  simd?: Simd;
  catchment?: string;
  catchment_rc?: string;
  catchment_rank?: number;
  catchment_rc_rank?: number;
  top_school_rank?: number;
  travel: Travel;
  area?: { median: number; basis: string; vs_pct: number };
  /** "motivated" (lender/executor/trustee sale, cash buyers...) and/or "needs_work". */
  flags?: ("motivated" | "needs_work")[];
  closing_date?: string;
  source?: "auction";
  auction?: { house: string; date?: string; basis: string };
  epc?: { band?: string; floor_area_m2?: number; date?: string };
  floor_area_source?: "EPC";
  /** The same home was listed on ESPC before (merged; price history carried over). */
  relisted?: boolean;
  /** The same home is also being auctioned. */
  also_auction?: AuctionRef[];
  /** An auction lot that includes this home together with others (e.g. "70 and 70a"). */
  in_auction_lot?: AuctionRef[];
  /** On an auction lot: ESPC listings for homes that are part of this lot. */
  overlaps_espc?: { id: string; address: string; price: number; url: string }[];
}

export interface AuctionRef {
  house: string;
  date?: string;
  basis?: string;
  price: number;
  url: string;
  address: string;
}

export interface Area {
  district: string;
  count: number;
  median: number;
  mean: number;
  by_beds: Record<string, { count: number; median: number }>;
  lat: number;
  lng: number;
}

export interface Meta {
  sample?: boolean;
  generated_at?: string;
  listing_count?: number;
  waverley: { name: string; lat: number; lng: number };
  errors?: Record<string, string>;
  sources?: Record<string, { updated?: string; detail?: string; service_date?: string }>;
  travel_assumptions?: { arrive_by: string[] };
}

export interface TopSchool {
  rank: number;
  name: string;
  match: string;
  sector?: "ND" | "RC";
  neighbourhoods?: string;
  avg_price?: number;
  days_to_offer?: number;
  home_report_pct?: number;
}

export interface TopSchools {
  verified: boolean;
  published?: string;
  source: string;
  schools: TopSchool[];
}
