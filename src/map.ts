import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map as MLMap, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre 6 runs its tile worker from a separate ES module; let Vite bundle it and hand over the URL.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { PRICE_STOPS, SIMD_COLOURS, TIME_STOPS, VS_STOPS, gbp } from "./format";
import type { ColourBy, Filters } from "./filters";
import type { Area, Listing, Meta } from "./types";

const GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
const FONT_BOLD = ["Noto Sans Bold"];
const EMPTY = { type: "FeatureCollection", features: [] } as GeoJSON.FeatureCollection;
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";

export type Basemap = "light" | "roads" | "satellite";
export const BASEMAPS: { id: Basemap; label: string }[] = [
  { id: "light", label: "Light" },
  { id: "roads", label: "Roads" },
  { id: "satellite", label: "Satellite" },
];

const VECTOR_STYLES: Record<Exclude<Basemap, "satellite">, string> = {
  light: "https://tiles.openfreemap.org/styles/positron",
  roads: "https://tiles.openfreemap.org/styles/liberty",
};

const esriRaster = (path: string, attribution = ""): maplibregl.RasterSourceSpecification => ({
  type: "raster", tileSize: 256, maxzoom: 19, attribution,
  tiles: [`${ESRI}/${path}/MapServer/tile/{z}/{y}/{x}`],
});

/** Aerial imagery with road and place-name overlays on top (Esri World Imagery). */
const SATELLITE: StyleSpecification = {
  version: 8,
  glyphs: GLYPHS,
  sources: {
    imagery: esriRaster("World_Imagery", "Imagery © Esri, Maxar, Earthstar Geographics"),
    roads: esriRaster("Reference/World_Transportation"),
    places: esriRaster("Reference/World_Boundaries_and_Places"),
  },
  layers: [
    { id: "imagery", type: "raster", source: "imagery" },
    { id: "sat-roads", type: "raster", source: "roads", paint: { "raster-opacity": 0.85 } },
    { id: "sat-places", type: "raster", source: "places" },
  ],
};

const PLAIN: StyleSpecification = {
  version: 8,
  glyphs: GLYPHS,
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": "#eef0ec" } }],
};

async function styleFor(b: Basemap): Promise<StyleSpecification | string> {
  if (b === "satellite") return SATELLITE;
  try {
    const r = await fetch(VECTOR_STYLES[b], { signal: AbortSignal.timeout(6000) });
    if (r.ok) return VECTOR_STYLES[b];
  } catch {
    /* fall through to a plain background so the overlays still work offline */
  }
  return PLAIN;
}

// Our overlays that should sit under the basemap's labels rather than on top of them.
const UNDER_LABELS = new Set(["simd-fill", "catch-top-fill"]);

/** Base-map switcher shown on the map itself, so it's one tap away on a phone too. */
class BasemapControl implements maplibregl.IControl {
  private el = document.createElement("div");
  constructor(private current: Basemap, private onChange: (b: Basemap) => void) {}
  onAdd(): HTMLElement {
    this.el.className = "maplibregl-ctrl maplibregl-ctrl-group basemap-ctrl";
    this.el.setAttribute("role", "radiogroup");
    this.el.setAttribute("aria-label", "Base map");
    this.el.innerHTML = BASEMAPS.map((b) => `<button type="button" role="radio" data-basemap="${b.id}">${b.label}</button>`).join("");
    this.el.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).dataset.basemap as Basemap | undefined;
      if (b && b !== this.current) this.onChange(b);
    });
    this.set(this.current);
    return this.el;
  }
  onRemove(): void {
    this.el.remove();
  }
  set(b: Basemap): void {
    this.current = b;
    this.el.querySelectorAll<HTMLButtonElement>("button").forEach((btn) =>
      btn.setAttribute("aria-checked", String(btn.dataset.basemap === b)),
    );
  }
}

maplibregl.setWorkerUrl(workerUrl);

const step = (prop: unknown, stops: [number, string][]): unknown[] => {
  const e: unknown[] = ["step", prop, stops[0][1]];
  for (let i = 0; i < stops.length - 1; i++) e.push(stops[i][0], stops[i + 1][1]);
  return e;
};

export const colourExpr = (by: ColourBy): unknown[] =>
  by === "time" ? step(["get", "mins"], TIME_STOPS) : by === "vs" ? step(["get", "vs"], VS_STOPS) : step(["get", "price"], PRICE_STOPS);

export interface MapHandlers {
  onSelect: (id: string) => void;
  onMany: (ids: string[], lngLat: [number, number]) => void;
  onBackgroundClick: (lngLat: [number, number]) => void;
  onBasemap: (b: Basemap) => void;
}

export class HouseMap {
  map!: MLMap;
  private popup = new maplibregl.Popup({ closeButton: true, maxWidth: "280px" });
  private ownSources = new Set<string>();
  private ownLayers = new Set<string>();
  private basemap: Basemap = "light";
  private basemapCtrl!: BasemapControl;

  async init(container: HTMLElement, meta: Meta, h: MapHandlers, basemap: Basemap = "light"): Promise<void> {
    this.basemap = basemap;
    const style = await styleFor(basemap);
    this.map = new maplibregl.Map({
      container,
      style,
      center: [-3.2, 55.94],
      zoom: 10.4,
      minZoom: 9,
      maxBounds: [[-4.2, 55.6], [-2.4, 56.25]],
      attributionControl: { compact: true, customAttribution: "Listings © ESPC · SIMD © Scottish Government · Catchments © City of Edinburgh Council · Transit © OpenStreetMap contributors" },
    });
    this.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    this.map.addControl(new maplibregl.GeolocateControl({}), "top-left");
    this.map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    this.basemapCtrl = new BasemapControl(basemap, h.onBasemap);
    this.map.addControl(this.basemapCtrl, "top-left");
    await new Promise<void>((res) => this.map.once("load", () => res()));
    const before = this.map.getStyle();
    const [srcBefore, layersBefore] = [new Set(Object.keys(before.sources)), new Set(before.layers.map((l) => l.id))];
    this.addLayers(meta);
    const after = this.map.getStyle();
    this.ownSources = new Set(Object.keys(after.sources).filter((s) => !srcBefore.has(s)));
    this.ownLayers = new Set(after.layers.map((l) => l.id).filter((id) => !layersBefore.has(id)));
    this.bind(h);
  }

  /** Swap the base map, carrying our sources and layers (with their current data and visibility) across. */
  async setBasemap(b: Basemap): Promise<void> {
    if (b === this.basemap) return;
    this.basemap = b;
    this.basemapCtrl.set(b);
    const style = await styleFor(b);
    if (this.basemap !== b) return; // a quicker second click won
    this.map.setStyle(style, {
      transformStyle: (prev, next) => {
        if (!prev) return next;
        const ours = prev.layers.filter((l) => this.ownLayers.has(l.id));
        const under = ours.filter((l) => UNDER_LABELS.has(l.id));
        const over = ours.filter((l) => !UNDER_LABELS.has(l.id));
        const firstLabel = next.layers.findIndex((l) => l.type === "symbol");
        const base = [...next.layers];
        base.splice(firstLabel < 0 ? base.length : firstLabel, 0, ...under);
        const sources = { ...next.sources };
        for (const id of this.ownSources) sources[id] = prev.sources[id];
        return { ...next, glyphs: next.glyphs ?? GLYPHS, sources, layers: [...base, ...over] };
      },
    });
  }

  private addLayers(meta: Meta): void {
    const m = this.map;
    const firstLabel = m.getStyle().layers.find((l) => l.type === "symbol")?.id;
    const src = (id: string, data: string | GeoJSON.FeatureCollection, extra: object = {}) =>
      m.addSource(id, { type: "geojson", data, ...extra } as maplibregl.GeoJSONSourceSpecification);

    src("simd", "data/simd.geojson");
    src("catchments", "data/catchments.geojson");
    src("bus", "data/bus.geojson");
    src("tram", "data/tram.geojson");
    src("tramProposed", "data/tram_proposed.geojson");
    src("rail", "data/rail.geojson");
    src("railStations", "data/rail_stations.geojson");
    src("areas", EMPTY);
    src("listings", EMPTY, { cluster: true, clusterRadius: 38, clusterMaxZoom: 12 });
    src("selected", EMPTY);

    // Area layers sit under the basemap labels; lines and pins above.
    m.addLayer({ id: "simd-fill", type: "fill", source: "simd", paint: { "fill-color": "#ccc", "fill-opacity": 0.5, "fill-outline-color": "rgba(0,0,0,0.08)" } }, firstLabel);
    // Non-denominational top-10 catchments are shaded (darker = higher ranked). The Roman Catholic
    // catchment (St Thomas of Aquin's) covers much of the city and overlaps them, so it is outline-only.
    const isTop = [">", ["to-number", ["get", "top_rank"], 0], 0];
    const topND = ["all", isTop, ["!=", ["get", "sector"], "RC"]];
    const topRC = ["all", isTop, ["==", ["get", "sector"], "RC"]];
    m.addLayer({ id: "catch-all-line", type: "line", source: "catchments", paint: { "line-color": "#6b6b6b", "line-width": 1, "line-dasharray": [3, 2] } });
    m.addLayer({ id: "catch-top-fill", type: "fill", source: "catchments", filter: topND as never,
      paint: { "fill-color": "#7b3294", "fill-opacity": ["interpolate", ["linear"], ["get", "top_rank"], 1, 0.24, 10, 0.08] as never } }, firstLabel);
    m.addLayer({ id: "catch-top-line", type: "line", source: "catchments", filter: topND as never, paint: { "line-color": "#7b3294", "line-width": 2 } });
    m.addLayer({ id: "catch-rc-line", type: "line", source: "catchments", filter: topRC as never,
      paint: { "line-color": "#c2410c", "line-width": 2.5, "line-dasharray": [4, 2] } });

    m.addLayer({ id: "bus-line", type: "line", source: "bus", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["coalesce", ["get", "colour"], "#8c6bb1"] as never, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.2, 15, 3] as never, "line-opacity": 0.75 } });
    // Railway: grey line with white dashes (the usual map convention), stations as labelled squares.
    m.addLayer({ id: "rail-line", type: "line", source: "rail", paint: { "line-color": "#555", "line-width": ["interpolate", ["linear"], ["zoom"], 9, 2, 15, 4] as never } });
    m.addLayer({ id: "rail-line-dash", type: "line", source: "rail",
      paint: { "line-color": "#fff", "line-width": ["interpolate", ["linear"], ["zoom"], 9, 1, 15, 2] as never, "line-dasharray": [3, 3] } });
    m.addLayer({ id: "tram-proposed", type: "line", source: "tramProposed", layout: { "line-cap": "round" },
      paint: { "line-color": "#e4007c", "line-width": 3.5, "line-dasharray": [1.2, 1.2], "line-opacity": 0.85 } });
    m.addLayer({ id: "tram-line", type: "line", source: "tram", filter: ["==", ["get", "kind"], "line"], layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#b5121b", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 3, 15, 6] as never } });
    m.addLayer({ id: "tram-stops", type: "circle", source: "tram", filter: ["==", ["get", "kind"], "stop"], minzoom: 11.5,
      paint: { "circle-radius": 4, "circle-color": "#fff", "circle-stroke-color": "#b5121b", "circle-stroke-width": 2 } });
    m.addLayer({ id: "rail-stations", type: "circle", source: "railStations",
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 4, 15, 7] as never, "circle-color": "#1d2426", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    m.addLayer({ id: "rail-station-labels", type: "symbol", source: "railStations", minzoom: 10.5,
      layout: { "text-field": ["concat", ["get", "name"], " · ", ["to-string", ["get", "minutes"]], " min"] as never,
        "text-font": FONT_BOLD, "text-size": 11, "text-offset": [0, 1.1], "text-anchor": "top" },
      paint: { "text-color": "#1d2426", "text-halo-color": "#fff", "text-halo-width": 1.5 } });

    m.addLayer({ id: "areas-circle", type: "circle", source: "areas",
      paint: { "circle-radius": ["interpolate", ["linear"], ["get", "count"], 1, 14, 60, 30] as never, "circle-color": step(["get", "median"], PRICE_STOPS) as never,
        "circle-opacity": 0.85, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    m.addLayer({ id: "areas-label", type: "symbol", source: "areas",
      layout: { "text-field": ["format", ["get", "district"], { "font-scale": 0.8 }, "\n", {}, ["get", "label"], {}] as never, "text-font": FONT_BOLD, "text-size": 12, "text-allow-overlap": true },
      paint: { "text-color": "#fff", "text-halo-color": "rgba(0,0,0,0.5)", "text-halo-width": 1 } });

    m.addLayer({ id: "selected-line", type: "line", source: "selected", filter: ["==", ["geometry-type"], "LineString"],
      paint: { "line-color": "#111", "line-width": 2, "line-dasharray": [2, 2] } });

    m.addLayer({ id: "clusters", type: "circle", source: "listings", filter: ["has", "point_count"],
      paint: { "circle-color": "#263238", "circle-opacity": 0.85, "circle-radius": ["step", ["get", "point_count"], 14, 20, 18, 60, 24] as never, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    m.addLayer({ id: "cluster-count", type: "symbol", source: "listings", filter: ["has", "point_count"],
      layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": FONT_BOLD, "text-size": 12 }, paint: { "text-color": "#fff" } });
    m.addLayer({ id: "pins", type: "circle", source: "listings", filter: ["!", ["has", "point_count"]],
      paint: { "circle-color": colourExpr("price") as never, "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 5, 15, 9] as never,
        "circle-stroke-color": ["case", ["get", "plot"], "#15803d", ["get", "auction"], "#ea580c", ["get", "top"], "#7b3294", "#fff"] as never,
        "circle-stroke-width": ["case", ["get", "plot"], 3.5, ["get", "auction"], 3.5, ["get", "top"], 2.5, 1.5] as never } });
    m.addLayer({ id: "pin-labels", type: "symbol", source: "listings", filter: ["!", ["has", "point_count"]], minzoom: 13.5,
      layout: { "text-field": ["get", "label"], "text-font": FONT_BOLD, "text-size": 11, "text-offset": [0, -1.4], "text-anchor": "bottom" },
      paint: { "text-color": "#111", "text-halo-color": "#fff", "text-halo-width": 1.5 } });
    m.addLayer({ id: "selected-pin", type: "circle", source: "selected", filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-radius": 13, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#111", "circle-stroke-width": 3 } });

    // Waverley marker
    const el = document.createElement("div");
    el.className = "waverley-marker";
    el.title = meta.waverley.name;
    el.innerHTML = `<span>🚆</span><b>Waverley</b>`;
    new maplibregl.Marker({ element: el }).setLngLat([meta.waverley.lng, meta.waverley.lat]).addTo(m);
  }

  private bind(h: MapHandlers): void {
    const m = this.map;
    m.on("click", "pins", (e) => {
      // Several homes can share one spot (ESPC puts every plot of a development on the same point):
      // offer a choice instead of silently opening whichever pin is drawn on top.
      const box: [maplibregl.PointLike, maplibregl.PointLike] = [[e.point.x - 6, e.point.y - 6], [e.point.x + 6, e.point.y + 6]];
      const ids = [...new Set(m.queryRenderedFeatures(box, { layers: ["pins"] }).map((f) => String(f.properties?.id)))];
      if (ids.length > 1) h.onMany(ids, [e.lngLat.lng, e.lngLat.lat]);
      else if (ids.length === 1) h.onSelect(ids[0]);
    });
    m.on("click", "clusters", async (e) => {
      const f = e.features?.[0];
      if (!f) return;
      const zoom = await (m.getSource("listings") as GeoJSONSource).getClusterExpansionZoom(f.properties!.cluster_id);
      m.easeTo({ center: (f.geometry as GeoJSON.Point).coordinates as [number, number], zoom });
    });
    m.on("click", (e) => {
      if (m.queryRenderedFeatures(e.point, { layers: ["pins", "clusters"] }).length) return;
      h.onBackgroundClick([e.lngLat.lng, e.lngLat.lat]);
    });
    for (const layer of ["pins", "clusters"]) {
      m.on("mouseenter", layer, () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", layer, () => (m.getCanvas().style.cursor = ""));
    }
  }

  showPopup(lngLat: [number, number], html: string): void {
    this.popup.setLngLat(lngLat).setHTML(html).addTo(this.map);
  }

  setListings(ls: Listing[], topSet = new Set<string>()): void {
    const fc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: ls.map((l) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [l.lng, l.lat] },
        properties: { id: l.id, price: l.price, mins: l.travel.best_min, vs: l.area?.vs_pct ?? 0, top: topSet.has(l.id) || !!l.top_school_rank,
          auction: !!l.auction, plot: l.kind === "plot",
          label: `${l.kind === "plot" ? "Plot " : l.auction ? "Auction " : ""}${gbp(l.price)}` },
      })),
    };
    (this.map.getSource("listings") as GeoJSONSource).setData(fc);
  }

  setAreas(areas: Area[]): void {
    (this.map.getSource("areas") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: areas.map((a) => ({ type: "Feature", geometry: { type: "Point", coordinates: [a.lng, a.lat] }, properties: { ...a, label: gbp(a.median) } })),
    } as GeoJSON.FeatureCollection);
  }

  select(l: Listing | null, meta: Meta): void {
    const src = this.map.getSource("selected") as GeoJSONSource;
    if (!l) return void src.setData(EMPTY);
    src.setData({
      type: "FeatureCollection",
      features: [
        { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [l.lng, l.lat] } },
        { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [[l.lng, l.lat], [meta.waverley.lng, meta.waverley.lat]] } },
      ],
    });
  }

  applyFilters(f: Filters): void {
    const m = this.map;
    const vis = (id: string, on: boolean) => m.getLayer(id) && m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
    vis("simd-fill", f.layers.simd);
    vis("tram-line", f.layers.tram);
    vis("tram-stops", f.layers.tram);
    for (const id of ["rail-line", "rail-line-dash", "rail-stations", "rail-station-labels"]) vis(id, f.layers.rail);
    vis("tram-proposed", f.layers.tramProposed);
    vis("bus-line", f.layers.bus);
    vis("catch-top-fill", f.layers.catchTop);
    vis("catch-top-line", f.layers.catchTop);
    vis("catch-rc-line", f.layers.catchTop);
    vis("catch-all-line", f.layers.catchAll);
    vis("areas-circle", f.layers.areas);
    vis("areas-label", f.layers.areas);
    m.setFilter("bus-line", f.allBus ? null : ["==", ["get", "main"], true]);
    const key = f.simdDomain === "decile" ? "decile" : f.simdDomain;
    const match: unknown[] = ["match", ["to-number", ["get", key], 0]];
    SIMD_COLOURS.forEach((c, i) => match.push(i + 1, c));
    match.push("rgba(0,0,0,0)");
    m.setPaintProperty("simd-fill", "fill-color", match as never);
    m.setPaintProperty("pins", "circle-color", colourExpr(f.colourBy) as never);
  }

  flyTo(l: Listing): void {
    this.map.flyTo({ center: [l.lng, l.lat], zoom: Math.max(this.map.getZoom(), 14), padding: this.padding(), speed: 1.4 });
  }

  private padding() {
    const narrow = window.matchMedia("(max-width: 760px)").matches;
    return narrow ? { top: 40, bottom: window.innerHeight * 0.45, left: 0, right: 0 } : { top: 0, bottom: 0, left: 0, right: 380 };
  }
}
