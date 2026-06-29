/* ============================================================
   Gas + CCS Atlas — application logic.
   Tile-free thematic map of every existing US natural-gas power plant (EPA eGRID).
   Click a plant → least-cost CO₂ transport + storage to the nearest existing storage
   well (default) or nearest geologic basin (toggle), with the route drawn on the map.
   Works offline by double-clicking index.html (no server); all data is bundled as
   window globals (data_bundle.js, us_states.js, geometry_basins.js).
   ============================================================ */
(function () {
  "use strict";

  // ---------- helpers ----------
  function fmt(v) {
    if (v == null || isNaN(v)) return "—";
    if (v >= 1000) return (v / 1000).toFixed(1) + "k";
    if (v >= 100) return v.toFixed(0);
    if (v >= 10) return v.toFixed(1);
    if (v >= 1) return v.toFixed(2);
    return v.toFixed(3);
  }
  function money(v) { return v == null ? "—" : "$" + (v >= 100 ? v.toFixed(0) : v.toFixed(1)); }
  function cap1(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : "—"; }
  function clamp(lo, v, hi) { return Math.max(lo, Math.min(hi, v)); }

  // ---------- data ----------
  const PLANTS = window.GAS_PLANTS || [];
  const WELLS = window.WELLS || [];
  const TRANSPORT = window.TRANSPORT || {};
  const US_STATES = window.US_STATES || { type: "FeatureCollection", features: [] };
  const BASINS = window.GEO_US_BASINS || { type: "FeatureCollection", features: [] };

  // ---------- constants ----------
  // Plant fill encodes the CO₂ transport+storage cost to the NEAREST WELL (the map's default story).
  const COST_BANDS = [
    { max: 40, color: "#37c98e", label: "≤ $40 / tCO₂" },
    { max: 80, color: "#e0a838", label: "$40 – 80 / tCO₂" },
    { max: Infinity, color: "#e0603b", label: "> $80 / tCO₂" },
  ];
  const NO_DEST = "#6d7f8c";
  const WELL_COLOR = "#e85ad6";  // magenta — distinct from plant cost bands (green/amber/red) & routes

  const ROUTE_MODE = {
    pipeline: { color: "#3b9eff", label: "CO₂ pipeline (existing)", dash: null },
    truck:    { color: "#e0843b", label: "Truck",                   dash: null },
    rail:     { color: "#8a6fd4", label: "Rail",                    dash: null },
    ship:     { color: "#46b3ff", label: "Ship (coastal)",          dash: "8 5" },
    barge:    { color: "#3fb6a8", label: "Barge (river)",           dash: "8 5" },
  };

  function costColor(total) {
    if (total == null) return NO_DEST;
    return COST_BANDS.find(b => total <= b.max).color;
  }
  function tr(plant) { return TRANSPORT[String(plant.id)] || {}; }
  function destOf(plant) {
    const t = tr(plant);
    return state.dest === "basin" ? t.to_basin : t.to_well;
  }

  // ---------- state ----------
  const state = { openPlant: null, dest: "well", showRoute: true };

  const dom = {
    hovertip: document.getElementById("hovertip"),
    detail: document.getElementById("detail"),
    detailBody: document.getElementById("detail-body"),
    legend: document.getElementById("legend"),
    overlayList: document.getElementById("overlay-list"),
  };

  // ============================================================
  // Map + panes
  // ============================================================
  const map = L.map("map", {
    center: [39.5, -97.5], zoom: 4, minZoom: 3, maxZoom: 9,
    zoomControl: true, attributionControl: false,
  });
  L.control.attribution({ prefix: false })
    .addAttribution("eGRID2023 · EPA GHGRP Class VI/RR wells · NATCARB basins · NETL transport costs")
    .addTo(map);

  map.createPane("landPane").style.zIndex = 400;
  map.createPane("basinPane").style.zIndex = 410;
  map.createPane("pointPane").style.zIndex = 450;
  map.createPane("routePane").style.zIndex = 470;
  // Plants AND wells share ONE canvas renderer so Leaflet hit-tests both — two stacked canvases
  // would let only the topmost receive clicks. Wells are drawn after plants, so they sit on top.
  const pointRenderer = L.canvas({ pane: "pointPane" });
  const plantRenderer = pointRenderer;
  const wellRenderer = pointRenderer;
  const routeRenderer = L.svg({ pane: "routePane" });

  // ---------- static land basemap (non-interactive; the choropleth's stand-in) ----------
  L.geoJSON(US_STATES, {
    pane: "landPane", interactive: false,
    style: { fillColor: "#222d37", color: "#384654", weight: 0.7, fillOpacity: 1, opacity: 0.9 },
  }).addTo(map);

  // ============================================================
  // Layers
  // ============================================================
  function plantRadius(co2) { return clamp(2.5, 2.5 + Math.sqrt(co2 || 0.1) * 2.8, 14); }

  const plantsLayer = L.layerGroup();
  PLANTS.forEach(p => {
    if (p.lat == null || p.lon == null) return;
    const d = (tr(p).to_well) || null;
    const m = L.circleMarker([p.lat, p.lon], {
      renderer: plantRenderer, radius: plantRadius(p.co2_mtpa),
      fillColor: costColor(d ? d.total_usd : null), color: "#10161c",
      weight: 0.8, fillOpacity: 0.9,
    });
    m._plant = p;
    m.on("click", () => openDetailPlant(p));
    m.on("mouseover", e => showPlantTip(e, p));
    m.on("mousemove", moveHoverTip);
    m.on("mouseout", hideHoverTip);
    m.addTo(plantsLayer);
  });

  const wellsLayer = L.layerGroup();
  WELLS.forEach(w => {
    if (w.lat == null || w.lon == null) return;
    const op = w.status === "operational" ? 0.95 : w.status === "issued" ? 0.8
      : w.status === "draft" ? 0.55 : 0.35;
    const r = w.co2_mtpa ? clamp(4, 4 + Math.sqrt(w.co2_mtpa) * 2.6, 13) : 5;
    L.circleMarker([w.lat, w.lon], {
      renderer: wellRenderer, radius: r, fillColor: WELL_COLOR, color: "#2a0f24",
      weight: 1.2, fillOpacity: op,
    }).bindPopup(wellPopup(w), { maxWidth: 280 }).addTo(wellsLayer);
  });

  const basinsLayer = L.geoJSON(BASINS, {
    pane: "basinPane", interactive: false,
    style: { fillColor: "#6f93c9", color: "#7aa6ff", weight: 0.8, fillOpacity: 0.13, opacity: 0.45 },
  });

  const routeGroup = L.featureGroup();

  function wellPopup(w) {
    const cls = w.well_class === "VI/RR" ? "Geologic sequestration (Subpart RR)"
      : w.well_class === "VI" ? "Class VI (CO₂ storage)"
      : w.well_class === "V" ? "Class V" : "CO₂ storage";
    return `<b>${w.name}</b><br>${cls} · ${cap1(w.status)}<br>
      ${w.co2_mtpa ? "Reported CO₂: <b>" + fmt(w.co2_mtpa) + " Mt/yr</b><br>" : ""}${w.state || ""}
      <div class="pop-src">Source: ${w.source || "EPA"}</div>`;
  }

  // ============================================================
  // Hover tooltip
  // ============================================================
  function showPlantTip(e, p) {
    const d = tr(p).to_well;
    dom.hovertip.innerHTML = `<div class="ht-name">${p.name}</div>` +
      `<span class="ht-val">${fmt(p.co2_mtpa)} Mt CO₂/yr</span>` +
      (d ? ` · ${money(d.total_usd)}/t to well` : "");
    dom.hovertip.classList.remove("hidden");
    moveHoverTip(e);
  }
  function moveHoverTip(e) {
    dom.hovertip.style.left = e.originalEvent.clientX + "px";
    dom.hovertip.style.top = e.originalEvent.clientY + "px";
  }
  function hideHoverTip() { dom.hovertip.classList.add("hidden"); }

  // ============================================================
  // Transport route drawing
  // ============================================================
  function redrawRoute() {
    routeGroup.clearLayers();
    if (!state.showRoute || !state.openPlant) return;
    const d = destOf(state.openPlant);
    if (!d) return;
    if (!d.legs || !d.legs.length) {
      // on-site / in-basin storage: mark the plant itself
      L.circleMarker([state.openPlant.lat, state.openPlant.lon], {
        renderer: routeRenderer, radius: 11, fill: false, color: "#37c98e", weight: 2.5, dashArray: "4 4",
      }).bindTooltip("On-site geologic storage (plant sits over a saline basin)",
        { direction: "top" }).addTo(routeGroup);
      if (!map.hasLayer(routeGroup)) routeGroup.addTo(map);
      return;
    }
    d.legs.forEach(leg => {
      const m = ROUTE_MODE[leg.mode] || { color: "#aaa", label: leg.mode, dash: null };
      const line = (leg.path && leg.path.length > 1) ? leg.path : [leg.from, leg.to];
      L.polyline(line, { color: "#0e1419", weight: 7, opacity: 0.5, renderer: routeRenderer }).addTo(routeGroup);
      L.polyline(line, { color: m.color, weight: 4, opacity: 0.95, renderer: routeRenderer, dashArray: m.dash })
        .bindTooltip(`${m.label}: ${fmt(leg.km)} km`, { sticky: true }).addTo(routeGroup);
    });
    // destination marker
    const last = d.legs[d.legs.length - 1];
    if (last && last.to) {
      L.circleMarker(last.to, { renderer: routeRenderer, radius: 5, fillColor: "#fff",
        color: "#10161c", weight: 1.5, fillOpacity: 1 })
        .bindTooltip(d.dest_name || "storage", { direction: "top" }).addTo(routeGroup);
    }
    if (!map.hasLayer(routeGroup)) routeGroup.addTo(map);
  }

  // ============================================================
  // Detail panel
  // ============================================================
  function openDetailPlant(p) {
    state.openPlant = p;
    state.dest = "well";
    renderDetail();
    dom.detail.classList.remove("hidden");
    fitRoute();
  }
  function closeDetail() { dom.detail.classList.add("hidden"); state.openPlant = null; redrawRoute(); }

  function renderDetail() {
    const p = state.openPlant;
    if (!p) return;
    const t = tr(p);
    const fuel = { NG: "Natural gas", OG: "Other gas", PG: "Process gas", BFG: "Blast-furnace gas",
                   LFG: "Landfill gas" }[p.primary_fuel] || p.primary_fuel || "Gas";
    let html = `<div class="d-region">Natural gas power plant · ${p.state}</div>
      <div class="d-name">${p.name}</div>
      <div class="d-metrics">
        <div><div class="k">Nameplate capacity</div><div class="v">${fmt(p.capacity_mw)} MW</div></div>
        <div><div class="k">Annual CO₂</div><div class="v">${fmt(p.co2_mtpa)} Mt/yr</div></div>
        <div><div class="k">Net generation</div><div class="v">${p.generation_mwh != null ? fmt(p.generation_mwh / 1000) + " GWh" : "—"}</div></div>
        <div><div class="k">Primary fuel</div><div class="v">${fuel}</div></div>
      </div>`;

    // destination toggle
    html += `<div class="seg" id="dest-seg" style="margin:14px 0 12px">
      <button class="seg-btn ${state.dest === "well" ? "active" : ""}" data-dest="well">Nearest well</button>
      <button class="seg-btn ${state.dest === "basin" ? "active" : ""}" data-dest="basin">Nearest basin</button>
    </div>`;

    html += transportCard(p, t);

    html += `<p class="hint" style="margin-top:14px">Costs are <b>screening estimates</b> for CO₂
      <b>transport + geologic storage</b> only — they exclude the capture cost (an indicative NGCC
      capture cost is ~$50–70/tCO₂ on top). Routing uses <b>existing</b> CO₂ trunk pipelines plus
      truck / rail / barge — <b>no new pipelines are built</b>; great-circle screening, not surveyed
      routing.</p>`;

    dom.detailBody.innerHTML = html;

    // bind destination toggle
    dom.detailBody.querySelectorAll("#dest-seg .seg-btn").forEach(btn => {
      btn.onclick = () => { state.dest = btn.dataset.dest; renderDetail(); redrawRoute(); fitRoute(); };
    });
    redrawRoute();
  }

  function transportCard(p, t) {
    const d = state.dest === "basin" ? t.to_basin : t.to_well;
    if (!d) {
      return `<div class="chart-card"><div class="chart-title">No ${state.dest === "basin" ? "basin" : "storage well"} reachable</div>
        <div class="chart-sub">No suitable destination found within range for this plant.</div></div>`;
    }
    const modes = (d.modes || []).map(m => (ROUTE_MODE[m] || { label: m }).label).filter((v, i, a) => a.indexOf(v) === i).join(" → ");
    let sub;
    if (state.dest === "basin") {
      sub = d.in_basin
        ? `Plant sits over the <b>${d.dest_name}</b> saline basin → <b>on-site storage</b> (no long-haul transport).`
        : `Least-cost route to the <b>${d.dest_name}</b> basin · ${modes || "—"} · ${fmt(d.total_km)} km.`;
    } else {
      sub = `Least-cost route to <b>${d.dest_name}</b>${d.confidence && d.confidence !== "firm" ? ` <span class="lowsup">(${d.confidence}-permit well)</span>` : ""} · ${modes || "—"} · ${fmt(d.total_km)} km.`;
    }
    const row = (lbl, v, bold) => `<div${bold ? ' style="grid-column:1/3"' : ""}>
      <div class="k">${lbl}</div><div class="v"${bold ? ' style="font-size:18px;font-weight:800"' : ""}>${v}</div></div>`;
    let rows = row("Transport (haulage)", money(d.transport_usd) + "/t");
    if (d.liquefaction_usd > 0) rows += row("Liquefaction", money(d.liquefaction_usd) + "/t");
    rows += row("Geologic storage", money(d.storage_usd) + "/t");
    rows += row("Total transport + storage", money(d.total_usd) + "/tCO₂", true);
    rows += row("Annual cost", "$" + fmt(d.annual_usd_m) + "M/yr");
    return `<div class="chart-card">
      <div class="chart-title">CO₂ transport &amp; storage <span class="hint" style="font-weight:400">(screening)</span></div>
      <div class="chart-sub">${sub}</div>
      <div class="d-metrics">${rows}</div>
    </div>`;
  }

  function fitRoute() {
    if (state.showRoute && routeGroup.getLayers().length) {
      try { map.fitBounds(routeGroup.getBounds(), { paddingTopLeft: [360, 60], paddingBottomRight: [400, 60], maxZoom: 7 }); }
      catch (_) {}
    }
  }

  // ============================================================
  // Overlay toggles + legend
  // ============================================================
  function buildOverlays() {
    const defs = [
      { id: "plants", label: "Gas power plants", layer: plantsLayer, on: true },
      { id: "wells", label: "CO₂ storage wells", layer: wellsLayer, on: true },
      { id: "basins", label: "Saline storage basins", layer: basinsLayer, on: true },
    ];
    defs.forEach(def => {
      if (def.on) def.layer.addTo(map);
      const lbl = document.createElement("label");
      lbl.className = "chk";
      lbl.innerHTML = `<input type="checkbox" ${def.on ? "checked" : ""} /> <span class="sw sw-${def.id}"></span> ${def.label}`;
      lbl.querySelector("input").onchange = e =>
        e.target.checked ? def.layer.addTo(map) : map.removeLayer(def.layer);
      dom.overlayList.appendChild(lbl);
    });
    // route toggle
    const lbl = document.createElement("label");
    lbl.className = "chk";
    lbl.innerHTML = `<input type="checkbox" checked /> <span class="sw" style="background:#3b9eff"></span> CO₂ transport route <span class="hint" style="font-weight:400">(on click)</span>`;
    lbl.querySelector("input").onchange = e => { state.showRoute = e.target.checked; redrawRoute(); };
    dom.overlayList.appendChild(lbl);
  }

  function renderLegend() {
    let html = `<div class="legend-note" style="margin:0 0 6px">Plant fill = CO₂ transport + storage cost to the nearest <b>existing well</b>:</div>`;
    COST_BANDS.forEach(b => {
      html += `<div class="legend-row"><span class="box" style="background:${b.color}"></span>${b.label}</div>`;
    });
    html += `<div class="legend-note" style="margin-top:8px">Circle size ∝ annual CO₂. Switch a plant's destination to the nearest geologic basin in its panel.</div>`;
    html += `<div class="legend-row" style="margin-top:10px"><span class="box" style="background:${WELL_COLOR};border-radius:50%"></span>Existing CO₂ storage well</div>`;
    html += `<div class="legend-row"><span class="box" style="background:rgba(111,147,201,.3);border:1px solid #7aa6ff"></span>Saline storage basin</div>`;
    html += `<div class="legend-note" style="margin-top:10px">Route modes:</div>`;
    ["pipeline", "truck", "rail", "barge"].forEach(m => {
      html += `<div class="legend-row"><span class="box" style="background:${ROUTE_MODE[m].color}"></span>${ROUTE_MODE[m].label}</div>`;
    });
    dom.legend.innerHTML = html;
  }

  // ============================================================
  // Methodology modal
  // ============================================================
  const METHOD_HTML = `
    <h2>Methodology &amp; sources</h2>
    <p>This is a screening tool for siting carbon capture &amp; storage (CCS) at existing US natural-gas
    power plants. It answers: for each plant, where would its captured CO₂ go, and what would
    <b>transport + geologic storage</b> cost?</p>
    <h3>Gas plants</h3>
    <p>All ${PLANTS.length.toLocaleString()} existing US natural-gas-fired power plants from
    <a href="https://www.epa.gov/egrid">EPA eGRID2023</a> (plant primary fuel = gas), with nameplate
    capacity, net generation and annual CO₂ (converted from short tons to tonnes).</p>
    <h3>Storage</h3>
    <p><b>Wells</b> — real permitted/operating CO₂ storage wells (EPA Class VI &amp; Subpart RR geologic
    sequestration; <i>not</i> Class III). <b>Basins</b> — NATCARB saline storage formation polygons; a
    plant inside a basin can store on-site, and basins are treated as unconstrained capacity (the
    "assume wells aren't the binding constraint" view).</p>
    <h3>Transport</h3>
    <p>The CO₂ is routed by a least-cost engine over real <b>existing</b> CO₂ trunk pipelines (Cortez,
    Bravo, Sheep Mountain, Central Basin, Denbury Green/NEJD, Greencore, …), priced below barge so the
    route rides them when available, with truck / rail / barge as the fallback. <b>No new pipelines are
    built</b> — a plant trucks its CO₂ onto existing infrastructure or directly to storage. Liquefaction
    ($25/tCO₂) is added once when a non-pipeline mode is used; a pure-pipeline route skips it.</p>
    <h3>Storage cost</h3>
    <p>Flat ~$10/tCO₂ onshore saline injection + monitoring (NETL FECM/NETL saline screening range
    $8–11/tCO₂). <b>Capture cost is excluded</b> — add ~$50–70/tCO₂ for an indicative NGCC capture
    figure to get an all-in number.</p>
    <p class="pop-src">Costs are great-circle, screening-level estimates with real uncertainty; pipeline
    geometry is curated/approximate, not surveyed. For prioritisation, not project design.</p>`;

  function openMethod() { document.getElementById("method-body").innerHTML = METHOD_HTML;
    document.getElementById("method-modal").classList.remove("hidden"); }
  function closeMethod() { document.getElementById("method-modal").classList.add("hidden"); }

  // ============================================================
  // Wire up + init
  // ============================================================
  document.getElementById("detail-close").onclick = closeDetail;
  document.getElementById("open-method").onclick = openMethod;
  document.getElementById("method-close").onclick = closeMethod;
  document.getElementById("method-modal").onclick = e => {
    if (e.target.id === "method-modal") closeMethod();
  };
  map.on("mouseout", hideHoverTip);

  buildOverlays();
  renderLegend();

  const totalCO2 = PLANTS.reduce((s, p) => s + (p.co2_mtpa || 0), 0);
  document.getElementById("stat-co2").textContent = Math.round(totalCO2);

  // fit to the contiguous US (exclude AK/HI/PR from the initial view)
  map.fitBounds([[24.5, -125], [49.5, -66.5]], { paddingTopLeft: [340, 0] });

  // debug / embedding handle
  window.GASCCS = { map, state, openDetailPlant, closeDetail, redrawRoute,
    setDest(d) { state.dest = d; renderDetail(); redrawRoute(); },
    plantByName(n) { return PLANTS.find(p => p.name === n); } };
})();
