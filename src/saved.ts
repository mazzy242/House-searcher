import type { Listing } from "./types";

/** Saved searches and lists of homes, kept in this browser (localStorage). */

export interface SavedSearch { name: string; hash: string }
/** Enough about a home to show it after it has left ESPC (sold or withdrawn). */
export interface Snap { address?: string; title?: string; price: number; url: string; image?: string }
export interface SavedList { name: string; ids: string[]; snap: Record<string, Snap> }
export interface SavedState { searches: SavedSearch[]; lists: SavedList[] }

export const REJECTED = "Rejected";
export const DEFAULT_LISTS = ["Shortlist", "Viewing", REJECTED];
const KEY = "house-searcher:v1";

const fresh = (): SavedState => ({ searches: [], lists: DEFAULT_LISTS.map((name) => ({ name, ids: [], snap: {} })) });

function defaultStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

const snapOf = (l: Listing): Snap => ({ address: l.address, title: l.title, price: l.price, url: l.url, image: l.image });

export class Saved {
  state: SavedState;

  constructor(private storage: Pick<Storage, "getItem" | "setItem"> | null = defaultStorage()) {
    this.state = this.load();
  }

  private load(): SavedState {
    try {
      const raw = this.storage?.getItem(KEY);
      const s = raw ? (JSON.parse(raw) as SavedState) : null;
      if (s && Array.isArray(s.searches) && Array.isArray(s.lists)) {
        if (!s.lists.some((l) => l.name === REJECTED)) s.lists.push({ name: REJECTED, ids: [], snap: {} });
        return s;
      }
    } catch {
      /* unreadable or blocked: start afresh (kept in memory only) */
    }
    return fresh();
  }

  private save(): void {
    try {
      this.storage?.setItem(KEY, JSON.stringify(this.state));
    } catch {
      /* private mode or storage full: changes last until the page is closed */
    }
  }

  list(name: string): SavedList | undefined {
    return this.state.lists.find((l) => l.name === name);
  }

  ids(name: string): Set<string> {
    return new Set(this.list(name)?.ids ?? []);
  }

  /** Every home on any list except Rejected. */
  savedIds(): Set<string> {
    return new Set(this.state.lists.filter((l) => l.name !== REJECTED).flatMap((l) => l.ids));
  }

  listsFor(id: string): string[] {
    return this.state.lists.filter((l) => l.ids.includes(id)).map((l) => l.name);
  }

  /** Add the home to the list, or take it off. Returns whether it is now on the list. */
  toggle(name: string, l: Listing): boolean {
    const list = this.list(name);
    if (!list) return false;
    const on = !list.ids.includes(l.id);
    if (on) {
      list.ids.push(l.id);
      list.snap[l.id] = snapOf(l);
      // Rejecting a home takes it off the other lists, and shortlisting it un-rejects it.
      for (const other of this.state.lists) {
        if (other !== list && (name === REJECTED || other.name === REJECTED)) this.drop(other, l.id);
      }
    } else {
      this.drop(list, l.id);
    }
    this.save();
    return on;
  }

  remove(name: string, id: string): void {
    const list = this.list(name);
    if (list) this.drop(list, id);
    this.save();
  }

  private drop(list: SavedList, id: string): void {
    list.ids = list.ids.filter((x) => x !== id);
    delete list.snap[id];
  }

  /** Keep the snapshots of saved homes up to date while they are still listed. */
  refresh(listings: Listing[]): void {
    const byId = new Map(listings.map((l) => [l.id, l]));
    for (const list of this.state.lists) for (const id of list.ids) {
      const l = byId.get(id);
      if (l) list.snap[id] = snapOf(l);
    }
    this.save();
  }

  addList(name: string): boolean {
    name = name.trim();
    if (!name || this.list(name)) return false;
    this.state.lists.splice(this.state.lists.length - 1, 0, { name, ids: [], snap: {} }); // Rejected stays last
    this.save();
    return true;
  }

  renameList(from: string, to: string): boolean {
    to = to.trim();
    const list = this.list(from);
    if (!list || from === REJECTED || !to || this.list(to)) return false;
    list.name = to;
    this.save();
    return true;
  }

  deleteList(name: string): boolean {
    if (name === REJECTED || !this.list(name)) return false;
    this.state.lists = this.state.lists.filter((l) => l.name !== name);
    this.save();
    return true;
  }

  saveSearch(name: string, hash: string): void {
    name = name.trim();
    if (!name) return;
    const s = this.state.searches.find((x) => x.name === name);
    if (s) s.hash = hash;
    else this.state.searches.push({ name, hash });
    this.save();
  }

  deleteSearch(name: string): void {
    this.state.searches = this.state.searches.filter((s) => s.name !== name);
    this.save();
  }

  /** Everything saved, packed into a URL parameter (photos left out to keep the link short). */
  exportParam(): string {
    const lists = this.state.lists.map((l) => ({
      name: l.name, ids: l.ids,
      snap: Object.fromEntries(Object.entries(l.snap).map(([id, s]) => [id, { ...s, image: undefined }])),
    }));
    const bytes = new TextEncoder().encode(JSON.stringify({ searches: this.state.searches, lists }));
    let bin = "";
    bytes.forEach((b) => (bin += String.fromCharCode(b)));
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  static decode(param: string): SavedState | null {
    try {
      const bin = atob(param.replace(/-/g, "+").replace(/_/g, "/"));
      const s = JSON.parse(new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)))) as SavedState;
      return Array.isArray(s.searches) && Array.isArray(s.lists) ? s : null;
    } catch {
      return null;
    }
  }

  /** Add someone else's lists and searches to ours: homes are added to lists of the same name. */
  merge(other: SavedState): void {
    for (const s of other.searches) if (s?.name && typeof s.hash === "string") this.saveSearch(s.name, s.hash);
    for (const o of other.lists) {
      if (!o?.name || !Array.isArray(o.ids)) continue;
      let list = this.list(o.name);
      if (!list) {
        this.addList(o.name);
        list = this.list(o.name)!;
      }
      for (const id of o.ids) {
        if (list.ids.includes(id)) continue;
        list.ids.push(id);
        if (o.snap?.[id]) list.snap[id] = o.snap[id];
      }
    }
    this.save();
  }
}
