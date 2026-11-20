import { button, choices, field, h, icon, section, segmented, slider, toggle } from "../controls.js";
import { $, ctl, presetValues, savePrefs, state } from "../core.js";
import { regenerate, renderCitiesPane, showSide } from "./cities.js";
import { renderPcgPane } from "./pcg.js";
import { renderEditPane, renderUefnPane } from "./uefn.js";
import { generate } from "./world.js";
import { buildScene } from "../view/scene.js";
import { DEBUG_VIEWS } from "../view/terrain.js";

// ------------------------------------------------------------------ sections (the left sidebar's nav)
const TABS = [["world", "World", "world"], ["terrain", "Terrain", "terrain"], ["roads", "Roads", "road"],
  ["cities", "Cities", "city"], ["pcg", "Layers", "layers"], ["edit", "Edit roads", "edit"]];
// (UEFN is built by an AI from this blueprint - the header's Build in UEFN - so there is no push section)
const GEN_TABS = new Set(["world", "terrain", "roads"]);
function renderTabs() {
  $("nav").replaceChildren(...TABS.map(([id, label, ic]) => {
    const b = h("button", { type: "button", class: "dk-nav-item", role: "tab", id: `tab-${id}`, "aria-controls": `pane-${id}`,
      "aria-selected": id === state.tab ? "true" : "false", "aria-current": id === state.tab ? "page" : null,
      tabindex: id === state.tab ? "0" : "-1", "data-live-id": `tab:${id}` }, icon(ic, 16), h("span", {}, label));
    b.addEventListener("click", () => openTab(id));
    return b;
  }));
  $("nav").onkeydown = (e) => {
    const k = { ArrowDown: 1, ArrowUp: -1 }[e.key];
    if (!k) return;
    e.preventDefault();
    const i = TABS.findIndex((t) => t[0] === state.tab);
    openTab(TABS[(i + k + TABS.length) % TABS.length][0]);
    $(`tab-${state.tab}`).focus();
  };
}
function openTab(id) {
  state.tab = id;
  $("pane-uefn")?.classList.remove("is-open");
  for (const [t] of TABS) {
    const b = $(`tab-${t}`);
    b?.setAttribute("aria-selected", t === id ? "true" : "false");
    b?.setAttribute("tabindex", t === id ? "0" : "-1");
    b?.toggleAttribute("aria-current", t === id);
  }
  showSide();
  // the left side follows the section: the layer order in Layers, the road actors in Edit roads, else the cities
  $("leftLayers").hidden = id !== "pcg";
  $("leftActors").hidden = id !== "edit";
  $("leftCities").hidden = id === "pcg" || id === "edit";
  document.body.dataset.liveView = `${state.net || "networks"}:${id}`;
  savePrefs();
}

// What the foot button (and every live change) redoes follows what is picked: a picked city only that city,
// the Terrain tab only the ground, anything else the whole world.
function scope() {
  const c = state.cities.find((x) => x.id === state.cityPick);
  if (c && state.tab !== "pcg" && state.tab !== "edit") return { kind: "city", c };
  return { kind: state.tab === "terrain" ? "terrain" : "all" };
}
// the Terrain tab's button: a new terrain seed each press, so the mountains, rivers and lakes come out all new
function newTerrainSeed() {
  state.gen.terrain_seed = 1 + Math.floor(Math.random() * 999998);
  ctl.terrain_seed?.set(state.gen.terrain_seed);
  savePrefs();
}
// a world of this name was generated already: it has trees, props and roads standing on its saved ground
function built() {
  const name = (state.gen.name || "").trim().toLowerCase();
  return !!name && state.nets.some((n) => n.generated && n.name.toLowerCase() === name);
}
function runGenerate() {
  const s = scope();
  if (s.kind === "city") return regenerate(`Rebuilding ${s.c.name}`);  // the world with its cities as they are: only this one's changes show
  // new ground is always saved: a built world is rebuilt on it (same cities), so trees, props and roads come with
  // it; a world not built yet is saved as ground only. The quick preview saved nothing: a tab switch reloaded the
  // saved world and the new ground was gone
  return generate({ terrainOnly: s.kind === "terrain" });
}
function syncGenerate() {
  if (!ctl.generate) return;
  const s = scope();
  const again = built();
  const [label, title] = s.kind === "city" ? [`Regenerate ${s.c.name}`, `Rebuilds ${s.c.name}: its streets, blocks and buildings (offline, nothing changes in UEFN)`]
    : s.kind === "terrain" ? ["Regenerate terrain", again
      ? "New ground with a new terrain seed: different mountains, rivers and lakes. The cities keep their layout; roads, trees and props are rebuilt on the new ground"
      : "New ground with a new terrain seed: different mountains, rivers and lakes, saved as this world's ground (Generate world adds the cities on it)"]
    : again ? ["Regenerate everything", "Rebuilds this whole world: the terrain, every city and every road (offline, nothing changes in UEFN)"]
    : ["Generate world", "Builds the terrain, cities and roads of a new world (offline, nothing changes in UEFN)"];
  ctl.generate.querySelector("span").textContent = label;
  ctl.generate.title = title;
}
// a generate setting changed: only remembered; nothing is rebuilt until the foot button is pressed
function setGen(key, v) {
  state.gen[key] = v;
  savePrefs();
  syncGenerate();
}
const m = (v) => `${Math.round(v)} m`;
const pct = (v) => `${Math.round(v * 100)} %`;
function sl(key, label, min, max, step, fmt, hint) {
  ctl[key] = slider({ label, min, max, step, value: state.gen[key], fmt, hint, onChange: (v) => setGen(key, v) });
  return ctl[key].el;
}

// backend/terrain/config.py PRESETS, in the order the person picks from
const PRESET_OPTIONS = [
  ["Coastal", "Coastal", "Sea on one side, a range inland, plains between", "wave"],
  ["Plains", "Plains", "Wide flat land, low hills, no sea", "grid"],
  ["Mountainous", "Mountainous", "Tall ranges over much of the map", "terrain"],
  ["Alpine", "Alpine", "2500 m peaks, snow, lakes in the valleys", "top"],
  ["Tropical", "Tropical", "Hot and wet, sea on two sides, many rivers", "water"],
  ["Desert", "Desert", "Hot and dry, plateaus, few rivers", "sun"],
  ["Island", "Island", "One big island in the sea", "pin"],
  ["Mediterranean", "Mediterranean", "Warm, dry summers, hills down to the sea", "lake"],
  ["Continental", "Continental", "Inland, cool, lakes", "world"],
  ["Mixed", "Mixed", "The engine's defaults: a bit of everything", "mixed"],
  ["Classic", "Classic", "The old hill generator (Shape sliders below)", "clock"],
];

function buildPanes() {
  // ---- World
  ctl.name = field({ label: "World name", value: state.gen.name, placeholder: "Valley", onChange: (v) => setGen("name", v.trim()) });
  ctl.seed = field({ label: "Seed", value: state.gen.seed, onChange: (v) => setGen("seed", parseInt(v, 10) || 1),
    button: { icon: "dice", title: "New random seed", onClick: (input) => { input.value = Math.floor(Math.random() * 99999); setGen("seed", +input.value); } } });
  ctl.ground = choices({ label: "Ground", value: state.gen.ground, onChange: (v) => setGen("ground", v), options: [
    ["new", "New terrain", "Natural hills and water, with a heightmap to import", "terrain"],
    ["net", "Selected network's ground", "Cities sit on the open network's scanned terrain", "layers"]] });
  $("pane-world").replaceChildren(
    section("Basics", ctl.name.el, sl("cities", "Cities", 0, 12, 1, (v) => v ? String(v) : "None, terrain only", "0 = just the ground and its heightmap; add cities later on the Cities tab"), sl("size_km", "Map size", 1, 20, 0.5, (v) => `${v} km`),
      sl("city_size", "City size", 0.3, 4, 0.05, (v) => `×${(+v).toFixed(2)}`, "Every city bigger or smaller (regenerating scales the cities you have)"),
      sl("city_gap_m", "Distance between cities", 0, 6000, 50, m, "Open country between two cities' edges, at least: cities closer than this are pushed apart"),
      ctl.seed.el),
    section("Where it goes", ctl.ground.el),
    section("Ground height", sl("relief_m", "Ground height", 0, 250, 5, m, "How far the everyday ground rolls up and down everywhere: hills and valleys. Low = flat land. Mountains in places are on the Terrain tab"),
      sl("sea_m", "Water level", -1000, 200, 5, (v) => v <= -1000 ? "Off" : m(v), "Ground below this becomes lakes and sea; streets bridge it"),
      (ctl.city_ground = segmented({ label: "Ground under the cities", value: state.gen.city_ground, onChange: (v) => setGen("city_ground", v),
        options: [["natural", "Natural", "terrain"], ["flatten", "Flatten", "layers"]] })).el,
      h("div", { class: "rn-hint" }, "Natural keeps the hills as they are: a moved city sits on the land it lands on. "
        + "Flatten levels the land under each city; lakes and the sea stay where they are either way. "
        + "New terrain only: a world on scanned ground keeps that ground."),
      (ctl.island = segmented({ label: "Island", value: state.gen.island, onChange: (v) => setGen("island", v),
        options: [["none", "Off", "terrain"], ["one", "One island", "wave"], ["many", "Islands", "layers"]] })).el,
      h("div", { class: "rn-hint" }, "One island: the land is one big island in the sea, big enough for every city. "
        + "Islands: each city on its own island plus a few islets; highways bridge the water. Cities always stay on "
        + "dry land. With Water level Off the sea sits at 0 m. New terrain only.")),
    section("This world", h("div", { id: "worldSummary" }), h("div", { id: "worldHeightmap" })));
  // ---- Terrain
  // Preview look: a native dropdown (5 looks + the engine's debug maps would never fit a segmented row)
  const lookSel = h("select", { class: "dk-input rn-select", "aria-label": "Preview look" },
    ...[["natural", "Natural"], ["height", "Height"], ["slope", "Slope"], ["biome", "Biome"], ["rivers", "Rivers"]].map(([v, l]) => h("option", { value: v }, l)),
    h("optgroup", { label: "Debug" }, ...DEBUG_VIEWS.map(([v, l]) => h("option", { value: `debug:${v}` }, l))));
  lookSel.value = state.view.look;
  lookSel.addEventListener("change", () => { state.view.look = lookSel.value; savePrefs(); buildScene(); });
  ctl.look = { el: h("div", { class: "rn-field" }, h("div", { class: "rn-field__label" }, "Preview look"), lookSel),
    set: (v) => { lookSel.value = v; } };
  ctl.shadows = toggle({ label: "Shadows", iconName: "shadow", value: state.view.shadows, onChange: (v) => { state.view.shadows = v; savePrefs(); buildScene(); } });
  const sunAz = slider({ label: "Sun direction", min: 0, max: 360, step: 5, value: state.view.sunAz, fmt: (v) => `${v}°`,
    onInput: (v) => { state.view.sunAz = v; buildScene(); }, onChange: (v) => { state.view.sunAz = v; savePrefs(); buildScene(); } });
  const sunEl = slider({ label: "Sun height", min: 5, max: 85, step: 1, value: state.view.sunEl, fmt: (v) => `${v}°`,
    onInput: (v) => { state.view.sunEl = v; buildScene(); }, onChange: (v) => { state.view.sunEl = v; savePrefs(); buildScene(); } });
  const zx = slider({ label: "Height exaggeration", min: 1, max: 5, step: 0.25, value: state.view.zx || 1, fmt: (v) => `×${v}`,
    hint: "Preview only: the heightmap and roads keep real heights",
    onChange: (v) => { state.view.zx = v; savePrefs(); state.framed = false; buildScene(); } });
  ctl.terrain_seed = field({ label: "Terrain seed", value: state.gen.terrain_seed, onChange: (v) => setGen("terrain_seed", Math.max(0, parseInt(v, 10) || 0)),
    button: { icon: "dice", title: "New terrain seed: all new mountains, rivers and lakes", onClick: (input) => { newTerrainSeed(); input.value = state.gen.terrain_seed; setGen("terrain_seed", state.gen.terrain_seed); } } });
  // a preset fills its values into every engine slider; moving one afterwards keeps the preset and overrides that key
  // label "": the section is titled Preset already
  ctl.preset = choices({ label: "", value: state.gen.preset, options: PRESET_OPTIONS, onChange: (v) => {
    Object.assign(state.gen, presetValues(v));
    syncControls();
    setGen("preset", v);
  } });
  ctl.preset.el.setAttribute("aria-label", "Preset");
  const deg = (v) => `${Math.round(v)}°`;
  $("pane-terrain").replaceChildren(
    section("Preset", ctl.preset.el, ctl.terrain_seed.el,
      h("div", { class: "rn-hint" }, "Regenerate terrain picks a new terrain seed each time. Type one back to get that ground again; 0 = the world seed.")),
    section("Mountains", sl("mountain_m", "Mountain height", 0, 2500, 25, (v) => v ? m(v) : "Off",
        "Mountain ranges in some places only, as tall as this, on top of the ground; the rest stays as flat as Ground height. Cities and the way between them stay clear. Above 256 m the heightmap's Z scale grows to fit (shown under This world)"),
      sl("mountain_cover", "Mountain area", 0.05, 0.8, 0.05, pct, "How much of the land the ranges cover"),
      sl("mountain_intensity", "Mountain intensity", 0, 1, 0.05, pct, "How sharp and tall the ranges get: low = worn old hills, high = jagged spines and peaks")),
    section("Land", sl("flatland", "Flatland", 0, 1, 0.05, pct, "Share of the free land left as flat plains, where cities go"),
      sl("hills", "Hills", 0, 1, 0.05, pct, "Rolling hills and foothills between the plains and the ranges"),
      sl("plateaus", "Plateaus", 0, 1, 0.05, pct, "Flat-topped high ground with steep edges")),
    section("Coast", (ctl.coast = segmented({ label: "Sea", value: state.gen.coast, onChange: (v) => setGen("coast", v), options: [
        ["none", "None", "terrain"], ["one", "One side", "wave"], ["two", "Two sides", "water"], ["island", "Island", "pin"],
        ["archipelago", "Islands", "layers"]] })).el,
      sl("coast_side_deg", "Sea side", 0, 360, 5, deg, "Where the sea is: 0° = east (+X), 90° = south (+Y)"),
      sl("land_size", "Island size", 0.5, 1.6, 0.05, (v) => `×${v.toFixed(2)}`, "Sea: Island only. How much of the map the island covers: ×1 is the old size; bigger fills more of the map (it never runs off its edge)")),
    section("Water", sl("rivers", "Rivers", 0, 1, 0.05, pct, "How many rivers run down from the mountains to lakes and the sea"),
      sl("lakes", "Lakes", 0, 1, 0.05, pct, "How many hollows fill up into lakes"),
      sl("erosion", "Erosion", 0, 1, 0.05, pct, "Rain carving valleys and gullies into the slopes")),
    section("Climate", sl("temp_c", "Temperature", -10, 35, 1, (v) => `${Math.round(v)} °C`, "Mean temperature at sea level; it falls with height"),
      sl("rain", "Rain", 0, 1, 0.05, pct, "Dry desert to wet rainforest"),
      sl("snowline_m", "Snowline", 300, 4000, 50, m, "Ground above this is snow and rock")),
    section("Vegetation", sl("tree_density", "Trees", 0, 3, 0.05, (v) => v ? `×${v.toFixed(2)}` : "None",
        "How dense the wild forest is: ×1 is the Trees layer as set on the PCG tab, 0 = no wild trees"),
      sl("forest_cover", "Forest cover", 0.05, 1, 0.05, pct, "Share of the land that is forest; the rest is meadow and clearings"),
      sl("groves", "Groves", 0, 1, 0.05, pct, "In open country (meadow, grassland, scrub): 0 = single trees here and there, 100 % = trees only in groves"),
      sl("bush_density", "Bushes", 0, 3, 0.05, (v) => v ? `×${v.toFixed(2)}` : "None", "How dense the scrub and undergrowth is"),
      sl("tree_shore_m", "Shore gap", 0, 80, 1, m, "How far trees keep from the sea, lakes and rivers (conifers a little more, bushes and palms much less). Sand stays bare either way")),
    section("Shape", sl("hill_km", "Hill size", 0.3, 10, 0.1, (v) => `${v.toFixed(1)} km`),
      sl("roughness", "Roughness", 0.2, 0.8, 0.02, pct), sl("detail", "Detail", 1, 8, 1, (v) => `${v} layers`),
      sl("ridges", "Mountain ridges", 0, 1, 0.05, pct, "0 = rolling hills, 100 % = sharp crests"),
      sl("peaks", "Peaks", 0, 1, 0.05, pct, "Wide low valleys with tall sharp peaks"),
      sl("smooth", "Smoothness", 0, 1, 0.05, pct, "0 = raw noise, 100 % = soft rolling hills with no sharp little bumps")),
    // UEFN caps every landscape in a level together at 2048 x 2048 samples: 2017 (32 x 63 quads + 1) is the most that fits
    section("Landscape for UEFN", sl("landscape_px", "Resolution", 505, 4033, 504, (v) => {
      const mpp = (state.gen.size_km || 6) * 1000 / (v - 1);
      return `${v} × ${v} · ${mpp.toFixed(1)} m a pixel${v * v > 2048 * 2048 ? " · ABOVE UEFN LIMIT" : ""}`;
    }, "More pixels = finer cuts and smoother slopes. UEFN refuses more than 2048 × 2048 in total: 2017 is the most it takes")),
    section("Water and steps", h("div", { class: "rn-hint" }, "Water level is on the World tab."),
      sl("water_depth_m", "Lake depth", 0, 80, 1, m, "How far lake beds drop below the water line"),
      sl("terrace_m", "Terraces", 0, 50, 1, (v) => v ? m(v) : "Off"),
      sl("flatten_m", "City edge blend", 0, 2000, 25, m, "With flattened cities: how far the flat city ground blends back into the hills")),
    section("Preview", ctl.look.el, h("div", { class: "rn-hint" }, "Biome, Rivers and Debug need an engine preset (not Classic); Debug maps come from the last generated world."), zx.el, sunAz.el, sunEl.el, ctl.shadows.el));
  // ---- Roads
  // road types from our kit (citygen.ROAD_TYPES): one-way, dirt, two-lane with or without sidewalks, four- and six-lane
  const seg = (key, label, options) => (ctl[key] = segmented({ label, value: state.gen[key], options, onChange: (v) => setGen(key, v) })).el;
  ctl.highway = segmented({ label: "Highways", value: state.gen.highway, options: [["2L", "2 lanes", "route"], ["4L", "4 lanes", "road"], ["6L", "6 lanes", "road"]],
    onChange: (v) => setGen("highway", v) });
  const roadKinds = section("Road types",
    seg("core_roads", "Downtown", [["2S", "2 + sidewalk", "route"], ["4L", "4 lanes", "road"], ["6L", "6 lanes", "road"]]),
    seg("one_way_core", "One-way side streets downtown", [["no", "Two-way", "route"], ["yes", "One-way", "top"]]),
    seg("neighbourhood_roads", "Neighbourhoods", [["2L", "2 lanes", "route"], ["2S", "2 + sidewalk", "route"], ["4L", "4 lanes", "road"]]),
    seg("suburb_roads", "Suburbs", [["1W", "1 lane", "top"], ["DT", "Dirt", "terrain"], ["2L", "2 lanes", "route"], ["2S", "2 + sidewalk", "route"]]));
  const junctions = section("Highways", ctl.highway.el,
    seg("interchange", "Where a highway meets a city", [["direct", "Direct", "route"], ["diamond", "Diamond", "grid"],
      ["trumpet", "Trumpet", "refresh"], ["cloverleaf", "Clover", "mixed"], ["stack", "Stack", "layers"]]),
    seg("overpasses", "Where a highway crosses a street", [["yes", "Bridge over", "top"], ["no", "Cross at grade", "route"]]),
    seg("freeways", "Urban freeways through big cities", [["yes", "Freeways", "layers"], ["no", "None", "route"]]),
    seg("interchange_models", "Freeway interchanges", [["yes", "Blender models", "layers"], ["no", "Generated ramps", "route"]]),
    h("div", { class: "rn-hint" }, "Blender models (IC_Diamond, Parclo, DiamondFrontage, Stack...) go wherever their footprint " +
      "(about 1.4 km along the freeway) is on free land; the freeway and cross road join them at their sockets."),
    sl("highway_curve", "Highway curve", 0, 0.3, 0.01, pct),
    h("div", { class: "rn-hint" }, "Diamond, trumpet and cloverleaf are built from our ramp, bridge and road pieces: the highway " +
      "bridges over a cross road with slip ramps (diamond), one loop (trumpet) or four loops (cloverleaf). Stack rises onto the " +
      "elevated deck. You see every ramp in the 3D view."));
  const coast = section("Waterfront and airports",
    seg("waterfront", "Waterfront (quays, marinas, beaches, ports)", [["yes", "Develop", "wave"], ["no", "Leave natural", "terrain"]]),
    seg("canals", "Canals", [["none", "None", "route"], ["canal_city", "Canal city", "wave"]]),
    seg("shipyards", "Shipyards on deep industrial shore", [["auto", "Plan them", "wrench"], ["none", "None", "route"]]),
    seg("airports", "Airports (by city size)", [["auto", "Plan them", "route"], ["none", "None", "terrain"]]),
    seg("military_base", "Military airbase", [["no", "No", "route"], ["yes", "Yes", "layers"]]),
    sl("wind_deg", "Prevailing wind", 0, 360, 5, (v) => `${Math.round(v)}°`, "Runways are turned into the wind"));
  $("pane-roads").replaceChildren(
    section("Streets", sl("block_m", "Block size", 50, 250, 5, m, "A block's short side"),
      sl("block_ratio", "Block shape", 1, 3, 0.1, (v) => v <= 1.01 ? "Square" : `1 : ${Math.round(v * 10) / 10}`,
        "Square blocks, or long ones: 1 : 2 is twice as long as it is wide"),
      sl("wobble", "Curvy streets", 0, 0.45, 0.01, pct,
        "0 = a ruled grid. Any more and the whole street net drifts in long waves, main roads with it. Higher: the streets between main roads bow as well (everywhere in an Organic city, past downtown in a Mixed one, never in a Grid one)"),
      sl("follow_hills", "Streets follow the hills", 0, 1, 0.05, pct,
        "On sloping ground: a street across the slope runs along the hillside, one up it winds, and local streets too steep to climb are left out. 0 = the grid ignores the hills. Needs natural ground under the cities; never in a Grid city"),
      sl("green_lots", "Lots left green", 0, 0.6, 0.05, pct, "Share of every block's lots planted with trees instead of built on"),
      sl("prune", "Side streets removed", 0, 0.5, 0.01, pct)),
    section("Districts", sl("downtown", "Downtown ring", 0.1, 0.8, 0.01, pct, "Four-lane grid inside this share of each city's radius"),
      sl("normal", "Normal city ring", 0.2, 0.95, 0.01, pct, "Streets with pavements up to here; housing beyond")),
    roadKinds, junctions, coast);
  // ---- Footer
  ctl.generate = button("Generate world", "refresh", () => {
    if (scope().kind === "terrain") newTerrainSeed();
    runGenerate();
  }, "primary");
  $("foot").replaceChildren(ctl.generate);
  syncGenerate();
  renderCitiesPane();
  renderPcgPane();
  renderEditPane();
  renderUefnPane();
}

function syncControls() {
  for (const [k, c] of Object.entries(ctl)) if (k in state.gen && c?.set) c.set(state.gen[k]);
  ctl.look?.set(state.view.look);
  ctl.shadows?.set(state.view.shadows);
}


export { TABS, GEN_TABS, renderTabs, openTab, syncGenerate, setGen, m, pct, sl, buildPanes, syncControls };
