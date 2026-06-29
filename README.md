# Gas + CCS Atlas

An interactive map of **every existing US natural-gas power plant** with the **least-cost CO₂
transport + geologic storage** for each — the point-source companion to the BiCRS Atlas.

## 🌎 Live map

**<https://hausfath.github.io/gas_ccs_map/src/index.html>**

## Open it locally

Double-click **`src/index.html`** (or open it in any browser). No server, no build step, works
offline — all data is bundled as JavaScript.

## What you can do

- See all ~1,700 existing US gas power plants (EPA eGRID2023), sized by annual CO₂ and **colored by
  the CO₂ transport + storage cost to the nearest existing storage well**.
- Overlay **CO₂ storage wells** (EPA Class VI / Subpart RR — real permitted/operating sequestration
  sites) and **NATCARB saline storage basins**.
- **Click any plant** for a panel with its capacity, generation, annual CO₂, and the **least-cost
  route + $/tCO₂** to store it. Toggle the destination between the **nearest existing well** (default)
  and the **nearest geologic basin** — the route on the map and the cost update instantly.
- CO₂ is routed by a multimodal least-cost engine that **rides existing CO₂ trunk pipelines**
  (Cortez, Bravo, Sheep Mountain, Central Basin, Denbury Green/NEJD, Greencore, …) where cheapest,
  with truck / rail / barge as the fallback. **No new pipelines are built** — the cost reflects each
  plant's access to *existing* infrastructure.

## Build the data

```bash
python scripts/build_gas_plants.py      # EPA eGRID2023 → data/processed/gas_plants.json
python scripts/build_transport.py        # per-plant least-cost route to nearest well + basin
python scripts/bundle_data.py            # → src/data_bundle.js (window globals)
```

`scripts/build_gas_plants.py` expects `data/raw/egrid2023_data_rev2.xlsx`
(from <https://www.epa.gov/egrid/detailed-data>).

## Cost model

`scripts/transport_common.py` (multimodal least-cost graph + the CO₂ pipeline mode) and
`scripts/build_transport.py` (per-plant solve + flow-scaled new-pipeline cost). See
**`docs/METHODOLOGY.md`** for parameters and sources. Costs are screening-level and **exclude
capture** (transport + storage only).

## Layout

- `src/` — frontend (vanilla JS + vendored Leaflet, `app.js`, `index.html`, `styles.css`).
- `data/processed/` — `gas_plants.json`, `wells_us.json`, `storage_basins.json`, `transport.json`.
- `data/geo/` — `us_states.js` (basemap), `geometry_basins.js`, `co2_pipelines_us.json`,
  `transport_nodes_us.json`.
- `scripts/` — Python build pipeline.

Reuses datasets and the transport engine from the BiCRS Atlas.
