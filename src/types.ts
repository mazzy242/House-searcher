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
  lat: number;
  lng: number;
  price: number;
  price_qualifier?: string;
  bedrooms?: number;
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
