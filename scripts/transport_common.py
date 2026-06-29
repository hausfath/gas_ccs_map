#!/usr/bin/env python3
"""
Shared multimodal CO₂/biomass transport-cost model (to_do item 4, v2).

Least-cost combination of TRUCK + RAIL + SHIP + BARGE from a region centroid to the nearest
OPERATING geologic-storage well, and the carbon-density-weighted delivered cost ($/tCO₂) per payload.

v2 over v1:
  - RAIL: 233 real NTAD intermodal terminals (was 44 curated) → short, realistic first-mile trucking.
  - SHIP (coastal): port-to-port legs routed by `searoute` — real marine geometry that goes AROUND
    land (no more straight lines across Florida), with real sea distance. Cached at build time.
  - BARGE (inland): a curated navigable-river network (Mississippi/Ohio/Missouri/Illinois/Tennessee
    corridors as ordered waypoints). Barge legs route ALONG the channel and are drawn following the
    river — they never cross land. Corridors connect only at real confluences (junctions), so barge
    requires a connected waterway.
  - Each ship/barge leg carries a `path` polyline (the real water geometry) for the map; truck/rail
    legs are straight. Consecutive same-mode hops are merged into one leg.

The cost-minimising PATH is payload-independent (carbon density is a scalar multiplier), so one
Dijkstra per region is scaled per payload (+ a one-off CO₂ liquefaction cost for the gaseous payload).
"""
import heapq
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # scripts/
from geo_utils import haversine_km  # noqa: E402

# --- Mode cost model ($/tonne-km, per-tonne handling on mode entry, GC->routed detour factor). ---
# PIPELINE is the gas-CCS addition: existing CO₂ trunk pipelines, priced BELOW barge so the
# least-cost solver routes captured CO₂ through real pipelines wherever a corridor is available.
# usd_per_tkm anchored to NETL (~$11/tCO₂ for 3.2 Mt/yr over 160 km → trunk pipelines are very cheap
# per t-km); the $6/t handling is the compression/entry cost of getting onto a trunk line (keeps very
# short hops on a truck). Dense-phase pipeline CO₂ does NOT need the $25/t liquefaction (see below).
MODES = {
    # EXISTING CO₂ trunk pipelines (already built, shared, large-throughput) — cheap per t-km, BELOW
    # barge, so the solver rides them whenever a corridor is available. Low handling = a tie-in, not
    # a fresh compression train. usd_per_tkm anchored to large-network NETL/ZEP economies of scale.
    "pipeline": {"usd_per_tkm": 0.008, "handling_usd_per_t": 0.5, "detour": 1.10},
    "truck": {"usd_per_tkm": 0.12, "handling_usd_per_t": 2.0, "detour": 1.40},
    "rail":  {"usd_per_tkm": 0.035, "handling_usd_per_t": 4.0, "detour": 1.20},
    "ship":  {"usd_per_tkm": 0.015, "handling_usd_per_t": 5.0, "detour": 1.00},  # searoute = real km
    "barge": {"usd_per_tkm": 0.012, "handling_usd_per_t": 4.0, "detour": 1.05},  # inland river barge
}
CO2_LIQUEFACTION_USD_PER_T = 25.0   # once, to move captured CO₂ by truck/rail/ship/barge (not pipeline)
# Pipeline moves dense-phase CO₂; the others move it as discrete (liquefied/refrigerated) cargo and so
# incur the one-off liquefaction cost. A pure-pipeline route skips it.
PIPELINE_MODES = {"pipeline"}
NON_PIPELINE_MODES = {"truck", "rail", "ship", "barge"}

# Storage-well confidence tiers by permit status (how likely to be operational in time for a
# project starting today; see the Class VI permit-conversion analysis):
#   firm    — operational, or ISSUED (final permit granted; ~80-90% reach injection) → real storage.
#   draft   — draft permit at public-comment stage (~55-70%) → usable but lower confidence.
#   pending — application under review, no draft (~30-50%, slow/uncertain) → fallback only.
# (EU "construction" maps to issued; "planned" to pending — done in the EU transport build.)
STATUS_TIER = {"operational": "firm", "issued": "firm", "draft": "draft", "pending": "pending"}
FIRM_STATUSES = {"operational", "issued"}

# Payload carbon density: tonnes MOVED per tonne CO₂ stored (drives the whole cost). Derived from
# the material's carbon mass-fraction as transported:  mass = (12/44) / f_C = 0.273 / f_C  (storing
# 1 t CO₂ needs 0.273 t C). So a denser-carbon payload is cheaper to haul per tCO₂.
#   co2     1.00  — captured CO₂ is 27.3% C and the whole molecule is stored (+ liquefaction $).
#   bio_oil 0.55  — pyrolysis bio-oil ~50% C as transported (raw ~55-65% C dry but carries water).
#   bio_oil_htl 0.45 — HTL bio-crude is more deoxygenated (~60-65% C), so denser than pyrolysis oil.
#   slurry  — biomass injected as a pumpable slurry; carbon density depends HEAVILY on feedstock
#             (dry C fraction × as-injected solids fraction), so it is feedstock-specific below.
PAYLOAD_MASS_PER_TCO2 = {"co2": 1.00, "bio_oil": 0.55, "bio_oil_htl": 0.45, "slurry": 2.6}

# Biomass-injection (slurry) mass per tCO₂ by dominant feedstock. f_C_hauled = dry-C × solids:
#   woody  ~50% C dry × ~30% solids = 0.15 -> 1.8 ;  crop ~45% × ~28% = 0.126 -> 2.2 ;
#   manure/biosolids ~38% C dry × ~18% solids = 0.068 -> 4.0 (mostly water) ;  msw/mixed -> 2.6.
SLURRY_MASS_BY_FEEDSTOCK = {
    "forestry_woody": 1.8, "ag_dry": 2.2, "manure_wet": 4.0, "msw": 2.6, "mixed": 2.6,
}

PATHWAY_PAYLOAD = {
    "beccs": "co2", "beccs_pp": "co2", "wte_ccs": "co2", "ad_ccs": "co2",
    "bio_oil": "bio_oil", "bio_oil_htl": "bio_oil_htl", "injection": "slurry",
}

# Graph fan-out (k-nearest keeps per-region Dijkstra small).
K_RAIL_NEIGHBORS = 7    # higher k -> better long-haul corridor connectivity in the rail graph
WELL_RAIL_LASTMILE = 3
WELL_PORT_LASTMILE = 2
WELL_RIVER_LASTMILE = 2
WELL_PIPE_LASTMILE = 3  # short tie-in from a well to nearby CO₂ pipeline waypoints
PIPE_TIEIN_MAX_KM = 80  # a well only counts as pipeline-connected if a trunk waypoint is within this
PORT_RAIL_LINK = 2
ORIG_WELL = 40         # connect the plant to (effectively) every well by a dedicated pipeline spur —
                       # for CO₂ you can build a line to any storage site; existing trunks then undercut
ORIG_RAIL = 3
ORIG_PORT = 2
ORIG_RIVER = 2
ORIG_PIPE = 4           # origin (plant) → nearest CO₂ pipeline waypoints, by short truck/gathering line

# River confluences that connect corridors but don't share a waypoint name.
RIVER_JUNCTIONS = [("Grafton IL", "St Louis")]   # Illinois R meets the Mississippi above St Louis

_SEAROUTE_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "data", "geo", "transport_raw", "searoute_cache.json")


def _leg_cost(mode, km):
    m = MODES[mode]
    return m["handling_usd_per_t"] + m["usd_per_tkm"] * km * m["detour"]


def _km(a, b):
    return haversine_km(a[1], a[0], b[1], b[0])   # nodes are [lat, lon]


# --- searoute coastal geometry/distance, cached on disk (key "lat,lon|lat,lon") ---
_searoute = None
_sr_cache = None
_sr_dirty = False


def _load_searoute():
    global _searoute, _sr_cache
    if _sr_cache is None:
        try:
            _sr_cache = json.load(open(_SEAROUTE_CACHE))
        except (FileNotFoundError, ValueError):
            _sr_cache = {}
        try:
            import searoute as sr
            _searoute = sr
        except ImportError:
            _searoute = False
            print("  (searoute not installed — coastal ship legs fall back to great-circle)")
    return _searoute


def _save_searoute_cache():
    if _sr_dirty:
        os.makedirs(os.path.dirname(_SEAROUTE_CACHE), exist_ok=True)
        with open(_SEAROUTE_CACHE, "w") as f:
            json.dump(_sr_cache, f, separators=(",", ":"))


def _sea_route(a, b):
    """(km, path[[lat,lon]...]) along real sea lanes between coastal points a,b ([lat,lon])."""
    global _sr_dirty
    sr = _load_searoute()
    key = f"{a[0]:.3f},{a[1]:.3f}|{b[0]:.3f},{b[1]:.3f}"
    if key in _sr_cache:
        c = _sr_cache[key]
        return c["km"], c["path"]
    if not sr:
        return _km(a, b), [list(a), list(b)]
    try:
        r = sr.searoute([a[1], a[0]], [b[1], b[0]])   # lon,lat
        km = round(float(r.properties["length"]))
        coords = [[round(y, 3), round(x, 3)] for x, y in r["geometry"]["coordinates"]]
        if len(coords) < 2:
            coords = [list(a), list(b)]
    except Exception:
        km, coords = round(_km(a, b)), [list(a), list(b)]
    _sr_cache[key] = {"km": km, "path": coords}
    _sr_dirty = True
    return km, coords


class TransportGraph:
    """Static multimodal graph (wells + rail terminals + coastal ports + river waypoints). Per region
    add a temporary origin, Dijkstra to the nearest well, then drop the origin."""

    def __init__(self, wells, terminals, coastal_ports, river_corridors, pipeline_corridors=None):
        self.nodes = {}                # id -> {pos,kind,name,basin?,status?}
        self.adj = {}                  # id -> list[(nbr, cost, mode, km, path|None)]
        self._wells, self._terms, self._ports, self._rivers = [], [], [], []
        self._pipes = []               # CO₂ pipeline waypoint node ids

        for i, w in enumerate(wells):
            nid = f"W{i}"
            self.nodes[nid] = {"pos": [w["lat"], w["lon"]], "kind": "well", "name": w["name"],
                               "status": w.get("status", "operational"),
                               "marine": bool(w.get("marine"))}   # offshore storage → reached by ship
            self._wells.append(nid)
        for i, t in enumerate(terminals):
            nid = f"R{i}"
            self.nodes[nid] = {"pos": [t["lat"], t["lon"]], "kind": "rail",
                               "name": t.get("name", "rail terminal")}
            self._terms.append(nid)
        for i, p in enumerate(coastal_ports):
            nid = f"P{i}"
            self.nodes[nid] = {"pos": [p["lat"], p["lon"]], "kind": "port",
                               "name": p.get("name", "port"), "basin": p.get("basin", "?")}
            self._ports.append(nid)
        # river waypoints: shared name == same node (junction)
        self._wp_by_name = {}
        for corridor in river_corridors.values():
            prev = None
            for (name, lat, lon) in corridor:
                if name not in self._wp_by_name:
                    nid = f"V{len(self._rivers)}"
                    self.nodes[nid] = {"pos": [lat, lon], "kind": "river", "name": name}
                    self._wp_by_name[name] = nid
                    self._rivers.append(nid)
                nid = self._wp_by_name[name]
                if prev is not None and prev != nid:
                    self._barge_edge(prev, nid)
                prev = nid
        for a, b in RIVER_JUNCTIONS:
            if a in self._wp_by_name and b in self._wp_by_name:
                self._barge_edge(self._wp_by_name[a], self._wp_by_name[b])

        # CO₂ pipeline corridors: same representation as rivers (ordered [name, lat, lon] waypoints;
        # a shared waypoint NAME across corridors = a junction/hub, e.g. "Denver City TX").
        self._pipe_by_name = {}
        for corridor in (pipeline_corridors or {}).values():
            prev = None
            for (name, lat, lon) in corridor:
                if name not in self._pipe_by_name:
                    nid = f"PL{len(self._pipes)}"
                    self.nodes[nid] = {"pos": [lat, lon], "kind": "pipeline", "name": name}
                    self._pipe_by_name[name] = nid
                    self._pipes.append(nid)
                nid = self._pipe_by_name[name]
                if prev is not None and prev != nid:
                    self._pipe_edge(prev, nid)
                prev = nid

        for nid in self.nodes:
            self.adj.setdefault(nid, [])
        self._build_static_edges()

    # edge helpers ---------------------------------------------------------
    def _edge(self, a, b, mode, km=None, path=None, bidir=True):
        pa, pb = self.nodes[a]["pos"], self.nodes[b]["pos"]
        if km is None:
            km = _km(pa, pb)
        cost = _leg_cost(mode, km)
        self.adj.setdefault(a, []).append((b, cost, mode, km, path))
        if bidir:
            rpath = list(reversed(path)) if path else None
            self.adj.setdefault(b, []).append((a, cost, mode, km, rpath))

    def _barge_edge(self, a, b):
        pa, pb = self.nodes[a]["pos"], self.nodes[b]["pos"]
        self._edge(a, b, "barge", km=_km(pa, pb), path=[list(pa), list(pb)])

    def _pipe_edge(self, a, b):
        pa, pb = self.nodes[a]["pos"], self.nodes[b]["pos"]
        self._edge(a, b, "pipeline", km=_km(pa, pb), path=[list(pa), list(pb)])

    def _nearest(self, src_pos, pool, k):
        cand = sorted(((_km(src_pos, self.nodes[n]["pos"]), n) for n in pool))
        return [n for _, n in cand[:k]]

    def _build_static_edges(self):
        # rail network
        for t in self._terms:
            for nbr in self._nearest(self.nodes[t]["pos"], self._terms, K_RAIL_NEIGHBORS + 1):
                if nbr != t:
                    self._edge(t, nbr, "rail")
        # coastal ship network: searoute between nearest same/adjacent-basin ports
        # navigably-connected sea basins (US + EU; scopes build separately so no transatlantic mix)
        OPEN = {"Gulf": {"Gulf", "Atlantic"}, "Atlantic": {"Atlantic", "Gulf", "NorthSea"},
                "Pacific": {"Pacific"}, "GreatLakes": {"GreatLakes"},
                "NorthSea": {"NorthSea", "Atlantic", "Baltic"}, "Baltic": {"Baltic", "NorthSea"},
                "Mediterranean": {"Mediterranean", "BlackSea", "Atlantic"},
                "BlackSea": {"BlackSea", "Mediterranean"}}
        for p in self._ports:
            bp = self.nodes[p]["basin"]
            pool = [q for q in self._ports if q != p and self.nodes[q]["basin"] in OPEN.get(bp, {bp})]
            for nbr in self._nearest(self.nodes[p]["pos"], pool, 4):
                km, path = _sea_route(self.nodes[p]["pos"], self.nodes[nbr]["pos"])
                self._edge(p, nbr, "ship", km=km, path=path, bidir=False)
        # link ports & wells to the rail/river networks via truck last-mile
        for p in self._ports:
            for t in self._nearest(self.nodes[p]["pos"], self._terms, PORT_RAIL_LINK):
                self._edge(p, t, "truck")
        for w in self._wells:
            if self.nodes[w]["marine"]:
                # offshore storage (e.g. North-Sea CCS): reached from coastal ports by SHIP, not truck
                for p in self._nearest(self.nodes[w]["pos"], self._ports, 3):
                    km, path = _sea_route(self.nodes[p]["pos"], self.nodes[w]["pos"])
                    self._edge(w, p, "ship", km=km, path=list(reversed(path)))
                continue
            for t in self._nearest(self.nodes[w]["pos"], self._terms, WELL_RAIL_LASTMILE):
                self._edge(w, t, "truck")
            for p in self._nearest(self.nodes[w]["pos"], self._ports, WELL_PORT_LASTMILE):
                self._edge(w, p, "truck")
            for v in self._nearest(self.nodes[w]["pos"], self._rivers, WELL_RIVER_LASTMILE):
                self._edge(w, v, "truck")
            # short tie-in from a well to nearby CO₂ pipeline waypoints (so a route can ride an
            # existing trunk pipeline up to the storage hub, then a short hop to the injection well).
            # Priced at the existing-pipeline (tie-in) rate — but ONLY if a trunk is genuinely nearby,
            # else the well is not pipeline-connected (avoids spurious cheap cross-country tie-ins).
            for pl in self._nearest(self.nodes[w]["pos"], self._pipes, WELL_PIPE_LASTMILE):
                if _km(self.nodes[w]["pos"], self.nodes[pl]["pos"]) <= PIPE_TIEIN_MAX_KM:
                    self._edge(w, pl, "pipeline")

    # per-region solve -----------------------------------------------------
    def least_cost_to_well(self, origin_pos):
        """Full Dijkstra (no early stop) → cheapest per-tonne path to the nearest well in each
        CONFIDENCE TIER (by permit status; see STATUS_TIER): 'firm' = operational + issued (high odds
        of being operational in time, treated as real storage), 'draft' = draft permit (moderate),
        'pending' = pending application (speculative, fallback only). Returns
        {tier: (cost, legs, name, status) | None}."""
        O = "_O"
        self.nodes[O] = {"pos": list(origin_pos), "kind": "origin", "name": "origin"}
        self.adj[O] = []
        # The plant trucks its CO₂ to the nearest access point — directly to a well, or onto an
        # existing pipeline / rail terminal / port / river — then rides existing infrastructure.
        # NO new dedicated pipelines are built (the cost of greenfield CO₂ pipelines is out of scope).
        onshore_wells = [w for w in self._wells if not self.nodes[w]["marine"]]
        for w in self._nearest(origin_pos, onshore_wells, ORIG_WELL):
            self._edge(O, w, "truck", bidir=False)
        for pl in self._nearest(origin_pos, self._pipes, ORIG_PIPE):
            self._edge(O, pl, "truck", bidir=False)
        for t in self._nearest(origin_pos, self._terms, ORIG_RAIL):
            self._edge(O, t, "truck", bidir=False)
        for p in self._nearest(origin_pos, self._ports, ORIG_PORT):
            self._edge(O, p, "truck", bidir=False)
        for v in self._nearest(origin_pos, self._rivers, ORIG_RIVER):
            self._edge(O, v, "truck", bidir=False)

        dist, prev = self._dijkstra(O)

        best = {"firm": None, "draft": None, "pending": None}   # tier -> (cost, well_id)
        for w in self._wells:
            if w not in dist:
                continue
            tier = STATUS_TIER.get(self.nodes[w]["status"], "pending")
            if best[tier] is None or dist[w] < best[tier][0]:
                best[tier] = (dist[w], w)

        def pack(entry):
            if entry is None:
                return None
            cost, wid = entry
            legs = self._reconstruct(prev, wid)
            return (round(cost, 2), legs, self.nodes[wid]["name"], self.nodes[wid]["status"])

        result = {tier: pack(entry) for tier, entry in best.items()}
        del self.nodes[O]
        del self.adj[O]
        return result

    def _dijkstra(self, source):
        """Standard Dijkstra over the (temporarily augmented) graph from `source`."""
        dist = {source: 0.0}
        prev = {}
        pq = [(0.0, source)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, float("inf")):
                continue
            for v, c, mode, km, path in self.adj.get(u, []):
                nd = d + c
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = (u, mode, km, path)
                    heapq.heappush(pq, (nd, v))
        return dist, prev

    def least_cost_to_target(self, origin_pos, target_pos, target_name="storage"):
        """Least-cost multimodal route from a plant to an ARBITRARY target point (e.g. the nearest
        edge of a saline basin), using existing pipelines + truck/rail/barge/ship — NO new pipelines.
        Returns (cost, legs) or None. The plant trucks onto existing infrastructure (or directly to
        the target); nearby pipeline/rail/river/port nodes truck the last mile into the target."""
        O, T = "_O", "_T"
        self.nodes[O] = {"pos": list(origin_pos), "kind": "origin", "name": "origin"}
        self.nodes[T] = {"pos": list(target_pos), "kind": "target", "name": target_name}
        self.adj[O] = []
        self.adj[T] = []
        # plant trucks onto existing infrastructure (or straight to the target)
        for pl in self._nearest(origin_pos, self._pipes, ORIG_PIPE):
            self._edge(O, pl, "truck", bidir=False)
        for t in self._nearest(origin_pos, self._terms, ORIG_RAIL):
            self._edge(O, t, "truck", bidir=False)
        for p in self._nearest(origin_pos, self._ports, ORIG_PORT):
            self._edge(O, p, "truck", bidir=False)
        for v in self._nearest(origin_pos, self._rivers, ORIG_RIVER):
            self._edge(O, v, "truck", bidir=False)
        self._edge(O, T, "truck", bidir=False)   # direct truck plant → basin edge
        # last-mile truck from nearby existing infrastructure INTO the target
        for pool, k in ((self._pipes, WELL_PIPE_LASTMILE), (self._terms, WELL_RAIL_LASTMILE),
                        (self._rivers, WELL_RIVER_LASTMILE), (self._ports, WELL_PORT_LASTMILE)):
            for n in self._nearest(target_pos, pool, k):
                self._edge(n, T, "truck", bidir=False)

        dist, prev = self._dijkstra(O)
        out = None
        if T in dist:
            out = (round(dist[T], 2), self._reconstruct(prev, T))
        for n in (O, T):
            del self.nodes[n]
            del self.adj[n]
        return out

    def _reconstruct(self, prev, well):
        # walk back to origin collecting (a, b, mode, km, path)
        hops = []
        cur = well
        while cur in prev:
            p, mode, km, path = prev[cur]
            hops.append((p, cur, mode, km, path))
            cur = p
        hops.reverse()
        # merge consecutive same-mode hops into one leg (concatenating water geometry)
        legs = []
        for a, b, mode, km, path in hops:
            pa, pb = self.nodes[a]["pos"], self.nodes[b]["pos"]
            seg = path if path else [list(pa), list(pb)]
            if legs and legs[-1]["mode"] == mode:
                L = legs[-1]
                L["km"] = round(L["km"] + km * MODES[mode]["detour"])
                pts = L["path"]
                pts.extend(seg[1:] if seg and pts and pts[-1] == seg[0] else seg)
                L["to"] = [round(pb[0], 3), round(pb[1], 3)]
                L["to_name"] = self.nodes[b]["name"] if self.nodes[b]["kind"] != "origin" else None
            else:
                legs.append({
                    "mode": mode,
                    "from": [round(pa[0], 3), round(pa[1], 3)],
                    "to": [round(pb[0], 3), round(pb[1], 3)],
                    "km": round(km * MODES[mode]["detour"]),
                    "path": [[round(x, 3), round(y, 3)] for x, y in seg],
                    "to_name": self.nodes[b]["name"] if self.nodes[b]["kind"] != "origin" else None,
                })
        # drop trivial straight path on land legs (frontend draws from->to); keep water geometry
        for L in legs:
            if L["mode"] in ("truck", "rail"):
                L.pop("path", None)
        return legs


def build_records(graph, regions, cap=100.0):
    """Per-region transport records with status tiering, shared by all scopes.
    `regions` = iterable of (region_id, [lat, lon]) or (region_id, [lat, lon], dominant_feedstock).
    The dominant feedstock (if given) sets the feedstock-specific slurry carbon density. Returns
    (records_dict, stats_dict). Operating well preferred where its CO₂-delivered cost ≤ cap; else a
    permitted well 'rescues' the region. Stores legs (water geometry), per-payload cost, dest, etc."""
    from collections import Counter
    out, mode_use = {}, Counter()
    n_path = n_nopath = n_rescued = 0
    for region in regions:
        rid, pos = region[0], region[1]
        dom = region[2] if len(region) > 2 else None
        res = graph.least_cost_to_well(pos)
        firm, draft, pend = res["firm"], res["draft"], res["pending"]

        def co2_of(entry):
            return payload_costs(entry[0], True, dom)["co2"] if entry else None

        # Prefer a FIRM well (operational/issued) when its delivered cost is within the cap; then a
        # draft permit; then a pending application as a last-resort fallback. If none is affordable,
        # fall back to the cheapest well that has any path (it will grade "poor").
        chosen = None
        if firm and co2_of(firm) is not None and co2_of(firm) <= cap:
            chosen = firm
        elif draft and co2_of(draft) is not None and co2_of(draft) <= cap:
            chosen = draft; n_rescued += 1
        elif pend and co2_of(pend) is not None and co2_of(pend) <= cap:
            chosen = pend; n_rescued += 1
        if chosen is None:
            cands = [e for e in (firm, draft, pend) if e]
            chosen = min(cands, key=lambda e: e[0]) if cands else None
        if chosen is None:
            n_nopath += 1
            continue
        per_tonne, legs, dest, dest_status = chosen[0], chosen[1], chosen[2], chosen[3]
        n_path += 1
        modes = sorted({leg["mode"] for leg in legs})
        for m in modes:
            mode_use[m] += 1
        rec = {
            "per_tonne_usd": per_tonne, "dest_well": dest, "dest_status": dest_status,
            "legs": legs, "by_payload": payload_costs(per_tonne, bool(legs), dom),
            "modes": modes, "total_km": sum(leg["km"] for leg in legs),
        }
        if firm:
            rec["firm_co2_usd"] = co2_of(firm)   # nearest firm-storage CO₂ cost, for reference
        out[rid] = rec
    return out, {"paths": n_path, "no_path": n_nopath, "rescued": n_rescued, "modes": dict(mode_use)}


def payload_costs(per_tonne_usd, has_path, dom=None):
    """Delivered $/tCO₂ per payload class. The slurry (biomass-injection) factor is feedstock-
    specific (`dom` = dominant feedstock) since a manure slurry hauls ~2× the mass of woody residue
    per tCO₂; other payloads are feedstock-independent."""
    if per_tonne_usd is None:
        return {k: None for k in PAYLOAD_MASS_PER_TCO2}
    out = {}
    for payload, mass in PAYLOAD_MASS_PER_TCO2.items():
        if payload == "slurry":
            mass = SLURRY_MASS_BY_FEEDSTOCK.get(dom, mass)
        c = per_tonne_usd * mass
        if payload == "co2" and has_path:
            c += CO2_LIQUEFACTION_USD_PER_T
        out[payload] = round(c, 1)
    return out


def save_caches():
    _save_searoute_cache()
