import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map as MLMap, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre 6 runs its tile worker from a separate ES module; let Vite bundle it and hand over the URL.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { PRICE_STOPS, SIMD_COLOURS, TIME_STOPS, VS_STOPS, gbp } from "./format";
import type { ColourBy, Filters } from "./filters";
import type { Area, Listing, Meta } from "./types";

const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
const GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
const FONT_BOLD = ["Noto Sans Bold"];
const EMPTY = { type: "FeatureCollection", features: [] } as GeoJSON.FeatureCollection;

maplibregl.setWorkerUrl(workerUrl);

async function loadStyle(): Promise<StyleSpecification | string> {
  try {
    const r = await fetch(BASEMAP, { signal: AbortSignal.timeout(6000) });
    if (r.ok) return BASEMAP;
  } catch {
    /* fall through to a plain background so the overlays still work offline */
  }
  return {
    version: 8,
    glyphs: GLYPHS,
    sources: {},
    layers: [{ id: "bg", type: "background", paint: { "background-color": "#eef0ec" } }],
  };
}

const step = (prop: unknown, stops: [number, string][]): unknown[] => {
  const e: unknown[] = ["step", prop, stops[0][1]];
  for (let i = 0; i < stops.length - 1; i++) e.push(stops[i][0], stops[i + 1][1]);
  return e;
};

export const colourExpr = (by: ColourBy): unknown[] =>
  by === "time" ? step(["get", "mins"], TIME_STOPS) : by === "vs" ? step(["get", "vs"], VS_STOPS) : step(["get", "price"], PRICE_STOPS);

export interface MapHandlers {
  onSelect: (id: string) => void;
  onBackgroundClick: (lngLat: [number, number]) => void;
}

export class HouseMap {
  map!: MLMap;
  private popup = new maplibregl.Popup({ closeButton: true, maxWidth: "280px" });

  async init(container: HTMLElement, meta: Meta, h: MapHandlers): Promise<void> {
    const style = await loadStyle();
    this.map = new maplibregl.Map({
      container,
      style,
      center: [-3.22, 55.935],
      zoom: 11.2,
      minZoom: 9,
      maxBounds: [[-4.2, 55.6], [-2.4, 56.25]],
      attributionControl: { compact: true, customAttribution: "Listings © ESPC · SIMD © Scottish Government · Catchments © City of Edinburgh Council · Transit © OpenStreetMap contributors" },
    });
    this.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    this.map.addControl(new maplibregl.GeolocateControl({}), "top-left");
    this.map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    await new Promise<void>((res) => this.map.once("load", () => res()));
    this.addLayers(meta);
    this.bind(h);
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
    m.addLayer({ id: "tram-proposed", type: "line", source: "tramProposed", layout: { "line-cap": "round" },
      paint: { "line-color": "#e4007c", "line-width": 3.5, "line-dasharray": [1.2, 1.2], "line-opacity": 0.85 } });
    m.addLayer({ id: "tram-line", type: "line", source: "tram", filter: ["==", ["get", "kind"], "line"], layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#b5121b", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 3, 15, 6] as never } });
    m.addLayer({ id: "tram-stops", type: "circle", source: "tram", filter: ["==", ["get", "kind"], "stop"], minzoom: 11.5,
      paint: { "circle-radius": 4, "circle-color": "#fff", "circle-stroke-color": "#b5121b", "circle-stroke-width": 2 } });

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
        "circle-stroke-color": ["case", ["get", "top"], "#7b3294", "#fff"] as never, "circle-stroke-width": ["case", ["get", "top"], 2.5, 1.5] as never } });
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
      const id = e.features?.[0]?.properties?.id;
      if (id) h.onSelect(String(id));
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
        properties: { id: l.id, price: l.price, mins: l.travel.best_min, vs: l.area?.vs_pct ?? 0, top: topSet.has(l.id) || !!l.top_school_rank, label: gbp(l.price) },
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
