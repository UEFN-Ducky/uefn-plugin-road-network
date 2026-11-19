import * as THREE from "./vendor/three@0.160.0/build/three.module.min.js";
import { h, icon, iconHTML, slider, segmented, toggle, chip, choices, field, section, foldedSections, button } from "./controls.js";
import { syncPlan, parseLiveView } from "./sync.js";
import { marchHeightfield } from "./ground.js";

// ------------------------------------------------------------------ bridge
const CHANNEL = "uefn-plugin-ui";
let callSeq = 0;
function call(method, params = {}, timeoutMs = 0) {
  // crypto.randomUUID exists only on secure pages: the panel streamed to a phone over plain http has none, and
  // the first call threw (stuck on "Loading worlds…")
  const id = crypto.randomUUID?.() ?? `${Date.now().toString(36)}-${(++callSeq).toString(36)}-${Math.random().toString(36).slice(2)}`;
  return new Promise((resolve, reject) => {
    let timer = 0;
    function onMsg(ev) {
      const d = ev.data;
      if (!d || d.channel !== CHANNEL || d.id !== id) return;
      window.removeEventListener("message", onMsg);
      clearTimeout(timer);
      d.ok ? resolve(d.result) : reject(new Error(d.error || "bridge error"));
    }
    window.addEventListener("message", onMsg);
    // a reply lost on the way (Ducky restarted, the plugin reinstalled) must never leave the panel waiting forever
    if (timeoutMs) timer = setTimeout(() => {
      window.removeEventListener("message", onMsg);
      reject(new Error(`Ducky didn't answer (${params.method || method}, ${Math.round(timeoutMs / 1000)} s). Try again.`));
    }, timeoutMs);
    parent.postMessage({ channel: CHANNEL, id, method, params }, "*");
  });
}
// reads answer in seconds; a rebuild, a push into UEFN or a city preview can take minutes
const RPC_WAIT = { generate: 0, import_all: 0, reimport: 0, pull: 0, pcg_build: 0, repair: 0, drape: 0, scan_ground: 0, tune: 0,
  heightmap: 0, pcg_preview: 600000, sync: 300000, city_add: 300000, city_save: 300000, city_delete: 300000 };
const hist = { undo: [], redo: [], busy: false, timer: 0 };  // this world's history: the steps to undo, the steps to redo
// calls that change nothing: every other one may have added a step to the world's history
const RPC_READS = new Set(["history", "geometry", "get", "list", "stats", "cities", "holds", "hold", "release", "version", "chats",
  "generate_options", "pcg_layers", "pcg_preview", "pcg_plan", "pcg_assets", "pcg_exact", "ground_fit", "presets", "projects",
  "build_brief", "check", "copy_text", "sync", "note_draft", "send_note", "terrain_map", "terrain_preview"]);
const rpc = (method, params = {}) => call("plugin.call", { method, params }, RPC_WAIT[method] ?? 90000)
  .catch((err) => ({ ok: false, error: err.message }))
  .then((out) => { if (!RPC_READS.has(method) && method !== "undo" && method !== "redo") rt.historySoon(); return out; });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const $ = (id) => document.getElementById(id);
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue("--" + name).trim();
const alert = (title, message) => call("ui.alert", { title, message }).catch(() => {});

function showError(msg, retry) {
  $("err").innerHTML = msg ? `<div class="dk-error"><div class="dk-error__title">Problem</div>${esc(msg)}</div>` : "";
  if (msg && retry) $("err").firstElementChild.append(button("Try again", "refresh", () => { showError(""); retry(); }));
}
// the loading card: a title, what is happening now, and a bar (filled when the step count is known)
const loading = { title: "", text: "", step: 0, of: 0 };
function busy(msg, opts = {}) {
  const host = $("busy");
  if (!msg) { loading.title = ""; host.replaceChildren(); return; }
  Object.assign(loading, { title: msg, text: opts.text || "", step: opts.step || 0, of: opts.of || 0 });
  drawLoading();
}
function drawLoading() {
  const host = $("busy");
  if (!loading.title) return;
  const pct = loading.of ? Math.round((loading.step / loading.of) * 100) : 0;
  let card = host.querySelector(".rn-load");
  if (!card) {
    card = h("div", { class: "rn-load", role: "status", "aria-live": "polite" },
      h("div", { class: "rn-load__title" }, h("span", { class: "rn-load__name" }), h("span", { class: "rn-load__pct" })),
      h("div", { class: "rn-load__step" }),
      h("div", { class: "rn-load__bar" }, h("div", { class: "rn-load__fill" })));
    host.replaceChildren(card);
  }
  card.querySelector(".rn-load__name").textContent = loading.title;
  card.querySelector(".rn-load__pct").textContent = loading.of ? `${pct} %` : "";
  card.querySelector(".rn-load__step").textContent = loading.text;
  const fill = card.querySelector(".rn-load__fill");
  fill.classList.toggle("is-indet", !loading.of);
  fill.style.width = loading.of ? `${Math.max(4, pct)}%` : "";
}

// ------------------------------------------------------------------ state
// ponytail: a copy of backend/terrain/config.py KEYS (default, min, max), CHOICES and PRESETS; tests/test_terrain_ui.mjs
// fails when the two drift apart
const TERRAIN_KEYS = {
  mountain_m: [1200, 0, 2500], mountain_cover: [0.2, 0.05, 0.8], mountain_intensity: [0.5, 0, 1], flatland: [0.5, 0, 1],
  hills: [0.5, 0, 1], plateaus: [0.1, 0, 1], coast_side_deg: [0, 0, 360], sea_level_m: [0, -50, 50], erosion: [0.5, 0, 1],
  thermal: [0.5, 0, 1], rivers: [0.5, 0, 1], lakes: [0.4, 0, 1], temp_c: [14, -10, 35], rain: [0.5, 0, 1],
  snowline_m: [1900, 300, 4000], detail: [0.5, 0, 1], hill_km: [2, 0.3, 10], relief_m: [60, 0, 250],
};
const TERRAIN_CHOICES = { coast: ["one", ["none", "one", "two", "island", "archipelago"]] };
const TERRAIN_PRESETS = {
  Coastal: { mountain_intensity: 0.55, flatland: 0.6, hills: 0.55, coast: "one", mountain_m: 1600, mountain_cover: 0.25, plateaus: 0.05 },
  Plains: { mountain_m: 300, mountain_cover: 0.05, mountain_intensity: 0.2, flatland: 0.9, hills: 0.25, plateaus: 0.05, coast: "none", relief_m: 30, hill_km: 4 },
  Mountainous: { mountain_m: 2200, mountain_cover: 0.45, mountain_intensity: 0.85, flatland: 0.25, hills: 0.6, coast: "none", relief_m: 120 },
  Alpine: { mountain_m: 2500, mountain_cover: 0.5, mountain_intensity: 0.95, flatland: 0.2, hills: 0.6, coast: "none", snowline_m: 1400, temp_c: 8, lakes: 0.7, relief_m: 140 },
  Tropical: { temp_c: 28, rain: 0.9, coast: "two", mountain_m: 1200, mountain_cover: 0.15, snowline_m: 4000, rivers: 0.7 },
  Desert: { temp_c: 30, rain: 0.1, coast: "none", mountain_m: 900, mountain_cover: 0.12, plateaus: 0.45, rivers: 0.2, lakes: 0.1, erosion: 0.3, snowline_m: 4000 },
  Island: { coast: "island", mountain_m: 1200, mountain_cover: 0.15, mountain_intensity: 0.6, flatland: 0.55, temp_c: 22, rain: 0.65 },
  Mediterranean: { coast: "one", temp_c: 19, rain: 0.35, hills: 0.65, mountain_m: 1400, mountain_cover: 0.2, flatland: 0.45, coast_side_deg: 270 },
  Continental: { coast: "none", temp_c: 9, rain: 0.5, flatland: 0.7, mountain_m: 1000, mountain_cover: 0.12, lakes: 0.6 },
  Mixed: {},
  Classic: { classic: true },
};
// what choosing a preset puts in the sliders: the engine default under the preset's own values, for the keys the
// panel has (detail is the old generator's octave count there, so the engine's 0..1 detail is left out).
// Classic runs the old generator, which reads none of these: the sliders keep what they show.
function presetValues(name) {
  const p = TERRAIN_PRESETS[name];
  if (!p || p.classic) return {};
  const out = {};
  for (const [k, [d]] of Object.entries(TERRAIN_KEYS)) if (k in GEN_DEFAULTS && k !== "detail") out[k] = p[k] ?? d;
  for (const [k, [d]] of Object.entries(TERRAIN_CHOICES)) out[k] = p[k] ?? d;
  return out;
}
const GEN_DEFAULTS = {
  name: "Valley", cities: 3, size_km: 6, relief_m: 60, seed: 7, ground: "new",
  hill_km: 2, roughness: 0.5, detail: 5, ridges: 0, peaks: 0, flatten_m: 300, sea_m: -1000, water_depth_m: 12, terrace_m: 0, smooth: 0.5, landscape_px: 2017,
  // the engine's sliders, as the Coastal preset sets them (presetValues("Coastal"))
  preset: "Coastal", mountain_m: 1600, mountain_cover: 0.25, mountain_intensity: 0.55, flatland: 0.6, hills: 0.55, plateaus: 0.05,
  coast: "one", coast_side_deg: 0, erosion: 0.5, rivers: 0.5, lakes: 0.4, temp_c: 14, rain: 0.5, snowline_m: 1900,
  block_m: 100, block_ratio: 1, wobble: 0.12, follow_hills: 0.8, green_lots: 0, prune: 0.22, downtown: 0.4, normal: 0.75, highway: "4L", highway_curve: 0.08,
  core_roads: "4L", neighbourhood_roads: "2S", suburb_roads: "2L", one_way_core: "no", interchange: "direct", overpasses: "yes",
  city_size: 1, city_gap_m: 300, freeways: "yes", waterfront: "yes", canals: "none", shipyards: "auto",
  airports: "auto", military_base: "no", wind_deg: 240, city_ground: "natural", island: "none",
};
const OPTION_KEYS = ["hill_km", "roughness", "detail", "ridges", "peaks", "flatten_m", "sea_m", "water_depth_m", "terrace_m", "smooth", "landscape_px",
  "preset", "mountain_m", "mountain_cover", "mountain_intensity", "flatland", "hills", "plateaus", "coast", "coast_side_deg", "erosion",
  "rivers", "lakes", "temp_c", "rain", "snowline_m", "block_m", "block_ratio", "wobble", "follow_hills", "green_lots",
  "prune", "downtown", "normal", "highway", "highway_curve", "core_roads", "neighbourhood_roads", "suburb_roads", "one_way_core",
  "interchange", "overpasses", "city_size", "city_gap_m", "freeways", "interchange_models", "waterfront", "canals", "shipyards", "airports", "military_base", "wind_deg", "city_ground", "island"];
const VIEW_DEFAULTS = { zx: 1, pcg: true, terrain: true, water: true, districts: true, points: false, look: "natural", sunAz: 135, sunEl: 52, shadows: true };
const state = {
  nets: [], net: "", actor: "", geo: null, sel: null, cities: [], tab: "world", framed: false, live: false,
  gen: { ...GEN_DEFAULTS }, view: { ...VIEW_DEFAULTS }, tune: { side: 8, end: 10, tol: 50 },
};
const ctl = {};  // control handles by key, so loading a world can set them

// road families by mesh name: half width (cm), colour variable, legend label
const FAMILY = [
  [/BoulevardSidewalk/, 2000, "accent", "Boulevard + pavement"],
  [/BridgeSidewalk/, 2000, "red", "Boulevard bridge"],
  [/OneLaneSidewalk/, 650, "green", "One-way + pavement"],
  [/Freeway/, 1650, "accent", "Freeway"],
  [/FourLane/, 1400, "blue", "Four-lane"],
  [/TwoLaneSidewalk/, 875, "green", "Two-lane + pavement"],
  [/TwoLane|OneLane|Dirt/, 525, "amber", "Two-lane"],
  [/Elev_Deck/, 1650, "purple", "Elevated deck"],
  [/Elev_Ramp/, 650, "yellow", "Flyover ramp"],
  [/Bridge/, 925, "red", "Bridge"],
  [/Water_Canal/, 1200, "blue", "Canal"],
  [/Water_DryDock/, 2000, "red", "Dry dock"],
  [/Air_Runway/, 2250, "fg", "Runway"],
  [/Air_Taxiway/, 1150, "yellow", "Taxiway"],
  [/Path_Sidewalk/, 190, "fg", "Sidewalk"],
  [/Path_Walkway/, 80, "fg-dim", "Walkway"],
  [/Path_DrivewayDouble/, 275, "fg-dim", "Double driveway"],
  [/Path_Driveway/, 175, "fg-dim", "Driveway"],
  [/Path_PrivateStreet/, 400, "fg-dim", "Private street"],
];
function family(mesh) {
  for (const f of FAMILY) if (f[0].test(mesh)) return f;
  return [null, 500, "fg-dim", "Other"];
}
const savePrefs = () => call("prefs.set", { id: "ui", value: { gen: state.gen, view: state.view, tab: state.tab, tune: state.tune } }).catch(() => {});  // view.leftW: the sidebar width

// ------------------------------------------------------------------ 3D scene
const view = $("view");
const renderer = new THREE.WebGLRenderer({ antialias: true });
const FULL_DPR = Math.min(2, window.devicePixelRatio || 1);  // while the view moves it draws at 1x (loop)
renderer.setPixelRatio(FULL_DPR);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;  // (PCFSoft sampled every shadowed pixel many more times)
// the sun never moves with the camera: the shadow map (every building, tree and the terrain drawn again) is
// redrawn when the view comes to rest, never on each frame of a fly / orbit (loop)
renderer.shadowMap.autoUpdate = false;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.outputColorSpace = THREE.SRGBColorSpace;
view.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(50, 1, 5, 400000);
const groups = { terrain: new THREE.Group(), water: new THREE.Group(), roads: new THREE.Group(), pieces: new THREE.Group(),
  props: new THREE.Group(), points: new THREE.Group(), districts: new THREE.Group(), pcg: new THREE.Group() };
Object.values(groups).forEach((g) => scene.add(g));
const marks = new THREE.Group();  // the person's pin / drawn area: kept when the scene is rebuilt
scene.add(marks);
const hemi = new THREE.HemisphereLight(0xffffff, 0xffffff, 0.55);
scene.add(hemi);
const sun = new THREE.DirectionalLight(0xffffff, 2.2);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0004;
sun.shadow.normalBias = 0.6;
scene.add(sun, sun.target);

// UE (cm, X fwd, Y right, Z up, left-handed) -> three (m, right-handed, Y up)
// z is scaled by the Terrain tab's height exaggeration so gentle hills read in the preview
const toV = (x, y, z) => new THREE.Vector3(y / 100, z * (state.view.zx || 1) / 100, -x / 100);
// A colour is an Appearance variable ("fg-dim") or a mix of two ("mix:fg-dim:amber:0.25" = fg-dim, 25 % toward
// amber), so the 3D view and its legend follow the person's theme and the panel holds no colour literals
function col(name) {
  const s = String(name);
  if (s.startsWith("mix:")) {
    const [, a, b, t] = s.split(":");
    return col(a).lerp(col(b), parseFloat(t) || 0);
  }
  const c = new THREE.Color();
  c.setStyle(cssVar(s) || cssVar("fg") || "transparent");
  return c;
}
// The outdoors in the theme's own hues, toned like the real thing (the UI's greens and blues are signal colours:
// grass in pure --green read neon). sat scales the hue's saturation, light is the sRGB lightness.
function tone(name, sat, light) {
  const c = col(name), hsl = {};
  c.getHSL(hsl, THREE.SRGBColorSpace);
  return c.setHSL(hsl.h, Math.min(1, hsl.s * sat), light, THREE.SRGBColorSpace);
}
// one small, steady variation per building or tree (from where it stands), so a block isn't one flat colour
function vary(base, x, y, amount = 1) {
  const r = Math.sin(x * 0.0123 + y * 0.0457) * 43758.5453, f = r - Math.floor(r);
  const r2 = Math.sin(x * 0.0311 - y * 0.0173) * 24634.6345, g = r2 - Math.floor(r2);
  return base.clone().offsetHSL((g - 0.5) * 0.03 * amount, (f - 0.5) * 0.1 * amount, (f - 0.5) * 0.14 * amount);
}
const swatch = (c) => {
  const s = String(c);
  if (!s.startsWith("mix:")) return `var(--${s})`;
  const [, a, b, t] = s.split(":");
  return `color-mix(in srgb, var(--${b}) ${Math.round((parseFloat(t) || 0) * 100)}%, var(--${a}))`;
};
// what the city is made of, so the preview reads like the place (road types are told apart by width, not colour)
const MAT = { asphalt: "mix:bg:fg:0.16", sidewalk: "mix:fg-dim:fg:0.35", pavers: "mix:fg-dim:amber:0.3",
  drive: "mix:bg:fg:0.26", paint: "mix:fg-dim:fg:0.8", centre: "mix:yellow:fg:0.2", barrier: "mix:fg-dim:fg:0.55" };
// a road piece's material slots (backend/road_profiles.SLOTS) as the preview's colours
const SLOT_MAT = { Asphalt: MAT.asphalt, Pavers: MAT.sidewalk, Plinth: MAT.barrier, Paint: MAT.paint, PaintYellow: MAT.centre };
const PAINT_PERIOD = 1000;  // road_profiles.PAINT_PERIOD: dashes line up from tile to tile
// the asphalt part of a road whose mesh carries its own sidewalks (2S 8.75 m -> 5.25 m, 4L 14 m -> 10.5 m)
const ASPHALT_HALF = { 875: 525, 1400: 1050, 650: 300, 2000: 1650 };
const toUE = (v) => [Math.round(-v.z * 100), Math.round(v.x * 100), Math.round(v.y * 100 / (state.view.zx || 1))];
// what other modules set (an ES import is read-only): flags of the view loop and hooks into later modules
const rt = {
  liveReady: false,  // set once the page has started (the live helpers exist then)
  // layer names hidden in the 3D view only (the eye in Layer order): nothing in the world or UEFN changes
  // (kept in the saved prefs as view.hidden: a plugin panel has no localStorage, touching it stops the page)
  hiddenLayers: new Set(),
  drag: null,  // the press under way on the view (view/camera.js pointer handlers)
  needsDraw: true,
  visible: true,
};

export { CHANNEL, call, RPC_WAIT, hist, RPC_READS, rpc, esc, $, cssVar, alert, showError, loading, busy, drawLoading,
  TERRAIN_KEYS, TERRAIN_CHOICES, TERRAIN_PRESETS, presetValues, GEN_DEFAULTS, OPTION_KEYS, VIEW_DEFAULTS, state, ctl, FAMILY, family, savePrefs, view, renderer, FULL_DPR, scene, camera, groups, marks, hemi, sun, toV, col, tone, vary, swatch, MAT, SLOT_MAT, PAINT_PERIOD, ASPHALT_HALF, toUE, rt };
