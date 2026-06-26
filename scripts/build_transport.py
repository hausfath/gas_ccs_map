#!/usr/bin/env python3
"""
Per-gas-plant CO₂ transport + storage cost, to BOTH (i) the nearest existing storage well and
(ii) the nearest geologic (saline) storage basin — stored together so the frontend well/basin
toggle needs no recompute.

Transport routing reuses the BiCRS multimodal least-cost engine (truck/rail/ship/barge), extended
with real CO₂ trunk pipelines (priced below barge) and a "new dedicated pipeline spur" baseline.
See scripts/transport_common.py for the mode model.

Cost = transport ($/tCO₂, from the chosen route) + storage ($/tCO₂, flat saline screening value).
The per-t-km of a NEW dedicated pipeline is flow-scaled here (economies of scale: a small plant pays
more per tonne than a large one). Liquefaction ($25/t) applies only if the route uses a non-pipeline
mode (dense-phase pipeline CO₂ skips it).
"""
import json
import os

from geo_utils import haversine_km, nearest_point_on_ring_km, point_in_polygon
from transport_common import (CO2_LIQUEFACTION_USD_PER_T, MODES, NON_PIPELINE_MODES,
                              TransportGraph)

HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(HERE, "..", *a)

STORAGE_USD_PER_TCO2 = 10.0     # onshore saline injection + monitoring (NETL screening ~$8-11/t)
ONSITE_TRANSPORT_USD = 3.0      # in-basin: short gathering + compression, no long-haul transport
FLOW_REF_MTPA = 2.0             # reference plant CO₂ flow for the new-pipeline economies-of-scale curve


def flow_factor(mtpa):
    """New-dedicated-pipeline $/t-km multiplier vs the reference flow. Small plants can't amortize a
    pipeline, so they pay more per tonne; large plants get a modest discount. Clamped for sanity."""
    m = max(mtpa or 0.0, 0.05)
    f = (m / FLOW_REF_MTPA) ** -0.4
    return max(0.7, min(4.0, f))


def transport_from_legs(legs, mtpa):
    """Recompute delivered transport $/tCO₂ from the chosen route's legs (handling once per merged
    leg; new-pipeline per-t-km flow-scaled). Returns (transport_usd, liquefaction_usd)."""
    ff = flow_factor(mtpa)
    total = 0.0
    for leg in legs:
        m = leg["mode"]
        rate = MODES[m]["usd_per_tkm"] * (ff if m == "pipeline_new" else 1.0)
        total += MODES[m]["handling_usd_per_t"] + rate * leg["km"]
    liq = CO2_LIQUEFACTION_USD_PER_T if any(l["mode"] in NON_PIPELINE_MODES for l in legs) else 0.0
    return round(total, 2), round(liq, 2)


def pack(transport_usd, liq_usd, mtpa):
    storage = STORAGE_USD_PER_TCO2
    total = round(transport_usd + storage, 2)
    return {
        "transport_usd": transport_usd,
        "liquefaction_usd": liq_usd,
        "storage_usd": storage,
        "total_usd": total,
        "annual_usd_m": round(total * (mtpa or 0.0), 2),  # $M/yr = $/t × Mt/yr
    }


def well_route(graph, plant):
    """Least-cost multimodal route to the nearest well, preferring a FIRM (operational/issued) well;
    falls back to draft/pending permits, flagged as lower confidence."""
    res = graph.least_cost_to_well([plant["lat"], plant["lon"]])
    for tier, conf in (("firm", "firm"), ("draft", "draft"), ("pending", "pending")):
        if res.get(tier):
            _cost, legs, name, status = res[tier]
            transport, liq = transport_from_legs(legs, plant["co2_mtpa"])
            out = {"dest_name": name, "dest_status": status, "confidence": conf,
                   "total_km": sum(l["km"] for l in legs),
                   "modes": sorted({l["mode"] for l in legs}), "legs": legs}
            out.update(pack(transport, liq, plant["co2_mtpa"]))
            return out
    return None


def basin_route(plant, basins):
    """In-basin → on-site storage (nominal transport). Else a new dedicated pipeline straight to the
    nearest basin edge. Basins are treated as unconstrained capacity (the user's toggle semantics)."""
    lon, lat, mtpa = plant["lon"], plant["lat"], plant["co2_mtpa"]
    # in-basin?
    for b in basins:
        w, s, e, n = b["bbox"]
        if w <= lon <= e and s <= lat <= n and point_in_polygon(lon, lat, b["geometry"]):
            out = {"dest_name": b["name"], "in_basin": True, "total_km": 0,
                   "modes": ["onsite"], "legs": []}
            out.update(pack(ONSITE_TRANSPORT_USD, 0.0, mtpa))
            return out
    # nearest basin edge (vertex-level screening)
    best_km, best_pt, best_name = float("inf"), None, None
    for b in basins:
        geom = b["geometry"]
        rings = ([geom["coordinates"][0]] if geom["type"] == "Polygon"
                 else [poly[0] for poly in geom["coordinates"]])
        for ring in rings:
            d, pt = nearest_point_on_ring_km(lon, lat, ring)
            if d < best_km:
                best_km, best_pt, best_name = d, pt, b["name"]
    if best_pt is None:
        return None
    km = round(best_km * MODES["pipeline_new"]["detour"])
    leg = {"mode": "pipeline_new", "from": [lat, lon], "to": best_pt, "km": km,
           "path": [[lat, lon], best_pt], "to_name": best_name}
    transport, liq = transport_from_legs([leg], mtpa)
    out = {"dest_name": best_name, "in_basin": False, "total_km": km,
           "modes": ["pipeline_new"], "legs": [leg]}
    out.update(pack(transport, liq, mtpa))
    return out


def main():
    plants = json.load(open(P("data", "processed", "gas_plants.json")))
    wells = json.load(open(P("data", "processed", "wells_us.json")))
    basins = json.load(open(P("data", "processed", "storage_basins.json")))
    nodes = json.load(open(P("data", "geo", "transport_nodes_us.json")))
    pipes = json.load(open(P("data", "geo", "co2_pipelines_us.json")))
    pipes = {k: v for k, v in pipes.items() if not k.startswith("_")}

    graph = TransportGraph(wells, nodes["rail_terminals"], nodes["coastal_ports"],
                           nodes["river_corridors"], pipeline_corridors=pipes)

    out = {}
    n_pipe_well = n_no_well = 0
    for plant in plants:
        w = well_route(graph, plant)
        b = basin_route(plant, basins)
        if w is None:
            n_no_well += 1
        elif any(m in ("pipeline", "pipeline_new") for m in w["modes"]):
            n_pipe_well += 1
        out[str(plant["id"])] = {"co2_mtpa": plant["co2_mtpa"], "to_well": w, "to_basin": b}

    with open(P("data", "processed", "transport.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))

    print(f"wrote transport for {len(out)} plants -> data/processed/transport.json")
    print(f"  well routes using a pipeline leg: {n_pipe_well}; plants with no reachable well: {n_no_well}")
    # spot-checks
    by_name = {p["name"]: p for p in plants}
    for nm in ("West County Energy Center", "Union Power Station", "Crystal River"):
        p = by_name.get(nm)
        if not p:
            continue
        r = out[str(p["id"])]
        w, b = r["to_well"], r["to_basin"]
        print(f"\n{nm} ({p['state']}, {p['co2_mtpa']} Mt/yr):")
        if w:
            print(f"  well : {w['dest_name']} [{w['confidence']}] {w['total_km']}km "
                  f"{'+'.join(w['modes'])} -> transport ${w['transport_usd']} +stor ${w['storage_usd']}"
                  f" = ${w['total_usd']}/t (${w['annual_usd_m']}M/yr)")
        if b:
            tag = "ON-SITE" if b["in_basin"] else f"{b['total_km']}km"
            print(f"  basin: {b['dest_name']} {tag} -> ${b['total_usd']}/t")


if __name__ == "__main__":
    main()
