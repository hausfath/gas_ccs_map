# Methodology & sources

A screening tool for siting carbon capture & storage (CCS) at existing US natural-gas power plants:
for each plant, where would its captured CO₂ go, and what would **transport + geologic storage** cost?

## 1. Gas plants

All existing US natural-gas-fired power plants from **EPA eGRID2023** (rev2, 2025-06-12), Plant sheet,
filtered to plant primary fuel category `PLFUELCT == "GAS"` with valid coordinates and CO₂ > 0
(~1,700 plants). Fields: nameplate capacity (`NAMEPCAP`, MW), net generation (`PLNGENAN`, MWh), annual
CO₂ (`PLCO2AN`, short tons → tonnes × 0.90718), specific fuel (`PLPRMFL`). Source:
<https://www.epa.gov/egrid>.

## 2. Storage

- **Wells** — real permitted/operating CO₂ storage wells: EPA **Class VI** and **Subpart RR** geologic
  sequestration (from the BiCRS dataset, EPA GHGRP / UIC). Explicitly **not Class III** (solution
  mining / brine disposal, unrelated to CO₂). 36 wells, the default routing target; permit status
  (operational / issued / draft / pending) sets a confidence tier, and the default is the nearest
  *firm* (operational or issued) well.
- **Basins** — NATCARB saline storage formation polygons. A plant inside a basin can store **on-site**;
  basins are treated as **unconstrained capacity** (the "assume wells aren't the binding constraint"
  view the toggle provides).

## 3. Transport routing & cost

Reuses the BiCRS multimodal least-cost engine (`scripts/transport_common.py`): a graph of storage
wells, rail terminals, coastal ports, river corridors, and — added here — **existing CO₂ trunk
pipelines**. A Dijkstra solve from each plant returns the cheapest route to storage.

Mode cost model (`$/tonne·km`, per-tonne handling, detour factor):

| Mode | $/t·km | handling | notes |
|------|--------|----------|-------|
| `pipeline` (existing trunk) | 0.008 | 0.5 | already-built, shared, large throughput — **below barge**, so routes ride it when available |
| `pipeline_new` (dedicated spur) | 0.05 | 2.0 | new line a plant builds to reach storage; **flow-scaled** per plant (below) |
| truck | 0.12 | 2.0 | fallback (+ liquefaction) |
| rail | 0.035 | 4.0 | fallback (+ liquefaction) |
| barge | 0.012 | 4.0 | fallback (+ liquefaction) |
| ship | 0.015 | 5.0 | fallback (+ liquefaction) |

**Existing CO₂ pipelines** (`data/geo/co2_pipelines_us.json`) — curated waypoint geometry of the major
US trunk lines (Cortez, Sheep Mountain, Bravo, Central Basin, Canyon Reef in the Permian; Denbury
Green / NEJD / Free State on the Gulf Coast; Greencore and Beulah–Weyburn in the Rockies/Williston).
Approximate, traced from public maps (Global Energy Monitor CO₂ pipeline tracker; NETL/EIA), not
surveyed centerlines.

**New-pipeline cost is flow-scaled** (`build_transport.py`): `rate × (M / 2 Mtpa)^−0.4`, clamped
[0.7×, 4×]. Calibrated so a ~2–3 Mt/yr plant building a ~160 km line lands near NETL's base case
(~$11/tCO₂ for 3.2 Mt/yr over 160 km). Economies of scale: small plants pay more per tonne.

**Liquefaction** — `$25/tCO₂` is added once **only when the chosen route uses a non-pipeline mode**
(truck/rail/barge/ship move liquefied/refrigerated CO₂); pure-pipeline routes move dense-phase CO₂ and
skip it.

## 4. Storage cost

Flat **$10/tCO₂** onshore saline injection + monitoring — within the **FECM/NETL CO₂ Saline Storage
Cost Model** screening range ($8–11/tCO₂; e.g. Frio TX $5.97, Wolfcamp NM $7.32, Arbuckle OK $9.71,
Lower Tuscaloosa LA $10.31). In-basin (on-site) storage uses a nominal $3/tCO₂ short gathering +
compression instead of long-haul transport.

## 5. What's excluded / caveats

- **Capture cost is excluded.** The headline is transport + storage only; an indicative NGCC capture
  cost is ~$50–70/tCO₂ on top.
- Routing is **great-circle, screening-level**; pipeline geometry is curated/approximate.
- Per-tonne·km costs are flow-independent for the engine's path selection (only `pipeline_new` is
  flow-scaled in the reported cost). For prioritisation, not project design.

## Sources

- EPA eGRID2023 — <https://www.epa.gov/egrid>
- FECM/NETL CO₂ Transport Cost Model (2023/2024); "CO₂ Transport and Storage Costs in NETL Studies"
- FECM/NETL CO₂ Saline Storage Cost Model (2024)
- Global Energy Monitor CO₂ pipeline tracker; NATCARB / NETL saline storage assessments
