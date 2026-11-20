import { button, choices, h, icon, section, segmented, slider } from "../controls.js";
import { $, alert, busy, groups, loading, rpc, rt, showError, state, toV } from "../core.js";
import { heldElsewhere, holderOf, keepHold, live, peersIdle, tellGroup } from "../live.js";
import { GEN_TABS, TABS, m, syncGenerate } from "./tabs.js";
import { setTool } from "./tools.js";
import { currentNet, loadGeometry, loadNets, optionsOf } from "./world.js";
import { flyTo } from "../view/camera.js";
import { deleteCity, turnCity } from "../view/edit.js";
import { clear, drawCityMark, drawDistricts } from "../view/scene.js";
import { sampleZ } from "../view/terrain.js";

// ------------------------------------------------------------------ cities pane
const STYLE_OPTS = [["mixed", "Mixed", "mixed"], ["grid", "Grid", "grid"], ["organic", "Organic", "wave"]];
// the urban planner's result for one city (urban.py): population, what it needs, what it got, coverage by road
// the city's airport (airport.py): type, runway heading, what it got, its checks
function cityAirport(a) {
  if (!a) return h("span");
  const label = { general_aviation: "General aviation airfield", regional: "Regional airport", international: "International airport",
    cargo: "Cargo airport", military: "Military airbase" }[a.type] || a.type;
  const r = a.report || {};
  return h("div", { style: "margin-top: 10px" },
    h("div", { class: "rn-field__label" }, label),
    h("div", { class: "rn-kv" },
      h("span", {}, "Runways"), h("span", {}, `${r.runways || 0} · heading ${Math.round(a.heading)}°`),
      h("span", {}, "Aircraft stands"), h("span", {}, String(r.stands || 0)),
      h("span", {}, "Ground levelled"), h("span", {}, `${a.grading_m ?? 0} m average`)),
    h("div", { class: "dk-row", style: "margin-top: 8px; flex-wrap: wrap" },
      ...(a.facilities || []).slice(0, 14).map((x) => h("span", { class: "rn-tag" }, x.what.split(" (")[0]))),
    r.ok === false ? h("div", { class: "rn-hint", style: "margin-top: 6px; color: var(--amber)" },
      "Check: " + (r.problems || []).slice(0, 3).join("; ")) : h("span"));
}
// the waterfront planner's result for one city (waterfront.py): shoreline by use, developments, validator
function cityWater(w) {
  if (!w || !(w.segments || []).length) return h("span");
  const by = Object.entries(w.shoreline_m || {}).sort((a, b) => b[1] - a[1]);
  const devs = {};
  for (const d of w.developments || []) devs[d.type] = (devs[d.type] || 0) + 1;
  return h("div", { style: "margin-top: 10px" },
    h("div", { class: "rn-field__label" }, "Waterfront"),
    h("div", { class: "rn-kv" }, ...by.flatMap(([k, m]) => [h("span", {}, k[0].toUpperCase() + k.slice(1) + " shore"),
      h("span", {}, `${m.toLocaleString()} m`)])),
    h("div", { class: "dk-row", style: "margin-top: 8px; flex-wrap: wrap" },
      ...Object.entries(devs).map(([k, n]) => h("span", { class: "rn-tag" }, n > 1 ? `${k} × ${n}` : k))),
    ...(w.shipyards || []).map((y) => h("div", { class: "rn-hint", style: "margin-top: 6px" },
      `${y.type[0].toUpperCase() + y.type.slice(1)} shipyard · ${y.site_m[0]} × ${y.site_m[1]} m`
      + (y.docks ? ` · ${y.docks} dry dock${y.docks > 1 ? "s" : ""}` : "") + (y.report.ok ? "" : ` · check: ${y.report.problems[0]}`))),
    w.report && !w.report.ok ? h("div", { class: "rn-hint", style: "margin-top: 6px; color: var(--amber)" },
      "Check: " + w.report.problems.slice(0, 3).join("; ")) : h("span"));
}
const CAT_LABEL = { commercial: "Commercial", residential: "Residential", education: "Education", government: "Government & emergency",
  industrial: "Industrial", recreation: "Recreation", utilities: "Utilities", transportation: "Transport" };
const COVER_LABEL = { fire_station: "Fire (4 min drive)", police_precinct: "Police (6 min)", ambulance: "Ambulance (5 min)",
  neighbourhood_park: "Park (500 m walk)", convenience: "Corner shop (1 km walk)", neighbourhood_center: "Grocery (4 min)" };
function cityPlan(p) {
  if (!p) return h("div", { class: "rn-hint", style: "margin-top: 8px" }, "No city plan yet: generate again to plan its services.");
  const pop = p.population || {};
  const counts = Object.entries(p.counts || {}).sort((a, b) => b[1] - a[1]);
  const pct = (v) => `${Math.round(v * 100)} %`;
  return h("div", { style: "margin-top: 10px" },
    h("div", { class: "rn-field__label" }, "City plan"),
    h("div", { class: "rn-kv" },
      h("span", {}, "Population"), h("span", {}, (pop.total || 0).toLocaleString()),
      h("span", {}, "Households"), h("span", {}, (pop.households || 0).toLocaleString()),
      h("span", {}, "Jobs"), h("span", {}, (pop.total_jobs || 0).toLocaleString()),
      ...Object.entries(p.by_category || {}).filter(([, n]) => n).flatMap(([k, n]) => [h("span", {}, CAT_LABEL[k] || k), h("span", {}, `${n} sites`)])),
    h("div", { class: "rn-field__label", style: "margin-top: 8px" }, "Coverage by road"),
    h("div", { class: "rn-kv" }, ...Object.entries(p.coverage || {}).flatMap(([k, v]) => [h("span", {}, COVER_LABEL[k] || k),
      h("span", { style: v < 0.85 ? "color: var(--amber)" : "" }, pct(v))])),
    h("div", { class: "dk-row", style: "margin-top: 8px; flex-wrap: wrap" },
      ...counts.map(([k, n]) => h("span", { class: "rn-tag" }, `${k.replace(/_/g, " ")} × ${n}`))),
    ...(p.unmet || []).length ? [h("div", { class: "rn-hint", style: "margin-top: 6px" },
      "Not met: " + p.unmet.map((u) => u.type.replace(/_/g, " ")).join(", ") + " (no free land of that size where it is needed)")] : []);
}
// The right side is the details of what is picked: a picked city shows only that city (and the block picked in
// it); with nothing picked it is the open section's own settings.
function showSide() {
  const c = state.cities.find((x) => x.id === state.cityPick);
  const city = !!c && state.tab !== "pcg" && state.tab !== "edit";
  for (const [t] of TABS) $(`pane-${t}`)?.classList.toggle("is-open", !city && t === state.tab);
  $("pane-city")?.classList.toggle("is-open", city);
  const [, label = "", ic = "world"] = TABS.find((t) => t[0] === state.tab) || [];
  if (city) {
    const back = h("button", { type: "button", class: "rn-mini", title: `Close ${c.name}: back to ${label}`, "aria-label": `Close ${c.name}` }, icon("x", 12));
    back.addEventListener("click", () => selectCity(""));
    $("paneTitle").replaceChildren(icon("city", 16), h("span", { style: "flex: 1" }, c.name), back);
  } else $("paneTitle").replaceChildren(icon(ic, 16), h("span", {}, label));
  $("foot").hidden = !city && !GEN_TABS.has(state.tab);
  syncGenerate();  // the foot button says what it redoes: this city, the ground or everything
}
const ZONE_LABEL = { downtown: "Downtown", normal: "Normal", housing: "Housing", industrial: "Industry" };
const insidePoly = (poly, x, y) => {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [ax, ay] = poly[i], [bx, by] = poly[j];
    if ((ay > y) !== (by > y) && x < ax + (y - ay) * (bx - ax) / (by - ay)) hit = !hit;
  }
  return hit;
};
// the block picked in a city: remembered by a spot inside it, so it is still picked after the city is rebuilt
const pickedBlock = (c) => (state.block?.city === c?.id && (c.blocks || []).find((b) => insidePoly(b[4], state.block.x, state.block.y))) || null;
function blockSection(c) {
  const b = pickedBlock(c);
  if (!b) {
    return section("Block", h("div", { class: "rn-hint" }, (c.blocks || []).length
      ? "Click a block of this city on the map to see what it is and change it."
      : "Regenerate the world once: then every block of this city can be clicked."));
  }
  const own = (c.zones || []).find((z) => insidePoly(b[4], z.x, z.y));
  const kinds = state.blockKinds || {};
  return section("Block",
    h("div", { class: "rn-kv" },
      h("span", {}, "Zone"), h("span", {}, ZONE_LABEL[b[3]] || b[3]),
      h("span", {}, "Laid out as"), h("span", {}, kinds[b[2]] || (b[2] === "collector" ? "Superblock: one winding street" : b[2])),
      h("span", {}, "Planned by"), h("span", {}, own ? "You" : "The city")),
    segmented({ label: "Zone", value: b[3], options: (state.blockZones || []).map((z) => [z, ZONE_LABEL[z] || z]),
      onChange: (v) => setBlock(c, b, { zone: v }) }).el,
    choices({ label: "Laid out as", value: b[2], options: Object.entries(kinds).map(([id, label]) =>
      [id, label, "", id === "park" ? "terrain" : id === "parcels" ? "grid" : "city"]), onChange: (v) => setBlock(c, b, { kind: v }) }).el,
    ...(b[2] === "park" ? [] : [slider({ label: "Lots left green", min: 0, max: 100, step: 10, value: Math.round((b[5] || 0) * 100),
      fmt: (v) => `${Math.round(v)} %`, hint: `${lotsIn(c, b).length} lots in this block (outlined on the map). This share of them is planted with trees instead of built on`,
      onChange: (v) => setBlock(c, b, { green: v / 100 }) }).el]),
    ...(own ? [button("Back to the city's own plan", "refresh", () => setBlock(c, b, { reset: true }))] : []),
    h("div", { class: "rn-hint" }, "Only this block is laid out again; every other block of the city stays as it is."));
}
const lotsIn = (c, b) => (c.lots || []).filter((r) => insidePoly(b[4], r[0], r[1]));
function setBlock(c, b, change) {
  if (heldElsewhere(c)) { renderWorldCity(); return; }
  const own = (c.zones || []).find((z) => insidePoly(b[4], z.x, z.y)) || {};
  const next = { x: b[0], y: b[1], kind: change.reset ? "" : change.kind ?? own.kind ?? "", zone: change.reset ? "" : change.zone ?? own.zone ?? "" };
  const green = change.reset ? null : change.green ?? own.green ?? null;
  if (green != null) next.green = green;
  // the panel shows it at once; the block itself is laid out with the next rebuild
  c.zones = [...(c.zones || []).filter((z) => !insidePoly(b[4], z.x, z.y)), ...(next.kind || next.zone || green != null ? [next] : [])];
  if (next.kind) b[2] = next.kind;
  if (next.zone) b[3] = next.zone;
  if (green != null) b[5] = green;
  renderWorldCity();
  queueEdit(`Rebuilding a block of ${c.name}`, () => rpc("city_save", { net_id: state.net, city: c.id, zones: JSON.stringify(next) }));
}
// The picked city's details (the same saves as the Cities section)
function renderWorldCity() {
  showSide();
  const pane = $("pane-city");
  const c = state.cities.find((x) => x.id === state.cityPick);
  if (!pane) return;
  if (!c) return pane.replaceChildren();
  const d = c.districts || {}, editing = state.tool === "city";
  pane.replaceChildren(
    section("City",
      segmented({ label: "Road style", value: c.style, options: STYLE_OPTS, onChange: (v) => saveCity(c, { style: v }) }).el,
      slider({ label: "Radius", min: 150, max: 5000, step: 10, value: Math.round(c.radius / 100), fmt: m,
        hint: "Rebuilds this city at its new size; its neighbours move away if they would overlap",
        onChange: (v) => saveCity(c, { radius_m: v }) }).el,
      slider({ label: "Turned", min: 0, max: 359, step: 1, value: Math.round(c.turn_deg ?? 0), fmt: (v) => `${Math.round(v)}°`,
        hint: "Turns the whole city: its street grid, its edge and its industrial side",
        onChange: (v) => saveCity(c, { turn_deg: v }) }).el,
      h("div", { class: "dk-row", style: "margin-top: 8px; flex-wrap: wrap" },
        button(editing ? "Done editing on the map" : "Edit on the map", "edit", () => setTool(editing ? "select" : "city"),
          editing ? "primary" : "secondary", "Move it, turn it and shape its edge with the handles on the map (E)"),
        ...(c.shape?.length ? [button("Reset its edge", "refresh", () => saveCity(c, { shape: "reset" }), "secondary",
          "Back to the edge its seed gives it")] : []))),
    blockSection(c),
    section("What it has", ...cityContents(c)),
    section("Districts", h("div", { class: "rn-kv" },
      h("span", {}, "Downtown"), h("span", {}, `${Math.round((d.downtown?.radius || 0) / 100)} m`),
      h("span", {}, "Normal city"), h("span", {}, `${Math.round((d.normal?.radius || 0) / 100)} m`),
      h("span", {}, "Housing"), h("span", {}, `${Math.round((d.housing?.radius || 0) / 100)} m`))),
    section("Plan", cityPlan(c.plan), cityWater(c.waterfront), cityAirport(c.airport)));
  const hd = holderOf(c.id);
  if (hd) {  // a teammate has this city: everything about it can be read here, nothing changed
    for (const el of pane.children) el.inert = true;
    pane.prepend(h("div", { class: "rn-held" }, icon("lock", 16),
      h("span", {}, `${hd.name} has ${c.name}. You can look round it; it is yours to change when they let go.`)));
  }
}
function renderCitiesPane() {
  renderWorldCity();
  const pane = $("pane-cities");
  if (!state.cities.length) {
    pane.replaceChildren(h("div", { class: "dk-empty" }, h("div", { class: "dk-empty__title" }, "No cities yet"), "Generate a world to get cities with their own roads and districts."));
    return;
  }
  const cards = state.cities.map((c) => {
    const style = segmented({ label: "Road style", value: c.style, options: STYLE_OPTS, onChange: (v) => saveCity(c, { style: v }) });
    const radius = slider({ label: "Radius", min: 150, max: 5000, step: 10, value: Math.round(c.radius / 100), fmt: m,
      onChange: (v) => saveCity(c, { radius_m: v }) });
    const d = c.districts || {};
    const b = c.buildings || {};
    const sets = ["downtown", "normal", "housing"].map((k) => [k, (b[k] || []).length]);
    return h("div", { class: "rn-card", "data-live-id": `city:${c.id}` },
      h("div", { class: "rn-card__head" }, icon("city", 16), h("span", { class: "rn-card__name" }, c.name), h("span", { class: "rn-tag" }, `${Math.round(c.radius / 100)} m`)),
      style.el, radius.el,
      h("div", { class: "rn-kv" },
        h("span", {}, "Downtown"), h("span", {}, `${Math.round((d.downtown?.radius || 0) / 100)} m`),
        h("span", {}, "Normal city"), h("span", {}, `${Math.round((d.normal?.radius || 0) / 100)} m`),
        h("span", {}, "Housing"), h("span", {}, `${Math.round((d.housing?.radius || 0) / 100)} m`),
        ...sets.flatMap(([k, n]) => [h("span", {}, `${k[0].toUpperCase() + k.slice(1)} buildings`), h("span", {}, n ? `${n} meshes` : "Not set")])),
      cityPlan(c.plan), cityWater(c.waterfront), cityAirport(c.airport));
  });
  pane.replaceChildren(section(`Cities · ${state.cities.length}`, ...cards),
    h("div", { class: "rn-hint" }, "Each city keeps its own roads actor, style and building sets. Changing one places that city again; the others stay as they are."));
}
// ------------------------------------------------------------------ city edits: queued into one rebuild
// Moving, resizing, restyling, adding and deleting cities never rebuilds by itself: each edit shows at once and
// waits until the person presses Apply, so they can make several changes first (it rebuilt 1.2 s after the last
// one, mid-edit, Nov 20 2026). One rebuild takes them all; Discard drops them and shows the world as it is.
const edits = { list: [], running: false };
function queueEdit(title, apply, after) {
  edits.list.push({ title, apply, after });
  const n = edits.list.length;
  if (edits.running) {
    busy(loading.title || title, { text: `${n} more change${n > 1 ? "s" : ""} waiting: Apply when this rebuild is done`,
      step: loading.step, of: loading.of });
    return;
  }
  showPending();
}
// the waiting changes: Apply rebuilds once with all of them, Discard puts the world back as it is
function showPending() {
  const n = edits.list.length;
  if (!n) { busy(""); return; }
  busy("");
  $("busy").replaceChildren(h("div", { class: "rn-load", role: "status" },
    h("div", { class: "rn-load__title" }, h("span", { class: "rn-load__name" }, `${n} change${n > 1 ? "s" : ""} waiting`)),
    h("div", { class: "rn-load__step" }, "Nothing is rebuilt until you press Apply."),
    h("div", { class: "rn-btns", style: "margin-top: 8px" },
      button("Apply", "check", () => runEdits(), "primary", "Rebuild the world with every waiting change"),
      button("Discard", "x", discardEdits, "secondary", "Drop the waiting changes"))));
}
async function discardEdits() {
  edits.list = [];
  busy("");
  await loadGeometry(true);  // the world as it is: undoes what the panel showed
  renderWorldCity();
}
async function runEdits() {
  if (edits.running || !edits.list.length) return;
  edits.running = true;
  try {
    {
      const batch = edits.list.splice(0);
      const saved = [], failed = [];
      for (const e of batch) {  // saved only now: never while a rebuild runs (it would write the old city back)
        busy(e.title, { text: "Saving the change…" });
        const out = await e.apply();
        if (out?.ok === false) failed.push(out.error);
        else saved.push([e, out]);
      }
      if (saved.length) await regenerate(saved.length > 1 ? `Rebuilding: ${saved.length} changes` : saved[0][0].title);
      else { busy(""); await loadGeometry(true); }  // nothing changed: the world as it is (undoes what the panel showed)
      if (failed.length) showError(failed.join(" "));
      for (const [e, out] of saved) e.after?.(out);
    }
  } finally {
    edits.running = false;
    if (edits.list.length) showPending();  // made while it ran: they wait for Apply too
  }
}
function saveCity(c, change) {
  if (heldElsewhere(c)) { renderWorldCity(); return; }  // a teammate has this city: nothing of it changes from here
  // the ring and the panel follow at once; the city itself is rebuilt with the next rebuild
  if (change.radius_m) c.radius = change.radius_m * 100;
  if (change.style) c.style = change.style;
  if (change.x != null && change.y != null) { c.x = change.x; c.y = change.y; }
  if (change.turn_deg != null) turnCity(c, change.turn_deg);
  if (change.shape === "reset") delete c.shape;
  if (state.view.districts) { clear(groups.districts); drawDistricts(); }
  if (change.turn_deg != null || change.shape) renderWorldCity();
  drawCityMark();
  rt.needsDraw = true;
  queueEdit(`Rebuilding ${c.name}`, () => rpc("city_save", { net_id: state.net, city: c.id, ...change }));
}
// generate the open world again with the cities it has (moved, added, removed, resized), on the ground it uses:
// never new terrain for a world that sits on scanned ground
async function regenerate(title) {
  const n = currentNet();
  if (!n) return;
  const gen = n.generated || {};
  await peersIdle(title);  // a teammate's rebuild of this world first: two at once would undo each other
  state.generating = true;
  busy(title, { text: "Starting…", step: 0, of: 1 });
  tellGroup();
  const out = await rpc("generate", { name: n.name || state.net, seed: gen.seed ?? state.gen.seed, cities: "",
    size_km: gen.size_km ?? state.gen.size_km, relief_m: gen.relief_m ?? state.gen.relief_m,
    ground_from: gen.ground_from || "", options: JSON.stringify(optionsOf(state.gen)) });
  state.generating = false;
  tellGroup();
  live.shareNext = out.ok !== false;  // what this rebuild changes goes straight to teammates' screens
  if (state.lifted) state.lifted.roads = false;  // a moved city: its new roads are in, its buildings still to come
  busy("");
  if (out.ok === false) showError(out.error);
  else showError("");
  if (out.fitted?.length) alert("Cities kept on the map", out.fitted.join("\n"));
  // the world as it now is, also after a failed rebuild (a deleted city must not stay on screen)
  await loadNets(false);
  await loadGeometry(true);
}

// ------------------------------------------------------------------ left: the world's cities (outliner)
const fmtK = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n || 0));
function renderCityList() {
  const host = $("cityList");
  const add = $("cityAdd");
  const gen = !!currentNet()?.generated;
  add.replaceChildren(...(gen ? [h("button", { type: "button", class: "rn-mini", title: "Add a city: click where it goes on the map",
    onclick: () => setTool("addcity") }, icon("plus", 12), h("span", {}, "Add"))] : []));
  if (!state.net) return host.replaceChildren(h("div", { class: "rn-hint" }, "Open a world."));
  if (!state.cities.length) return host.replaceChildren(h("div", { class: "rn-hint" }, gen ? "No cities yet: add one." : "A world pulled from UEFN has no cities."));
  host.replaceChildren(...state.cities.map((c) => {
    const open = c.id === state.cityPick;
    const pop = c.plan?.population?.total;
    const row = h("button", { type: "button", class: "rn-city__row", "aria-expanded": open ? "true" : "false",
      title: `${c.name}: click to select and show it` }, icon("city", 16), h("span", { class: "rn-city__name" }, c.name),
      h("span", { class: "rn-city__pop" }, pop ? `${fmtK(pop)} people` : `${Math.round(c.radius / 100)} m`));
    row.addEventListener("click", () => selectCity(open ? "" : c.id));
    const del = h("button", { type: "button", class: "rn-city__del", title: `Delete ${c.name}`, "aria-label": `Delete ${c.name}` }, icon("trash", 14));
    del.addEventListener("click", (e) => { e.stopPropagation(); deleteCity(c); });
    row.append(del);
    const item = h("div", { class: `rn-city${open ? " is-open" : ""}`, "data-live-id": `city:${c.id}` }, row);
    if (open) item.append(h("div", { class: "rn-city__kids" }, ...cityContents(c)));
    return item;
  }));
}
// what is under a city, at a glance
function cityContents(c) {
  const kid = (ic, label, val) => h("div", { class: "rn-city__kid" }, icon(ic, 13), h("span", {}, label), h("b", {}, String(val)));
  const p = c.plan || {}, counts = p.counts || {}, w = c.waterfront || {}, a = c.airport;
  const sum = (keys) => keys.reduce((n, k) => n + (counts[k] || 0), 0);
  const out = [kid("grid", "Lots", fmtK((c.lots || []).length))];
  if (p.population) out.push(kid("city", "Homes", fmtK(p.population.households)));
  if ((c.freeways?.interchanges || []).length) out.push(kid("road", "Freeway interchanges", c.freeways.interchanges.length));
  if (p.counts) {
    out.push(kid("layers", "Schools", sum(["daycare", "elementary_school", "middle_school", "high_school", "college"])));
    out.push(kid("pin", "Emergency", sum(["fire_station", "police_hq", "police_precinct", "ambulance", "hospital"])));
    out.push(kid("mixed", "Shops and centres", sum(["convenience", "neighbourhood_center", "community_center_retail", "regional_mall", "gas_station"])));
    out.push(kid("terrain", "Parks", sum(["neighbourhood_park", "community_park", "sports_complex"])));
  }
  if (a) out.push(kid("route", ({ general_aviation: "Airfield", regional: "Regional airport", international: "International airport",
    military: "Airbase" })[a.type] || "Airport", `${a.report?.stands ?? 0} stands`));
  if ((w.segments || []).length) out.push(kid("water", "Waterfront", `${(w.developments || []).length} sites`));
  (w.shipyards || []).forEach((y) => out.push(kid("wrench", `${y.type[0].toUpperCase() + y.type.slice(1)} shipyard`, y.docks ? `${y.docks} docks` : "")));
  out.push(cityFeatures(c));
  return out;
}
// what this city has (its own choice over the world's): remove one with x, add one from + Add; saving rebuilds it
const CITY_FEATURES = [  // [key, label, icon, value when on, value when off] (citygen.FEATURES)
  ["airports", "Airport", "route", "auto", "none"], ["shipyards", "Shipyards", "wrench", "auto", "none"],
  ["freeways", "Freeways", "road", "yes", "no"], ["waterfront", "Waterfront", "water", "yes", "no"],
  ["canals", "Canals", "wave", "canal_city", "none"], ["planner", "City services", "layers", "yes", "no"],
  ["suburbs", "Winding suburbs", "road", "winding", "blocks"]];
function cityFeatures(c) {
  const own = c.features || {};
  const has = (k, on) => (own[k] ?? state.gen[k]) === on;
  const set = (k, v) => {  // shown at once (it waited for the rebuild: a click on + Add seemed to do nothing)
    c.features = { ...own, [k]: v };
    saveCity(c, { features: JSON.stringify({ [k]: v }) });
    renderWorldCity();
  };
  const box = h("div", { class: "rn-feat" }, h("div", { class: "rn-feat__head" }, "Features"));
  for (const [k, label, ic, on, off] of CITY_FEATURES) {
    if (!has(k, on)) continue;
    const rm = h("button", { type: "button", class: "rn-feat__x", title: `Take ${label.toLowerCase()} off ${c.name}`,
      "aria-label": `Remove ${label} from ${c.name}` }, icon("x", 12));
    rm.addEventListener("click", () => set(k, off));
    box.append(h("div", { class: "rn-city__kid" }, icon(ic, 13), h("span", {}, label), rm));
  }
  const missing = CITY_FEATURES.filter(([k, , , on]) => !has(k, on));
  if (missing.length) {
    const menu = h("div", { class: "rn-feat__menu", hidden: "" }, ...missing.map(([k, label, ic, on]) => {
      const b = h("button", { type: "button", class: "rn-feat__opt" }, icon(ic, 13), h("span", {}, label));
      b.addEventListener("click", () => set(k, on));
      return b;
    }));
    const add = h("button", { type: "button", class: "rn-mini", "aria-expanded": "false", title: `Add something to ${c.name}` },
      icon("plus", 12), h("span", {}, "Add"));
    add.addEventListener("click", () => {
      const open = menu.hidden;
      menu.hidden = !open;
      add.setAttribute("aria-expanded", String(open));
    });
    box.append(add, menu);
  }
  return box;
}
function selectCity(id) {
  if (id !== state.cityPick) { state.block = null; live.since = Date.now(); }  // picked now: behind anyone who already has it
  state.cityPick = id;
  tellGroup();
  keepHold();
  renderCityList();
  renderWorldCity?.();
  drawCityMark();
  rt.needsDraw = true;
  if (state.tool === "city") setTool("city");  // its banner names the city being edited
  const c = state.cities.find((x) => x.id === id);
  if (!c) return;
  const t = state.geo?.terrain || {};
  flyTo(toV(c.x, c.y, sampleZ(t, c.x, c.y) ?? 0), Math.max(800, (c.radius / 100) * 2.6));
  if (state.tab === "cities") document.querySelector(`#pane-cities [data-live-id="city:${id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

export { STYLE_OPTS, cityAirport, cityWater, CAT_LABEL, COVER_LABEL, cityPlan, showSide, ZONE_LABEL, insidePoly, pickedBlock, blockSection, lotsIn, setBlock, renderWorldCity, renderCitiesPane, edits, QUIET_MS, queueEdit, runEdits, saveCity, regenerate, fmtK, renderCityList, cityContents, CITY_FEATURES, cityFeatures, selectCity };
