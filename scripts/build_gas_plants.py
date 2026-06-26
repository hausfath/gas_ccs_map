#!/usr/bin/env python3
"""
Build data/processed/gas_plants.json from the EPA eGRID2023 plant file (PLNT23).

eGRID2023 (rev2, 2025-06-12): https://www.epa.gov/egrid/detailed-data
Plant sheet header is on the 2nd row; data starts on row 3. We keep gas-fired plants
(plant primary fuel category PLFUELCT == "GAS") with valid coordinates and CO2 > 0.

eGRID plant annual CO2 (PLCO2AN) is in SHORT TONS; we convert to tonnes for the map.
"""
import json
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "..", "data", "raw", "egrid2023_data_rev2.xlsx")
OUT = os.path.join(HERE, "..", "data", "processed", "gas_plants.json")

SHORT_TON_TO_TONNE = 0.90718474


def num(v):
    try:
        f = float(v)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


def main():
    df = pd.read_excel(RAW, sheet_name="PLNT23", header=1)
    gas = df[df["PLFUELCT"] == "GAS"].copy()

    plants = []
    for _, r in gas.iterrows():
        lat, lon = num(r["LAT"]), num(r["LON"])
        co2_short_tons = num(r["PLCO2AN"])
        if lat is None or lon is None or not co2_short_tons or co2_short_tons <= 0:
            continue
        co2_tonnes = co2_short_tons * SHORT_TON_TO_TONNE
        cap = num(r["NAMEPCAP"])
        gen = num(r["PLNGENAN"])
        plants.append({
            "id": int(r["ORISPL"]),
            "name": str(r["PNAME"]).strip(),
            "state": str(r["PSTATABB"]).strip(),
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "capacity_mw": round(cap, 1) if cap is not None else None,
            "co2_mtpa": round(co2_tonnes / 1e6, 4),
            "generation_mwh": round(gen) if gen is not None else None,
            "primary_fuel": str(r["PLPRMFL"]).strip(),   # e.g. NG, OG, PG (specific gas fuel)
        })

    plants.sort(key=lambda p: p["co2_mtpa"], reverse=True)
    with open(OUT, "w") as f:
        json.dump(plants, f, separators=(",", ":"))

    total_co2 = sum(p["co2_mtpa"] for p in plants)
    print(f"wrote {len(plants)} gas plants -> {OUT}")
    print(f"total CO2 = {total_co2:,.1f} Mt/yr; total capacity = "
          f"{sum(p['capacity_mw'] or 0 for p in plants):,.0f} MW")
    print("top 5 by CO2:")
    for p in plants[:5]:
        print(f"  {p['co2_mtpa']:6.2f} Mt  {p['capacity_mw']:>7} MW  {p['state']}  {p['name']}")


if __name__ == "__main__":
    main()
