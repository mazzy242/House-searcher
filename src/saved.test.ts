import { describe, expect, it } from "vitest";
import { REJECTED, Saved } from "./saved";
import type { Listing } from "./types";

const home = (id: string): Listing => ({
  id, url: `https://espc.com/${id}`, address: `${id} High Street`, lat: 0, lng: 0, price: 300_000, detached: false, garage: false,
  travel: { best_min: 20, best_how: "bus", walk_min: 60, cycle_min: 20, km: 5 },
});

const memory = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
};

describe("Saved", () => {
  it("starts with Shortlist, Viewing and Rejected and persists changes", () => {
    const store = memory();
    const a = new Saved(store);
    expect(a.state.lists.map((l) => l.name)).toEqual(["Shortlist", "Viewing", REJECTED]);
    a.toggle("Shortlist", home("1"));
    a.saveSearch("Maz", "maxPrice=500000");
    const b = new Saved(store);
    expect([...b.ids("Shortlist")]).toEqual(["1"]);
    expect(b.state.searches).toEqual([{ name: "Maz", hash: "maxPrice=500000" }]);
  });

  it("rejecting takes a home off the other lists, and shortlisting un-rejects it", () => {
    const s = new Saved(memory());
    s.toggle("Shortlist", home("1"));
    s.toggle("Viewing", home("1"));
    s.toggle(REJECTED, home("1"));
    expect(s.listsFor("1")).toEqual([REJECTED]);
    s.toggle("Viewing", home("1"));
    expect(s.listsFor("1")).toEqual(["Viewing"]);
    expect(s.toggle("Viewing", home("1"))).toBe(false);
    expect(s.listsFor("1")).toEqual([]);
  });

  it("keeps Rejected: can't be renamed or deleted, new lists go before it", () => {
    const s = new Saved(memory());
    expect(s.deleteList(REJECTED)).toBe(false);
    expect(s.renameList(REJECTED, "Nope")).toBe(false);
    expect(s.addList("Nichelle")).toBe(true);
    expect(s.addList("Nichelle")).toBe(false);
    expect(s.state.lists.at(-1)!.name).toBe(REJECTED);
    expect(s.renameList("Nichelle", "Nichelle's picks")).toBe(true);
    expect(s.deleteList("Nichelle's picks")).toBe(true);
  });

  it("shares through a link and merges without losing anything", () => {
    const a = new Saved(memory());
    a.toggle("Shortlist", home("1"));
    a.addList("Gardens");
    a.toggle("Gardens", home("2"));
    a.saveSearch("Nichelle", "types=detached");
    const b = new Saved(memory());
    b.toggle("Shortlist", home("3"));
    b.merge(Saved.decode(a.exportParam())!);
    expect([...b.ids("Shortlist")].sort()).toEqual(["1", "3"]);
    expect([...b.ids("Gardens")]).toEqual(["2"]);
    expect(b.list("Gardens")!.snap["2"].address).toBe("2 High Street");
    expect(b.state.searches.map((x) => x.name)).toEqual(["Nichelle"]);
    expect(Saved.decode("not-a-link")).toBeNull();
  });

  it("works (in memory) when storage is blocked", () => {
    const blocked = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    const s = new Saved(blocked);
    expect(s.toggle("Shortlist", home("1"))).toBe(true);
    expect(s.listsFor("1")).toEqual(["Shortlist"]);
  });
});
