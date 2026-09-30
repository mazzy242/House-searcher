import "./style.css";
import { DEFAULTS, type Filters, fromHash, isNew, isReduced, matches, sortListings, toHash } from "./filters";
import { PRICE_STOPS, SIMD_COLOURS, TIME_STOPS, VS_STOPS, daysAgo, esc, gbp, gbpFull, mins } from "./format";
import { PolygonIndex } from "./geo";
import { HouseMap } from "./map";
import type { Area, AuctionRef, Listing, Meta, Simd, TopSchool, TopSchools } from "./types";

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

const load = async <T>(path: string, fallback: T): Promise<T> => {
  try {
    const r = await fetch(`data/${path}`, { cache: "no-cache" });
    return r.ok ? ((await r.json()) as T) : fallback;
  } catch {
    return fallback;
  }
};

const SELECTS = ["school", "region", "saleType", "colourBy", "sort", "simdDomain"] as const;
const CHECKS = ["detached", "garage", "topSchool", "newOnly", "allBus", "motivated", "needsWork", "closingDate", "reduced"] as const;

const PRICES = [100, 150, 200, 250, 300, 350, 400, 450, 500, 600, 700, 800, 1000, 1250, 1500, 2000].map((k) => k * 1000);

let filters: Filters = fromHash(location.hash);
let listings: Listing[] = [];
let visible: Listing[] = [];
let areas: Area[] = [];
let meta: Meta;
let tops: TopSchools;
let selected: Listing | null = null;
let simdIndex: PolygonIndex<Simd>;
let catchIndex: PolygonIndex<{ school: string; sector: string; top_rank?: number }>;
const map = new HouseMap();

async function main(): Promise<void> {
  const emptyFc = { type: "FeatureCollection", features: [] } as GeoJSON.FeatureCollection;
  [listings, areas, meta, tops] = await Promise.all([
    load<Listing[]>("listings.json", []),
    load<Area[]>("areas.json", []),
    load<Meta>("meta.json", { waverley: { name: "Edinburgh Waverley", lat: 55.95196, lng: -3.18992 } }),
    load<TopSchools>("top_schools.json", { verified: false, source: "", schools: [] }),
  ]);
  buildControls();
  renderBanner();
  renderAbout();
  await map.init($("map"), meta, {
    onSelect: (id) => select(listings.find((l) => l.id === id) ?? null),
    onMany: showPicker,
    onBackgroundClick: showPointInfo,
    onBasemap: (basemap) => set({ basemap }),
  }, filters.basemap);
  // Lazy-load polygons for click-anywhere info (the map fetches its own copy).
  Promise.all([load("simd.geojson", emptyFc), load("catchments.geojson", emptyFc)]).then(([s, c]) => {
    simdIndex = new PolygonIndex<Simd>(s);
    catchIndex = new PolygonIndex(c);
  });
  map.setAreas(areas);
  update();
  const id = new URLSearchParams(location.search).get("id");
  if (id) select(listings.find((l) => l.id === id) ?? null, true);
}

// ------------------------------------------------------------------ controls

function buildControls(): void {
  const opt = (v: number, label: string) => `<option value="${v}">${label}</option>`;
  $("minPrice").innerHTML = opt(0, "No min") + PRICES.map((p) => opt(p, gbp(p))).join("");
  $("maxPrice").innerHTML = opt(0, "No max") + PRICES.map((p) => opt(p, gbp(p))).join("");
  // Every listing already has 2+ bedrooms (the scraper only keeps houses with 2 or more).
  $("minBeds").innerHTML = [0, 3, 4, 5]
    .map((b) => `<button role="radio" data-beds="${b}">${b ? `${b}+` : "2+"}</button>`).join("");
  const regions = [...new Set(listings.map((l) => l.region).filter(Boolean) as string[])];
  const order = ["Edinburgh", "Midlothian", "East Lothian", "West Lothian", "Fife"];
  regions.sort((a, b) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99 || a.localeCompare(b));
  $("region").innerHTML += regions
    .map((r) => `<option value="${esc(r)}">${esc(r)} (${listings.filter((l) => l.region === r).length})</option>`).join("");
  const schools = [...new Set(listings.flatMap((l) => [l.catchment, l.catchment_rc]).filter(Boolean) as string[])].sort();
  const rank = (s: string) => topFor(s)?.rank;
  $("school").innerHTML += schools.map((s) => `<option value="${esc(s)}">${esc(s)}${rank(s) ? ` (#${rank(s)})` : ""}</option>`).join("");

  for (const id of ["minPrice", "maxPrice", "maxMins", "minSimd"] as const) {
    $<HTMLSelectElement>(id).addEventListener("change", (e) => set({ [id]: Number((e.target as HTMLSelectElement).value) }));
  }
  for (const id of SELECTS) {
    $<HTMLSelectElement>(id).addEventListener("change", (e) => set({ [id]: (e.target as HTMLSelectElement).value } as Partial<Filters>));
  }
  for (const id of CHECKS) {
    $<HTMLInputElement>(id).addEventListener("change", (e) => set({ [id]: (e.target as HTMLInputElement).checked }));
  }
  $("show").addEventListener("click", (e) => {
    const v = (e.target as HTMLElement).dataset.show as Filters["show"] | undefined;
    if (v) set({ show: v });
  });
  $("minBeds").addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).dataset.beds;
    if (b != null) set({ minBeds: Number(b) });
  });
  document.querySelectorAll<HTMLInputElement>("[data-layer]").forEach((el) =>
    el.addEventListener("change", () => set({ layers: { ...filters.layers, [el.dataset.layer!]: el.checked } })),
  );
  $("allBus").addEventListener("change", () => {
    if (filters.allBus && !filters.layers.bus) set({ layers: { ...filters.layers, bus: true } });
  });
  $("reset").addEventListener("click", () => set({ ...DEFAULTS, layers: filters.layers, colourBy: filters.colourBy, simdDomain: filters.simdDomain, basemap: filters.basemap }));

  const panel = $("panel");
  $("toggle-panel").addEventListener("click", () => {
    const open = panel.classList.toggle("open");
    $("toggle-panel").setAttribute("aria-expanded", String(open));
  });
  $("view-map").addEventListener("click", () => setView("map"));
  $("view-list").addEventListener("click", () => setView("list"));
  const pickSchool = (e: Event) => {
    const li = (e.target as HTMLElement).closest<HTMLElement>("[data-school]");
    if (!li?.dataset.school) return;
    set({ school: filters.school === li.dataset.school ? "" : li.dataset.school });
  };
  $("tops").addEventListener("click", pickSchool);
  $("tops").addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), pickSchool(e)));
  $("cards").addEventListener("click", (e) => {
    const card = (e.target as HTMLElement).closest<HTMLElement>("[data-id]");
    if (card && !(e.target as HTMLElement).closest("a")) {
      setView("map");
      select(listings.find((l) => l.id === card.dataset.id) ?? null, true);
    }
  });
  $("detail").addEventListener("click", (e) => {
    if ((e.target as HTMLElement).closest("[data-close]")) select(null);
  });
  document.addEventListener("keydown", (e) => e.key === "Escape" && select(null));
  window.addEventListener("hashchange", () => {
    const next = toHash(fromHash(location.hash));
    if (next !== toHash(filters)) {
      filters = fromHash(location.hash);
      update();
    }
  });
}

function set(patch: Partial<Filters>): void {
  filters = { ...filters, ...patch };
  history.replaceState(null, "", `${location.pathname}${location.search}${toHash(filters) ? `#${toHash(filters)}` : ""}`);
  update();
}

function syncControls(): void {
  const f = filters;
  for (const id of [...(["minPrice", "maxPrice", "maxMins", "minSimd"] as const), ...SELECTS]) {
    $<HTMLSelectElement>(id).value = String(f[id]);
  }
  for (const id of CHECKS) $<HTMLInputElement>(id).checked = f[id];
  document.querySelectorAll<HTMLButtonElement>("#show button").forEach((b) =>
    b.setAttribute("aria-checked", String(b.dataset.show === f.show)),
  );
  document.querySelectorAll<HTMLButtonElement>("#minBeds button").forEach((b) =>
    b.setAttribute("aria-checked", String(Number(b.dataset.beds) === f.minBeds)),
  );
  document.querySelectorAll<HTMLInputElement>("[data-layer]").forEach((el) => (el.checked = !!f.layers[el.dataset.layer!]));
  $("simd-domain-wrap").hidden = !f.layers.simd;
}

function update(): void {
  syncControls();
  visible = listings.filter((l) => matches(l, filters));
  map.applyFilters(filters);
  void map.setBasemap(filters.basemap);
  map.setListings(visible);
  const total = listings.length;
  const med = median(visible.map((l) => l.price));
  $("stats").innerHTML = `<b>${visible.length}</b> of ${total} homes${med ? ` · median ${gbp(med)}` : ""}`;
  renderLegend();
  renderAreas();
  renderTops();
  if (!$("list").hidden) renderList();
  if (selected && !visible.includes(selected)) select(null);
}

function setView(v: "map" | "list"): void {
  $("list").hidden = v !== "list";
  $("view-map").setAttribute("aria-selected", String(v === "map"));
  $("view-list").setAttribute("aria-selected", String(v === "list"));
  if (v === "list") {
    renderList();
    $("detail").hidden = true;
  } else {
    map.map.resize();
    if (selected) $("detail").hidden = false;
  }
}

// ------------------------------------------------------------------ rendering

const median = (xs: number[]): number => {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
};

function renderBanner(): void {
  const b = $("banner");
  const errs = Object.keys(meta.errors ?? {});
  if (meta.sample) {
    b.innerHTML = `<p><b>Sample data.</b> These are made-up listings to preview the site. Real ESPC listings, SIMD, bus routes and timetable-based journey times appear after the first daily refresh runs.</p><button aria-label="Dismiss">×</button>`;
    b.hidden = false;
  } else if (errs.length) {
    b.innerHTML = `<p>Last refresh couldn't update: <b>${errs.map(esc).join(", ")}</b>. Showing the most recent data available.</p><button aria-label="Dismiss">×</button>`;
    b.className = "banner warn";
    b.hidden = false;
  }
  b.querySelector("button")?.addEventListener("click", () => {
    b.hidden = true;
    map.map?.resize();
  });
}

function renderAbout(): void {
  const updated = meta.generated_at ? new Date(meta.generated_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "never";
  const arr = meta.travel_assumptions?.arrive_by ?? [];
  $("about").innerHTML = `
    <h2>About the data</h2>
    <p>Updated ${esc(updated)}. Prices are <em>asking</em> prices from ESPC; averages are medians of current listings by postcode district.</p>
    <p>Time to Waverley: fastest of walking, bus/tram (weekday timetable, arriving ${esc(arr[0] ?? "")}–${esc(arr[arr.length - 1] ?? "")}) and train from a nearby station. Door to door, including walking.</p>
    <p>Top-10 schools: <a href="${esc(tops.source)}" target="_blank" rel="noopener">ESPC list</a> (Sunday Times league tables)${tops.verified ? "" : " <span class=\"muted\">(order not yet verified)</span>"}. School price stats are ESPC's figures for each catchment.
      Catchments: ${esc(meta.sources?.catchments?.detail ?? "City of Edinburgh Council")}. Always confirm with the council before buying.</p>
    <p>Proposed tram routes are indicative only.</p>`;
}

/** The ESPC top-10 entry for a council catchment school name, if it is one. */
function topFor(school?: string): TopSchool | undefined {
  if (!school) return undefined;
  const name = school.toLowerCase().replace(/\u2019/g, "'");
  const rc = /\brc\b|catholic|st thomas|st augustine|holy rood/.test(name);
  return tops.schools.find((t) => name.includes(t.match) && (t.sector ?? "ND") === (rc ? "RC" : "ND"));
}

function schoolStats(t: TopSchool): string {
  const bits = [
    t.avg_price ? `avg sold ${gbp(t.avg_price)}` : "",
    t.days_to_offer ? `${t.days_to_offer} days to under offer` : "",
    t.home_report_pct ? `${t.home_report_pct}% of Home Report paid` : "",
  ].filter(Boolean);
  return `<div class="school-stats"><b>#${t.rank} ${esc(t.name)}</b>${t.sector === "RC" ? " (Roman Catholic)" : ""}<br>${bits.join(" · ")}${t.neighbourhoods ? `<div class="muted">${esc(t.neighbourhoods)}</div>` : ""}</div>`;
}

function renderTops(): void {
  const counts = new Map<string, number>();
  for (const l of visible) for (const s of [l.catchment, l.catchment_rc]) if (s) counts.set(s, (counts.get(s) ?? 0) + 1);
  const councilName = (t: TopSchool) =>
    [...new Set(listings.flatMap((l) => [l.catchment, l.catchment_rc]))].find((s) => s && topFor(s)?.rank === t.rank);
  $("tops-note").textContent = tops.published ? `(ESPC, ${new Date(tops.published).toLocaleDateString("en-GB", { month: "short", year: "numeric" })})` : "";
  $("tops").innerHTML = tops.schools
    .map((t) => {
      const cn = councilName(t);
      const n = cn ? counts.get(cn) ?? 0 : 0;
      return `<li tabindex="0" role="button" data-school="${esc(cn ?? "")}" aria-pressed="${!!cn && filters.school === cn}" title="${esc(t.neighbourhoods ?? "")}">
        <span class="r">${t.rank}</span><span>${esc(t.name.replace(/ School$/, "").replace(" Community High", ""))}${t.sector === "RC" ? `<span class="rc">RC</span>` : ""}</span>
        <span class="n">${n} home${n === 1 ? "" : "s"}</span></li>`;
    })
    .join("");
}

function renderLegend(): void {
  const f = filters;
  const stops = f.colourBy === "time" ? TIME_STOPS : f.colourBy === "vs" ? VS_STOPS : PRICE_STOPS;
  const fmt = (v: number) => (f.colourBy === "time" ? `${v}` : f.colourBy === "vs" ? `${v > 0 ? "+" : ""}${v}%` : gbp(v));
  const unit = f.colourBy === "time" ? " min" : "";
  const items = stops.map(([v, c], i) => {
    const lo = i ? fmt(stops[i - 1][0]) : null;
    const label = i === stops.length - 1 ? `${lo}${unit}+` : lo ? `${lo}–${fmt(v)}${unit}` : `< ${fmt(v)}${unit}`;
    return `<span><i style="background:${c}"></i>${label}</span>`;
  });
  let simd = "";
  if (f.layers.simd) {
    simd = `<div class="simd-scale"><span>Most deprived</span>${SIMD_COLOURS.map((c, i) => `<i title="Decile ${i + 1}" style="background:${c}"></i>`).join("")}<span>Least</span></div>`;
  }
  $("legend").innerHTML = `<div class="legend-items">${items.join("")}</div>${simd}<div class="muted small">Purple ring = in a top-10 school catchment. Thick orange ring = auction lot. Green ring = plot / land. Orange dashed outline = St Thomas of Aquin's (RC) catchment.</div>`;
}

function renderAreas(): void {
  const beds = filters.minBeds ? String(Math.min(filters.minBeds, 5)) : "";
  $("areas-note").textContent = beds ? `(${beds}${beds === "5" ? "+" : ""}-bed)` : "";
  const rows = areas
    .map((a) => {
      const b = beds ? a.by_beds[beds] : { count: a.count, median: a.median };
      return b ? { d: a.district, ...b } : null;
    })
    .filter((r): r is { d: string; count: number; median: number } => !!r)
    .sort((x, y) => y.median - x.median);
  const max = Math.max(...rows.map((r) => r.median), 1);
  $("areas").innerHTML = rows.length
    ? `<tbody>${rows.map((r) => `<tr><th>${esc(r.d)}</th><td class="bar"><i style="width:${(100 * r.median) / max}%"></i></td><td>${gbp(r.median)}</td><td class="muted">${r.count}</td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="muted">No listings yet</td></tr></tbody>`;
}

/** Links between an ESPC listing and auction lots for the same home (ESPC is the main record). */
function crossRefs(l: Listing): string {
  const lot = (a: AuctionRef, what: string) => `<div class="xref">🔨 <b>${what}</b> ${esc(a.house)}${a.date ? `, ${shortDate(a.date)}` : ""} ·
    ${esc(a.basis ?? "Guide price")} ${gbpFull(a.price)}${a.address ? `<div class="muted small">Lot: ${esc(a.address)}</div>` : ""}
    <a href="${esc(a.url)}" target="_blank" rel="noopener">View lot ↗</a></div>`;
  return [
    ...(l.also_auction ?? []).map((a) => lot(a, "Also for sale at auction:")),
    ...(l.in_auction_lot ?? []).map((a) => lot(a, "Also included in a larger auction lot:")),
    ...(l.overlaps_espc ?? []).map((e) => `<div class="xref">🏠 <b>Part of this lot is also on ESPC:</b> ${esc(e.address)} · ${gbpFull(e.price)}
      <button class="link-btn" data-pick="${esc(e.id)}">Show it</button> <a href="${esc(e.url)}" target="_blank" rel="noopener">ESPC ↗</a></div>`),
    l.relisted ? `<div class="xref">↻ <b>Re-listed:</b> this home was on ESPC before under another listing; the price history below includes it.</div>` : "",
  ].join("");
}

const shortDate = (iso: string): string =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

function badges(l: Listing): string {
  const b: string[] = [];
  if (l.kind === "plot") b.push(`<span class="badge plot">🌱 Plot / land${l.plot_acres ? ` · ${l.plot_acres} acres` : ""}</span>`);
  if (l.auction) b.push(`<span class="badge auction">🔨 Auction${l.auction.date ? ` ${shortDate(l.auction.date)}` : ""}</span>`);
  for (const a of l.also_auction ?? []) b.push(`<span class="badge auction">🔨 Also at auction${a.date ? ` ${shortDate(a.date)}` : ""}</span>`);
  if (l.in_auction_lot?.length) b.push(`<span class="badge auction">🔨 Part of an auction lot</span>`);
  if (l.overlaps_espc?.length) b.push(`<span class="badge">Also on ESPC</span>`);
  if (l.relisted) b.push(`<span class="badge reduced">Re-listed</span>`);
  if (l.closing_date) b.push(`<span class="badge closing">⏱ Closing date ${shortDate(l.closing_date)}</span>`);
  if (l.flags?.includes("motivated")) b.push(`<span class="badge motivated">Motivated seller</span>`);
  if (l.flags?.includes("needs_work")) b.push(`<span class="badge work">Needs work</span>`);
  if (l.bathrooms) b.push(`<span class="badge size">🛁 ${l.bathrooms} bath${l.bathrooms === 1 ? "" : "s"}</span>`);
  if (l.floor_area_m2) b.push(`<span class="badge size">📐 ${l.floor_area_m2} m²</span>`);
  if (l.epc?.band) b.push(`<span class="badge epc epc-${l.epc.band.toLowerCase()}">EPC ${l.epc.band}</span>`);
  if (l.detached) b.push(`<span class="badge">Detached</span>`);
  if (l.garage) b.push(`<span class="badge">Garage</span>`);
  if (l.top_school_rank) b.push(`<span class="badge school">Top-10 school #${l.top_school_rank}</span>`);
  if (isNew(l)) b.push(`<span class="badge new">New</span>`);
  if (isReduced(l)) b.push(`<span class="badge reduced">Reduced</span>`);
  return b.join("");
}

function simdBar(decile?: number): string {
  if (!decile) return `<span class="muted">Not available yet</span>`;
  return `<span class="deciles" aria-label="SIMD decile ${decile} of 10">${SIMD_COLOURS.map((c, i) => `<i style="background:${i < decile ? c : "var(--line)"}"></i>`).join("")}</span> <b>${decile}</b>/10`;
}

function renderDetail(l: Listing): void {
  const t = l.travel;
  const hist = l.price_history ?? [];
  const age = daysAgo(l.first_seen);
  const vs = l.area;
  const domains = l.simd ? (["income", "employment", "health", "education", "access", "crime", "housing"] as const)
    .filter((k) => l.simd![k] != null).map((k) => `<span title="${k} decile">${k[0].toUpperCase() + k.slice(1)} <b>${l.simd![k]}</b></span>`).join("") : "";
  $("detail").innerHTML = `
    <button class="close" data-close aria-label="Close">×</button>
    ${l.image ? `<img class="photo" src="${esc(l.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}
    <div class="body">
      <div class="price">${l.auction ? `<span class="q">${esc(l.auction.basis)}</span> ` : l.price_qualifier ? `<span class="q">${esc(l.price_qualifier)}</span> ` : ""}${gbpFull(l.price)}</div>
      <h3>${esc(l.title || `${l.bedrooms ?? "?"} bedroom ${l.property_type ?? "home"}`)}</h3>
      <div class="addr">${esc(l.address)}${l.region && l.region !== "Edinburgh" ? ` · ${esc(l.region)}` : ""}${l.approx_location ? ` <span class="muted">(location approximate)</span>` : ""}</div>
      <div class="badges">${badges(l)}</div>
      ${crossRefs(l)}
      ${l.auction ? `<div class="auction-note">
        <b>${esc(l.auction.house)}${l.auction.date ? ` · auction ${shortDate(l.auction.date)}` : ""}</b>
        The ${esc(l.auction.basis.toLowerCase())} is where bidding starts, not the likely price. The winning bidder usually pays a
        10% deposit on the day and completes within about 28 days, so a normal mortgage rarely works: cash or bridging finance.
        Read the legal pack (and any Home Report) before bidding.</div>` : ""}

      <div class="waverley">
        <div class="big"><span>🚆 Waverley</span><b>${mins(t.best_min)}</b></div>
        <div class="how">${esc(t.best_how)}</div>
        <dl class="modes">
          ${t.pt_min != null ? `<div><dt>Bus/tram</dt><dd>${mins(t.pt_min)}</dd></div>` : ""}
          ${t.rail_min != null ? `<div><dt>Train</dt><dd>${mins(t.rail_min)}</dd></div>` : ""}
          <div><dt>Cycle</dt><dd>${mins(t.cycle_min)}</dd></div>
          <div><dt>Walk</dt><dd>${mins(t.walk_min)}</dd></div>
        </dl>
        ${t.pt_how && t.pt_how !== t.best_how ? `<div class="how muted">Bus/tram: ${esc(t.pt_how)}</div>` : ""}
      </div>

      <dl class="facts">
        ${l.kind === "plot" ? `<div><dt>Plot size</dt><dd>${l.plot_acres ? `${l.plot_acres} acres <span class="muted">(${Math.round(l.plot_acres * 4047).toLocaleString("en-GB")} m²) · ${gbpFull(Math.round(l.price / l.plot_acres))}/acre</span>` : `<span class="muted">Not stated – see the listing</span>`}</dd></div>` : ""}
        <div${l.kind === "plot" ? " hidden" : ""}><dt>Rooms</dt><dd>${l.bedrooms ?? "–"} bed · ${l.bathrooms ?? "–"} bath</dd></div>
        <div${l.kind === "plot" ? " hidden" : ""}><dt>Floor area</dt><dd>${l.floor_area_m2 ? `${l.floor_area_m2} m² <span class="muted">· ${gbpFull(Math.round(l.price / l.floor_area_m2))}/m²${l.floor_area_source === "EPC" ? " · from the EPC" : ""}</span>` : `<span class="muted">Not listed</span>`}</dd></div>
        ${l.epc?.band ? `<div><dt>EPC</dt><dd><span class="badge epc epc-${l.epc.band.toLowerCase()}">${l.epc.band}</span>${l.epc.date ? ` <span class="muted small">certificate ${esc(l.epc.date.slice(0, 7))}</span>` : ""}</dd></div>` : ""}
        ${l.closing_date ? `<div><dt>Closing date</dt><dd>${esc(shortDate(l.closing_date))} <span class="muted small">– offers by then, usually at noon</span></dd></div>` : ""}
        <div><dt>Type</dt><dd>${l.kind === "plot" ? "Plot / land" : esc(l.property_type ?? "–")}</dd></div>
        <div><dt>SIMD</dt><dd>${simdBar(l.simd?.decile)}${l.simd?.name ? `<div class="muted small">${esc(l.simd.name)}</div>` : ""}${domains ? `<div class="domains">${domains}</div>` : ""}</dd></div>
        <div><dt>Catchment</dt><dd>${l.catchment || l.catchment_rc ? "" : `<span class="muted">Outside Edinburgh – check with ${esc(l.region ?? "the local")} council</span>`}${esc(l.catchment ?? "")}${topFor(l.catchment) ? ` <b class="rank">#${topFor(l.catchment)!.rank}</b>` : ""}${l.catchment_rc ? `<div class="small">RC: ${esc(l.catchment_rc)}${topFor(l.catchment_rc) ? ` <b class="rank">#${topFor(l.catchment_rc)!.rank}</b>` : ""}</div>` : ""}${[topFor(l.catchment), topFor(l.catchment_rc)].filter((t): t is TopSchool => !!t).map(schoolStats).join("")}</dd></div>
        ${vs ? `<div><dt>vs area</dt><dd><b class="${vs.vs_pct > 5 ? "up" : vs.vs_pct < -5 ? "down" : ""}">${vs.vs_pct > 0 ? "+" : ""}${vs.vs_pct}%</b> vs ${esc(l.district)} ${vs.basis === "all" ? "" : `${esc(vs.basis)} `}median ${gbp(vs.median)}</dd></div>` : ""}
        ${hist.length > 1 ? `<div><dt>History</dt><dd>${hist.map(([d, p]) => `${gbp(p)} <span class="muted small">${esc(d)}</span>`).join(" → ")}</dd></div>` : ""}
        ${age != null ? `<div><dt>Listed</dt><dd>${age === 0 ? "today" : `${age} day${age === 1 ? "" : "s"} ago`}</dd></div>` : ""}
      </dl>
      <a class="cta" href="${esc(l.url)}" target="_blank" rel="noopener">View on ${esc(l.auction?.house ?? "ESPC")} ↗</a>
    </div>`;
}

function select(l: Listing | null, fly = false): void {
  selected = l;
  map.select(l, meta);
  const d = $("detail");
  if (!l) {
    d.hidden = true;
    const u = new URL(location.href);
    u.searchParams.delete("id");
    history.replaceState(null, "", u);
    return;
  }
  renderDetail(l);
  d.hidden = false;
  d.scrollTop = 0;
  const u = new URL(location.href);
  u.searchParams.set("id", l.id);
  history.replaceState(null, "", u);
  if (fly) map.flyTo(l);
}

function renderList(): void {
  const ls = sortListings(visible, filters.sort);
  $("list-count").textContent = `${ls.length} homes`;
  $("cards").innerHTML = ls.slice(0, 400).map((l) => `
    <div class="card" data-id="${esc(l.id)}" tabindex="0">
      ${l.image ? `<img src="${esc(l.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : `<div class="noimg">🏠</div>`}
      <div class="c-body">
        <div class="c-price">${gbpFull(l.price)} ${l.area ? `<span class="${l.area.vs_pct > 5 ? "up" : l.area.vs_pct < -5 ? "down" : "muted"} small">${l.area.vs_pct > 0 ? "+" : ""}${l.area.vs_pct}% vs area</span>` : ""}</div>
        <div class="c-title">${esc(l.title || "")}</div>
        <div class="muted small">${esc(l.address)}</div>
        <div class="c-meta"><span>🚆 ${mins(l.travel.best_min)}</span><span>SIMD ${l.simd?.decile ?? "–"}</span><span>${esc(l.catchment ?? "")}</span></div>
        <div class="badges">${badges(l)}</div>
      </div>
    </div>`).join("") || `<p class="muted">No homes match these filters.</p>`;
  $("cards").querySelectorAll<HTMLElement>(".card").forEach((c) =>
    c.addEventListener("keydown", (e) => e.key === "Enter" && c.click()),
  );
}

/** Several homes on one spot: list them in a popup to choose from. */
function showPicker(ids: string[], lngLat: [number, number]): void {
  const ls = ids.map((id) => listings.find((l) => l.id === id)).filter((l): l is Listing => !!l)
    .sort((a, b) => a.price - b.price);
  map.showPopup(lngLat, `<div class="picker"><b>${ls.length} homes here</b>${ls.map((l) => `
    <button data-pick="${esc(l.id)}"><span>${esc((l.address ?? "").split(",")[0])}</span>
      <span class="muted">${l.bedrooms ?? "?"} bed · ${l.auction ? "🔨 " : ""}${gbp(l.price)}</span></button>`).join("")}</div>`);
}

document.addEventListener("click", (e) => {
  const b = (e.target as HTMLElement).closest<HTMLElement>("[data-pick]");
  if (b) select(listings.find((l) => l.id === b.dataset.pick) ?? null);
});

function showPointInfo([lng, lat]: [number, number]): void {
  if (selected) return select(null);
  const z = simdIndex?.at(lng, lat)[0];
  const c = catchIndex?.at(lng, lat) ?? [];
  const parts: string[] = [];
  if (z?.decile) parts.push(`<b>SIMD decile ${z.decile}</b> of 10${z.name ? `<br><span class="muted">${esc(z.name)}</span>` : ""}`);
  for (const s of c) {
    parts.push(`${s.sector === "RC" ? "RC catchment" : "Catchment"}: <b>${esc(s.school)}</b>${s.top_rank ? ` (#${s.top_rank})` : ""}`);
    const t = topFor(s.school);
    if (t) parts.push(schoolStats(t));
  }
  if (parts.length) map.showPopup([lng, lat], parts.join("<br>"));
}

main();
