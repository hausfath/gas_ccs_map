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

**No new pipelines are built.** A plant trucks its CO₂ onto existing infrastructure — directly to a
well, or onto an existing CO₂ pipeline / rail terminal / port / river — then rides it. Greenfield CO₂
pipeline construction is out of scope, so the cost honestly reflects how far each plant is from
*existing* transport and storage.

Mode cost model (`$/tonne·km`, per-tonne handling, detour factor):

| Mode | $/t·km | handling | notes |
|------|--------|----------|-------|
| `pipeline` (existing trunk) | 0.008 | 0.5 | already-built, shared, large throughput — **below barge**, so routes ride it when available |
| truck | 0.12 | 2.0 | first/last mile onto infrastructure, or direct (+ liquefaction) |
| rail | 0.035 | 4.0 | fallback (+ liquefaction) |
| barge | 0.012 | 4.0 | fallback (+ liquefaction) |
| ship | 0.015 | 5.0 | fallback (+ liquefaction) |

**Existing CO₂ pipelines** (`data/geo/co2_pipelines_us.json`) — curated waypoint geometry of the major
US trunk lines (Cortez, Sheep Mountain, Bravo, Central Basin, Canyon Reef in the Permian; Denbury
Green / NEJD / Free State on the Gulf Coast; Greencore and Beulah–Weyburn in the Rockies/Williston).
Approximate, traced from public maps (Global Energy Monitor CO₂ pipeline tracker; NETL/EIA), not
surveyed centerlines. The `pipeline` per-t-km is anchored to large-network NETL/ZEP economies of scale.

**Liquefaction** — `$25/tCO₂` is added once **when the chosen route uses a non-pipeline mode**
(truck/rail/barge/ship move liquefied/refrigerated CO₂); a pure-pipeline route moves dense-phase CO₂
and skips it. (Without new pipelines, most routes truck onto infrastructure first, so this usually
applies.)

## 4. Storage cost

Flat **$10/tCO₂** onshore saline injection + monitoring — within the **FECM/NETL CO₂ Saline Storage
Cost Model** screening range ($8–11/tCO₂; e.g. Frio TX $5.97, Wolfcamp NM $7.32, Arbuckle OK $9.71,
Lower Tuscaloosa LA $10.31). In-basin (on-site) storage uses a nominal $3/tCO₂ short gathering +
compression instead of long-haul transport.

## 5. What's excluded / caveats

- **Capture cost is excluded.** The headline is transport + storage only; an indicative NGCC capture
  cost is ~$50–70/tCO₂ on top.
- **No new pipelines** — the model only uses existing infrastructure, so a plant far from any existing
  pipeline/rail/well shows a high (truck-dominated) cost. That is the intended, conservative signal.
- Routing is **great-circle, screening-level**; pipeline geometry is curated/approximate. Per-tonne·km
  costs are flow-independent. For prioritisation, not project design.

## Sources

- EPA eGRID2023 — <https://www.epa.gov/egrid>
- FECM/NETL CO₂ Transport Cost Model (2023/2024); "CO₂ Transport and Storage Costs in NETL Studies"
- FECM/NETL CO₂ Saline Storage Cost Model (2024)
- Global Energy Monitor CO₂ pipeline tracker; NATCARB / NETL saline storage assessments
