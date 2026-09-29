type Ring = number[][];

const inRing = (x: number, y: number, ring: Ring): boolean => {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
};

const inPolygon = (x: number, y: number, rings: Ring[]): boolean =>
  inRing(x, y, rings[0]) && !rings.slice(1).some((h) => inRing(x, y, h));

/** Point-in-polygon index over a GeoJSON FeatureCollection (bbox-prefiltered). */
export class PolygonIndex<P = Record<string, unknown>> {
  private items: { bbox: [number, number, number, number]; polys: Ring[][]; props: P }[] = [];

  constructor(fc: GeoJSON.FeatureCollection | null) {
    for (const f of fc?.features ?? []) {
      const g = f.geometry;
      const polys = g?.type === "Polygon" ? [g.coordinates] : g?.type === "MultiPolygon" ? g.coordinates : [];
      if (!polys.length) continue;
      let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
      for (const p of polys) for (const [x, y] of p[0]) {
        x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
      }
      this.items.push({ bbox: [x0, y0, x1, y1], polys, props: f.properties as P });
    }
  }

  at(lng: number, lat: number): P[] {
    return this.items
      .filter(({ bbox: [x0, y0, x1, y1] }) => lng >= x0 && lng <= x1 && lat >= y0 && lat <= y1)
      .filter(({ polys }) => polys.some((p) => inPolygon(lng, lat, p)))
      .map((i) => i.props);
  }
}
