export const gbp = (n: number): string =>
  n >= 1_000_000 ? `£${(n / 1_000_000).toFixed(n % 1_000_000 ? 2 : 1).replace(/\.?0+$/, "")}m` : `£${Math.round(n / 1000)}k`;

export const gbpFull = (n: number): string => `£${n.toLocaleString("en-GB")}`;

export const mins = (m: number | undefined): string =>
  m == null ? "–" : m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;

export const esc = (s: unknown): string =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export const daysAgo = (iso?: string): number | null => {
  if (!iso) return null;
  const d = (Date.now() - new Date(iso).getTime()) / 86_400_000;
  return Number.isFinite(d) ? Math.floor(d) : null;
};

/** SIMD decile 1 (most deprived) .. 10 (least deprived): red -> amber -> green. */
export const SIMD_COLOURS = [
  "#b2182b", "#d6604d", "#f4a582", "#fdd49e", "#fee8a8",
  "#e6f0b0", "#c2e3a0", "#92cf8e", "#5aae61", "#1b7837",
];

/** Time to Waverley buckets (minutes) and colours. */
export const TIME_STOPS: [number, string][] = [
  [15, "#1a9850"], [25, "#91cf60"], [35, "#fee08b"], [45, "#fc8d59"], [999, "#d73027"],
];

/** Price buckets and colours (sequential blues). */
export const PRICE_STOPS: [number, string][] = [
  [200_000, "#c6dbef"], [300_000, "#9ecae1"], [400_000, "#6baed6"], [550_000, "#3182bd"], [750_000, "#08519c"], [1e12, "#08306b"],
];

/** vs area median (%), diverging. */
export const VS_STOPS: [number, string][] = [
  [-15, "#1a9850"], [-5, "#91cf60"], [5, "#bdbdbd"], [15, "#fc8d59"], [1e9, "#d73027"],
];
