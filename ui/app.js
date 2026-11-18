import * as THREE from "./vendor/three@0.160.0/build/three.module.min.js";
import { h, icon, iconHTML, slider, segmented, toggle, chip, choices, field, section, foldedSections, button } from "./controls.js";
import { syncPlan, parseLiveView } from "./sync.js";

// ------------------------------------------------------------------ bridge
const CHANNEL = "uefn-plugin-ui";
function call(method, params = {}, timeoutMs = 0) {
  const id = crypto.randomUUID();
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
  "build_brief", "check", "copy_text", "sync", "note_draft", "send_note"]);
const rpc = (method, params = {}) => call("plugin.call", { method, params }, RPC_WAIT[method] ?? 90000)
  .catch((err) => ({ ok: false, error: err.message }))
  .then((out) => { if (!RPC_READS.has(method) && method !== "undo" && method !== "redo") historySoon(); return out; });
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
const GEN_DEFAULTS = {
  name: "Valley", cities: 3, size_km: 6, relief_m: 60, seed: 7, ground: "new",
  hill_km: 2, roughness: 0.5, detail: 5, ridges: 0, peaks: 0, flatten_m: 300, sea_m: -1000, water_depth_m: 12, terrace_m: 0, smooth: 0.5, landscape_px: 2017,
  block_m: 100, block_ratio: 1, wobble: 0.12, follow_hills: 0.8, green_lots: 0, prune: 0.22, downtown: 0.4, normal: 0.75, highway: "4L", highway_curve: 0.08,
  core_roads: "4L", neighbourhood_roads: "2S", suburb_roads: "2L", one_way_core: "no", interchange: "direct", overpasses: "yes",
  city_size: 1, city_gap_m: 300, freeways: "yes", waterfront: "yes", canals: "none", shipyards: "auto",
  airports: "auto", military_base: "no", wind_deg: 240, city_ground: "natural",
};
const OPTION_KEYS = ["hill_km", "roughness", "detail", "ridges", "peaks", "flatten_m", "sea_m", "water_depth_m", "terrace_m", "smooth", "landscape_px", "block_m", "block_ratio", "wobble", "follow_hills", "green_lots",
  "prune", "downtown", "normal", "highway", "highway_curve", "core_roads", "neighbourhood_roads", "suburb_roads", "one_way_core",
  "interchange", "overpasses", "city_size", "city_gap_m", "freeways", "waterfront", "canals", "shipyards", "airports", "military_base", "wind_deg", "city_ground"];
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
const savePrefs = () => call("prefs.set", { id: "ui", value: { gen: state.gen, view: state.view, tab: state.tab, tune: state.tune, live: state.live } }).catch(() => {});  // view.leftW: the sidebar width

// ------------------------------------------------------------------ 3D scene
const view = $("view");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
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
function roadMaterial(mesh) {
  if (/Path_Sidewalk/.test(mesh)) return [MAT.sidewalk, "Sidewalk"];
  if (/Path_Walkway/.test(mesh)) return [MAT.pavers, "Walkway"];
  if (/Path_Driveway/.test(mesh)) return [MAT.drive, "Driveway"];
  if (/Path_PrivateStreet/.test(mesh)) return [MAT.asphalt, "Private street"];
  return [MAT.asphalt, /Bridge|Elev_/.test(mesh) ? "Bridge / deck" : "Road"];
}
// a band along a curve between lateral offsets off-half..off+half at z0 (z1 === z0), or a wall at `off` from z0
// up to z1; dash = [on, off] cm paints it dashed
function stripe(curve, off, half, z0, z1, dash, out) {
  let s = 0;
  for (let k = 0; k + 1 < curve.length; k++) {
    const a = curve[k], b = curve[k + 1], dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1;
    const nx = -dy / l, ny = dx / l, spans = [];
    if (!dash) spans.push([0, l]);
    else {
      const P = dash[0] + dash[1];
      for (let m = Math.floor(s / P) * P; m < s + l; m += P) {
        const d0 = Math.max(m, s) - s, d1 = Math.min(m + dash[0], s + l) - s;
        if (d1 > d0) spans.push([d0, d1]);
      }
    }
    const at = (d, side, z) => { const t = d / l; return toV(a[0] + dx * t + nx * side, a[1] + dy * t + ny * side, a[2] + (b[2] - a[2]) * t + z); };
    for (const [d0, d1] of spans) {
      const q = z1 === z0 ? [at(d0, off - half, z0), at(d0, off + half, z0), at(d1, off + half, z0), at(d1, off - half, z0)]
        : [at(d0, off, z0), at(d0, off, z1), at(d1, off, z1), at(d1, off, z0)];
      for (const v of [q[0], q[1], q[2], q[0], q[2], q[3]]) out.push(v.x, v.y, v.z);
    }
    s += l;
  }
}
// a road piece drawn from its cross-section (backend/road_profiles: the same one its UEFN mesh is built from):
// every part's top and both its faces, the deck's underside, the paint 1 cm proud
// a piece on one point (road_profiles.CONNECTORS): its top-view outline turned to the point's yaw, the slab's
// sides down to its bottom, the paint 1 cm up; 2 cm over the road it lies on so the two never flicker
function drawPlan(p, plan, put) {
  const yaw = (p[5] || 0) * Math.PI / 180, c = Math.cos(yaw), s = Math.sin(yaw), LIFT = 62;
  const at = ([x, y], z) => toV(p[1] + c * x - s * y, p[2] + s * x + c * y, p[3] + z);
  const fan = (poly, z, out) => {
    for (let i = 1; i + 1 < poly.length; i++) for (const q of [poly[0], poly[i], poly[i + 1]]) { const v = at(q, z); out.push(v.x, v.y, v.z); }
  };
  const top = put(MAT.asphalt), o = plan.outline;
  fan(o, LIFT, top);
  o.forEach((q, i) => {
    const r = o[(i + 1) % o.length], lo = LIFT + plan.slab;
    for (const v of [at(q, lo), at(r, lo), at(r, LIFT), at(q, lo), at(r, LIFT), at(q, LIFT)]) top.push(v.x, v.y, v.z);
  });
  for (const [poly, slot] of plan.paint) fan(poly, LIFT + 1, put(SLOT_MAT[slot] || MAT.paint));
}
// one face of a swept cross-section: the edge (ya, za) -> (yb, zb) carried along the curve
function sweepEdge(curve, ya, za, yb, zb, out, skip) {
  for (let k = 0; k + 1 < curve.length; k++) {
    const a = curve[k], b = curve[k + 1], dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1;
    const nx = -dy / l, ny = dx / l;
    if (skip && skip((a[0] + b[0]) / 2 + nx * skip.y, (a[1] + b[1]) / 2 + ny * skip.y)) continue;
    const at = (p, y, z) => toV(p[0] + nx * y, p[1] + ny * y, p[2] + z);
    const q = [at(a, ya, za), at(a, yb, zb), at(b, yb, zb), at(b, ya, za)];
    for (const v of [q[0], q[1], q[2], q[0], q[2], q[3]]) out.push(v.x, v.y, v.z);
  }
}
// What the ramp ends open in the roads they meet (g.joins, backend/ramp_joins): a freeway's parapet along a merge
// lane, a street's sidewalk across a ramp's mouth. Returns part name -> (x, y) => true where that part is left out.
function joinSkips(joins) {
  if (!joins) return () => null;
  // ponytail: every gap point against every parapet strip (a few million distance checks a rebuild); a grid if
  // worlds grow past a few hundred ramps
  const gaps = joins.gores.flatMap((g) => g.rows.map((r) => [r[0], r[1]]));
  const parapet = (x, y) => gaps.some((g) => (x - g[0]) ** 2 + (y - g[1]) ** 2 < 420 ** 2);
  const sidewalk = (x, y) => joins.flares.some(([ex0, ey0, ux, uy, tx, ty, D, r, walk, , , A = 650]) => {
    const lx = (x - ex0) * ux + (y - ey0) * uy, ly = (x - ex0) * tx + (y - ey0) * ty;
    return walk > 0 && lx >= D - walk - 60 && lx <= D + 60 && Math.abs(ly) < A + r + 200;
  });
  return (part, mesh) => part === "parapet" && /Freeway_Lane|Elev_Deck/.test(mesh) ? parapet : part === "sidewalk" ? sidewalk : null;
}
// the ramp ends themselves: merge lanes and street mouths, built from g.joins the way the Blender models are
function drawJoins(joins, put) {
  const LIFT = 60, tri = (out, ...vs) => { for (const v of vs) out.push(v.x, v.y, v.z); };
  const quad = (out, a, b, c, d) => tri(out, a, b, c, a, c, d);
  for (const { deck, rows } of joins.gores) {
    const P = (r, k, dz) => toV(r[0] + r[2] * (r[4] - k), r[1] + r[3] * (r[4] - k), r[5] + LIFT + dz);  // k cm in from the lane's outer edge
    const I = (r, dz) => toV(r[0], r[1], r[5] + LIFT + dz);
    const asphalt = put(MAT.asphalt), wall = put(MAT.barrier), paint = put(MAT.paint), depth = deck ? 150 : 30;
    for (let i = 1; i + 1 < rows.length; i++) {
      const a = rows[i], b = rows[i + 1];
      if (a[4] < 2) break;
      quad(asphalt, I(a, 0), P(a, 0, 0), P(b, 0, 0), I(b, 0));
      quad(wall, P(a, 0, -depth), P(a, 0, 0), P(b, 0, 0), P(b, 0, -depth));
      if (deck) quad(wall, I(a, -depth), P(a, 0, -depth), P(b, 0, -depth), I(b, -depth));
      if (deck && Math.min(a[4], b[4]) > 80) {  // the parapet follows the lane's outer edge to the tip
        quad(wall, P(a, 35, 105), P(a, 0, 105), P(b, 0, 105), P(b, 35, 105));
        quad(wall, P(a, 35, 0), P(a, 35, 105), P(b, 35, 105), P(b, 35, 0));
        quad(wall, P(a, 0, 0), P(a, 0, 105), P(b, 0, 105), P(b, 0, 0));
      }
      const k0 = deck ? 80 : 45;
      if (Math.min(a[4], b[4]) > 250) quad(paint, P(a, k0 + 15, 1.5), P(a, k0, 1.5), P(b, k0, 1.5), P(b, k0 + 15, 1.5));
    }
  }
  for (const [ex0, ey0, ux, uy, tx, ty, D, r, walk, z0, z1, A = 650] of joins.flares) {  // A: a ramp's half width, or a private street's
    const W = (x, y, dz = 0) => toV(ex0 + ux * x + tx * y, ey0 + uy * x + ty * y, z0 + (z1 - z0) * Math.max(0, Math.min(1, x / D)) + LIFT + dz);
    const asphalt = put(MAT.asphalt), walkOut = put(MAT.sidewalk);
    quad(asphalt, W(0, -A), W(D, -A), W(D, A), W(0, A));
    quad(put(MAT.paint), W(D - 160, -A + 60, 1.5), W(D - 120, -A + 60, 1.5), W(D - 120, A - 60, 1.5), W(D - 160, A - 60, 1.5));  // stop line
    for (const sy of [-1, 1]) {
      const arc = (t0, n) => Array.from({ length: n + 1 }, (_, k) => { const th = t0 + (Math.PI / 2 - t0) * k / n; return [D - r + r * Math.sin(th), sy * (A + r - r * Math.cos(th))]; });
      const kerb = arc(0, 10);  // the kerb curving out of the ramp into the street
      for (let i = 0; i + 1 < kerb.length; i++) tri(asphalt, W(D, sy * A), W(...kerb[i]), W(...kerb[i + 1]));
      if (!walk) continue;
      const sl = arc(Math.asin(Math.max(0, (r - walk) / r)), 8), pivot = [D - walk, sy * (A + r)];
      for (let i = 0; i + 1 < sl.length; i++) {
        tri(walkOut, W(...pivot, 15), W(...sl[i], 15), W(...sl[i + 1], 15));
        quad(walkOut, W(...sl[i]), W(...sl[i], 15), W(...sl[i + 1], 15), W(...sl[i + 1]));  // the kerb's face
      }
      quad(walkOut, W(D - walk, sy * (A + r), 15), W(D, sy * (A + r), 15), W(D, sy * (A + r + 500), 15), W(D - walk, sy * (A + r + 500), 15));
    }
  }
}
function drawProfile(curve, prof, lift, put, skips) {
  if (prof.shapes) {  // the real cross-sections (Jersey barriers, chamfered kerbs, tapered decks), as modelled
    for (const [part, slot, poly] of prof.shapes) {
      const out = put(SLOT_MAT[slot] || MAT.barrier), skip = skips?.(part, prof.mesh);
      if (skip) skip.y = poly.reduce((s, p) => s + p[0], 0) / poly.length;  // where across the road this part lies
      poly.forEach((p, i) => { const q = poly[(i + 1) % poly.length]; sweepEdge(curve, p[0], lift + p[1], q[0], lift + q[1], out, skip); });
    }
  }
  for (const [name, slot, y0, y1, z0, z1] of prof.shapes ? [] : prof.parts) {
    const out = put(SLOT_MAT[slot] || MAT.barrier), mid = (y0 + y1) / 2, hw = (y1 - y0) / 2;
    stripe(curve, mid, hw, lift + z1, lift + z1, null, out);
    for (const y of [y0, y1]) stripe(curve, y, 0, lift + z0, lift + z1, null, out);
    if (name === "deck") stripe(curve, mid, hw, lift + z0, lift + z0, null, out);
  }
  for (const [y, w, dash, slot] of prof.paint) {
    stripe(curve, y, w / 2, lift + 1, lift + 1, dash ? [dash, PAINT_PERIOD - dash] : null, put(SLOT_MAT[slot]));
  }
}
// buildings by what they are: warm houses, pale civic, cool glass towers - never grass green or water blue
const ROLE_COLOR = { house: "mix:fg-dim:amber:0.45", mansion: "mix:fg:amber:0.25", apartment: "mix:fg-dim:red:0.25",
  shop: "mix:fg-dim:yellow:0.35", mall: "mix:fg:yellow:0.2", office: "mix:fg-dim:blue:0.3", skyscraper: "mix:fg-dim:blue:0.5",
  hospital: "fg", school: "mix:amber:red:0.35", police: "mix:blue:bg:0.35", fire_station: "red", factory: "mix:fg-dim:amber:0.15",
  warehouse: "mix:fg-dim:bg:0.25", gas_station: "mix:fg:amber:0.12", park: "green", parking: MAT.drive,
  civic: "mix:fg:purple:0.25", utility: "mix:fg-dim:yellow:0.3", transit: "mix:fg:blue:0.35",
  // nature and street pieces
  tree: "mix:green:bg:0.35", street_tree: "green", bush: "mix:green:yellow:0.2", rock: "fg-dim", street_lamp: "mix:bg:fg:0.3",
  traffic_light: "mix:bg:fg:0.25", stop_sign: "red", street_sign: "mix:green:bg:0.3", sidewalk_corner: MAT.sidewalk,
  bench: "mix:amber:bg:0.45", bin: "mix:green:bg:0.6", hydrant: "red", bus_stop: "fg-dim", billboard: "fg",
  power_pole: "mix:amber:bg:0.55" };

function themeColors() {
  const bg = col("bg");
  scene.background = bg;
  scene.fog = null;  // no haze: the far side of the world reads as clearly as the near one
  hemi.color = new THREE.Color(0xffffff).lerp(col("blue"), 0.2);  // daylight whatever the theme
  hemi.groundColor = col("bg").lerp(col("green-dim"), 0.4);
  placeCamera();
}

// detail texture: grayscale value noise (numbers only), multiplied over the terrain colours
const detailTex = (() => {
  const n = 256, data = new Uint8Array(n * n * 4);
  const rnd = (i, j) => { const s = Math.sin(i * 127.1 + j * 311.7) * 43758.5453; return s - Math.floor(s); };
  const val = (x, y, f) => {
    const ix = Math.floor(x * f), iy = Math.floor(y * f), fx = x * f - ix, fy = y * f - iy;
    const w = (i, j) => rnd((ix + i) % f, (iy + j) % f);
    const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
    return (w(0, 0) * (1 - sx) + w(1, 0) * sx) * (1 - sy) + (w(0, 1) * (1 - sx) + w(1, 1) * sx) * sy;
  };
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = i / n, y = j / n;
    const v = 0.5 * val(x, y, 8) + 0.3 * val(x, y, 32) + 0.2 * val(x, y, 128);
    const g = Math.round(205 + v * 50);
    const k = (j * n + i) * 4;
    data[k] = data[k + 1] = data[k + 2] = g;
    data[k + 3] = 255;
  }
  const t = new THREE.DataTexture(data, n, n);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.magFilter = THREE.LinearFilter;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  t.generateMipmaps = true;
  t.needsUpdate = true;
  return t;
})();

// orbit camera: left drag rotate, right drag (or shift) pan, wheel zoom
const orbit = { target: new THREE.Vector3(), dist: 3000, az: 0.6, el: 0.9 };
function placeCamera() {
  const { target, dist, az, el } = orbit;
  camera.position.set(target.x + dist * Math.cos(el) * Math.sin(az), target.y + dist * Math.sin(el), target.z + dist * Math.cos(el) * Math.cos(az));
  camera.lookAt(target);
  if (scene.fog) { scene.fog.near = dist * 1.4; scene.fog.far = dist * 5; }
  needsDraw = true;
  if (liveReady) { live.moved = performance.now(); sendMyCamera(); }
}
let liveReady = false;  // set once the page has started (the live helpers below exist then)
// Fly camera, like the Unreal viewport: hold the right mouse button to look around, W A S D to move, Q / E to go
// down / up, Shift to go faster, and the wheel (while held) to set the flying speed.
const fly = { on: false, keys: new Set(), speed: 1, last: 0 };
const FLY_KEYS = { w: 1, a: 1, s: 1, d: 1, q: 1, e: 1 };
function orbitOffset() {
  const { dist, az, el } = orbit;
  return new THREE.Vector3(dist * Math.cos(el) * Math.sin(az), dist * Math.sin(el), dist * Math.cos(el) * Math.cos(az));
}
function flyLook(dx, dy) {  // turn in place: the eye stays, the point it looks at swings round it
  const eye = camera.position.clone();
  orbit.az -= dx * 0.004;
  orbit.el = Math.max(-1.45, Math.min(1.55, orbit.el + dy * 0.004));
  orbit.target.copy(eye).sub(orbitOffset());
  placeCamera();
}
function flyStep(now) {
  if (!fly.on || !fly.keys.size) { fly.last = 0; return; }
  const dt = Math.min(0.1, fly.last ? (now - fly.last) / 1000 : 0.016);
  fly.last = now;
  const f = new THREE.Vector3().subVectors(orbit.target, camera.position).normalize();
  const r = new THREE.Vector3().crossVectors(f, camera.up).normalize();
  const v = new THREE.Vector3();
  if (fly.keys.has("w")) v.add(f);
  if (fly.keys.has("s")) v.sub(f);
  if (fly.keys.has("d")) v.add(r);
  if (fly.keys.has("a")) v.sub(r);
  if (fly.keys.has("e")) v.y += 1;
  if (fly.keys.has("q")) v.y -= 1;
  if (!v.lengthSq()) return;
  // speed 1 crosses the whole world in about a minute; the wheel takes it from a walk to a jet
  const world = state.box && !state.box.isEmpty() ? state.box.getSize(new THREE.Vector3()).length() : 20000;
  orbit.target.addScaledVector(v.normalize(), (world / 60) * fly.speed * (fly.shift ? 3 : 1) * dt);
  placeCamera();
}
function showFlySpeed() {
  const el = $("zoomPct");
  if (el) el.textContent = `Fly ×${fly.speed < 1 ? fly.speed.toFixed(2) : fly.speed.toFixed(1)}`;
}
let drag = null;
renderer.domElement.addEventListener("contextmenu", (e) => e.preventDefault());
// Pointer tool (like the Workflows canvas): press on a city and drag it to move it; drag empty ground to orbit.
// Hand tool: drag to pan. Always: right drag orbits, Shift / middle drag pans, the wheel zooms.
renderer.domElement.addEventListener("pointerdown", (e) => {
  drag = { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, button: e.button, moved: false };
  renderer.domElement.setPointerCapture(e.pointerId);
  if (e.button === 2) {  // flying: the pointer hides and stays put, as in a game
    fly.on = true; fly.keys.clear(); showFlySpeed();
    renderer.domElement.style.cursor = "none";
    try { renderer.domElement.requestPointerLock?.()?.catch?.(() => {}); } catch { /* no pointer lock here: the hidden cursor is enough */ }
  }
  if (e.button === 0 && state.tool === "area") { drag.drawing = true; note.points = []; addAreaPoint(e); }
  if (e.button === 0 && state.tool === "select" && !e.shiftKey) {
    drag.cityCand = cityUnder(e);  // a plain click picks it, then its blocks; the Pointer never moves a city
  }
  if (e.button === 0 && state.tool === "city" && !e.shiftKey) {  // Edit city: one of its handles, else the city itself
    const hnd0 = handleUnder(e), hnd = hnd0 && heldElsewhere(hnd0.c) ? null : hnd0, hit = hnd0 ? null : cityUnder(e);
    if (hnd) {  // (as it stands now, for Escape)
      const c = hnd.c;
      hnd.was = { turn_deg: c.turn_deg, spin: c.spin, shape: c.shape && [...c.shape], angle: c.districts?.industrial?.angle,
        districts: c.districts && JSON.parse(JSON.stringify(c.districts)) };
      drag.handle = hnd;
    }
    else if (hit) { drag.cityCand = hit; drag.cityMove = true; }
    if (hnd || hit) renderer.domElement.style.cursor = "grab";
  }
  if (e.button === 0 && state.tool === "lake" && !e.shiftKey) {  // Lake tool: a point of the picked lake
    const pt = lakePointUnder(e), l = pt && lakesOf().find((x) => x.id === pt.id);
    if (l) { drag.lakePt = { lake: l, k: pt.k, was: [...l.pts[pt.k]] }; renderer.domElement.style.cursor = "grab"; }
  }
  if (e.button === 0 && state.tool === "move" && !e.shiftKey) {  // Move tool: a road under the pointer, else the city
    const pt = roadPointUnder(e), hit = pt ? null : cityUnder(e);
    if (pt) drag.ptCand = pt;
    else if (hit) { drag.cityCand = hit; drag.cityMove = true; }
    if (pt || hit) renderer.domElement.style.cursor = "grab";
  }
});
renderer.domElement.addEventListener("pointermove", (e) => {
  if (!drag) { hoverCursor(e); return; }
  const locked = document.pointerLockElement === renderer.domElement;  // a locked pointer reports its travel only
  const dx = locked ? e.movementX : e.clientX - drag.x, dy = locked ? e.movementY : e.clientY - drag.y;
  if (locked || Math.abs(e.clientX - drag.x0) + Math.abs(e.clientY - drag.y0) > 5) drag.moved = true;
  if (drag.handle) { if (drag.moved) dragHandle(e); return; }  // turning or shaping a city: the camera stays
  if (drag.cityCand && drag.cityMove && drag.moved && !drag.city) startCityDrag(drag.cityCand);
  if (drag.city) { dragCity(e); return; }  // moving a city: it follows the pointer, the camera stays
  if (drag.ptCand && drag.moved) { dragRoadPoint(e); return; }  // moving one part of a road
  if (drag.lakePt) { if (drag.moved) dragLakePoint(e); return; }  // moving one point of a lake
  if (drag.drawing) {  // drawing an area: a point every few pixels, no orbit
    if (Math.abs(dx) + Math.abs(dy) > 6) { drag.x = e.clientX; drag.y = e.clientY; addAreaPoint(e); }
    return;
  }
  drag.x = e.clientX; drag.y = e.clientY;
  const pan = drag.button === 1 || (e.shiftKey && drag.button !== 2) || (drag.button === 0 && state.tool === "hand");  // flying: Shift is speed
  if (pan) {
    const s = orbit.dist / 900;
    const right = new THREE.Vector3().subVectors(camera.position, orbit.target).cross(camera.up).normalize();
    const fwd = new THREE.Vector3(Math.sin(orbit.az), 0, Math.cos(orbit.az));
    orbit.target.addScaledVector(right, dx * s).addScaledVector(fwd, -dy * s);
  } else if (drag.button === 2) {
    flyLook(dx, dy);  // right button: look around from where you are
    return;
  } else {
    orbit.az -= dx * 0.005;
    orbit.el = Math.max(0.05, Math.min(1.55, orbit.el + dy * 0.005));
  }
  placeCamera();
  renderZoom();
});
renderer.domElement.addEventListener("pointerup", (e) => {
  renderer.domElement.style.cursor = "";
  if (document.pointerLockElement) document.exitPointerLock();
  if (fly.on) { fly.on = false; fly.keys.clear(); fly.shift = false; renderZoom(); }
  if (drag?.drawing) { drag = null; finishArea(); return; }
  if (drag?.handle) { const d = drag; drag = null; dropHandle(d.handle); return; }
  if (drag?.city) { const d = drag; drag = null; dropCity(d.city); return; }
  if (drag?.ptCand) { const d = drag; drag = null; if (d.ptTo) dropRoadPoint(d); return; }
  if (drag?.lakePt) { const d = drag; drag = null; if (d.lakePt.moved) dropLakePoint(d.lakePt); return; }
  if (drag && !drag.moved && drag.button === 0) {
    if (state.tool === "pin") placePin(e);
    else if (state.tool === "lake") lakeClick(e);
    else if (state.tool === "addcity") addCityAt(e);
    else if (state.tool === "city") clickCity(drag.cityCand, e);
    else if (state.tool === "select" && !pick(e)) clickCity(drag.cityCand, e);
  }
  drag = null;
});
renderer.domElement.addEventListener("wheel", (e) => {
  e.preventDefault();
  if (fly.on) {  // flying: the wheel is the throttle, as in Unreal
    fly.speed = Math.max(0.02, Math.min(20, fly.speed * (e.deltaY > 0 ? 0.8 : 1.25)));
    showFlySpeed();
    return;
  }
  orbit.dist = Math.max(30, Math.min(150000, orbit.dist * (e.deltaY > 0 ? 1.15 : 0.87)));
  placeCamera();
  renderZoom();
  if (state.tool === "city") drawCityMark();  // its handles keep their size on screen
  if (state.tool === "lake") drawLakes();
}, { passive: false });
renderer.domElement.addEventListener("dblclick", () => { if (state.tool === "lake") finishLake(); });
let needsDraw = true;
let visible = true;
function resize() {
  const w = view.clientWidth || 1, hh = view.clientHeight || 1;
  renderer.setSize(w, hh, false);
  camera.aspect = w / hh;
  camera.updateProjectionMatrix();
  needsDraw = true;
}
new ResizeObserver(resize).observe(view);
function loop(now) {
  flyStep(now || performance.now());
  const gliding = liveReady && stepGhosts();  // teammates' bodies on their way to where they now look
  if (visible && (needsDraw || gliding)) { renderer.render(scene, camera); placeLabels(); if (liveReady) placePeerLabels(); needsDraw = false; }
  requestAnimationFrame(loop);
}
function clear(group) {
  while (group.children.length) {
    const c = group.children.pop();
    c.geometry?.dispose?.();
    c.material?.dispose?.();  // the shared detail texture stays: materials never dispose their maps
  }
}

// ------------------------------------------------------------------ geometry
function fwd(p) { // p = [i,x,y,z,pitch,yaw,w,mesh,raise]
  const cp = Math.cos(p[4] * Math.PI / 180), sp = Math.sin(p[4] * Math.PI / 180);
  const cy = Math.cos(p[5] * Math.PI / 180), sy = Math.sin(p[5] * Math.PI / 180);
  return [cp * cy, cp * sy, sp];
}
function hermite(a, b, ta, tb, n) {
  // start tangent = ta * fwd(a); end tangent = -tb * fwd(b) (UE connection convention)
  const fa = fwd(a), fb = fwd(b), out = [];
  for (let k = 0; k <= n; k++) {
    const t = k / n, t2 = t * t, t3 = t2 * t;
    const h00 = 2 * t3 - 3 * t2 + 1, h10 = t3 - 2 * t2 + t, h01 = -2 * t3 + 3 * t2, h11 = t3 - t2;
    out.push([0, 1, 2].map((i) => h00 * a[1 + i] + h10 * ta * fa[i] + h01 * b[1 + i] + h11 * (-tb) * fb[i]));
  }
  return out;
}
function ribbon(samples, half, lift) {
  const pos = [];
  for (let k = 0; k < samples.length - 1; k++) {
    const a = samples[k], b = samples[k + 1];
    const dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1;
    const nx = -dy / l * half, ny = dx / l * half;
    const q = [[a[0] + nx, a[1] + ny, a[2]], [a[0] - nx, a[1] - ny, a[2]], [b[0] + nx, b[1] + ny, b[2]], [b[0] - nx, b[1] - ny, b[2]]]
      .map((p) => toV(p[0], p[1], p[2] + lift));
    for (const v of [q[0], q[2], q[1], q[1], q[2], q[3]]) pos.push(v.x, v.y, v.z);
  }
  return pos;
}
const smooth = (e0, e1, x) => { const t = Math.max(0, Math.min(1, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };

function terrainMesh(t) {
  const n = t.grid, pos = [], uv = [], idx = [];
  const H = t.heights;
  let lo = Infinity, hi = -Infinity;
  for (const row of H) for (const v of row) if (v !== null && v !== undefined) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  const reps = Math.max(8, n / 6);
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = t.min[0] + (t.max[0] - t.min[0]) * (i + 0.5) / n, y = t.min[1] + (t.max[1] - t.min[1]) * (j + 0.5) / n;
    const v = toV(x, y, H[j][i] ?? lo);
    pos.push(v.x, v.y, v.z);
    uv.push(i / n * reps, j / n * reps);
  }
  // cells with no landscape under them (off the island) are left out, never drawn at a made-up height
  // ... and the square the Real pieces view covers is left open: its own full-resolution ground fills it
  const ex = state.view.pcg && state.exact?.ground ? state.exact : null;
  const cw = (t.max[0] - t.min[0]) / n, ch = (t.max[1] - t.min[1]) / n;
  const inHole = (j, i) => ex && Math.abs(t.min[0] + cw * (i + 0.5) - ex.x) < ex.r - cw && Math.abs(t.min[1] + ch * (j + 0.5) - ex.y) < ex.r - ch;
  const known = (j, i) => H[j][i] !== null && H[j][i] !== undefined && !inHole(j, i);
  for (let j = 0; j < n - 1; j++) for (let i = 0; i < n - 1; i++) {
    if (!(known(j, i) && known(j, i + 1) && known(j + 1, i) && known(j + 1, i + 1))) continue;
    const a = j * n + i, b = a + 1, c = a + n, d = c + 1;
    idx.push(a, b, c, b, d, c);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  // keep normals pointing up whichever way the triangles wind
  const nrm = geo.getAttribute("normal");
  let down = 0;
  for (let k = 0; k < nrm.count; k++) if (nrm.getY(k) < 0) down++;
  if (down > nrm.count / 2) { geo.setIndex(idx.map((_, k) => idx[k - (k % 3) + [0, 2, 1][k % 3]])); geo.computeVertexNormals(); }
  // colour by look: natural (sand, grass, rock, snow), height bands or slope
  const pal = { sand: tone("amber", 0.38, 0.68), grassLo: tone("green", 0.36, 0.42).lerp(tone("amber", 0.4, 0.42), 0.22),
    grassHi: tone("green", 0.42, 0.29), dirt: tone("amber", 0.3, 0.4), rock: tone("fg-dim", 1, 0.52).lerp(tone("amber", 0.15, 0.5), 0.2),
    snow: col("fg"), low: col("blue"), mid: col("green"), high: col("amber"), top: col("red") };
  const snowFrom = lo + 60000;  // snow only on real mountains (600 m up), never on a valley's hilltops
  const sea = t.sea ?? null;
  const colors = new Float32Array(nrm.count * 3);
  const c = new THREE.Color();
  for (let k = 0; k < nrm.count; k++) {
    const j = Math.floor(k / n), i = k % n;
    const z = H[j][i] ?? lo;
    const tH = hi > lo ? (z - lo) / (hi - lo) : 0.3;
    const slope = 1 - nrm.getY(k);  // 0 flat .. 1 vertical
    const jitter = (Math.sin(i * 12.9898 + j * 78.233) * 43758.5453) % 1;
    if (state.view.look === "height") {
      if (tH < 0.33) c.copy(pal.low).lerp(pal.mid, tH / 0.33);
      else if (tH < 0.66) c.copy(pal.mid).lerp(pal.high, (tH - 0.33) / 0.33);
      else c.copy(pal.high).lerp(pal.top, (tH - 0.66) / 0.34);
    } else if (state.view.look === "slope") {
      c.copy(pal.mid).lerp(pal.high, smooth(0.02, 0.12, slope)).lerp(pal.top, smooth(0.12, 0.3, slope));
    } else {
      c.copy(pal.grassLo).lerp(pal.grassHi, smooth(0.2, 0.75, tH));
      c.lerp(pal.dirt, smooth(0.04, 0.1, slope) * 0.6);
      c.lerp(pal.rock, smooth(0.1, 0.22, slope));
      c.lerp(pal.snow, smooth(snowFrom, snowFrom + 15000, z) * (1 - smooth(0.15, 0.3, slope)));
      if (sea !== null) c.lerp(pal.sand, 1 - smooth(sea + 150, sea + 600, z));
      c.multiplyScalar(0.94 + 0.08 * Math.abs(jitter));
    }
    colors[k * 3] = c.r; colors[k * 3 + 1] = c.g; colors[k * 3 + 2] = c.b;
  }
  geo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  const mat = new THREE.MeshStandardMaterial({ vertexColors: true, map: state.view.look === "natural" ? detailTex : null,
    roughness: 0.95, metalness: 0, side: THREE.DoubleSide });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.receiveShadow = true;
  mesh.castShadow = true;
  return { mesh, lo, hi };
}

function buildScene() {
  Object.values(groups).forEach(clear);
  const g = state.geo;
  $("emptyView").innerHTML = g ? "" : `<div class="dk-empty"><div class="dk-empty__title">No world open</div>Generate one in the World tab, or pull roads from UEFN.</div>`;
  if (!g) { $("hud").innerHTML = ""; needsDraw = true; return; }
  // the ground as the city leaves it (lots level, roads cut in), once it is worked out for this terrain. Matched
  // by the raw terrain's own numbers, not the object: a geometry reload brought the raw hills back over the
  // roads until the next city refresh (Nov 12 2026)
  g.rawTerrain ||= g.terrain;
  g.terrain = state.ground?.net === state.net && state.ground.key === terrainKey(g.rawTerrain) ? state.ground.t : g.rawTerrain;
  const byColor = {}, legend = {}, box = new THREE.Box3();
  const skips = joinSkips(g.joins), joined = new Set(g.joins?.done || []);
  if (g.joins) drawJoins(g.joins, (key) => byColor[key] ||= []);
  for (const [aslug, actor] of Object.entries(g.actors || {})) {
    // each city's roads are their own meshes (key#city): a city being moved takes its roads with it
    const mine = cityOfActor(aslug);
    const bc = !mine ? byColor : new Proxy(byColor, { get: (t, k) => t[`${String(k)}#${mine}`],
      set: (t, k, v) => { t[`${String(k)}#${mine}`] = v; return true; } });
    const pts = new Map(actor.pts.map((p) => [p[0], p]));
    const dim = state.actor && state.actor !== aslug;
    for (const s of actor.segs) {
      const a = pts.get(s[1]), b = pts.get(s[2]);
      if (!a || !b || !s[5]) continue; // mesh-less links only join the graph
      const f = family(s[5]);
      const [m, label] = roadMaterial(s[5]);
      const curve = hermite(a, b, s[3], s[4], 6);
      const lift = 60;  // every road a little over its ground: a ramp at grade drawn flush vanished into it
      const put = (key) => bc[key + (dim ? "|dim" : "")] ||= [];
      const prof = g.profiles?.[s[5].split("/").pop().split(".")[0]];
      if (prof) {  // the real piece: its parts and paint, as its UEFN mesh is built
        drawProfile(curve, prof, lift, put, skips);
        legend[label] = m;
        if (prof.sidewalk) legend["Sidewalk"] = MAT.sidewalk;
        continue;
      }
      const inner = /Sidewalk|FourLane/.test(s[5]) ? ASPHALT_HALF[f[1]] : undefined;
      if (inner && m === MAT.asphalt) {  // a road with its own sidewalks: concrete to the full width, asphalt inside
        (bc[MAT.sidewalk + (dim ? "|dim" : "")] ||= []).push(...ribbon(curve, f[1], lift - 4));
        legend["Sidewalk"] = MAT.sidewalk;
      }
      (bc[m + (dim ? "|dim" : "")] ||= []).push(...ribbon(curve, inner && m === MAT.asphalt ? inner : f[1], lift));
      legend[label] = m;
    }
    // a junction is the crossing of its arms (each arm's asphalt from its mouth through the centre, as wide
    // as that road), with a sidewalk corner square between neighbouring arms - not a disc
    const arms = new Map();
    for (const s of actor.segs) {
      if (s[5]) continue;
      for (const [j, m] of [[s[1], s[2]], [s[2], s[1]]]) {
        const jp = pts.get(j), mp = pts.get(m);
        if (jp && mp && jp[7] && jp[7] !== "SM_Junc_Empty") (arms.get(j) || arms.set(j, []).get(j)).push(mp);
      }
    }
    for (const [j, list] of arms) {
      const c = pts.get(j);
      if ((c[6] || 0) > 8000) continue;  // broken data: drawn as the red disc below
      junctionTris(c, list, !/airport$/.test(aslug), bc);  // the mesh the junction gets, not a stand-in
      legend["Road"] = MAT.asphalt;
      legend["Sidewalk"] = MAT.sidewalk;
    }
    for (const p of actor.pts) {
      if (!p[7] || p[7] === "SM_Junc_Empty") continue;
      if (joined.has(`${aslug}:${p[0]}`)) continue;  // its merge lane / street mouth is drawn (drawJoins)
      if (g.profiles?.[p[7]]?.plan) {  // a ramp's end piece (gore / flare), as its UEFN mesh is built
        drawPlan(p, g.profiles[p[7]], (key) => bc[key + (dim ? "|dim" : "")] ||= []);
        legend["Ramp end piece"] = MAT.asphalt;
        continue;
      }
      const drawn = arms.has(p[0]) && (p[6] || 0) <= 8000;  // its crossing is drawn above: the disc only takes clicks
      // a node wider than 80 m is broken data: drawn red at a capped size so it never hides the map
      const giant = (p[6] || 0) > 8000;
      const r = giant ? 40 : Math.max(6, (p[6] || 600) / 100);
      if (giant) legend["Broken node (run Check and repair)"] = "red";
      const disc = new THREE.Mesh(new THREE.CircleGeometry(r, 24), new THREE.MeshStandardMaterial({ color: col(giant ? "red" : "fg-dim"), roughness: 0.8,
        side: THREE.DoubleSide, transparent: drawn, opacity: drawn ? 0 : 1, depthWrite: !drawn }));
      disc.rotation.x = -Math.PI / 2;
      disc.position.copy(toV(p[1], p[2], p[3] + 70));
      disc.receiveShadow = true;
      disc.userData = { actor: aslug, point: p };
      groups.pieces.add(disc);
      legend["Junction"] = "fg-dim";
    }
    if (state.view.points && (!state.actor || state.actor === aslug)) {
      const arr = [];
      for (const p of actor.pts) { const v = toV(p[1], p[2], p[3] + 90); arr.push(v.x, v.y, v.z); }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(arr, 3));
      const cloud = new THREE.Points(geo, new THREE.PointsMaterial({ color: col("accent"), size: 6, sizeAttenuation: false }));
      cloud.userData = { actor: aslug, pts: actor.pts };
      groups.points.add(cloud);
    }
    for (const p of actor.pts) box.expandByPoint(toV(p[1], p[2], p[3]));
  }
  for (const [key, arr] of Object.entries(byColor)) {
    const [mat, city = ""] = key.split("#"), [c, dim] = mat.split("|");
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(arr, 3));
    geo.computeVertexNormals();
    const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: dim ? col(c).multiplyScalar(0.45) : col(c), roughness: 0.75,
      side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2 }));
    mesh.receiveShadow = true;
    mesh.userData.city = city;
    groups.roads.add(mesh);
  }
  for (const pr of g.props || []) {
    const hgt = 20;
    const m = new THREE.Mesh(new THREE.BoxGeometry(2.6, hgt, 2.6), new THREE.MeshStandardMaterial({ color: col("muted"), roughness: 0.9 }));
    m.position.copy(toV(pr.x, pr.y, pr.z - hgt * 50));
    m.castShadow = true;
    groups.props.add(m);
  }
  const t = g.terrain || {};
  let span = box.isEmpty() ? 2000 : box.getSize(new THREE.Vector3()).length();
  const center = box.isEmpty() ? new THREE.Vector3() : box.getCenter(new THREE.Vector3());
  if (t.heights && t.heights.length && state.view.terrain) {
    const { mesh } = terrainMesh(t);
    groups.terrain.add(mesh);
    const tb = new THREE.Box3().setFromObject(mesh);
    span = Math.max(span, tb.getSize(new THREE.Vector3()).length());
    tb.getCenter(center);
    if (box.isEmpty()) box.copy(tb);
    if (t.sea !== null && t.sea !== undefined && state.view.water) {
      const w = (t.max[0] - t.min[0]) / 100, d = (t.max[1] - t.min[1]) / 100;
      const water = new THREE.Mesh(new THREE.PlaneGeometry(d, w), new THREE.MeshStandardMaterial({ color: tone("blue", 0.55, 0.4),
        roughness: 0.12, metalness: 0.1, transparent: true, opacity: 0.84 }));
      water.rotation.x = -Math.PI / 2;
      water.position.copy(toV((t.min[0] + t.max[0]) / 2, (t.min[1] + t.max[1]) / 2, t.sea));
      water.receiveShadow = true;
      groups.water.add(water);
      legend["Lake"] = "blue";
    }
  }
  if (state.view.districts) drawDistricts();
  if (state.view.pcg) { drawPcg(legend); drawExact(legend); }
  // sun: azimuth and height from the Terrain tab, shadow box fitted to the world
  const az = state.view.sunAz * Math.PI / 180, el = state.view.sunEl * Math.PI / 180;
  const dir = new THREE.Vector3(Math.cos(el) * Math.sin(az), Math.sin(el), Math.cos(el) * Math.cos(az));
  sun.position.copy(center).addScaledVector(dir, span);
  sun.target.position.copy(center);
  const sc = sun.shadow.camera;
  sc.left = sc.bottom = -span / 2; sc.right = sc.top = span / 2; sc.near = 1; sc.far = span * 2.5;
  sc.updateProjectionMatrix();
  sun.castShadow = state.view.shadows;
  sun.color = new THREE.Color(0xffffff).lerp(col("amber"), 0.08 + 0.3 * (1 - state.view.sunEl / 90));  // warmer when low
  renderHud();
  if (liveReady) { drawPeers(); drawRemoteSel(); }
  if (!state.framed && !box.isEmpty()) { frame(box); state.framed = true; }
  state.box = box;
  drawCityMark();
  if (state.fadeNext) { state.fadeNext = false; fadeIn([groups.roads, groups.pieces, groups.pcg]); }
  hideLifted();
  needsDraw = true;
}
// changes come in smoothly: the new roads and buildings fade in and the city rises out of the ground
function fadeIn(list, ms = 700) {
  const mats = new Set();
  list.forEach((g) => g.traverse((o) => { const m = o.material; (Array.isArray(m) ? m : m ? [m] : []).forEach((x) => mats.add(x)); }));
  const items = [...mats].map((m) => ({ m, t: m.transparent, o: m.opacity ?? 1 }));
  items.forEach(({ m }) => { m.transparent = true; m.opacity = 0; m.needsUpdate = true; });
  const start = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - start) / ms), e = 1 - Math.pow(1 - k, 3);
    items.forEach(({ m, o }) => { m.opacity = o * e; });
    groups.pcg.scale.y = 0.25 + 0.75 * e;
    needsDraw = true;
    if (k < 1) requestAnimationFrame(step);
    else items.forEach(({ m, t, o }) => { m.transparent = t; m.opacity = o; m.needsUpdate = true; });
  };
  requestAnimationFrame(step);
}
// the camera glides to a place instead of jumping
function flyTo(target, dist, ms = 650) {
  const from = orbit.target.clone(), d0 = orbit.dist, start = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - start) / ms), e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    orbit.target.lerpVectors(from, target, e);
    orbit.dist = d0 + (dist - d0) * e;
    placeCamera();
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
// the picked city on the map: its real edge, the block picked in it and, with the Edit city tool, its handles
// (a dot on the edge at each of the outline's directions, a white one out past the edge that turns it, and its
// districts' own, each district in its colour: the middle one moves the centre district to another spot inside the
// city, the one on a ring makes that district bigger or smaller, the one on the industrial side swings it round)
const cityMark = new THREE.Group();
scene.add(cityMark);
const cityGhost = new THREE.Group();  // a city being moved: a light copy of its buildings (liftCity)
scene.add(cityGhost);
// Lakes drawn by hand (the Lake tool): each a line with a width, dug into the world's own terrain by the next
// rebuild (citygen.lake_list / terrain). pick: the lake being changed; draft: the points of one being drawn.
const lakeMark = new THREE.Group();
scene.add(lakeMark);
const lake = { pick: "", draft: [], width: 200 };
const lakesOf = () => currentNet()?.generated?.lakes || [];
const lakeWorld = () => !!currentNet()?.generated && !currentNet().generated.ground_from;  // (scanned ground is never dug)
// a lake's shore: the line round its middle line, half its width away, round at both ends
function lakeShore(pts, half) {
  const arc = (c, a0, turn, n) => Array.from({ length: n + 1 }, (_, k) => [c[0] + Math.cos(a0 - turn * k / n) * half, c[1] + Math.sin(a0 - turn * k / n) * half]);
  if (pts.length < 2) return arc(pts[0], 0, Math.PI * 2, 48);
  const left = [], right = [];
  pts.forEach((p, i) => {
    const a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)], L = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    const nx = -(b[1] - a[1]) / L, ny = (b[0] - a[0]) / L;
    left.push([p[0] + nx * half, p[1] + ny * half]);
    right.push([p[0] - nx * half, p[1] - ny * half]);
  });
  const end = pts[pts.length - 1], l1 = left[left.length - 1], r0 = right[0];
  return [...left, ...arc(end, Math.atan2(l1[1] - end[1], l1[0] - end[0]), Math.PI, 10), ...right.reverse(),
    ...arc(pts[0], Math.atan2(r0[1] - pts[0][1], r0[0] - pts[0][0]), Math.PI, 10)];
}
function drawLakes() {
  clear(lakeMark);
  needsDraw = true;
  if (state.tool !== "lake" || !state.geo) return;
  const t = state.geo.terrain || {};
  const up = (x, y, lift = 300) => toV(x, y, Math.max(sampleZ(t, x, y) ?? 0, t.sea ?? -1e9) + lift);
  const line = (pts, colour, opacity) => new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts.map(([x, y]) => up(x, y))),
    new THREE.LineBasicMaterial({ color: col(colour), depthTest: false, transparent: true, opacity }));
  const size = Math.max(5, orbit.dist / 75);
  const dot = (x, y, colour, data = {}) => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(size, 14, 10), new THREE.MeshBasicMaterial({ color: col(colour), depthTest: false }));
    m.position.copy(up(x, y, 500));
    m.renderOrder = 5;
    m.userData = data;
    lakeMark.add(m);
  };
  const shape = (pts, half, colour, opacity) => {  // its middle line and its shore
    if (pts.length > 1) lakeMark.add(line(pts, colour, opacity));
    lakeMark.add(line(lakeShore(pts, half), colour, opacity * 0.75));
  };
  for (const l of lakesOf()) {
    const on = l.id === lake.pick;
    shape(l.pts, l.width_m * 50, "blue", on ? 1 : 0.55);
    if (on) l.pts.forEach(([x, y], k) => dot(x, y, "blue", { lakePt: { id: l.id, k } }));
  }
  if (lake.draft.length) {
    shape(lake.draft, lake.width * 50, "accent", 1);
    lake.draft.forEach(([x, y]) => dot(x, y, "accent"));
  }
}
function drawCityMark() {
  clear(cityMark);
  const c = state.cities.find((x) => x.id === state.cityPick);
  if (!c || !state.geo) return;
  const t = state.geo.terrain || {};
  const [cx, cy] = c.dragTo || [c.x, c.y];
  const up = (x, y, lift) => toV(x, y, (sampleZ(t, x, y) ?? 0) + lift);
  const edge = (deg, lift = 400) => {
    const a = deg * Math.PI / 180, r = c.radius * cityOutline(c, deg);
    return up(cx + Math.cos(a) * r, cy + Math.sin(a) * r, lift);
  };
  const line = (pts, colour, opacity = 0.9) => new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: col(colour), depthTest: false, transparent: true, opacity }));
  cityMark.add(line(Array.from({ length: 181 }, (_, k) => edge(k * 2)), "accent"));
  const b = pickedBlock(c);
  if (b) {  // the picked block: its outline and a wash over it
    const ring = [...b[4], b[4][0]].map(([x, y]) => up(x, y, 150)), mid = up(b[0], b[1], 150), fan = [];
    for (let i = 0; i + 1 < ring.length; i++) fan.push(mid, ring[i], ring[i + 1]);
    cityMark.add(new THREE.Mesh(new THREE.BufferGeometry().setFromPoints(fan),
      new THREE.MeshBasicMaterial({ color: col("accent"), transparent: true, opacity: 0.3, depthTest: false, side: THREE.DoubleSide })));
    cityMark.add(line(ring, "accent", 1));
    const seg = [];  // the lots it is cut into: each one gets a building, or trees
    for (const [x, y, yaw, w, d] of lotsIn(c, b)) {
      const f = yaw * Math.PI / 180, fx = Math.cos(f), fy = Math.sin(f);
      const corner = (u, v) => up(x + fx * u * d / 2 - fy * v * w / 2, y + fy * u * d / 2 + fx * v * w / 2, 170);
      const q = [corner(1, 1), corner(1, -1), corner(-1, -1), corner(-1, 1)];
      for (let i = 0; i < 4; i++) seg.push(q[i], q[(i + 1) % 4]);
    }
    if (seg.length) {
      cityMark.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(seg),
        new THREE.LineBasicMaterial({ color: col("fg"), depthTest: false, transparent: true, opacity: 0.75 })));
    }
  }
  if (state.tool !== "city") return;
  const size = Math.max(5, orbit.dist / 75);
  const dot = (v, handle, k, colour) => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(size, 14, 10), new THREE.MeshBasicMaterial({ color: col(colour), depthTest: false }));
    m.position.copy(v);
    m.renderOrder = 5;
    m.userData = { handle, k };
    cityMark.add(m);
  };
  const n = state.shapePoints || 16;
  for (let k = 0; k < n; k++) dot(edge(k * 360 / n + (c.spin || 0), 500), "shape", k, "accent");
  const a = (c.turn_deg ?? 0) * Math.PI / 180, r = c.radius * 1.42;
  const out = up(cx + Math.cos(a) * r, cy + Math.sin(a) * r, 500);
  cityMark.add(line([up(cx, cy, 500), out], "fg", 0.55));
  dot(out, "turn", 0, "fg");
  if (c.dragTo) return;  // (being moved: its districts come with it when it lands)
  const d = c.districts || {}, R = c.radius, core = cityCore(c) || [0, 0], base = c.turn_deg ?? 0;
  const at = (share, deg, lift = 500) => { const [x, y] = ringPoint(c, share, deg); return up(x, y, lift); };
  const down = (d.downtown?.radius || R * 0.4) / R, normal = (d.normal?.radius || R * 0.75) / R;
  for (const [share, colour] of [[down, DISTRICT_COLOR.downtown], [normal, DISTRICT_COLOR.normal]]) {
    cityMark.add(line(Array.from({ length: 121 }, (_, k) => at(share, k * 3, 350)), colour, 0.8));
  }
  dot(up(c.x + core[0], c.y + core[1], 500), "core", 0, DISTRICT_COLOR.downtown);
  dot(at(down, base + 45), "downtown", 0, DISTRICT_COLOR.downtown);
  dot(at(normal, base + 135), "normal", 0, DISTRICT_COLOR.normal);
  if (d.industrial) {
    const ia = d.industrial.angle * Math.PI / 180, ir = R * 0.88 * cityOutline(c, d.industrial.angle);
    dot(up(c.x + Math.cos(ia) * ir, c.y + Math.sin(ia) * ir, 500), "industry", 0, DISTRICT_COLOR.industrial);
  }
}
// road_io._junction_geometry for our flat kit, drawn here exactly as the mesh is built (keep the two in step):
// the deck out to every arm's mouth, rounded corners between arms, a stop line and a zebra crossing on each
// arm of a 3+ way junction, and the sidewalk corners that join the arms' own sidewalks.
const WALK_OF = { 875: 350, 1400: 350, 650: 350, 2000: 350 };  // citygen.WALK by half width: how much of a road's half is sidewalk
function junctionTris(c, list, paint, byColor) {
  const TAU = Math.PI * 2, DEG = Math.PI / 180;
  const arms = list.map((m) => ({ a: (Math.atan2(m[2] - c[2], m[1] - c[1]) + TAU) % TAU, W: Math.max(272.5, m[6] || 500),
    D: Math.hypot(m[1] - c[1], m[2] - c[2]) })).sort((p, q) => p.a - q.a);
  const n = arms.length;
  if (n < 2) return;
  const W = arms.map((r) => r.W), K = W.map((w) => Math.max(0, Math.min(w - 150, WALK_OF[Math.round(w)] || 0)));
  const U = arms.map((r) => [Math.cos(r.a), Math.sin(r.a)]), N = U.map(([x, y]) => [-y, x]);
  const gap = arms.map((r, k) => ((arms[(k + 1) % n].a - r.a + TAU) % TAU) || TAU);
  const need = new Array(n).fill(0);
  gap.forEach((g, k) => {
    const v = g < 179 * DEG ? Math.max(W[k], W[(k + 1) % n]) / Math.tan(g / 2) + 300 : 0;
    need[k] = Math.max(need[k], v);
    need[(k + 1) % n] = Math.max(need[(k + 1) % n], v);
  });
  // each arm's mouth is where its road really ends (the mouth point): the deck reaches exactly that far, so a
  // road never stops short of its junction. (The mesh builder moves the mouths to `need` when it builds.)
  const d = need.map((x, k) => arms[k].D > 100 ? arms[k].D : Math.max(700, Math.min(3000, x)));
  const pt = (k, along, side) => [U[k][0] * along + N[k][0] * side, U[k][1] * along + N[k][1] * side];
  const meet = (k, j, wk, wj) => {
    const p1 = [N[k][0] * wk, N[k][1] * wk], p2 = [-N[j][0] * wj, -N[j][1] * wj];
    const den = U[k][0] * U[j][1] - U[k][1] * U[j][0];
    if (Math.abs(den) < 1e-6) return null;
    const s = ((p2[0] - p1[0]) * U[j][1] - (p2[1] - p1[1]) * U[j][0]) / den;
    return [p1[0] + U[k][0] * s, p1[1] + U[k][1] * s];
  };
  const corner = (k, j, wk, wj) => {  // arm k's left edge round to arm j's right edge: 7 points
    const L = pt(k, d[k], wk), R = pt(j, d[j], -wj), g = gap[k], mid = [(L[0] + R[0]) / 2, (L[1] + R[1]) / 2];
    const square = g > 181 * DEG && n === 2;
    const C = (g < 179 * DEG || square) ? meet(k, j, wk, wj) || mid : mid;
    const chain = [L];
    for (let i = 1; i <= 5; i++) {
      const t = i / 6;
      if (square) {
        const u = Math.min(1, 2 * t), v = Math.max(0, 2 * t - 1);
        chain.push([L[0] + (C[0] - L[0]) * u + (R[0] - C[0]) * v, L[1] + (C[1] - L[1]) * u + (R[1] - C[1]) * v]);
      } else {
        const a = (1 - t) ** 2, b = 2 * (1 - t) * t, cc = t * t;
        chain.push([a * L[0] + b * C[0] + cc * R[0], a * L[1] + b * C[1] + cc * R[1]]);
      }
    }
    chain.push(R);
    return chain;
  };
  const put = (key, z, ...ps) => {
    const arr = byColor[key] ||= [];
    for (const p of ps) { const v = toV(c[1] + p[0], c[2] + p[1], c[3] + z); arr.push(v.x, v.y, v.z); }
  };
  const quad = (key, z, a, b, e, f) => put(key, z, a, b, e, a, e, f);
  const DECK = 65, WALK = 75, PAINT = 68;  // lifts over the ground, as the roads' own deck and sidewalks
  for (let k = 0; k < n; k++) {
    const j = (k + 1) % n, outer = corner(k, j, W[k], W[j]);
    put(MAT.asphalt, DECK, [0, 0], pt(k, d[k], -W[k]), pt(k, d[k], W[k]));
    for (let i = 0; i + 1 < outer.length; i++) put(MAT.asphalt, DECK, [0, 0], outer[i], outer[i + 1]);
    if (K[k] > 0 || K[j] > 0) {
      const inner = corner(k, j, W[k] - K[k], W[j] - K[j]);
      for (let i = 0; i + 1 < outer.length; i++) quad(MAT.sidewalk, WALK, inner[i], inner[i + 1], outer[i + 1], outer[i]);
    }
    const w = W[k] - K[k] - 60;
    if (paint && n >= 3 && w >= 150) {
      const bar = (a0, a1, s0, s1) => quad("fg", PAINT, pt(k, a0, s0), pt(k, a1, s0), pt(k, a1, s1), pt(k, a0, s1));
      bar(d[k] - 70, d[k] - 40, -w, w);  // stop line
      for (let s = -w; s + 50 <= w; s += 110) bar(d[k] - 420, d[k] - 120, s, s + 50);  // zebra
    }
  }
}
// citygen.outline(): a city's edge in a direction as a factor of its radius (keep the two in step): the seed's own
// waves, turned with the city (spin), or the edge drawn by hand (shape: a factor at each handle, eased between them)
function cityOutline(c, angleDeg) {
  angleDeg -= c.spin || 0;
  if (c.shape?.length) {
    const n = c.shape.length, t = (((angleDeg % 360) + 360) % 360) / 360 * n, i = Math.floor(t) % n;
    const p = c.shape[i], q = c.shape[(i + 1) % n];
    return p + (q - p) * (1 - Math.cos(Math.PI * (t - Math.floor(t)))) / 2;
  }
  const s = Math.trunc(c.seed || 1), a = angleDeg * Math.PI / 180;
  let f = 0;
  for (const [k, amp] of [[2, 0.11], [3, 0.08], [5, 0.05], [7, 0.025]]) {
    const phase = (((s * (k * 7919 + 13)) % 997 + 997) % 997) / 997 * 2 * Math.PI;
    f += amp * Math.sin(k * a + phase);
  }
  return 1 + f;
}
// A city's districts lie round its centre district, which can be moved off the city's own centre (districts.core,
// cm from it). Keep in step with citygen.core_reach / zone_share: how far the city's edge is from that spot in each
// of 360 directions, and the point `share` of the way from it to the edge.
// (not the accent colour, which the city's own edge dots have: in most themes that is the amber of industry)
const DISTRICT_COLOR = { downtown: "red", normal: "blue", housing: "muted", industrial: "green" };
const cityCore = (c) => { const k = c.districts?.core; return k && (k[0] || k[1]) ? k : null; };
function coreReach(c) {
  const core = cityCore(c);
  if (!core) return null;
  const key = [c.x, c.y, c.radius, c.seed, c.spin, (c.shape || []).join(), core[0], core[1]].join("|");
  if (c._reach?.key === key) return c._reach.list;
  const px = c.x + core[0], py = c.y + core[1], R = c.radius, list = [];
  const out = (x, y) => Math.hypot(x - c.x, y - c.y) > R * cityOutline(c, Math.atan2(y - c.y, x - c.x) * 180 / Math.PI);
  for (let k = 0; k < 360; k++) {
    const dx = Math.cos(k * Math.PI / 180), dy = Math.sin(k * Math.PI / 180), step = R / 40;
    let t = 0;
    while (t < 2.8 * R && !out(px + dx * (t + step), py + dy * (t + step))) t += step;
    let lo = t, hi = t + step;
    for (let i = 0; i < 16; i++) { const mid = (lo + hi) / 2; if (out(px + dx * mid, py + dy * mid)) hi = mid; else lo = mid; }
    list.push((lo + hi) / 2);
  }
  c._reach = { key, list };
  return list;
}
function ringPoint(c, share, deg) {
  const a = deg * Math.PI / 180, reach = coreReach(c);
  if (!reach) { const r = c.radius * share * cityOutline(c, deg); return [c.x + Math.cos(a) * r, c.y + Math.sin(a) * r]; }
  const t = ((deg % 360) + 360) % 360, i = Math.floor(t) % 360, core = cityCore(c);
  const r = (reach[i] + (reach[(i + 1) % 360] - reach[i]) * (t - Math.floor(t))) * share;
  return [c.x + core[0] + Math.cos(a) * r, c.y + core[1] + Math.sin(a) * r];
}
// where each instance of an instanced mesh stands (x, y in world cm), so a city's own can be told apart
const xyOf = (rows) => { const a = new Float32Array(rows.length * 2); rows.forEach((p, i) => { a[2 * i] = p[0]; a[2 * i + 1] = p[1]; }); return a; };

// PCG preview: one instanced shape per layer, sized in metres like the real thing
const PCG_COLORS = ["amber", "yellow", "purple", "red", "accent", "fg-dim"];  // street pieces: never grass green
function pcgShape(name, kind) {
  if (/traffic|stop|sign|hydrant/i.test(name)) return [new THREE.CylinderGeometry(0.12, 0.12, /hydrant/i.test(name) ? 0.9 : 4, 6),
    /hydrant/i.test(name) ? 0.45 : 2];
  if (/bench|bin|trash/i.test(name)) return [new THREE.BoxGeometry(1.8, 0.9, 0.6), 0.45];
  if (/billboard/i.test(name)) return [new THREE.BoxGeometry(10, 5, 0.4), 8];
  if (/bus stop/i.test(name)) return [new THREE.BoxGeometry(4, 2.6, 1.6), 1.3];
  if (/rock|stone|boulder/i.test(name)) return [new THREE.DodecahedronGeometry(1.4), 0.9];
  if (/grass|bush|shrub|flower/i.test(name)) return [new THREE.SphereGeometry(0.7, 6, 4), 0.5];
  if (/lamp|light|pole|post/i.test(name)) return [new THREE.CylinderGeometry(0.15, 0.2, 7, 6), 3.5];
  if (kind === "buildings" || /house|building|shop|tower/i.test(name)) return [new THREE.BoxGeometry(11, 9, 16), 4.5];
  return [new THREE.ConeGeometry(2.6, 10, 7), 5];  // trees
}
// Building forms (pcg_layers.FORMS): the parts each form is made of, in metres in the lot's own frame
// (fx = toward the street, fy = along it, z = base). Every lot has its own form, width, depth and height.
const FORM_STOREY = 3.84;
function formParts(form, w, d, h) {
  const P = Math.min(10 * FORM_STOREY, h * 0.42);  // a podium is at most 10 storeys
  const box = (fx, fy, z, sx, sy, sz) => ({ c: false, fx, fy, z, sx, sy, sz });
  const cyl = (fx, fy, z, r, sz) => ({ c: true, fx, fy, z, sx: r * 2, sy: r * 2, sz });
  const m = Math.min(w, d);
  switch (form) {
    case "slab": return [box(0, 0, 0, d * 0.55, w * 1.0, h)];
    case "tower": return [box(0, 0, 0, d * 0.7, w * 0.7, h)];
    case "podium_tower": return [box(0, 0, 0, d, w, P), box(-d * 0.08, 0, P, d * 0.5, w * 0.5, h - P)];
    case "stepped": return [box(0, 0, 0, d, w, h * 0.55), box(0, 0, h * 0.55, d * 0.74, w * 0.74, h * 0.25), box(0, 0, h * 0.8, d * 0.48, w * 0.48, h * 0.2)];
    case "round": return [cyl(0, 0, 0, m * 0.45, h)];
    case "round_podium": return [box(0, 0, 0, d, w, P), cyl(-d * 0.05, 0, P, m * 0.3, h - P)];
    case "twin": return [box(0, 0, 0, d, w, P), box(0, -w * 0.24, P, d * 0.42, w * 0.34, h - P), box(0, w * 0.24, P, d * 0.42, w * 0.34, (h - P) * 0.72)];
    case "spire": return [box(0, 0, 0, d * 0.62, w * 0.62, h * 0.82), box(0, 0, h * 0.82, d * 0.4, w * 0.4, h * 0.1), cyl(0, 0, h * 0.92, 0.6, h * 0.22)];
    case "L": return [box(0, -w * 0.3, 0, d, w * 0.4, h), box(-d * 0.3, w * 0.2, 0, d * 0.4, w * 0.6, h * 0.82)];
    case "courtyard": {
      const t = m * 0.26;
      return [box(d / 2 - t / 2, 0, 0, t, w, h), box(-d / 2 + t / 2, 0, 0, t, w, h * 0.9),
        box(0, w / 2 - t / 2, 0, d - 2 * t, t, h * 0.95), box(0, -w / 2 + t / 2, 0, d - 2 * t, t, h)];
    }
    case "shed": return [box(0, 0, 0, d, w, h * 0.72), box(0, 0, h * 0.72, d * 0.28, w * 0.96, h * 0.28)];
    case "gable": {  // a house: the main body under a pitched roof (ridge along the street), a lower garage wing
      // at the driveway's side (the drive runs at fy -0.15 w, pcg_layers._lot_ground), its front on the lot's front
      const roof = [0.84, 0.62, 0.4, 0.18].map((s, i) => box(-d * 0.1, w * 0.12, h * (0.6 + i * 0.1), d * s, w * 0.74, h * 0.1));
      return [box(-d * 0.1, w * 0.12, 0, d * 0.8, w * 0.7, h * 0.6), box(d * 0.22, -w * 0.24, 0, d * 0.56, w * 0.4, h * 0.34),
        box(d * 0.22, -w * 0.24, h * 0.34, d * 0.5, w * 0.34, h * 0.06), ...roof];
    }
    case "flat": return [box(0, 0, 0, d, w, Math.max(0.4, h))];
    default: return [box(0, 0, 0, d, w, h)];
  }
}
function drawBuildings(layer, colour, name) {
  const zx = state.view.zx || 1;
  const forms = layer.forms || [];
  const boxes = [], cyls = [];
  const dummy = new THREE.Object3D();
  const base = layer.role === "park" ? tone("green", 0.5, 0.34) : col(colour);  // lawns, not the UI's signal green
  const glass = layer.role === "skyscraper" || layer.role === "office";
  for (const p of layer.points || []) {
    const [x, y, z, yaw, , hgt, fi, wv, dv] = p;
    if (inExact(x, y)) continue;  // drawn there as its real pieces
    const [w0, d0] = layer.size_m || [12, 12];
    const w = (wv || w0) * 0.92, d = (dv || d0) * 0.92;
    const r = yaw * Math.PI / 180, fxv = Math.cos(r), fyv = Math.sin(r);
    const tint = vary(base, x, y, glass ? 1.6 : 1);  // each building its own shade of its kind
    for (const part of formParts(forms[fi] || "box", w, d, hgt || 6)) {
      const ux = x + (part.fx * fxv - part.fy * fyv) * 100, uy = y + (part.fx * fyv + part.fy * fxv) * 100;
      dummy.position.copy(toV(ux, uy, z + part.z * 100));
      dummy.position.y += part.sz * zx / 2;
      dummy.rotation.set(0, -r, 0);
      // part.sx runs along the facing (fx), part.sy across it: after this rotation the facing is the box's z
      dummy.scale.set(part.sy, Math.max(0.2, part.sz) * zx, part.sx);
      dummy.updateMatrix();
      (part.c ? cyls : boxes).push([dummy.matrix.clone(), tint, p]);
    }
  }
  const mat = new THREE.MeshStandardMaterial({ roughness: glass ? 0.35 : 0.7, metalness: glass ? 0.35 : 0.05 });
  for (const [list, geo] of [[boxes, new THREE.BoxGeometry(1, 1, 1)], [cyls, new THREE.CylinderGeometry(0.5, 0.5, 1, 20)]]) {
    if (!list.length) continue;
    const mesh = new THREE.InstancedMesh(geo, mat, list.length);
    list.forEach(([mtx, tint], i) => { mesh.setMatrixAt(i, mtx); mesh.setColorAt(i, tint); });
    mesh.userData.xy = xyOf(list.map((row) => row[2]));
    mesh.userData.info = (i) => {
      const p = list[i][2];
      return layerInfo(name, layer, p, [["Form", forms[p[6]] || "box"], ["Lot", `${p[7] || "?"} m wide × ${p[8] || "?"} m deep, ${p[5] || "?"} m tall`],
        ["Built as", layer.build === "modular" ? "kit modules stacked by the grammar" : "one kit building stretched to the lot"],
        ["Its real pieces", "press Real pieces here on the Layers tab"]]);
    };
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    groups.pcg.add(mesh);
  }
}
// Every building's own foundation, as the push builds it (backend/kit_bake.foundation: change both together): a
// plinth course round its foot and a wall under that, 1 m into the ground below its lowest corner. In the concrete
// of the roads' side walls, and always there, so a building on a slope stands on it and never on a loose block.
// Then the frontage slabs from each facade to the kerb (row [.., foundation_m, paths [[width_m, [x, y, z, ...]]]]).
const FOUND = { course: 0.5, out: 0.3, wallOut: 0.15, embed: 1.0 };
function drawLotGround(layer) {
  const zx = state.view.zx || 1;
  const dummy = new THREE.Object3D();
  const found = [], walks = [], foundAt = [], walksAt = [];
  for (const p of layer.points || []) {
    const [x, y, z, yaw, , , , w, d, depth, paths] = p;
    if (inExact(x, y)) continue;
    const parts = layer.role === "park" ? []  // a park is planted ground, not a slab
      : [[0.02, FOUND.course, FOUND.out], [FOUND.course, Math.max(0, depth || 0) + FOUND.embed - FOUND.course, FOUND.wallOut]];
    for (const [drop, deep, out] of parts) {
      dummy.position.copy(toV(x, y, z - (drop + deep / 2) * 100));
      dummy.rotation.set(0, -yaw * Math.PI / 180, 0);
      dummy.scale.set(w + 2 * out, deep * zx, d + 2 * out);  // frontage across the facing, depth along it (the box's z)
      dummy.updateMatrix();
      found.push(dummy.matrix.clone());
      foundAt.push(p);
    }
    for (const [width, flat] of paths || []) {
      if (width <= 8) continue;  // walkways (Paths actor) and driveways (Access actor) are drawn as splines
      for (let i = 3; i + 2 < flat.length; i += 3) {
        const a = toV(flat[i - 3], flat[i - 2], flat[i - 1] + 15), b = toV(flat[i], flat[i + 1], flat[i + 2] + 15);
        dummy.position.copy(a).add(b).multiplyScalar(0.5);
        dummy.scale.set(width, 0.15, a.distanceTo(b));
        dummy.lookAt(b);
        dummy.updateMatrix();
        walks.push(dummy.matrix.clone());
        walksAt.push(p);
      }
    }
  }
  for (const [list, colour, at] of [[found, col(MAT.barrier), foundAt], [walks, col("fg-dim").lerp(col("fg"), 0.5), walksAt]]) {
    if (!list.length) continue;
    const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ color: colour, roughness: 0.95 }), list.length);
    list.forEach((m, i) => mesh.setMatrixAt(i, m));
    mesh.userData.xy = xyOf(at);
    mesh.receiveShadow = true;
    groups.pcg.add(mesh);
  }
}
// only: just these layers. from: the layers to draw instead of the world's (same names in the same order, so each
// keeps its colour): a rebuild draws what stayed and what is new of a layer apart (revealLayers)
function drawPcg(legend, only, from) {
  const layers = from || state.pcg || {};
  const dummy = new THREE.Object3D();
  Object.entries(layers).forEach((entry, k) => {
    const n0 = groups.pcg.children.length;
    drawLayer(entry, k);
    for (let i = n0; i < groups.pcg.children.length; i++) groups.pcg.children[i].userData.layer = entry[0];
  });
  function drawLayer([name, layer], k) {
    const pts = layer.points || [];
    if (only && !(name in only)) return;
    if (!pts.length || layer.role === "sidewalk_corner") return;  // corners are part of each junction's mesh now
    const colour = ROLE_COLOR[layer.role] || PCG_COLORS[k % PCG_COLORS.length];
    if (layer.kind === "site") {  // a neighbourhood's pieces, pre-scaled: [x, y, z, yaw, sx, sy, sz] x box_m
      const [bx, by, bz] = layer.box_m || [1, 1, 1];
      const stall = layer.mesh === "SM_Kit_ParkingStall";  // a painted line on the car park's asphalt, not a white slab
      const tint = layer.tint || (stall ? MAT.paint : layer.mesh === "SM_City_CT_MidRise" ? "fg-dim" : MAT.drive);
      const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ color: col(tint), roughness: 0.9 }), pts.length);
      const zx = state.view.zx || 1;
      pts.forEach(([x, y, z, yaw, sx, sy, sz], i) => {
        if (layer.mesh === STAY) {
          stayBar(dummy, x, y, z + 60, yaw, sx, sz);
          dummy.updateMatrix();
          return mesh.setMatrixAt(i, dummy.matrix);
        }
        const a = yaw * Math.PI / 180, side = stall ? by * sy * 50 : 0;  // the line down one long side of the stall
        // (roads are drawn 60 cm up so they never sink into the ground: a piece lying on one goes up with it, and
        // so does the approach wall a low deck stands on, or a slot of daylight shows under the deck)
        dummy.position.copy(toV(x - Math.sin(a) * side, y + Math.cos(a) * side, z + (layer.on_road || layer.mesh === "SM_Kit_ApproachWall" ? 60 : 20)));
        dummy.position.y += bz * sz * zx / 2;
        dummy.rotation.set(0, -yaw * Math.PI / 180, 0);
        dummy.scale.set(stall ? 0.12 : by * sy, Math.max(0.05, bz * sz) * zx, bx * sx);
        if (inExact(x, y)) dummy.scale.setScalar(0);
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
      });
      mesh.receiveShadow = true;
      mesh.userData.xy = xyOf(pts);
      mesh.userData.info = (i) => layerInfo(name, layer, pts[i]);
      groups.pcg.add(mesh);
      legend[`${name} · ${layer.count.toLocaleString()}`] = tint;
      return;
    }
    const building = layer.kind === "buildings";
    // (a park is what stands in it: its trees, paths, benches and playground are layers of their own, on the
    // ground as it lies. Its lot was drawn as one flat green box, level, hanging over the hillside.)
    if (building && layer.role !== "park") drawBuildings(layer, colour, name);
    if (building) legend[`${name} · ${layer.count.toLocaleString()}`] = colour;
    const [geo, lift] = building ? [null, 0] : pcgShape(name, layer.kind);
    if (!building) {
      // trees and bushes in leaf greens, not the UI's signal green; each one a little different
      const leafy = layer.role === "tree" || layer.role === "street_tree" || layer.role === "bush" || layer.kind === "foliage" && !/rock|stone|boulder/i.test(name);
      const base = leafy ? tone("green", 0.45, layer.role === "bush" ? 0.4 : 0.33).lerp(tone("amber", 0.4, 0.34), layer.role === "bush" ? 0.25 : 0.12)
        : col(colour);
      const mesh = new THREE.InstancedMesh(geo, new THREE.MeshStandardMaterial({ roughness: 0.85 }), pts.length);
      mesh.userData.xy = xyOf(pts);
      mesh.userData.info = (i) => layerInfo(name, layer, pts[i]);
      pts.forEach(([x, y, z, yaw, s], i) => {
        dummy.position.copy(toV(x, y, z));
        dummy.rotation.set(0, -yaw * Math.PI / 180, 0);
        dummy.scale.setScalar(s);
        dummy.position.y += lift * s;
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
        mesh.setColorAt(i, leafy ? vary(base, x, y, 1.4) : base);
      });
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      groups.pcg.add(mesh);
    }
    if (building) drawLotGround(layer);
    legend[`${name} · ${layer.count.toLocaleString()}`] = colour;
  }
}
// Real pieces: what the push places round the view's centre, each piece as its mesh's own box (kit bounds) at
// its real position, turn and stretch, on the ground sampled as the landscape has it. A roof beside its
// building or a hill through a floor shows here, before anything goes to UEFN.
const inExact = (x, y) => !!state.exact && Math.abs(x - state.exact.x) <= state.exact.r && Math.abs(y - state.exact.y) <= state.exact.r;
// A bridge's stay cable as its piece makes it (road_io SM_Kit_BridgeStay; keep the two in step): a bar from sz m
// over its origin down to sx m out along its yaw, so from a tower leg's top to the edge of the deck
const STAY = "SM_Kit_BridgeStay", UP = new THREE.Vector3(0, 1, 0);
function stayBar(dummy, x, y, z, yaw, sx, sz) {
  const a = yaw * Math.PI / 180;
  const top = toV(x, y, z + sz * 100), foot = toV(x + Math.cos(a) * sx * 100, y + Math.sin(a) * sx * 100, z);
  const run = foot.sub(top), len = run.length();
  dummy.position.copy(top).addScaledVector(run, 0.5);
  dummy.quaternion.setFromUnitVectors(UP, run.normalize());
  dummy.scale.set(0.35, len, 0.35);
}
const EXACT_TINT = [[/Floor|Roof|Slab|Parking|Apron/, "mix:fg-dim:bg:0.35"], [/Glass|Curtain|Storefront|Window|Lobby|Band/, "mix:blue:fg:0.55"],
  [/Awning|Door|Entrance|Gate|Garage|SallyPort/, "amber"], [/Facade|Parapet|Corner|Column|Pillar/, "fg-dim"]];
async function loadExact(keep) {
  if (!state.net) return;
  const c = orbit.target;  // three (m) -> UE (cm): x = -z, y = x
  const at = keep && state.exact ? { x: state.exact.x, y: state.exact.y } : { x: -c.z * 100, y: c.x * 100 };
  busy("Building the real pieces here…");
  const r = await rpc("pcg_exact", { net_id: state.net, ...at, radius_m: 400 });
  busy("");
  if (r.ok === false) return showError(r.error);
  state.exact = r;
  buildScene();
}
function drawExact(legend) {
  const ex = state.exact;
  if (!ex) return;
  const zx = state.view.zx || 1, dummy = new THREE.Object3D();
  const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ roughness: 0.8 }), Math.max(1, ex.pieces));
  let i = 0;
  const owner = [];  // instance -> [mesh index, its row], for a click
  mesh.userData.info = (n) => pieceInfo(ex, owner[n][0], owner[n][1]);
  ex.meshes.forEach(([name, x0, y0, z0, x1, y1, z1], k) => {
    const tint = col((EXACT_TINT.find(([re]) => re.test(name)) || [0, "mix:fg:bg:0.25"])[1]);
    for (const row of ex.rows[k]) {
      const [x, y, z, yaw, sx, sy, sz] = row;
      owner.push([k, row]);
      if (name === STAY) {
        stayBar(dummy, x, y, z, yaw, sx, sz);
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
        mesh.setColorAt(i++, tint);
        continue;
      }
      const r = yaw * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
      const cx = (x0 + x1) / 2 * sx, cy = (y0 + y1) / 2 * sy;  // the box's middle in the piece's own frame
      dummy.position.copy(toV(x + cx * c - cy * s, y + cx * s + cy * c, z + (z0 + z1) / 2 * sz));
      dummy.rotation.set(0, -r, 0);
      dummy.scale.set(Math.max(2, Math.abs((y1 - y0) * sy)) / 100, Math.max(2, Math.abs((z1 - z0) * sz)) * zx / 100, Math.max(2, Math.abs((x1 - x0) * sx)) / 100);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      mesh.setColorAt(i++, vary(tint, x, y));
    }
  });
  mesh.count = i;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  groups.pcg.add(mesh);
  const g = ex.ground;
  if (g) {  // one vertex per heightmap pixel: the triangles the landscape gets
    const rows = g.z.length, cols = g.z[0].length, pos = [], idx = [];
    for (let j = 0; j < rows; j++) for (let a = 0; a < cols; a++) { const v = toV(g.x + a * g.dx, g.y + j * g.dy, g.z[j][a]); pos.push(v.x, v.y, v.z); }
    for (let j = 0; j < rows - 1; j++) for (let a = 0; a < cols - 1; a++) { const p = j * cols + a; idx.push(p, p + 1, p + cols, p + 1, p + cols + 1, p + cols); }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    geo.setIndex(idx);
    geo.computeVertexNormals();
    const land = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: tone("green", 0.5, 0.34), roughness: 0.95, side: THREE.DoubleSide }));
    land.receiveShadow = true;
    groups.pcg.add(land);
  }
  const lost = Object.keys(ex.unknown || {}).length;
  legend[`Real pieces · ${ex.pieces.toLocaleString()}${lost ? ` (${lost} meshes with no known size not drawn)` : ""}`] = "fg";
}
let pcgPreviewTimer = 0;
function schedulePcgPreview(delay = 700) {
  clearTimeout(pcgPreviewTimer);
  pcgPreviewTimer = setTimeout(loadPcgPreview, delay);
}
// Foliage layers are the big replies (hundreds of thousands of trees): each comes on its own; the rest come
// PCG_BATCH layers to a call, the road walls with the last (every call re-reads the world's inputs once).
const PCG_BATCH = 12;
// One at a time: a second ask while the city is coming in (a teammate's change arrives in two syncs) waits and runs
// once after it, instead of two loads swapping the same layers over each other
let pcgLoading = false, pcgAgain = false;
async function loadPcgPreview() {
  if (pcgLoading) { pcgAgain = true; return; }
  pcgLoading = true;
  try { await loadPcgPreviewNow(); } finally { pcgLoading = false; }
  if (pcgAgain) { pcgAgain = false; return loadPcgPreview(); }
}
async function loadPcgPreviewNow() {
  if (!state.net || !(state.layers || []).length) { state.pcg = {}; settleCity(); state.lifted = null; buildScene(); loadSync(); return; }
  const net = state.net;
  const out = {};
  const todo = state.layers.filter((x) => x.enabled);
  const calls = todo.filter((l) => l.kind === "foliage").map((l) => [l]);
  const rest = todo.filter((l) => l.kind !== "foliage");
  for (let i = 0; i < rest.length; i += PCG_BATCH) calls.push(rest.slice(i, i + PCG_BATCH));
  calls.push([{ id: "road-walls", name: "Bridges and sites" }]);  // the built-in layers: piers, approach walls, neighbourhood sites, planted lots
  const picked = {};
  // the card is the city's own: a rebuild's or an edit's card stays up while the city fills in under it
  live.sharing = !!live.shareNext && Object.values(peers).some((p) => p.state?.net === state.net);
  live.shareNext = false;
  live.shared = 0;
  // (a teammate's change: their PC already showed it here, this one only catches up, so no card over the map)
  const hush = !live.sharing && Date.now() < (live.quietUntil || 0);
  const card = (msg, opts) => { if (!hush && (!loading.title || loading.title === "Placing the city")) busy(msg, opts); };
  for (const [i, ls] of calls.entries()) {  // the first call computes the whole city; the rest read it from the cache
    card("Placing the city", { text: i ? `${ls.map((l) => l.name).join(", ")}` : "Placing every building, prop and tree…",
      step: i, of: calls.length });
    const r = await rpc("pcg_preview", { net_id: net, layer_id: ls.map((l) => l.id).join(",") });
    if (net !== state.net) return card("");
    if (r.ok === false && ls[0].id !== "road-walls") { card(""); settleCity(); state.lifted = null; buildScene(); return showError(r.error, () => loadPcgPreview()); }
    if (r.ok !== false) {
      Object.assign(picked, r.layers || {});
      await revealLayers(r.layers || {}, i === 0);  // a few layers at a time, each rising out of the ground
      if (net !== state.net) return card("");
    }
  }
  // the layer order the panel has always drawn: each layer in turn, then the walls and sites
  for (const l of todo) if (l.name in picked) out[l.name] = picked[l.name];
  Object.assign(out, picked);
  // the ground follows the city: re-cut from this layout, every time the city changes
  card("Placing the city", { text: "Fitting the ground to the roads and the lots…", step: calls.length, of: calls.length });
  const gr = await rpc("ground_fit", { net_id: net });
  card("");
  if (net !== state.net) return;
  const raw = state.geo?.rawTerrain || state.geo?.terrain;
  if (gr.ok !== false && gr.grid && raw?.heights) {  // buildScene swaps it in
    state.ground = { net, key: terrainKey(raw), t: { ...raw, grid: gr.grid, heights: gr.heights, max: gr.max || raw.max }, fit: gr.ground_fit };
  }
  state.pcg = out;
  live.sharing = false;
  state.lifted = null;  // every layer is in: the moved city's old place is whatever the rebuild left there
  renderPcgCounts();
  state.fadeNext = false;  // it already came in, layer by layer (revealLayers)
  if (state.exact) await loadExact(true);  // the city changed: the real pieces on screen are rebuilt with it
  else buildScene();
  loadSync();  // after the city: the badge reads the preview just computed instead of building its own
}
function renderPcgCounts() {
  document.querySelectorAll("[data-pcg-count]").forEach((el) => {
    const v = (state.pcg || {})[el.dataset.pcgCount];
    el.textContent = v ? `${v.count.toLocaleString()} placed` : "—";
  });
}
function drawDistricts() {
  const t = state.geo?.terrain || {};
  for (const c of state.cities) {
    const z = sampleZ(t, c.x, c.y);
    for (const [ring, colour] of Object.entries(DISTRICT_COLOR).filter(([k]) => k !== "industrial")) {
      const r = c.districts?.[ring]?.radius;
      if (!r) continue;
      const pts = [];
      for (let k = 0; k <= 96; k++) {
        // the same irregular edge the generator uses, round the centre district where that was moved
        const [x, y] = ringPoint(c, r / c.radius, k / 96 * 360);
        pts.push(toV(x, y, (sampleZ(t, x, y) ?? z ?? 0) + 250));
      }
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      const line = new THREE.Line(geo, new THREE.LineDashedMaterial({ color: col(colour), dashSize: 18, gapSize: 12, transparent: true, opacity: 0.85 }));
      line.computeLineDistances();
      groups.districts.add(line);
    }
    const ind = c.districts?.industrial;
    const down = c.districts?.downtown?.radius, outer = c.districts?.housing?.radius;
    if (ind && down && outer) {  // the industrial wedge, edge to edge
      const a0 = (ind.angle - (ind.spread || 75) / 2) * Math.PI / 180, span = (ind.spread || 75) * Math.PI / 180;
      const pts = [];
      for (let k = 0; k <= 24; k++) { const a = a0 + span * k / 24, f = cityOutline(c, a * 180 / Math.PI); pts.push([c.x + Math.cos(a) * outer * f, c.y + Math.sin(a) * outer * f]); }
      for (let k = 24; k >= 0; k--) { const a = a0 + span * k / 24, f = cityOutline(c, a * 180 / Math.PI); pts.push([c.x + Math.cos(a) * down * f, c.y + Math.sin(a) * down * f]); }
      pts.push(pts[0]);
      const geo = new THREE.BufferGeometry().setFromPoints(pts.map(([x, y]) => toV(x, y, (sampleZ(t, x, y) ?? z ?? 0) + 300)));
      const line = new THREE.Line(geo, new THREE.LineDashedMaterial({ color: col(DISTRICT_COLOR.industrial), dashSize: 24, gapSize: 10 }));
      line.computeLineDistances();
      groups.districts.add(line);
    }
  }
}
const terrainKey = (t) => t?.heights?.length ? `${t.grid}|${t.min}|${t.max}|${t.heights[0][0]}|${t.heights[t.grid >> 1]?.[t.grid >> 1]}|${t.heights[t.grid - 1]?.[t.grid - 1]}` : "";
function sampleZ(t, x, y) {
  const n = t.grid;
  if (!n || !t.heights?.length) return null;
  const i = Math.max(0, Math.min(n - 1, Math.floor((x - t.min[0]) / (t.max[0] - t.min[0]) * n)));
  const j = Math.max(0, Math.min(n - 1, Math.floor((y - t.min[1]) / (t.max[1] - t.min[1]) * n)));
  return t.heights[j]?.[i] ?? null;
}
function frame(box) {
  box = box || state.box;
  if (!box || box.isEmpty()) return;
  flyTo(box.getCenter(new THREE.Vector3()), box.getSize(new THREE.Vector3()).length() * 0.75, state.framed ? 450 : 1);
  setTimeout(renderZoom, 500);
}
// city name tags that follow the camera
function placeLabels() {
  const host = $("labels");
  if (!state.view.districts || !state.geo) { host.innerHTML = ""; return; }
  if (host.children.length !== state.cities.length) {
    // (a city a teammate has: their name on its tag)
    host.innerHTML = state.cities.map((c) => {
      const hd = liveReady ? holderOf(c.id) : null;
      return `<div class="rn-label" data-live-id="city:${esc(c.id)}">${iconHTML("pin", 12)}${esc(c.name)}${hd ? ` · ${iconHTML("lock", 11)}${esc(hd.name)}` : ""}</div>`;
    }).join("");
  }
  const w = view.clientWidth, hh = view.clientHeight, t = state.geo.terrain || {};
  state.cities.forEach((c, k) => {
    const [cx, cy] = c.dragTo || [c.x, c.y];  // a city being dragged: its tag goes with it
    const v = toV(cx, cy, (sampleZ(t, cx, cy) ?? 0) + 4000).project(camera);
    const el = host.children[k];
    if (!el) return;
    el.style.display = v.z > 1 ? "none" : "";
    el.style.left = ((v.x + 1) / 2 * w) + "px";
    el.style.top = ((1 - v.y) / 2 * hh) + "px";
  });
}

// ------------------------------------------------------------------ picking + editing
const ray = new THREE.Raycaster();
function pick(e) {
  const r = renderer.domElement.getBoundingClientRect();
  const m = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(m, camera);
  ray.params.Points.threshold = orbit.dist / 400;
  const hits = ray.intersectObjects([...groups.points.children, ...groups.pieces.children], false);
  if (!hits.length) return inspectAt();
  const hit = hits[0];
  if (hit.object.isPoints) select(hit.object.userData.actor, hit.object.userData.pts[hit.index]);
  else select(hit.object.userData.actor, hit.object.userData.point);
  openTab("edit");
  return true;
}
// Click a building, prop or piece: what it is, which layer placed it, which mesh it uses and where it stands.
const xyz = (x, y, z) => `${Math.round(x)}, ${Math.round(y)}, ${Math.round(z)} cm`;
// the PCG tile a piece is built in (kit_bake.tiles): 400 m city tiles, 1.6 km 'n' tiles for trees, bushes and rocks
const NATURE_RE = /SM_Kit_(Tree|Bush|Rock|Boulder|Stump|Log|Cliff|Fern|GrassTuft|Flowers)/;
const tileOf = (x, y, nature) => nature ? `n${Math.floor(x / 160000)}_${Math.floor(y / 160000)}`
  : `${Math.floor(x / 40000)}_${Math.floor(y / 40000)}`;
function layerInfo(name, layer, p, more = []) {
  const nature = layer.kind === "foliage" || ["tree", "street_tree", "bush", "rock"].includes(layer.role);
  const tile = tileOf(p[0], p[1], nature);
  return { title: name, tile, rows: [["Layer", name], ["Kind", layer.role || layer.kind || "prop"],
    ["Mesh", layer.mesh || (layer.meshes || []).map((m) => m.name || m.path || m).join(", ") || "picked from the kit by its kind"],
    ...more, ["Where", xyz(p[0], p[1], p[2])], ["PCG tile", tile], ["In this layer", (layer.count || 0).toLocaleString()]] };
}
function pieceInfo(ex, k, row) {
  const [name, x0, y0, z0, x1, y1, z1] = ex.meshes[k], [x, y, z, yaw, sx, sy, sz] = row;
  const from = name.startsWith("SM_BK_") ? "BuildingKit.blend module (WorldGen/BuildingKit), stacked by the grammar"
    : name.startsWith("SM_City_") ? "whole kit building (WorldGen/City), stretched to its lot" : "City Builder kit (WorldGen/Kit)";
  return { title: name, tile: tileOf(x, y, NATURE_RE.test(name)), rows: [["Piece", name], ["Comes from", from],
    ["Size", `${Math.round((x1 - x0) * sx)} × ${Math.round((y1 - y0) * sy)} × ${Math.round((z1 - z0) * sz)} cm`],
    ["Stretch", `${sx} × ${sy} × ${sz}`], ["Turn", `${yaw}°`], ["Where", xyz(x, y, z)],
    ["Same piece in view", ex.rows[k].length.toLocaleString()], ["All pieces in view", ex.pieces.toLocaleString()]] };
}
function showInspect(info) {
  let el = $("inspect");
  if (!el) {
    el = h("div", { class: "rn-help", id: "inspect", role: "status" });
    el.style.bottom = "112px";
    el.style.width = "340px";
    $("view").parentElement.appendChild(el);
  }
  el.hidden = !info;
  if (info) el.replaceChildren(h("b", {}, info.title), h("div", { class: "rn-kv" }, ...info.rows.flatMap(([k, v]) => [h("span", {}, k), h("span", {}, String(v))])),
    ...(info.tile && state.net ? [h("button", { type: "button", class: "rn-mini", style: "pointer-events: auto; margin-top: 6px",
      onclick: () => onPcgPlace(info.tile) }, `Push tile ${info.tile} to UEFN`)] : []));
}
function inspectAt() {  // the ray is already set by pick()
  const hit = ray.intersectObjects(groups.pcg.children, false).find((x) => x.object.userData.info && x.instanceId != null);
  showInspect(hit ? hit.object.userData.info(hit.instanceId) : null);
  return !!hit;
}
function select(aslug, p) {
  state.sel = { actor: aslug, p };
  renderSelection();
  shareSelection(aslug, p);  // the group sees the picked point
}

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

// a generate setting changed: remember it, and rebuild right away when live preview is on
let liveTimer = 0;
// The button at the foot says what it will do: a world of this name is rebuilt whole (terrain, every city, every
// road), a new name makes a new world. One city is rebuilt from the Cities tab, not from here.
function syncGenerate() {
  if (!ctl.generate) return;
  const name = (state.gen.name || "").trim().toLowerCase();
  const again = !!name && state.nets.some((n) => n.generated && n.name.toLowerCase() === name);
  ctl.generate.querySelector("span").textContent = again ? "Regenerate everything" : "Generate world";
  ctl.generate.title = again ? "Rebuilds this whole world: the terrain, every city and every road (offline, nothing changes in UEFN)"
    : "Builds the terrain, cities and roads of a new world (offline, nothing changes in UEFN)";
}
function setGen(key, v) {
  state.gen[key] = v;
  savePrefs();
  syncGenerate();
  if (state.live) { clearTimeout(liveTimer); liveTimer = setTimeout(() => generate(), 650); }
}
const m = (v) => `${Math.round(v)} m`;
const pct = (v) => `${Math.round(v * 100)} %`;
function sl(key, label, min, max, step, fmt, hint) {
  ctl[key] = slider({ label, min, max, step, value: state.gen[key], fmt, hint, onChange: (v) => setGen(key, v) });
  return ctl[key].el;
}

function buildPanes() {
  // ---- World
  ctl.name = field({ label: "World name", value: state.gen.name, placeholder: "Valley", onChange: (v) => setGen("name", v.trim()) });
  ctl.seed = field({ label: "Seed", value: state.gen.seed, onChange: (v) => setGen("seed", parseInt(v, 10) || 1),
    button: { icon: "dice", title: "New random seed", onClick: (input) => { input.value = Math.floor(Math.random() * 99999); setGen("seed", +input.value); } } });
  ctl.ground = choices({ label: "Ground", value: state.gen.ground, onChange: (v) => setGen("ground", v), options: [
    ["new", "New terrain", "Natural hills and water, with a heightmap to import", "terrain"],
    ["net", "Selected network's ground", "Cities sit on the open network's scanned terrain", "layers"]] });
  $("pane-world").replaceChildren(
    section("Basics", ctl.name.el, sl("cities", "Cities", 1, 12, 1, (v) => String(v)), sl("size_km", "Map size", 1, 20, 0.5, (v) => `${v} km`),
      sl("city_size", "City size", 0.3, 4, 0.05, (v) => `×${(+v).toFixed(2)}`, "Every city bigger or smaller (regenerating scales the cities you have)"),
      sl("city_gap_m", "Distance between cities", 0, 6000, 50, m, "Open country between two cities' edges, at least: cities closer than this are pushed apart"),
      ctl.seed.el),
    section("Where it goes", ctl.ground.el),
    section("Ground height", sl("relief_m", "Hill height", 0, 250, 5, m, "Highest hills above the middle, deepest valleys below it"),
      sl("sea_m", "Water level", -1000, 200, 5, (v) => v <= -1000 ? "Off" : m(v), "Ground below this becomes lakes and sea; streets bridge it"),
      (ctl.city_ground = segmented({ label: "Ground under the cities", value: state.gen.city_ground, onChange: (v) => setGen("city_ground", v),
        options: [["natural", "Natural", "terrain"], ["flatten", "Flatten", "layers"]] })).el,
      h("div", { class: "rn-hint" }, "Natural keeps the hills as they are: a moved city sits on the land it lands on. "
        + "Flatten levels the land under each city; lakes and the sea stay where they are either way. "
        + "New terrain only: a world on scanned ground keeps that ground.")),
    section("This world", h("div", { id: "worldSummary" }), h("div", { id: "worldHeightmap" })));
  // ---- Terrain
  ctl.look = segmented({ label: "Preview look", value: state.view.look, options: [["natural", "Natural", "terrain"], ["height", "Height", "layers"], ["slope", "Slope", "wave"]],
    onChange: (v) => { state.view.look = v; savePrefs(); buildScene(); } });
  ctl.shadows = toggle({ label: "Shadows", iconName: "shadow", value: state.view.shadows, onChange: (v) => { state.view.shadows = v; savePrefs(); buildScene(); } });
  const sunAz = slider({ label: "Sun direction", min: 0, max: 360, step: 5, value: state.view.sunAz, fmt: (v) => `${v}°`,
    onInput: (v) => { state.view.sunAz = v; buildScene(); }, onChange: (v) => { state.view.sunAz = v; savePrefs(); buildScene(); } });
  const sunEl = slider({ label: "Sun height", min: 5, max: 85, step: 1, value: state.view.sunEl, fmt: (v) => `${v}°`,
    onInput: (v) => { state.view.sunEl = v; buildScene(); }, onChange: (v) => { state.view.sunEl = v; savePrefs(); buildScene(); } });
  const zx = slider({ label: "Height exaggeration", min: 1, max: 5, step: 0.25, value: state.view.zx || 1, fmt: (v) => `×${v}`,
    hint: "Preview only: the heightmap and roads keep real heights",
    onChange: (v) => { state.view.zx = v; savePrefs(); state.framed = false; buildScene(); } });
  $("pane-terrain").replaceChildren(
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
    section("Water and steps", h("div", { class: "rn-hint" }, "Hill height and water level are on the World tab."),
      sl("water_depth_m", "Lake depth", 0, 80, 1, m, "How far lake beds drop below the water line"),
      sl("terrace_m", "Terraces", 0, 50, 1, (v) => v ? m(v) : "Off"),
      sl("flatten_m", "City edge blend", 0, 2000, 25, m, "With flattened cities: how far the flat city ground blends back into the hills")),
    section("Preview", ctl.look.el, zx.el, sunAz.el, sunEl.el, ctl.shadows.el));
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
  ctl.live = toggle({ label: "Live preview", iconName: "play", value: state.live, onChange: (v) => { state.live = v; savePrefs(); } });
  ctl.generate = button("Generate world", "refresh", () => generate(), "primary");
  $("foot").replaceChildren(ctl.live.el, ctl.generate);
  syncGenerate();
  renderCitiesPane();
  renderPcgPane();
  renderEditPane();
  renderUefnPane();
}

// ------------------------------------------------------------------ PCG layers pane
const KIND_OPTS = [
  ["buildings", "Buildings", "Lots facing the street: frontage, depth, height", "city"],
  ["along_roads", "Along the streets", "Lamps, trees, benches, bins, hydrants, bus stops", "route"],
  ["at_junctions", "Junction corners", "Traffic lights, stop signs, street-name signs", "grid"],
  ["foliage", "Foliage (painted)", "Trees, rocks, bushes: Foliage mode, not PCG", "terrain"],
  ["scatter", "Scatter (PCG)", "Anything else spread over the ground", "points"],
];
const ASSET_OPTS = [
  ["own", "Our own kit", "WorldGen City and Kit meshes, BuildingKit storeys from Blender", "layers"],
  ["fortnite", "Fortnite pieces", "Fortnite's own props and buildings", "world"],
  ["mixed", "Mixed", "Our kit first, Fortnite for anything missing", "mixed"],
];
const TREE_OPTS = [
  ["kit", "Our own trees", "The kit's trees, placed by PCG as instances", "layers"],
  ["fortnite", "Fortnite trees", "Fortnite's own tree props in the cities, the ones players can chop: each one is an actor. The forest outside stays our own trees", "world"],
];
const WHERE_OPTS = [
  ["outside", "Outside the cities", "Countryside between towns", "terrain"],
  ["everywhere", "Everywhere", "Whole map, cities too", "world"],
  ["cities", "In the cities", "Every district of every city", "city"],
  ["downtown", "Downtown", "Inner ring of each city", "grid"],
  ["normal", "Normal city", "Between downtown and housing", "mixed"],
  ["housing", "Housing", "Outer ring of each city", "pin"],
  ["industrial", "Industrial zone", "One wedge of each city's outer rings", "wrench"],
  ["shipyards", "Shipyards", "On every shipyard's paved platform", "wrench"],
];
const BUILDING_FORMS = [["box", "Block"], ["slab", "Slab"], ["tower", "Tower"], ["podium_tower", "Podium + tower"],
  ["stepped", "Stepped"], ["round", "Round"], ["round_podium", "Podium + round"], ["twin", "Twin towers"], ["spire", "Spire"],
  ["L", "L-shape"], ["courtyard", "Courtyard"], ["shed", "Shed"], ["gable", "Pitched roof"], ["flat", "Flat lot"]];
const KIND_ICON = { scatter: "points", along_roads: "route", buildings: "city", at_junctions: "grid", foliage: "terrain" };
let pcgSaveTimer = 0;
function renderPcgPane() {
  const pane = $("pane-pcg");
  if (!pane) return;
  if (!state.net) {
    pane.replaceChildren(h("div", { class: "dk-empty" }, h("div", { class: "dk-empty__title" }, "No world open"), "Open or generate a world first."));
    return;
  }
  const layers = state.layers || [];
  const head = h("div", { class: "rn-btns" },
    button("Whole city", "city", async () => {
      const out = await rpc("pcg_preset", { net_id: state.net, preset: "full_city" });
      if (out.ok === false) return showError(out.error);
      await loadLayers();
    }, "secondary", "Every layer a city needs in one click: skyscrapers, offices, malls, parking, hospitals, schools, police and fire stations, parks, factories, warehouses, apartments, shops, gas stations, mansions, houses; traffic lights, stop and street signs, lamps, street trees, bus stops, benches, bins, hydrants, billboards, power poles; trees, rocks and bushes as foliage (layers you already have keep their settings)"),
    button("New layer", "edit", async () => {
      const name = `Layer ${layers.length + 1}`;
      const out = await rpc("pcg_layer_save", { net_id: state.net, layer: JSON.stringify({ name, order: layers.length + 1 }) });
      if (out.ok === false) return showError(out.error);
      state.pcgOpen = out.id;
      await loadLayers();
    }));
  const cards = layers.map((l, idx) => layerCard(l, layers.slice(0, idx)));
  // presets: the layers of this world kept under a name, and the saved ones (a person's or an AI's) to put back
  const saved = (state.presets || []).filter((p) => !p.built_in);
  let saving = false;
  const savePreset = async (name) => {
    if (saving || !String(name || "").trim()) return;
    saving = true;
    const out = await rpc("preset_save", { name, net_id: state.net });
    saving = false;
    if (out.ok === false) return showError(out.error);
    await loadLayers();
  };
  const keep = field({ label: "Save these layers as a preset", value: "", placeholder: "Preset name", onChange: savePreset,
    button: { icon: "check", title: "Save these layers as a preset", onClick: (input) => savePreset(input.value) } });
  const presetRows = saved.map((p) => {
    const use = h("button", { type: "button", class: "rn-mini", title: `Put ${p.name}'s ${p.layers} layers on this world: its settings replace same-named layers` }, "Use");
    use.addEventListener("click", async () => {
      const ok = await call("ui.confirm", { title: `Use ${p.name}`, confirm: "Use it",
        message: `${p.layers} layers from ${p.name} go on this world. Layers of the same name take the preset's settings; your other layers stay.` }).catch(() => null);
      if (!ok?.confirmed) return;
      const out = await rpc("pcg_preset", { net_id: state.net, preset: p.id, replace: true });
      if (out.ok === false) return showError(out.error);
      await loadLayers();
    });
    const del = h("button", { type: "button", class: "rn-mini", title: `Delete the preset ${p.name}`, "aria-label": `Delete the preset ${p.name}` }, icon("trash", 12));
    del.addEventListener("click", async () => {
      const ok = await call("ui.confirm", { title: `Delete ${p.name}`, danger: true, confirm: "Delete",
        message: `The preset ${p.name} goes. Worlds it was used on keep their layers.` }).catch(() => null);
      if (!ok?.confirmed) return;
      const out = await rpc("preset_delete", { preset: p.id, confirm: true });
      if (out.ok === false) return showError(out.error);
      await loadLayers();
    });
    return h("div", { class: "rn-city__kid", title: p.note || (p.layer_names || []).join(", ") }, icon("layers", 13),
      h("span", {}, `${p.name} · ${p.layers} layers`), use, del);
  });
  const pieces = choices({ label: "Pieces", value: state.assetSet || "own", options: ASSET_OPTS, onChange: async (v) => {
    const out = await rpc("pcg_assets", { net_id: state.net, asset_set: v });
    if (out.ok === false) return showError(out.error);
    state.assetSet = out.asset_set;
  } }).el;
  const trees = choices({ label: "City trees", value: state.trees || "kit", options: TREE_OPTS, onChange: async (v) => {
    const out = await rpc("pcg_assets", { net_id: state.net, trees: v });
    if (out.ok === false) return showError(out.error);
    state.trees = out.trees;
  } }).el;
  const place = button("Push city to UEFN", "upload", onPcgPlace, "primary",
    "Builds the city as PCG in the open UEFN level, 400 m tile by tile: only the tiles you changed are rebuilt");
  place.style.width = "100%";
  pane.replaceChildren(
    section("What it's built from", pieces, h("div", { class: "rn-hint", style: "margin-top: 6px" },
      "Every layer is built from the project's own kit (layers with meshes of their own keep them)."),
      h("div", { style: "margin-top: 8px" }, trees)),
    section("Placement layers", head, h("div", { class: "rn-hint", style: "margin: 8px 0" },
      "Layers place in order: each one keeps clear of the roads and the layers you tick under Keep clear of.")),
    ...(cards.length ? cards : [h("div", { class: "dk-empty" }, h("div", { class: "dk-empty__title" }, "No layers yet"), "Add the starter layers or a new one.")]),
    section("Presets", ...presetRows, ...(layers.length ? [keep.el] : []),
      ...(!presetRows.length ? [h("div", { class: "rn-hint" }, "No saved presets yet. An AI can make one too (road_network_preset_save).")] : [])),
    section("Build", h("div", { id: "pcgPlanInfo", class: "rn-hint", style: "margin-bottom: 8px" }),
      h("div", { class: "rn-stack" }, button("Refresh 3D preview", "refresh", () => loadPcgPreview()),
        button("Real pieces here", "city", () => loadExact(), "secondary",
          "Draws every wall, roof and floor the push will place within 400 m of the view's centre, on the ground UEFN will have"),
        button("Back to the simple view", "refresh", () => { state.exact = null; buildScene(); }), place)));
  renderPcgCounts();
  if (layers.length) rpc("pcg_plan", { net_id: state.net }).then((p) => {
    const el = $("pcgPlanInfo");
    if (!el) return;
    el.innerHTML = p.ok === false ? esc(p.error)
      : `One graph <b>${esc(p.graph)}</b>: ${p.layers.length} layers, ${p.nodes.length} nodes, ${p.params.length} params. Roads by tag <b>${esc(p.road_tag)}</b>.`
        + (p.notes.length ? `<ul style="margin: 6px 0 0 16px; padding: 0">${p.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "");
  });
}
function layerCard(l, earlier) {
  const open = state.pcgOpen === l.id;
  const save = (change, now) => {
    Object.assign(l, change);
    clearTimeout(pcgSaveTimer);
    const run = async () => {
      const out = await rpc("pcg_layer_save", { net_id: state.net, layer_id: l.id, layer: JSON.stringify(l) });
      if (out.ok === false) showError(out.error);
      else { showError(""); Object.assign(l, out.item); schedulePcgPreview(); }
    };
    now ? run().then(renderPcgPane) : (pcgSaveTimer = setTimeout(run, 350));
  };
  // folded: icon, name, count and the switch only (where it places is inside the card)
  const enabled = toggle({ label: "", value: l.enabled, onChange: (v) => save({ enabled: v }, true) });
  enabled.el.setAttribute("aria-label", `${l.name} on`);
  enabled.el.setAttribute("title", l.enabled ? "On: placed in the preview and the build" : "Off");
  enabled.el.style.width = "auto";
  enabled.el.style.marginBottom = "0";
  const headBtn = h("button", { type: "button", class: "rn-card__head", style: "width: 100%; border: 0; background: none; color: inherit; font: inherit; padding: 0; cursor: pointer; margin: 0", "aria-expanded": open ? "true" : "false" },
    icon(KIND_ICON[l.kind] || "layers", 16), h("span", { class: "rn-card__name", style: "text-align: left" }, l.name),
    h("span", { class: "rn-tag", "data-pcg-count": l.name, title: "Placed by the preview" }, "—"));
  headBtn.addEventListener("click", () => { state.pcgOpen = open ? "" : l.id; renderPcgPane(); });
  const card = h("div", { class: "rn-card", "data-live-id": `layer:${l.id}`, style: l.enabled ? "" : "opacity: .6" },
    h("div", { style: "display: flex; align-items: center; gap: 8px" }, headBtn, enabled.el));
  if (!open) return card;
  const num = (key, label, min, max, step, fmt, hint) =>
    slider({ label, min, max, step, value: l[key], fmt, hint, onChange: (v) => save({ [key]: v }) }).el;
  const sw = (key, label, ic) => toggle({ label, iconName: ic, value: l[key], onChange: (v) => save({ [key]: v }) }).el;
  const name = field({ label: "Name", value: l.name, onChange: (v) => save({ name: v.trim() || l.name }, true) });
  const seed = field({ label: "Seed", value: l.seed, onChange: (v) => save({ seed: parseInt(v, 10) || 0 }),
    button: { icon: "dice", title: "New random seed", onClick: (input) => { input.value = Math.floor(Math.random() * 99999); save({ seed: +input.value }); } } });
  const spacing = l.kind === "scatter"
    ? [num("density", "Density", 0.01, 50, 0.01, (v) => `${v} per 100 m²`, "Points per 100 square metres before pruning")]
    : [num("spacing_m", "Spacing along the road", 2, 800, 1, m), num("setback_m", "Distance from the kerb", 0, 80, 0.5, m)];
  if (l.kind === "buildings") spacing.push(h("div", { class: "rn-field__label", style: "margin: 4px 0 6px" }, "Building forms (each lot picks one)"),
    h("div", { class: "dk-row", style: "margin-bottom: 12px" }, ...BUILDING_FORMS.map(([f, label]) => chip({ label, iconName: "city",
      value: !!(l.forms || {})[f], onChange: (v) => {
        const forms = { ...(l.forms || {}) };
        if (v) forms[f] = forms[f] || 1; else delete forms[f];
        save({ forms: Object.keys(forms).length ? forms : { box: 1 } }, true);
      } }).el)),
    num("size_jitter", "Size variety", 0, 0.6, 0.05, pct, "How much each lot's width and depth differ from the next"),
    choices({ label: "Build", value: l.build || "mesh", options: [["mesh", "One mesh, fitted", "A whole kit building stretched to the lot", "city"],
      ["modular", "Modular storeys", "BuildingKit storeys and wall modules by grammar", "layers"]],
      onChange: (v) => save({ build: v }, true) }).el,
    h("div", { class: "rn-hint", style: "margin: -6px 0 12px" },
      "Modular stacks BuildingKit storeys and 5.12 m wall modules on each lot, so windows and doors keep their size. Factories and warehouses always use whole meshes."));
  if (l.kind === "buildings") spacing.push(
    num("width_m", "Frontage", 1, 200, 1, m, "Width along the street"), num("depth_m", "Depth", 1, 200, 1, m, "Depth away from the street"),
    num("height_min_m", "Lowest", 1, 600, 1, m), num("height_max_m", "Tallest", 1, 600, 1, m, "Pick meshes in this height range"));
  const meshRows = l.meshes.map((mesh, k) => h("div", { class: "rn-card", style: "padding: 8px; margin-bottom: 6px" },
    h("div", { class: "rn-code", style: "margin-bottom: 6px" }, mesh.path),
    slider({ label: "Weight", min: 0.1, max: 10, step: 0.1, value: mesh.weight, fmt: (v) => `×${v}`,
      onChange: (v) => { l.meshes[k].weight = v; save({ meshes: l.meshes }); } }).el,
    button("Remove", null, () => { l.meshes.splice(k, 1); save({ meshes: l.meshes }, true); }, "ghost")));
  const addMesh = field({ label: "Add a mesh (asset path)", placeholder: "/<mount>/MovieTheater/WorldGen/Kit/SM_Tree_A",
    button: { icon: "check", title: "Add mesh", onClick: (input) => {
      const path = input.value.trim();
      if (!path) return input.focus();
      l.meshes.push({ path, weight: 1 });
      save({ meshes: l.meshes }, true);
    } } });
  const avoid = earlier.length ? h("div", { class: "dk-row" }, ...earlier.map((o) => chip({ label: o.name, iconName: KIND_ICON[o.kind] || "layers",
    value: l.avoid.includes(o.name),
    onChange: (v) => save({ avoid: v ? [...l.avoid, o.name] : l.avoid.filter((a) => a !== o.name) }) }).el))
    : h("div", { class: "rn-hint" }, "First layer: nothing placed before it.");
  card.append(
    name.el,
    choices({ label: "Kind", value: l.kind, options: KIND_OPTS, onChange: (v) => save({ kind: v }, true) }).el,
    ...(l.kind === "at_junctions" ? [num("corners", "Corners per junction", 1, 4, 1, (v) => String(v))] : []),
    choices({ label: "Where", value: l.where, options: WHERE_OPTS, onChange: (v) => save({ where: v }, true) }).el,
    section("Spacing", ...spacing),
    section("Look", num("scale_min", "Smallest", 0.1, 10, 0.05, (v) => `×${v}`), num("scale_max", "Largest", 0.1, 10, 0.05, (v) => `×${v}`),
      num("sink_cm", "Sink into ground", 0, 500, 5, (v) => `${v} cm`, "Roots and bases never float on slopes"),
      sw("random_yaw", "Random turn", "refresh"), sw("upright", "Stay upright on slopes", "top")),
    section("Keep clear", num("footprint", "Footprint", 0.1, 2, 0.05, pct, "Bounds used for overlap checks: shrink to the trunk, grow for wide bases"),
      sw("avoid_roads", "Off the roads", "road"), num("road_clear_m", "Road clearance", 0, 50, 0.5, m),
      sw("self_prune", "No overlaps inside this layer", "check"),
      h("div", { class: "rn-field__label", style: "margin: 4px 0 6px" }, "Keep clear of"), avoid),
    section("Meshes", ...meshRows, addMesh.el, sw("collision", "Collision (off for grass and dense scatter)", "shadow")),
    section("Random", seed.el),
    button("Delete layer", null, async () => {
      const ok = await call("ui.confirm", { title: "Delete layer", danger: true, confirm: "Delete", message: `Delete "${l.name}"? Nothing in UEFN changes.` }).catch(() => null);
      if (!ok?.confirmed) return;
      const out = await rpc("pcg_layer_delete", { net_id: state.net, layer_id: l.id });
      if (out.ok === false) return showError(out.error);
      await loadLayers();
    }, "danger"));
  return card;
}
async function loadLayers() {
  if (!state.net) { state.layers = []; renderPcgPane(); renderLayerList(); return; }
  const [out, assets, sets] = await Promise.all([rpc("pcg_layers", { net_id: state.net }), rpc("pcg_assets", { net_id: state.net }), rpc("presets")]);
  state.layers = out.ok === false ? [] : out.items || [];
  state.presets = sets.ok === false ? [] : sets.items || [];
  renderLayerList();
  state.assetSet = assets.ok === false ? "own" : assets.asset_set;
  state.trees = assets.ok === false ? "kit" : assets.trees || "kit";
  const pane = $("pane-pcg");
  if (pane && pane.contains(document.activeElement) && document.activeElement !== document.body) {
    // the person is typing or dragging in a layer card: redraw once they leave it, never under their hands
    pane.addEventListener("focusout", () => setTimeout(renderPcgPane, 0), { once: true });
  } else {
    renderPcgPane();
  }
  schedulePcgPreview(100);
}
// tile: push only that one tile (the inspector's button): rebuilt even when nothing in it changed, every other
// tile, the roads and the rest of the level left alone
async function onPcgPlace(tile) {
  if (!state.net) return;
  const one = typeof tile === "string" && tile ? { tiles: tile, force: true } : {};
  const ask = await rpc("pcg_build", { net_id: state.net, ...one });
  if (ask.ok !== false) return alert("Up to date", ask.note || "Nothing changed since the last push.");
  if (!/confirm=true/.test(ask.error || "")) return showError(ask.error);
  if (!ask.build?.length && !ask.remove?.length) return alert("Up to date", "Nothing changed since the last push: every tile is left as it is.");
  const ok = await call("ui.confirm", { title: one.tiles ? `Push tile ${one.tiles} to UEFN` : "Push city to UEFN", confirm: "Push",
    message: (ask.error || "").replace(" Call again with confirm=true.", "") + " Only those tiles change in the open UEFN level. Don't touch UEFN until it finishes." }).catch(() => null);
  if (!ok?.confirmed) return;
  busy(one.tiles ? `Pushing tile ${one.tiles} to UEFN…` : `Pushing ${ask.build.length} changed tiles to UEFN…`);
  const out = await rpc("pcg_build", { net_id: state.net, confirm: true, ...one });
  busy("");
  if (out.ok === false) return showError(out.error + (out.note ? ` ${out.note}` : ""));
  showError("");
  alert("Pushed", `${out.built_tiles.length} tiles rebuilt, ${out.removed_tiles.length} removed, ${out.unchanged_tiles} left as they were.`);
}

function syncControls() {
  for (const [k, c] of Object.entries(ctl)) if (k in state.gen && c?.set) c.set(state.gen[k]);
  ctl.look?.set(state.view.look);
  ctl.shadows?.set(state.view.shadows);
  ctl.live?.set(state.live);
}

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
  $("foot").hidden = city || !GEN_TABS.has(state.tab);
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
// Moving, resizing, restyling, adding and deleting cities never rebuilds per edit: each edit shows at once and
// waits; a moment after the last one, one rebuild takes them all, and edits made while a rebuild runs go into the
// next one. A rebuild per edit kept the person waiting, and rebuilds side by side undid each other's edits.
const edits = { list: [], running: false, timer: 0 };
const QUIET_MS = 1200;  // the pause after the last edit before the rebuild starts
function queueEdit(title, apply, after) {
  edits.list.push({ title, apply, after });
  clearTimeout(edits.timer);
  const n = edits.list.length;
  if (edits.running) {
    busy(loading.title || title, { text: `${n} more change${n > 1 ? "s" : ""} waiting for the next rebuild…`,
      step: loading.step, of: loading.of });
    return;
  }
  busy(title, { text: n > 1 ? `${n} changes: rebuilding once you pause…` : "Rebuilding once you pause…" });
  edits.timer = setTimeout(runEdits, QUIET_MS);
}
async function runEdits() {
  if (edits.running) return;
  edits.running = true;
  try {
    while (edits.list.length) {
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
  needsDraw = true;
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

// ------------------------------------------------------------------ edit pane
function renderEditPane() {
  const tune = (key, label, min, max, step, fmt) => slider({ label, min, max, step, value: state.tune[key], fmt,
    onChange: (v) => { state.tune[key] = v; savePrefs(); } }).el;
  $("pane-edit").replaceChildren(
    section("Pieces used", h("div", { id: "stats" })),
    section("Selected", h("div", { id: "sel" })),
    section("Thin and tune", tune("side", "Side falloff", 0, 30, 0.5, (v) => `${v} m`), tune("end", "End falloff", 0, 30, 0.5, (v) => `${v} m`),
      tune("tol", "Straight tolerance", 5, 300, 5, (v) => `${v} cm`),
      h("div", { class: "rn-btns" },
        button("Thin and tune", "wrench", onTune), button("Check and repair", "check", onRepair),
        button("Rescan ground", "refresh", onRescan, "secondary", "Read the terrain heights again (read-only). Do it while only the terrain is in the level"),
        button("Drape on ground", "terrain", onDrape, "secondary", "Put this actor's live road points onto the stored ground, smoothed, junctions flat"))));
  renderStats(state.stats);
  renderSelection();
}
function renderStats(st) {
  state.stats = st;
  const host = $("stats");
  if (!host) return;
  if (!st || st.ok === false) { host.innerHTML = `<div class="rn-hint">Open a network to see its pieces.</div>`; return; }
  const t = st.total || {};
  const meshes = [...Object.entries(t.segment_meshes || {}), ...Object.entries(t.point_meshes || {})].sort((a, b) => b[1] - a[1]);
  host.innerHTML = `<div class="rn-stat-row">
      <div class="rn-stat"><b>${((t.road_length_m || 0) / 1000).toFixed(1)}</b><span>km of road</span></div>
      <div class="rn-stat"><b>${t.points ?? 0}</b><span>points</span></div>
      <div class="rn-stat"><b>${t.segments ?? 0}</b><span>segments</span></div></div>
    <div class="rn-kv">${meshes.map(([k, v]) => `<span title="${esc(k)}">${esc(k.replace(/^SM_/, ""))}</span><span>${esc(v)}</span>`).join("")}</div>`;
}
function renderSelection() {
  const host = $("sel");
  if (!host) return;
  const s = state.sel;
  if (!s || !state.geo?.actors?.[s.actor]) {
    host.innerHTML = `<div class="rn-hint">Click a junction, or turn on Points in the view and click a spline point.</div>`;
    return;
  }
  const p = s.p;
  host.innerHTML = `<div class="rn-card">
    <div class="rn-card__head">${iconHTML("pin")}<span class="rn-card__name">${esc(state.geo.actors[s.actor].label)} · #${p[0]}</span><span class="rn-tag">${esc(p[7] ? "piece" : "road point")}</span></div>
    <div class="rn-kv"><span>Piece</span><span>${esc(p[7] || "—")}</span><span>Width</span><span>${(p[6] / 100).toFixed(1)} m</span>
      <span>Shapes ground</span><span>${p[8] ? "Yes" : "No"}</span></div>
    <div class="rn-section">Location (cm)</div>
    <div class="rn-xyz"><input class="dk-input" id="px" value="${p[1]}" aria-label="X"><input class="dk-input" id="py" value="${p[2]}" aria-label="Y"><input class="dk-input" id="pz" value="${p[3]}" aria-label="Z"></div>
    <div class="rn-btns" style="margin-top: 8px" id="selBtns"></div></div>`;
  const row = $("selBtns");
  row.append(button("Z −1 m", null, () => { $("pz").value = (+$("pz").value - 100).toFixed(1); }),
    button("Z +1 m", null, () => { $("pz").value = (+$("pz").value + 100).toFixed(1); }));
  const save = button("Save point", "check", async () => {
    const out = await rpc("move_point", { net_id: state.net, actor: s.actor, index: p[0], x: +$("px").value, y: +$("py").value, z: +$("pz").value });
    if (out.ok === false) return showError(out.error);
    showError("");
    await loadGeometry(true);
  }, "primary");
  save.style.gridColumn = "1 / -1";
  row.append(save);
}
const needActor = () => { if (!state.net || !state.actor) { showError("Pick a road actor on the left first."); return false; } return true; };
async function onTune() {
  if (!needActor()) return;
  busy("Thinning and tuning…");
  const out = await rpc("tune", { net_id: state.net, actor: state.actor, thin_tolerance_cm: state.tune.tol,
    side_falloff: state.tune.side * 100, end_falloff: state.tune.end * 100 });
  busy("");
  if (out.ok === false) return showError(out.error);
  showError("");
  alert("Tuned", `${out.label}: ${out.points_before} → ${out.points} points, falloff ${out.side_falloff / 100} m / ${out.end_falloff / 100} m. Reimport to apply.`);
  await loadGeometry(true);
}
async function onRepair() {
  if (!needActor()) return;
  const found = await rpc("check", { net_id: state.net, actor: state.actor });
  if (found.ok === false) return showError(found.error);
  if (found.clean) return alert("Check", `${found.label}: no broken nodes, links or tangents.`);
  const c = found.counts;
  const ok = await call("ui.confirm", { title: "Repair road data", confirm: "Repair",
    message: `${found.label}: ${c.giant_nodes} giant nodes, ${c.long_links} over-long join links, ${c.bad_tangents} runaway tangents. Repair drops those links and strips the giant junction pieces (only the stored network changes, not UEFN).` }).catch(() => null);
  if (!ok?.confirmed) return;
  const out = await rpc("repair", { net_id: state.net, actor: state.actor });
  if (out.ok === false) return showError(out.error);
  showError("");
  alert("Repaired", `${out.label}: ${out.links_dropped} links dropped, ${out.nodes_fixed} nodes fixed.`);
  await loadGeometry(true);
}
async function onRescan() {
  if (!state.net) return;
  busy("Reading the terrain heights…");
  const out = await rpc("scan_ground", { net_id: state.net });
  busy("");
  if (out.ok === false) return showError(out.error);
  showError("");
  await loadGeometry(true);
}
async function onDrape() {
  if (!needActor()) return;
  busy("Putting the roads on the ground…");
  const out = await rpc("drape", { net_id: state.net, actor: state.actor });
  busy("");
  if (out.ok === false) return showError(out.error);
  showError("");
  alert("Draped", `${out.label}: ${out.draped} points on the ground (median shift ${out.median_shift_cm} cm, max ${out.max_shift_cm} cm). Reimport to see it in UEFN.`);
  await loadGeometry(true);
}

// ------------------------------------------------------------------ UEFN pane
function renderUefnPane() {
  const generated = !!currentNet()?.generated;
  const hm = h("div", { id: "hmInfo" });
  $("pane-uefn").replaceChildren(
    section("Bring the world in", h("div", { class: "rn-steps" },
      h("div", { class: "rn-step" }, h("div", { class: "rn-stack" },
        h("div", {}, h("b", {}, "Terrain"), h("div", { class: "rn-hint" }, generated && !currentNet()?.generated?.ground_from
          ? "Make the heightmap, then import it in UEFN as a new landscape. Your current landscape is never changed."
          : "This network uses an existing terrain, so there is nothing to import.")),
        generated && !currentNet()?.generated?.ground_from ? button("Make heightmap", "download", onHeightmap) : null, hm)),
      h("div", { class: "rn-step" }, h("div", { class: "rn-stack" },
        h("div", {}, h("b", {}, "Roads"), h("div", { class: "rn-hint" }, "Builds every junction piece, then pastes each road actor as live splines. Keep your hands off UEFN while it runs.")),
        button("Import all into UEFN", "upload", onImportAll, "primary"))))),
    section("Selected actor", h("div", { class: "rn-btns" },
      button("Reimport", "refresh", onReimport, "secondary", "Remove this actor in UEFN and paste this version"),
      button("Copy text", "copy", onCopy, "secondary", "Spline text on the clipboard for a manual paste")),
    h("div", { class: "rn-hint", style: "margin-top: 8px" }, "The terrain is never written: the landscape's Splines edit layer follows the roads.")));
}
// the World section's heightmap: a world on New terrain makes a PNG to import as a new landscape in UEFN
function renderWorldHeightmap(n) {
  const box = $("worldHeightmap");
  if (!box) return;
  if (!n?.generated) return box.replaceChildren();
  if (n.generated.ground_from) {
    return box.replaceChildren(h("div", { class: "rn-hint", style: "margin-top: 8px" },
      "This world sits on scanned ground, so it has no heightmap of its own. For a heightmap: pick New terrain above, "
      + "Generate, then Make heightmap."));
  }
  box.replaceChildren(h("div", { class: "rn-stack", style: "margin-top: 8px" },
    button("Make heightmap", "download", onHeightmap, "secondary",
      "Write this world's terrain (lots levelled in) as a 16-bit PNG and the Landscape import settings"),
    h("div", { id: "hmInfoWorld" })));
}
async function onHeightmap() {
  if (!state.net) return;
  busy("Writing the heightmap…");
  const out = await rpc("heightmap", { net_id: state.net });
  busy("");
  if (out.ok === false) return showError(out.error);
  showError("");
  ($("hmInfoWorld") || $("hmInfo")).innerHTML = `<div class="rn-card"><div class="rn-kv"><span>Resolution</span><span>${esc(out.resolution)}</span>
    <span>Location</span><span>${esc(out.location.join(", "))}</span><span>Scale</span><span>${esc(out.scale.join(", "))}</span></div>
    <div class="rn-section">File</div><div class="rn-code">${esc(out.path)}</div>
    <div class="rn-hint" style="margin-top: 8px">${esc(out.how)}</div></div>`;
}
async function onImportAll() {
  if (!state.net) return;
  const ask = await rpc("import_all", { net_id: state.net });
  if (ask.ok !== false) return alert("Up to date", "Every road actor in UEFN already matches the plan.");
  const ok = await call("ui.confirm", { title: "Push roads to UEFN", danger: true, confirm: "Push", message: `${ask.error} Don't touch UEFN until it finishes.` }).catch(() => null);
  if (!ok?.confirmed) return;
  busy("Building junction pieces and pasting the changed roads into UEFN…");
  const out = await rpc("import_all", { net_id: state.net, confirm: true });
  busy("");
  if (out.ok === false) return showError(out.error);
  showError("");
  alert("Pushed", Object.entries(out.done).map(([k, v]) => `${k}: ${v.pieces} pieces`).join("\n")
    + (out.unchanged?.length ? `\nUnchanged (left alone): ${out.unchanged.join(", ")}` : ""));
}
async function onReimport() {
  if (!needActor()) return;
  const label = state.geo?.actors?.[state.actor]?.label || state.actor;
  const ok = await call("ui.confirm", { title: "Reimport into UEFN", danger: true, confirm: "Reimport",
    message: `This removes "${label}" from the open UEFN level and pastes this version as live splines. Don't touch UEFN until it finishes.` }).catch(() => null);
  if (!ok?.confirmed) return;
  busy(`Reimporting ${label}…`);
  const out = await rpc("reimport", { net_id: state.net, actor: state.actor, confirm: true });
  busy("");
  if (out.ok === false) return showError(out.error + (out.note ? " " + out.note : ""));
  showError("");
  alert("Reimported", `${label}: ${out.pieces} pieces in UEFN as live splines.`);
}
async function onCopy() {
  if (!needActor()) return;
  const out = await rpc("copy_text", { net_id: state.net, actor: state.actor });
  out.ok === false ? showError(out.error) : alert("Copied", out.note || "Spline text is on the clipboard.");
}

// ------------------------------------------------------------------ toolbar + hud
function renderToolbar() {
  const viewChip = (key, label, ic) => chip({ label, iconName: ic, value: state.view[key], onChange: (v) => { state.view[key] = v; savePrefs(); buildScene(); } }).el;
  $("toolbar").replaceChildren(
    button("Frame all", "frame", () => frame()), button("Top view", "top", () => { orbit.el = 1.55; placeCamera(); }),
    h("span", { class: "rn-sep" }),
    viewChip("terrain", "Terrain", "terrain"), viewChip("water", "Water", "water"), viewChip("districts", "Districts", "city"),
    viewChip("pcg", "PCG", "layers"), viewChip("points", "Points", "points"),
    h("span", { class: "rn-sep" }),
    // the exact walls, roofs and ground the push builds, round the view's centre: one click, where the eye already is
    chip({ label: "Real pieces", iconName: "city", value: !!state.exact,
      onChange: (v) => { if (v) loadExact(); else { state.exact = null; buildScene(); } } }).el);
}
function renderHud() {
  const n = currentNet();
  const t = state.stats?.total;
  $("hud").innerHTML = n ? `<b>${esc(n.name)}</b><span>${state.cities.length ? `${state.cities.length} cities · ` : ""}${t ? `${((t.road_length_m || 0) / 1000).toFixed(1)} km of road` : ""}</span>` : "";
  renderWorldHeightmap(n);
  const ws = $("worldSummary");
  if (ws) ws.innerHTML = n ? `<div class="rn-card"><div class="rn-card__head">${iconHTML(n.generated ? "world" : "network")}<span class="rn-card__name">${esc(n.name)}</span><span class="rn-tag">${n.generated ? "generated" : "from UEFN"}</span></div>
      <div class="rn-kv"><span>Road actors</span><span>${(n.actors || []).length}</span><span>Cities</span><span>${state.cities.length || "—"}</span>
      <span>Road length</span><span>${t ? ((t.road_length_m || 0) / 1000).toFixed(1) + " km" : "—"}</span></div></div>`
    : `<div class="rn-hint">Nothing open yet. Set the options and press Generate.</div>`;
}

// ------------------------------------------------------------------ data
const currentNet = () => state.nets.find((n) => n.id === state.net);
const optionsOf = (g) => Object.fromEntries(OPTION_KEYS.map((k) => [k, g[k]]));
async function loadNets(autoOpen = true) {
  const out = await rpc("list");
  if (out.ok === false) {
    $("nets").innerHTML = `<div class="dk-error"><div class="dk-error__title">Couldn't load networks</div>${esc(out.error)}</div>`;
    return;
  }
  state.nets = out.items || [];
  syncGenerate();
  $("nets").replaceChildren(...(state.nets.length ? state.nets.map((n) => {
    const b = h("button", { type: "button", class: "dk-nav-item", "data-live-id": `net:${n.id}`, "data-net": n.id, "aria-current": n.id === state.net ? "page" : null },
      icon(n.generated ? "world" : "network", 16), h("span", {}, n.name), h("span", { class: "rn-tag" }, n.generated ? "gen" : "UEFN"));
    b.addEventListener("click", () => openNet(n.id));
    return b;
  }) : [h("div", { class: "dk-empty" }, h("div", { class: "dk-empty__title" }, "No networks yet"), "Generate a world, or pull roads from UEFN below.")]));
  if (state.net && !currentNet()) state.net = "";
  renderWorldPick();
  if (!autoOpen) return;
  if (!state.net && state.nets[0]) openNet(state.nets[0].id);
  else if (!state.net) buildScene();
}
async function openNet(id) {
  state.net = id; state.framed = false; state.sel = null; state.actor = ""; state.pcg = {}; state.exact = null;
  state.cityPick = ""; state.block = null;
  settleCity();
  call("prefs.set", { id: "last_net", value: id }).catch(() => {});
  renderWorldPick();
  $("syncBadge")?.replaceChildren();  // filled after this world's city preview (loadPcgPreview)
  document.querySelectorAll("[data-net]").forEach((b) => b.toggleAttribute("aria-current", b.dataset.net === id));
  const g = currentNet()?.generated;
  if (g) {  // the generate tabs show this world's own settings
    Object.assign(state.gen, { name: currentNet().name, seed: g.seed ?? state.gen.seed, size_km: g.size_km ?? state.gen.size_km,
      relief_m: g.relief_m ?? state.gen.relief_m, cities: g.cities ?? state.gen.cities, ground: g.ground_from ? "net" : "new", ...(g.options || {}) });
    syncControls();
    syncGenerate();
  }
  document.body.dataset.liveView = `${id}:${state.tab}`;
  renderUefnPane();
  await loadGeometry(false);
}
let loadSeq = 0;
async function loadGeometry(keep, quiet = false) {
  if (!state.net) return;
  const seq = ++loadSeq, net = state.net;
  if (!quiet) busy("Loading the world", { text: "Roads, cities and their plans…" });
  const [geo, st, cities] = await Promise.all([loadAllGeometry(net), rpc("stats", { net_id: net }), rpc("cities", { net_id: net })]);
  if (seq !== loadSeq || net !== state.net) return;  // a newer load (or another world) took over
  if (!quiet || loading.title === "Loading the world") busy("");
  if (geo.ok === false) return showError(`Couldn't load ${currentNet()?.name || "the world"}: ${geo.error}`, () => loadGeometry(keep));
  state.geo = geo;
  state.cities = cities.ok === false ? [] : cities.items || [];
  if (cities.ok !== false) {  // what a block can be made, and how a city's edge is drawn: the generator's own lists
    Object.assign(state, { blockKinds: cities.block_kinds || {}, blockZones: cities.block_zones || [],
      shapePoints: cities.shape_points || 16, shapeRange: cities.shape_range || [0.74, 1.26] });
  }
  if (state.cities.length) {  // the Cities count follows the world's cities (added or removed ones included)
    state.gen.cities = state.cities.length;
    ctl.cities?.set?.(state.cities.length);
  }
  state.fadeNext = true;
  renderActors(geo);
  renderCityList();
  renderStats(st);
  renderCitiesPane();
  loadLayers();
  if (keep && state.sel) {
    const a = geo.actors[state.sel.actor];
    const p = a && a.pts.find((q) => q[0] === state.sel.p[0]);
    state.sel = p ? { actor: state.sel.actor, p } : null;
  }
  renderSelection();
  $("labels").innerHTML = "";
  buildScene();
  showError("");
  loadHistory();
  if (lake.pick && !lakesOf().some((l) => l.id === lake.pick)) lake.pick = "";
  drawLakes();
  if (state.tool === "lake") lakeBanner();
}
// A whole city is several MB: the terrain comes first, then each actor in pages of PAGE points
// and segments, so no single reply nears the panel bridge's 1 MB line.
const PAGE = 2500;
async function loadAllGeometry(net) {
  const [base, info] = await Promise.all([rpc("geometry", { net_id: net, actor: "-" })  /* "-" matches no actor: terrain and props only */, rpc("get", { net_id: net })]);
  if (base.ok === false) return base;
  if (info.ok === false) return info;
  const actors = {};
  // small actors (airports, freeways, highways: a few dozen points) share a call; big ones page on their own
  const batches = [];
  let group = [], room = PAGE;
  for (const [aslug, a] of Object.entries(info.actors || {})) {
    actors[aslug] = { label: a.label, pts: [], segs: [] };
    const size = Math.max(a.stats?.points ?? PAGE, a.stats?.segments ?? PAGE);  // unknown: a page of its own
    if (size >= PAGE) { batches.push([aslug]); continue; }
    if (size > room) { batches.push(group); group = []; room = PAGE; }
    group.push(aslug);
    room -= size;
  }
  if (group.length) batches.push(group);
  await Promise.all(batches.map(async (slugs) => {
    for (let offset = 0; ; offset += PAGE) {
      const page = await rpc("geometry", { net_id: net, actor: slugs.join(","), offset, limit: PAGE, terrain: false });
      if (page.ok === false) throw new Error(page.error);
      let more = false;
      for (const aslug of slugs) {
        const part = page.actors?.[aslug];
        if (!part) continue;
        actors[aslug].pts.push(...part.pts);
        actors[aslug].segs.push(...part.segs);
        if (offset + PAGE < Math.max(part.points_total, part.segments_total)) more = true;
      }
      if (!more) break;
    }
  })).catch((err) => { base.ok = false; base.error = err.message; });
  return { ...base, actors };
}
function renderActors(geo) {
  const rows = Object.entries(geo.actors || {});
  if (state.actor && !geo.actors[state.actor]) state.actor = "";
  $("actors").replaceChildren(...(rows.length ? [["", { label: "All actors", segs: rows.flatMap(([, a]) => a.segs) }], ...rows].map(([slug, a]) => {
    const b = h("button", { type: "button", class: "dk-nav-item", "data-live-id": `actor:${slug || "all"}`, "data-actor": slug, "aria-current": slug === state.actor ? "page" : null },
      icon(slug ? "route" : "layers", 16), h("span", {}, a.label), h("span", { class: "rn-count" }, a.segs.length));
    b.addEventListener("click", () => {
      state.actor = slug;
      document.querySelectorAll("[data-actor]").forEach((x) => x.toggleAttribute("aria-current", x === b));
      buildScene();
    });
    return b;
  }) : [h("div", { class: "rn-hint" }, "No road actors in this network.")]));
}

async function generate() {
  const other = peerBusy();
  if (other) return flash(`${other.name} is rebuilding this world. Yours can start when theirs is in.`);
  const g = state.gen;
  const name = (g.name || "").trim();
  if (!name) { openTab("world"); ctl.name?.input.focus(); return showError("Give the world a name."); }
  // the ground: a network pulled from UEFN, or (regenerating a world that sits on one) the ground it already uses
  const cur = currentNet();
  const groundFrom = g.ground !== "net" ? "" : !cur ? "" : cur.generated ? (cur.generated.ground_from || cur.id) : cur.id;
  if (g.ground === "net" && !groundFrom) {
    openTab("world");
    return showError("Open a network pulled from UEFN (or a world that sits on one) first: its ground is what the cities sit on.");
  }
  const prev = state.nets.find((n) => n.name.toLowerCase() === name.toLowerCase())?.generated;
  // the same world: keep its cities (moved, added, removed ones and their styles) unless the Cities count or the
  // seed changed; a different count places that many new cities
  const same = prev && prev.seed === g.seed && prev.size_km === g.size_km;
  const have = cur && cur.name.toLowerCase() === name.toLowerCase() ? state.cities.length : prev?.cities;
  const keep = same && have === g.cities;
  state.generating = true;
  busy(`Generating ${name}`, { text: "Starting…", step: 0, of: 1 });
  tellGroup();
  const out = await rpc("generate", { name, seed: g.seed, cities: keep ? "" : String(g.cities), size_km: g.size_km, relief_m: g.relief_m,
    ground_from: groundFrom, options: JSON.stringify(optionsOf(g)) });
  state.generating = false;
  busy("");
  tellGroup();
  if (out.ok === false) return showError(out.error);
  showError("");
  const reframe = out.id !== state.net;
  state.net = out.id;
  await loadNets();
  state.framed = !reframe;
  await openNet(out.id);
}

// pull row (left panel)
const pullName = h("input", { class: "dk-input", id: "newName", placeholder: "Network name", value: "City", "aria-label": "Network name" });
const pullBtn = h("button", { type: "button", class: "dk-btn dk-btn--secondary dk-btn--icon", title: "Read every road actor from the open UEFN level", "aria-label": "Pull from UEFN" }, icon("download", 16));
pullBtn.addEventListener("click", async () => {
  const name = pullName.value.trim();
  if (!name) return pullName.focus();
  busy("Reading road actors from UEFN…");
  const out = await rpc("pull", { name });
  busy("");
  if (out.ok === false) return showError(out.error);
  state.net = out.id;
  await loadNets();
  await openNet(out.id);
});
$("pullRow").append(pullName, pullBtn);

// ------------------------------------------------------------------ host events
window.addEventListener("message", (ev) => {
  const e = ev.data?.channel === CHANNEL ? ev.data.event : null;
  if (!e) return;
  if (e.type === "panel.visibility") {
    visible = !!e.visible;
    needsDraw = true;
    if (visible && (reloads.geometry || reloads.layers)) reloadSoon();  // changes made while the tab was hidden
  }
  if (e.type === "appearance_theme") { themeColors(); buildScene(); }
  if (e.type === "plugin_scope_changed") {
    if (e.reason === "synced") applySync(e.keys);  // teammates' changes: reload only what changed, keep what's open
    else { state.net = ""; state.geo = null; state.cities = []; state.layers = []; state.pcg = {}; loadNets(); buildScene(); }
  }
  if (e.type === "road_network_changed" && (!e.id || e.id === state.net)) {
    if (e.what === "progress") {  // generate's stages: the loading card follows them
      if (e.done) {  // a background job (a city push started by an agent) finished: its own card goes away
        if (loading.job) { loading.job = false; busy(""); reloadSoon("layers"); }
      } else if (e.title && (!loading.title || loading.job)) {
        // a job nobody started from this panel still shows its bar here: never a silent half hour
        loading.job = true;
        busy(e.title, { text: e.text, step: e.step || 0, of: e.of || 0 });
      } else if (loading.title) busy(loading.title, { text: e.text, step: (e.step || 0) + 1, of: (e.of || 1) + 1 });
    } else if (e.what === "hold") {
      refreshHolds();  // a ducky on this PC took or let go of a city
    } else if (e.what === "city") {
      // a city was added or removed: the regenerate that follows reloads everything
    } else if (state.generating || edits.running) {
      // our own rebuild: reloaded when it returns
    } else reloadSoon(e.what === "pcg" ? "layers" : "geometry");
  }
  if (e.type === "live.memberState") onMemberState(e.from, e.state);
  if (e.type === "live.message" && e.topic === "pcg") onPeerPcg(e.data);
  if (e.type === "live.state") {  // live went on or off: off, nobody is here any more
    live.on = !!e.on;
    if (!e.on) { for (const k of Object.keys(peers)) delete peers[k]; drawPeers(); }
    else tellGroup();
  }
  if (e.type === "live.shared" && e.key === "selection") onSharedSelection(e.value, e.by);
});
window.addEventListener("ducky-live-goto", (ev) => {  // the group moved to another world or tab
  const { net, tab } = parseLiveView(ev.detail?.view || ev.detail?.record);
  if (net && net !== state.net && state.nets.some((n) => n.id === net)) openNet(net);
  if (tab && tab !== state.tab && TABS.some((t) => t[0] === tab)) openTab(tab);
});

// An agent edits a world point by point and every change sends an event. A whole-world reload per event piled up
// dozens of reloads at once, and the panel lagged while nobody touched it (Nov 11 2026): events now only mark what
// changed, and one quiet reload follows a pause (none while the tab is hidden: it catches up when shown).
const reloads = { geometry: false, layers: false, timer: 0, running: false };
const RELOAD_PAUSE_MS = 800;
function reloadSoon(what) {
  if (what) reloads[what] = true;
  clearTimeout(reloads.timer);
  reloads.timer = setTimeout(runReloads, RELOAD_PAUSE_MS);
}
async function runReloads() {
  if (!visible || reloads.running) return;  // shown again / the running reload ends: it looks again
  if (state.generating || edits.running) { reloads.geometry = reloads.layers = false; return; }  // they reload after
  reloads.running = true;
  try {
    while (reloads.geometry || reloads.layers) {
      const geo = reloads.geometry, layers = reloads.layers;
      reloads.geometry = reloads.layers = false;
      if (geo) await loadGeometry(true, true);  // keeps the selection and camera; reloads the layers too
      else if (layers) await loadLayers();
    }
  } finally {
    reloads.running = false;
  }
}

async function applySync(keys) {
  const plan = syncPlan(keys, state.net);
  if (plan.nets) await loadNets(false);
  if (plan.geometry) reloadSoon("geometry");
  else if (plan.cities) {
    const c = await rpc("cities", { net_id: state.net });
    if (c.ok !== false) { state.cities = c.items || []; renderCitiesPane(); buildScene(); }
  }
  if (plan.layers && !plan.geometry) loadLayers();  // loadGeometry already reloads layers
}

// ------------------------------------------------------------------ live: bodies, holds, edits as they happen
// While the group is live everyone flies round the map as a ghost with their name under it: a body hovers over
// the spot each person looks at (your own shows while you move). The city a person has picked is theirs until
// they let go, as a Kanban card someone has open: the others see whose it is and can look, not change it. A city
// being dragged and a rebuild under way show on every screen as they happen; the change itself comes with sync
// (plugin_scope_changed) and is swapped in place, layer by layer.
// Everything here rides on each person's live state (live.setMyState): nothing is stored, nothing goes to a server.
const ME = Math.random().toString(36).slice(2, 10);  // this page, to tell its own pick from a teammate's
const live = { on: false, moved: 0, since: 0, idle: false, active: Date.now() };
const peers = {};  // device -> { name, tint, state, at (where its body is shown) }
const PEER_TINTS = ["blue", "green", "amber", "red"];
const tintOf = (name) => PEER_TINTS[[...String(name)].reduce((n, ch) => (n * 31 + ch.charCodeAt(0)) >>> 0, 7) % PEER_TINTS.length];
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
let camSent = 0, camTimer = 0;
function sendMyCamera() {  // my live state: where I look, the city I have, a city I am dragging, a rebuild I am running
  clearTimeout(camTimer);
  const now = Date.now(), every = drag?.city ? 90 : 250;
  if (now - camSent < every) { camTimer = setTimeout(sendMyCamera, every); return; }
  camSent = now;
  const t = orbit.target, m = drag?.city;
  call("live.setMyState", { state: { id: ME, net: state.net, tab: state.tab,
    city: live.idle ? "" : state.cityPick || "", since: live.since,
    busy: state.generating ? (loading.title || "Rebuilding") : "",
    ducky: (state.holds || []).filter((x) => x.kind === "ducky").map((x) => ({ city: x.city, who: x.who, task: x.task })),
    move: m ? { city: m.c.id, x: Math.round(m.to[0]), y: Math.round(m.to[1]) } : null,
    cam: { x: +t.x.toFixed(1), y: +t.y.toFixed(1), z: +t.z.toFixed(1), dist: Math.round(orbit.dist), az: +orbit.az.toFixed(3), el: +orbit.el.toFixed(3) } } }).catch(() => {});
}
const tellGroup = () => { if (liveReady) sendMyCamera(); };
function peerKey(from) { return String(from?.device || from?.user || from?.name || from || ""); }
function onMemberState(from, s) {
  const k = peerKey(from);
  if (!k) return;
  if (!s) delete peers[k];  // they left: what they had picked is free again
  else {
    const p = peers[k] ||= { name: String(from?.name || "Teammate"), tint: tintOf(from?.name || k), at: null };
    p.name = String(from?.name || p.name);
    p.state = s;
    live.on = true;
    if (s.busy && s.net === state.net) live.quietUntil = Date.now() + 120000;  // their change comes in without a loading card here
  }
  drawPeers();
}
// The teammate who has a city (picked it first and still has it picked), or null when it is free or mine. Everyone
// works it out from the same live states, so every screen names the same person.
function holderOf(id) {
  if (!id) return null;
  const here = (state.holds || []).find((x) => x.city === id && x.kind === "ducky");  // a ducky on this PC is changing it
  if (here) return { since: 0, id: `ducky:${here.who}`, name: here.who, tint: "accent", task: here.task };
  for (const p of Object.values(peers)) {  // ... or a teammate's ducky
    const dk = p.state?.net === state.net && (p.state.ducky || []).find((x) => x.city === id);
    if (dk) return { since: 0, id: `ducky:${dk.who}`, name: String(dk.who || `${p.name}'s ducky`), tint: p.tint, task: dk.task };
  }
  let best = null;
  for (const p of Object.values(peers)) {
    const s = p.state;
    if (!s || s.net !== state.net || s.city !== id) continue;
    const since = Number(s.since) || 0;
    if (!best || since < best.since || (since === best.since && String(s.id) < best.id)) best = { since, id: String(s.id), name: p.name, tint: p.tint };
  }
  if (!best) return null;
  const mine = state.cityPick === id && !live.idle;
  return mine && (live.since < best.since || (live.since === best.since && ME < best.id)) ? null : best;
}
function heldElsewhere(c) {  // true, and says whose it is, when a teammate or a ducky has this city
  const hd = c && holderOf(c.id);
  if (hd) flash(`${hd.name} has ${c.name}${hd.task ? ` (${hd.task})` : ""}. It is yours when they let go.`);
  return !!hd;
}
// The backend keeps the holds too (holds.py), so a ducky's tool call obeys them and a ducky's own hold shows here:
// this page tells it the city I picked, hears which cities duckies on this PC hold, and passes on what teammates
// hold (from the live group), every few seconds, which also tells it the panel is open.
let myHold = "", toldPeers = "", toldAt = 0;
async function refreshHolds() {
  if (!state.net) return;
  const r = await rpc("holds", { net_id: state.net }).catch(() => null);
  if (!r || r.ok === false) return;
  const was = JSON.stringify(state.holds || []);
  state.holds = r.holds || [];
  if (JSON.stringify(state.holds) !== was) { heldSig = ""; drawPeers(); tellGroup(); }
}
async function keepHold() {  // the city I have picked is held for me while I have it; the one I had is let go
  const want = state.cityPick && !live.idle && !holderOf(state.cityPick) ? state.cityPick : "";
  if (myHold && myHold !== want) rpc("release", { net_id: state.net, city: myHold }).catch(() => {});
  myHold = want;
  if (!want) return;
  const r = await rpc("hold", { net_id: state.net, city: want }).catch(() => null);
  if (r && r.ok === false) { myHold = ""; await refreshHolds(); }  // someone got there first (a ducky on this PC)
}
function relayHolds(force) {  // what teammates hold, for the backend: on a change, and every 10 s as a sign of life
  if (!state.net || !(live.on || Object.keys(peers).length)) return;
  const rows = [];
  for (const c of state.cities) {
    const hd = holderOf(c.id);
    if (hd && !(state.holds || []).some((x) => x.city === c.id && x.kind === "ducky")) rows.push({ city: c.id, who: hd.name });
  }
  const text = JSON.stringify(rows);
  if (!force && text === toldPeers && Date.now() - toldAt < 10000) return;
  toldPeers = text;
  toldAt = Date.now();
  rpc("holds", { net_id: state.net, teammates: text }).catch(() => {});
}
setInterval(() => { if (liveReady && visible) { relayHolds(); if (state.cityPick) keepHold(); } }, 10000);
const peerBusy = () => Object.values(peers).find((p) => p.state?.net === state.net && p.state.busy) || null;
async function peersIdle(title) {  // one rebuild of a world at a time: mine starts when a teammate's is in
  let waited = false;
  for (let n = 0; n < 480; n++) {
    const p = peerBusy();
    if (!p) break;
    waited = true;
    busy(title, { text: `${p.name} is rebuilding (${String(p.state.busy).toLowerCase()}). Yours starts when theirs is in…` });
    await sleep(500);
  }
  if (waited) await sleep(2500);  // their change reaches this PC with sync
}
let flashTimer = 0;
function flash(text) {
  live.flash = text;
  renderLiveNote();
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { live.flash = ""; renderLiveNote(); }, 3200);
}
function renderLiveNote() {  // top of the map: what teammates are doing to this world right now
  const host = $("liveNote");
  if (!host) return;
  const rows = Object.values(peers).filter((p) => p.state?.net === state.net && (p.state.busy || p.state.move)).map((p) =>
    h("div", { class: "rn-live__row" }, h("span", { class: "rn-live__dot", style: `background: var(--${p.tint})` }),
      `${p.name}: ${p.state.busy || `moving ${state.cities.find((c) => c.id === p.state.move.city)?.name || "a city"}`}`));
  if (live.flash) rows.push(h("div", { class: "rn-live__row" }, icon("lock", 12), live.flash));
  host.replaceChildren(...rows);
}
// On the map: the edge of every city a teammate has, in their colour, and a city a teammate is dragging, where it
// is right now
const peerGroup = new THREE.Group();
scene.add(peerGroup);
let heldSig = "";
function drawPeers() {
  clear(peerGroup);
  const t = state.geo?.terrain || {};
  const ring = (c, x, y, tint, real, wash) => {
    const pts = [];
    for (let k = 0; k <= 90; k++) {
      const deg = k * 4, a = deg * Math.PI / 180, r = c.radius * (real ? cityOutline(c, deg) : 1);
      const px = x + Math.cos(a) * r, py = y + Math.sin(a) * r;
      pts.push(toV(px, py, (sampleZ(t, px, py) ?? 0) + 700));
    }
    peerGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color: col(tint), depthTest: false, transparent: true, opacity: 0.95 })));
    if (wash) {
      const mid = toV(x, y, (sampleZ(t, x, y) ?? 0) + 700), fan = [];
      for (let k = 0; k + 1 < pts.length; k++) fan.push(mid, pts[k], pts[k + 1]);
      peerGroup.add(new THREE.Mesh(new THREE.BufferGeometry().setFromPoints(fan),
        new THREE.MeshBasicMaterial({ color: col(tint), transparent: true, opacity: 0.14, depthTest: false, side: THREE.DoubleSide })));
    }
  };
  if (state.geo) {
    for (const p of Object.values(peers)) {
      const s = p.state;
      if (!s || s.net !== state.net) continue;
      const held = s.city && state.cities.find((c) => c.id === s.city);
      if (held && holderOf(held.id)?.id === String(s.id)) ring(held, held.x, held.y, p.tint, true, false);
      const moving = s.move && state.cities.find((c) => c.id === s.move.city);
      if (moving) ring(moving, s.move.x, s.move.y, p.tint, false, true);
    }
  }
  syncGhosts();
  renderLiveNote();
  // whose a city is shows in its name tag and its details: redrawn only when that changes
  const sig = state.cities.map((c) => `${c.id}:${holderOf(c.id)?.id || ""}`).join("|");
  if (sig !== heldSig) {
    heldSig = sig;
    relayHolds(true);
    const host = $("labels");
    if (host) host.replaceChildren();  // placeLabels writes them again, with the holders' names
    if (state.cityPick) { renderWorldCity?.(); drawCityMark(); }
  }
  needsDraw = true;
}
// A ghost: a rounded body with two eyes, in its person's colour, hovering over the spot that person looks at and
// turned the way they look. It is drawn as big as it needs to be seen from here, near or far.
const ghostGroup = new THREE.Group();
scene.add(ghostGroup);
const ghosts = new Map();  // device ("me" for this page) -> its body
const GHOST_BODY = new THREE.LatheGeometry([[0.001, 1.5], [0.28, 1.46], [0.5, 1.28], [0.62, 0.95], [0.64, 0.3], [0.56, 0.0]]
  .map(([r, y]) => new THREE.Vector2(r, y)), 20);
const GHOST_EYE = new THREE.SphereGeometry(0.085, 10, 8);
function makeGhost(tint) {
  const g = new THREE.Group();
  const skin = new THREE.MeshStandardMaterial({ color: col(tint), emissive: col(tint), emissiveIntensity: 0.3, roughness: 0.55,
    transparent: true, opacity: 0.85, side: THREE.DoubleSide });
  g.add(new THREE.Mesh(GHOST_BODY, skin));
  for (const sx of [-0.2, 0.2]) {
    const eye = new THREE.Mesh(GHOST_EYE, new THREE.MeshBasicMaterial({ color: col("bg"), transparent: true }));
    eye.position.set(sx, 1.05, 0.55);
    g.add(eye);
  }
  g.userData.skin = skin;
  return g;
}
function syncGhosts() {
  const want = new Map(Object.entries(peers).filter(([, p]) => p.state?.cam && p.state.net === state.net).map(([k, p]) => [k, p.tint]));
  if (want.size) want.set("me", "accent");  // my own body: only with someone to fly with
  for (const [k, g] of ghosts) {
    if (want.has(k)) continue;
    ghostGroup.remove(g);
    g.userData.skin.dispose();
    ghosts.delete(k);
  }
  for (const [k, tint] of want) {
    if (ghosts.has(k)) continue;
    const g = makeGhost(tint);
    g.visible = false;
    ghosts.set(k, g);
    ghostGroup.add(g);
  }
}
// every frame: each body glides to where its person now looks; true while one is still on its way (keep drawing)
function stepGhosts() {
  if (!ghosts.size) return false;
  let moving = false;
  const put = (g, at, az, fade) => {
    const size = Math.max(3, Math.min(900, camera.position.distanceTo(at) * 0.024));
    g.position.set(at.x, at.y + size * 0.9, at.z);
    g.scale.setScalar(size);
    g.rotation.y = az + Math.PI;  // facing the way its person looks
    g.userData.size = size;
    g.visible = fade > 0.02;
    g.userData.skin.opacity = 0.85 * fade;
    for (const o of g.children) if (o.material !== g.userData.skin) o.material.opacity = fade;
  };
  for (const [k, g] of ghosts) {
    if (k === "me") {  // shown while I move, gone a moment after I stop: it never sits in the way of a click
      const still = performance.now() - live.moved, fade = Math.max(0, Math.min(1, 1 - (still - 900) / 600));
      put(g, orbit.target, orbit.az, fade);
      if (fade > 0 && fade < 1) moving = true;
      else if (fade === 1 && still < 900) moving = true;
      continue;
    }
    const p = peers[k], c = p?.state?.cam;
    if (!c) continue;
    const goal = new THREE.Vector3(c.x, c.y, c.z);
    if (!p.at || p.at.distanceTo(goal) > Math.max(4000, c.dist * 4)) p.at = goal.clone();  // (a jump across the map: no glide)
    else if (p.at.distanceTo(goal) > 0.05) { p.at.lerp(goal, 0.18); moving = true; }
    put(g, p.at, c.az, 1);
  }
  return moving;
}
function placePeerLabels() {  // each body's name, under it
  const host = $("peers");
  if (!host) return;
  const rows = [...ghosts].filter(([, g]) => g.visible).map(([k, g]) => [k, g, k === "me" ? "You" : peers[k]?.name || "Teammate", k === "me" ? "accent" : peers[k]?.tint || "accent"]);
  const sig = rows.map(([k, , name]) => `${k}:${name}`).join("|");
  if (host.dataset.sig !== sig) {
    host.dataset.sig = sig;
    host.replaceChildren(...rows.map(([, , name, tint]) => h("div", { class: "rn-label rn-label--peer" },
      h("span", { class: "rn-live__dot", style: `background: var(--${tint})` }), name)));
  }
  const w = view.clientWidth, hh = view.clientHeight;
  rows.forEach(([, g], i) => {
    const el = host.children[i], v = g.position.clone().project(camera);
    if (!el) return;
    el.style.display = v.z > 1 ? "none" : "";
    el.style.left = ((v.x + 1) / 2 * w) + "px";
    el.style.top = ((1 - v.y) / 2 * hh) + "px";
  });
}
// A person who stops touching the panel for a while lets go of the city they had (as a Kanban card ends its lock):
// it is theirs again, at the back of the line, the moment they come back to it.
const IDLE_MS = 90000;
function touched() {
  live.active = Date.now();
  if (!live.idle) return;
  live.idle = false;
  live.since = Date.now();
  tellGroup();
  keepHold();
}
for (const type of ["pointerdown", "keydown", "wheel"]) window.addEventListener(type, touched, { passive: true, capture: true });
setInterval(() => {
  if (!live.idle && state.cityPick && Date.now() - live.active > IDLE_MS) { live.idle = true; tellGroup(); keepHold(); }
}, 15000);
window.addEventListener("beforeunload", () => {
  call("live.setMyState", { state: null }).catch(() => {});
  if (myHold) rpc("release", { net_id: state.net, city: myHold }).catch(() => {});
});
// ---- a rebuild's result goes straight to teammates' screens
// The PC that rebuilt a city sends, layer by layer, what is new and what is gone (live.send: short messages from
// PC to PC, nothing stored). A teammate's page puts that in at once, as its own rebuild would, so they watch the
// change come in while their own PC places it in the background and then agrees (placement is the same on every
// PC: each piece comes from its own spot). A change too big to send (a whole new world) is left to each PC.
const spot = (p) => `${p[0]},${p[1]},${p[3]}`;
const SHARE_MAX = 90000, PART_BYTES = 12000;
const outbox = [];
let outTimer = 0;
function sendLive(topic, data) {  // at most ~22 a second: the link takes 30
  outbox.push([topic, data]);
  if (outTimer) return;
  outTimer = setInterval(() => {
    const m = outbox.shift();
    if (!m) { clearInterval(outTimer); outTimer = 0; return; }
    call("live.send", { topic: m[0], data: m[1] }).catch(() => {});
  }, 45);
}
function shareLayer(name, layer, oldPts) {
  const pts = layer.points || [], now = new Set(pts.map(spot)), had = new Set((oldPts || []).map(spot));
  const add = pts.filter((p) => !had.has(spot(p))), gone = [...had].filter((k) => !now.has(k));
  if (!add.length && !gone.length) return;
  live.shared = (live.shared || 0) + add.length + gone.length;
  if (live.shared > SHARE_MAX) return;
  const { points, ...meta } = layer;
  let part = { n: state.net, l: name, m: meta, a: [], g: [] }, size = JSON.stringify(meta).length + 80;
  const flush = (end) => {
    if (end) part.e = 1;
    sendLive("pcg", part);
    part = { n: state.net, l: name, a: [], g: [] };
    size = 80;
  };
  for (const p of add) {
    const bytes = JSON.stringify(p).length + 1;
    if (size + bytes > PART_BYTES && (part.a.length || part.g.length)) flush(false);
    part.a.push(p);
    size += bytes;
  }
  for (const k of gone) {
    if (size + k.length + 3 > PART_BYTES) flush(false);
    part.g.push(k);
    size += k.length + 3;
  }
  flush(true);
}
const inbox = {};  // layer -> what has come of it so far, until its last part
function onPeerPcg(d) {
  if (!d || d.n !== state.net || !d.l) return;
  live.quietUntil = Date.now() + 120000;
  const box = inbox[d.l] ||= { m: null, a: [], g: [] };
  if (d.m) box.m = d.m;
  for (const p of d.a || []) box.a.push(p);
  for (const k of d.g || []) box.g.push(k);
  if (!d.e) return;
  delete inbox[d.l];
  const old = state.pcg?.[d.l];
  if (!old && !box.m) return;
  const gone = new Set(box.g), kept = (old?.points || []).filter((p) => !gone.has(spot(p))), have = new Set(kept.map(spot));
  const points = kept.concat(box.a.filter((p) => !have.has(spot(p))));
  revealLayers({ [d.l]: { ...old, ...box.m, points, shown: points.length } }, false);
}
let remoteSel = null;
const selGroup = new THREE.Group();
scene.add(selGroup);
function shareSelection(aslug, p) {
  call("live.share", { key: "selection", value: p ? { net: state.net, actor: aslug, i: p[0] } : null }).catch(() => {});
}
function onSharedSelection(v, by) {
  remoteSel = v && v.net === state.net ? { ...v, by: String(by?.name || "Teammate") } : null;
  drawRemoteSel();
}
function drawRemoteSel() {
  clear(selGroup);
  const a = remoteSel && state.geo?.actors?.[remoteSel.actor];
  const p = a && a.pts.find((q) => q[0] === remoteSel.i);
  if (p) {
    const ring = new THREE.Mesh(new THREE.RingGeometry(14, 18, 32), new THREE.MeshBasicMaterial({ color: col("accent"), side: THREE.DoubleSide }));
    ring.rotation.x = -Math.PI / 2;
    ring.position.copy(toV(p[1], p[2], p[3] + 120));
    selGroup.add(ring);
  }
  needsDraw = true;
}

// ------------------------------------------------------------------ header: the open world, new, push
function renderHeader() {
  $("brand").replaceChildren(icon("city", 18), h("span", {}, "City Builder"));
  $("headActions").replaceChildren(button("New world", "plus", newWorld, "secondary", "Set up a new world in the World section"));
  // City Builder is the blueprint: an AI builds it in UEFN (the badge opens the chat with the build job typed in)
  $("headPush").replaceChildren(h("span", { id: "syncBadge" }));
  // the badge fills in once the world's city preview has loaded (loadPcgPreview): checking it first built the
  // whole city a second time while the roads were still loading
}
// UEFN vs this world: shows when the level is behind (generate as often as you like, nothing is blocked)
let syncSeq = 0;
async function loadSync() {
  const el = $("syncBadge");
  if (!el) return;
  if (!state.net) { el.replaceChildren(); return; }
  const seq = ++syncSeq;
  el.replaceChildren(h("span", { class: "rn-tag" }, "Checking UEFN…"));
  const s = await rpc("sync", { net_id: state.net });
  if (seq !== syncSeq) return;  // a newer check started (another world opened, or generated again)
  if (s.ok === false) { el.replaceChildren(); return; }
  const tone = s.in_sync ? "var(--green)" : "var(--amber)";
  const detail = s.in_sync ? "Everything in this world is built in the open UEFN level as it is here."
    : `${s.roads_changed.length} road actors changed and ${s.roads_never_pushed.length} never built; `
      + `${s.tiles.build} city tiles to build, ${s.tiles.remove} to remove. Generating again is fine: the next build in UEFN `
      + `only changes these.`;
  const b = h("button", { type: "button", class: "rn-tag", title: detail, style: `cursor: pointer; border: 1px solid ${tone}; color: ${tone}` },
    icon(s.in_sync ? "check" : "refresh", 12), h("span", { style: "margin-left: 4px" }, s.summary));
  b.addEventListener("click", () => s.in_sync ? loadSync() : askChat("build"));
  if (!s.in_sync) b.title = `${detail} Click: ask the chat to build it in UEFN.`;
  el.replaceChildren(b);
}
let worldDrop = null;
function renderWorldPick() {
  const host = $("worldPick");
  if (!state.nets.length) { worldDrop = null; host.replaceChildren(h("span", { class: "dk-muted" }, "No worlds yet")); return; }
  const opts = state.nets.map((n) => ({ value: n.id, label: n.generated ? n.name : `${n.name} (from UEFN)` }));
  if (!window.DuckyKit?.dropdown) {  // the kit not loaded yet: plain buttons, never a native select
    host.replaceChildren(...state.nets.map((n) => button(n.name, n.generated ? "world" : "network", () => openNet(n.id),
      n.id === state.net ? "primary" : "ghost")));
    return;
  }
  if (!worldDrop) {
    worldDrop = window.DuckyKit.dropdown({ label: "World", options: opts, value: state.net || opts[0].value,
      onChange: (v) => { if (v && v !== state.net) openNet(v); } });
    host.replaceChildren(worldDrop.el);
  } else {
    worldDrop.setOptions?.(opts);
    worldDrop.set?.(state.net);
  }
}
function newWorld() {
  state.net = ""; state.geo = null; state.cities = []; state.layers = []; state.pcg = {}; state.sel = null; state.actor = "";
  state.gen.name = "New world";
  if (ctl.name?.input) ctl.name.input.value = state.gen.name;
  renderLayerList();
  $("actors").replaceChildren(h("div", { class: "rn-hint" }, "Generate the world first."));
  worldDrop?.set?.("");
  openTab("world");
  buildScene();
  ctl.name?.input?.focus();
  ctl.name?.input?.select?.();
}

// ------------------------------------------------------------------ sidebar: layers (click to open, drag to reorder)
function renderLayerList() {
  const host = $("layerList");
  const layers = [...(state.layers || [])].sort((a, b) => (a.order - b.order) || a.name.localeCompare(b.name));
  if (!layers.length) {
    host.replaceChildren(h("div", { class: "rn-hint" }, state.net ? "No layers: add the starter set in Layers." : "Open a world."));
    return;
  }
  let dragId = "";
  const hint = h("div", { class: "rn-order-hint" }, "Placed top to bottom: drag to reorder");
  host.replaceChildren(hint, ...layers.map((l, i) => {
    const row = h("button", { type: "button", class: `dk-nav-item rn-layer${l.enabled ? "" : " is-off"}`, draggable: "true",
      "data-layer": l.id, "aria-current": state.pcgOpen === l.id && state.tab === "pcg" ? "page" : null,
      title: `${i + 1}. ${l.name}: placed after the layers above it; drag to change the order` },
      h("span", { class: "rn-num" }, `${i + 1}`),
      h("span", { class: "rn-dot", style: `background: ${swatch(ROLE_COLOR[l.role] || "fg-dim")}` }),
      h("span", { class: "rn-name" }, l.name), h("span", { class: "rn-count", "data-pcg-count": l.name }, ""), icon("grip", 14));
    row.lastChild.classList.add("rn-grab");
    row.addEventListener("click", () => {
      state.pcgOpen = l.id;
      openTab("pcg");
      renderPcgPane();
      renderLayerList();
      document.querySelector(`[data-live-id="layer:${l.id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    row.addEventListener("dragstart", (e) => { dragId = l.id; row.classList.add("is-dragging"); e.dataTransfer.effectAllowed = "move"; });
    row.addEventListener("dragend", () => row.classList.remove("is-dragging"));
    row.addEventListener("dragover", (e) => { e.preventDefault(); row.classList.add("is-over"); });
    row.addEventListener("dragleave", () => row.classList.remove("is-over"));
    row.addEventListener("drop", async (e) => {
      e.preventDefault();
      row.classList.remove("is-over");
      if (!dragId || dragId === l.id) return;
      const order = layers.filter((x) => x.id !== dragId);
      order.splice(order.findIndex((x) => x.id === l.id), 0, layers.find((x) => x.id === dragId));
      busy("Saving the layer order…");
      for (const [i, x] of order.entries()) {  // placing order: earlier layers claim their spots first
        if (x.order !== i + 1) await rpc("pcg_layer_save", { net_id: state.net, layer_id: x.id, layer: JSON.stringify({ ...x, order: i + 1 }) });
      }
      busy("");
      await loadLayers();
    });
    return row;
  }));
  renderPcgCounts();
}

// ------------------------------------------------------------------ side panels: resize handles and version
// Ducky's sidebar handle on both sides: drag, arrow keys, double-click for the starting width. Widths are kept.
const SPLITS = [
  { id: "splitLeft", key: "leftW", cssVar: "--left-w", min: 180, max: 480, start: 236, sign: 1 },
  { id: "splitRight", key: "rightW", cssVar: "--right-w", min: 260, max: 560, start: 328, sign: -1 },
];
function initSplits() {
  const root = document.documentElement;
  for (const s of SPLITS) {
    const handle = $(s.id);
    const setW = (w) => {
      state.view[s.key] = Math.max(s.min, Math.min(s.max, Math.round(w)));
      root.style.setProperty(s.cssVar, `${state.view[s.key]}px`);
      handle.setAttribute("aria-valuenow", state.view[s.key]);
      resize();
    };
    handle.setAttribute("aria-valuemin", s.min);
    handle.setAttribute("aria-valuemax", s.max);
    if (state.view[s.key]) setW(state.view[s.key]);
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const x0 = e.clientX, w0 = state.view[s.key] || s.start;
      handle.setPointerCapture(e.pointerId);
      handle.classList.add("is-dragging");
      document.body.classList.add("is-resizing");
      const move = (ev) => setW(w0 + (ev.clientX - x0) * s.sign);
      const up = () => {
        handle.classList.remove("is-dragging");
        document.body.classList.remove("is-resizing");
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        handle.removeEventListener("pointercancel", up);
        savePrefs();
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
      handle.addEventListener("pointercancel", up);
    });
    handle.addEventListener("dblclick", () => { setW(s.start); savePrefs(); });
    // the header button folds this panel away (view.leftHidden / view.rightHidden are kept)
    const side = s.key === "leftW" ? "left" : "right", fold = $(s.key === "leftW" ? "foldLeft" : "foldRight");
    const show = (open) => {
      state.view[`${side}Hidden`] = !open;
      document.body.classList.toggle(`rn-no-${side}`, !open);
      const tip = `${open ? "Hide" : "Show"} the ${side} panel`;
      fold.setAttribute("aria-expanded", String(open));
      fold.setAttribute("aria-label", tip);
      fold.title = tip;
      resize();
    };
    fold.replaceChildren(icon(side === "left" ? "panelLeft" : "panelRight", 16));
    fold.addEventListener("click", () => { show(!!state.view[`${side}Hidden`]); savePrefs(); });
    show(!state.view[`${side}Hidden`]);
    handle.addEventListener("keydown", (e) => {
      const k = { ArrowLeft: -16, ArrowRight: 16 }[e.key];
      if (!k) return;
      e.preventDefault();
      setW((state.view[s.key] || s.start) + k * s.sign);
      savePrefs();
    });
  }
}
async function showVersion() {
  const v = await rpc("version");
  $("version").replaceChildren(h("span", {}, "City Builder"), h("span", {}, v.version ? `v${v.version}` : ""));
}

// ------------------------------------------------------------------ map tools: point or draw, tell an AI
const note = { kind: "", points: [], chats: null };
// the map tool bar: the same bar as the Workflows canvas (icon buttons, tool tips, a zoom readout)
// ------------------------------------------------------------------ history: undo, redo, go back
// The backend keeps what every step changed (on this PC only) and puts it back exactly: the world's cities, roads,
// ground and layers as they were. UEFN is not touched; what is then waiting to be pushed shows as usual.
function historySoon() {
  clearTimeout(hist.timer);
  hist.timer = setTimeout(loadHistory, 400);
}
async function loadHistory() {
  if (!state.net) { hist.undo = []; hist.redo = []; renderHistory(); return; }
  const net = state.net, got = await rpc("history", { net_id: net });
  if (net !== state.net || got.ok === false) return;
  hist.undo = got.undo || [];
  hist.redo = got.redo || [];
  renderHistory();
}
async function stepHistory(back, steps = 1) {
  const list = back ? hist.undo : hist.redo;
  if (hist.busy || !state.net || !list.length || state.generating || edits.running || edits.list.length) return;
  hist.busy = true;
  const name = list[list.length - 1].label;
  busy(back ? "Undoing" : "Redoing", { text: steps > 1 ? `${steps} steps…` : `${name}…` });
  const got = await rpc(back ? "undo" : "redo", { net_id: state.net, steps });
  hist.busy = false;
  busy("");
  if (got.ok === false) { showError(got.error); return; }
  hist.undo = got.undo || [];
  hist.redo = got.redo || [];
  renderHistory();
  cancelDrag();
  await loadNets(false);  // (the step may have been the one that made the world, or its cities)
  if (currentNet()) await loadGeometry(true);
}
const ago = (at) => {
  const s = Math.max(0, Date.now() / 1000 - at);
  return s < 90 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
};
function renderHistory() {
  const host = $("historyList");
  if (host) {
    const row = (ent, cls, title, go) => {
      const b = h("button", { type: "button", class: `rn-hist__row ${cls}`, title }, icon(cls === "is-redo" ? "redo" : "clock", 14),
        h("span", { class: "rn-hist__name" }, ent.label), h("span", { class: "rn-hist__at" }, ago(ent.at)));
      b.addEventListener("click", go);
      return b;
    };
    const redo = hist.redo.map((ent, i) => row(ent, "is-redo", `Redo up to here: ${ent.label}`, () => stepHistory(false, hist.redo.length - i)));
    const undo = [...hist.undo].reverse().map((ent, i) => row(ent, "", `Go back to before this: ${ent.label}`, () => stepHistory(true, i + 1)));
    host.replaceChildren(...(redo.length || undo.length ? [...redo,
      ...(redo.length ? [h("div", { class: "rn-hist__now" }, "The world as it is now")] : []), ...undo]
      : [h("div", { class: "rn-hint" }, state.net ? "Nothing to go back to yet. Every change you make is listed here." : "Open a world.")]));
  }
  renderTools();
}

function renderTools() {
  const tool = (id, ic, hint, key) => {
    const b = h("button", { type: "button", class: "rn-tool", "aria-pressed": state.tool === id ? "true" : "false",
      title: key ? `${hint} (${key})` : hint, "aria-label": hint }, icon(ic, 16));
    b.addEventListener("click", () => setTool(state.tool === id && id !== "select" ? "select" : id));
    return b;
  };
  const act = (ic, fn, hint, key) => {
    const b = h("button", { type: "button", class: "rn-tool", title: key ? `${hint} (${key})` : hint, "aria-label": hint }, icon(ic, 16));
    b.addEventListener("click", fn);
    return b;
  };
  const zoom = h("button", { type: "button", class: "rn-zoom", id: "zoomPct", title: "Zoom: click to fit the whole world (F)",
    "aria-label": "Zoom, fit the world" }, "100%");
  zoom.addEventListener("click", () => frame());
  const hasNote = note.points.length > 0;
  $("tools").replaceChildren(
    tool("select", "pointer", "Pointer: click a city, then one of its blocks; nothing moves", "V"),
    tool("hand", "hand", "Hand: drag to pan the view", "H"),
    ...(currentNet()?.generated ? [tool("city", "city", "Edit city: drag it to move it, the white handle to turn it, the dots to shape its edge; the red, blue and green dots move and resize its districts; click a block to change it", "E")] : []),
    tool("move", "route", "Move road: press on a road and drag that part of it; the ground is cut again under it", "M"),
    h("span", { class: "rn-sep" }),
    tool("pin", "pin", "Pin a spot, then tell an AI what to do there", "P"),
    tool("area", "pen", "Draw round an area, then tell an AI what to do there", "A"),
    ...(lakeWorld() ? [tool("lake", "lake", "Lake: click along where it goes, double-click to finish, and the ground is dug there; click a lake to change its width, drag its dots, or delete it", "L")] : []),
    ...(currentNet()?.generated ? [tool("addcity", "plus", "Add a city: click where it goes", "C")] : []),
    ...(hasNote ? [act("x", clearNote, "Remove the pin or area")] : []),
    h("span", { class: "rn-sep" }),
    ...[["undo", true, hist.undo, "Undo", "Ctrl+Z"], ["redo", false, hist.redo, "Redo", "Ctrl+Y"]].map(([ic, back, list, word, key]) => {
      const b = act(ic, () => stepHistory(back), list.length ? `${word}: ${list[list.length - 1].label}` : `Nothing to ${word.toLowerCase()}`, key);
      b.disabled = !list.length;
      return b;
    }),
    h("span", { class: "rn-sep" }),
    act("frame", () => frame(), "Fit the whole world", "F"),
    zoom,
    act("top", () => topView(), "Top view", "T"),
    act("help", toggleHelp, "Controls", "?"));
  renderZoom();
}
// zoom readout: 100 % is the whole world in view
function renderZoom() {
  const el = $("zoomPct");
  if (!el || !state.box || state.box.isEmpty()) return;
  const fit = state.box.getSize(new THREE.Vector3()).length() * 0.75;
  el.textContent = `${Math.round((fit / orbit.dist) * 100)}%`;
}
function topView() {
  orbit.el = 1.55;
  placeCamera();
  renderZoom();
}
function toggleHelp() {
  const pop = $("toolHelp");
  if (pop && !pop.hidden) { pop.hidden = true; return; }
  const row = (keys, what) => h("div", { class: "rn-help__row" }, h("span", { class: "rn-help__keys" }, keys), h("span", {}, what));
  const box = pop || h("div", { class: "rn-help", id: "toolHelp", role: "dialog", "aria-label": "Map controls" });
  box.replaceChildren(h("div", { class: "rn-help__title" }, "Map controls"),
    row("Click a city", "Pick it; then click a block of it"), row("Edit city (E)", "Drag to move, white handle turns, dots shape its edge"),
    row("District dots", "Red middle: move the centre. Red / blue on a ring: its size. Green: industry's side"),
    row("Lake (L)", "Click along it, double-click to finish. Click a lake: its width, its dots, delete"),
    row("Left drag", "Orbit (Pointer) / pan (Hand)"),
    row("Hold right mouse", "Fly: look around"), row("+ W A S D, Q / E", "Fly: move, down / up (Shift: faster)"),
    row("+ Wheel", "Fly: speed"), row("Shift / middle drag", "Pan"), row("Wheel", "Zoom"),
    row("V H E M P A L C", "Pointer, Hand, Edit city, Move road, Pin, Area, Lake, Add city"), row("F / T", "Fit / top view"),
    row("Ctrl+Z / Ctrl+Y", "Undo / redo your last change"),
    row("Esc", "While dragging: cancel, nothing changes. Else back to Pointer"));
  box.hidden = false;
  if (!pop) $("view").parentElement.append(box);
}
function setTool(id) {
  state.tool = id;
  $("view").classList.toggle("is-drawing", id === "pin" || id === "area" || id === "addcity" || id === "lake");
  $("view").classList.toggle("is-hand", id === "hand");
  if (id !== "lake") { lake.draft = []; lake.pick = ""; }
  renderTools();
  renderWorldCity();
  drawCityMark();  // the Edit city tool shows the picked city's handles
  drawLakes();  // the Lake tool shows every lake's line and shore
  needsDraw = true;
  const banner = $("banner");
  if (id === "lake") return lakeBanner();
  if (id === "city") {
    const c = state.cities.find((x) => x.id === state.cityPick);
    banner.replaceChildren(h("span", {}, c ? `Editing ${c.name}: drag it to move it, the white handle to turn it, the dots to shape its edge, the coloured dots to move and resize its districts` : "Click a city to edit it"),
      h("button", { type: "button", class: "rn-mini", onclick: () => setTool("select") }, "Done"));
    banner.hidden = false;
  } else if (id === "addcity") {  // placing a new city: click the map, or let City Builder find room
    banner.replaceChildren(h("span", {}, "Click the map where the new city goes"),
      h("button", { type: "button", class: "rn-mini", onclick: () => addCityAt(null) }, "Find a free spot"),
      h("button", { type: "button", class: "rn-mini", onclick: () => setTool("select"), "aria-label": "Cancel" }, icon("x", 12)));
    banner.hidden = false;
  } else banner.hidden = true;
}
window.addEventListener("keydown", (e) => {
  if (e.target.closest?.("input, textarea, [contenteditable]")) return;  // typing, not shortcuts
  // Escape while something is held (a city, a handle, a road point, an area being drawn): it goes back to where
  // it was and nothing is changed. Nothing is ever saved before the button is let go.
  if (e.key === "Escape") {
    if (cancelDrag()) return;
    if (state.tool === "lake" && (lake.draft.length || lake.pick)) { lake.draft = []; lake.pick = ""; drawLakes(); lakeBanner(); return; }  // the lake being drawn, or the pick
    showInspect(null);
    if (state.tool !== "select") setTool("select");
    $("toolHelp")?.setAttribute("hidden", "");
    return;
  }
  if (e.key === "Enter" && state.tool === "lake" && lake.draft.length) { finishLake(); return; }
  if ((e.ctrlKey || e.metaKey) && !e.altKey && ["z", "y"].includes(e.key.toLowerCase())) {
    e.preventDefault();
    stepHistory(e.key.toLowerCase() === "z" && !e.shiftKey);  // Ctrl+Z undo; Ctrl+Y or Ctrl+Shift+Z redo
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (fly.on) {  // flying: W A S D Q E move, never switch tools
    fly.shift = e.shiftKey;
    if (FLY_KEYS[k]) { fly.keys.add(k); e.preventDefault(); }
    return;
  }
  const tools = { v: "select", h: "hand", e: "city", m: "move", p: "pin", a: "area", c: "addcity", l: "lake" };
  if (tools[k] && ((k !== "c" && k !== "e") || currentNet()?.generated) && (k !== "l" || lakeWorld())) setTool(tools[k]);
  else if (k === "f") frame();
  else if (k === "t") topView();
  else if (k === "?") toggleHelp();
});

window.addEventListener("keyup", (e) => { fly.keys.delete(e.key.toLowerCase()); fly.shift = e.shiftKey; });
window.addEventListener("blur", () => { fly.on = false; fly.keys.clear(); });

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
  ["canals", "Canals", "wave", "canal_city", "none"], ["planner", "City services", "layers", "yes", "no"]];
function cityFeatures(c) {
  const own = c.features || {};
  const has = (k, on) => (own[k] ?? state.gen[k]) === on;
  const set = (k, v) => saveCity(c, { features: JSON.stringify({ [k]: v }) });
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
  needsDraw = true;
  if (state.tool === "city") setTool("city");  // its banner names the city being edited
  const c = state.cities.find((x) => x.id === id);
  if (!c) return;
  const t = state.geo?.terrain || {};
  flyTo(toV(c.x, c.y, sampleZ(t, c.x, c.y) ?? 0), Math.max(800, (c.radius / 100) * 2.6));
  if (state.tab === "cities") document.querySelector(`#pane-cities [data-live-id="city:${id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
async function addCityAt(e) {
  const v = e ? groundAt(e) : null;
  const at = v ? toUE(v) : null;
  setTool("select");
  queueEdit("Adding a city", () => rpc("city_add", { net_id: state.net, ...(at ? { x: at[0], y: at[1] } : {}) }),
    (out) => { if (out?.id) selectCity(out.id); });
}
async function deleteCity(c) {
  if (heldElsewhere(c)) return;
  const ok = await call("ui.confirm", { title: `Delete ${c.name}`, danger: true, confirm: "Delete",
    message: `${c.name} and everything on it (roads, buildings, services, airport, waterfront) go; the other cities are rebuilt and their highways re-join without it. Nothing in UEFN changes until you build.` }).catch(() => null);
  if (!ok?.confirmed) return;
  // gone from the panel and the view at once; a city already deleted (another chat, an earlier try) is not an error
  state.cities = state.cities.filter((x) => x.id !== c.id);
  if (state.cityPick === c.id) state.cityPick = "";
  renderCityList();
  renderWorldCity();
  drawCityMark();
  needsDraw = true;
  queueEdit("Rebuilding the world", async () => {
    const out = await rpc("city_delete", { net_id: state.net, city: c.id, confirm: true });
    return out.ok === false && /^No city /.test(out.error || "") ? { ok: true, gone: true } : out;
  });
}
function groundAt(e) {
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const hit = ray.intersectObjects(groups.terrain.children, false)[0];
  if (hit) return hit.point;
  const out = new THREE.Vector3();
  return ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), out) ? out : null;
}
const toUE = (v) => [Math.round(-v.z * 100), Math.round(v.x * 100), Math.round(v.y * 100 / (state.view.zx || 1))];
function addAreaPoint(e) {
  const v = groundAt(e);
  if (v) { note.kind = "drawing"; note.points.push(toUE(v)); drawMarks(); }
}
function finishArea() {
  if (note.points.length < 3) { clearNote(); return; }
  note.kind = "area";
  drawMarks();
  renderTools();
  askChat("area");
}
function placePin(e) {
  const v = groundAt(e);
  if (!v) return;
  note.kind = "pin";
  note.points = [toUE(v)];
  drawMarks();
  renderTools();
  askChat("pin");
}
// Drag a city (Pointer tool): press on it, drag, let go; the world is generated again with it at its new place
function cityUnder(e) {
  const v = groundAt(e);
  if (!v || !state.cities.length) return null;
  const [x, y] = toUE(v);
  return state.cities.map((k) => [k, Math.hypot(k.x - x, k.y - y)])
    .filter(([k, d]) => d < k.radius * cityOutline(k, Math.atan2(y - k.y, x - k.x) * 180 / Math.PI))
    .sort((a, b) => a[1] - b[1])[0]?.[0] || null;
}
// A click on a city: the first picks it, the next ones pick the block under the pointer (a click on open land lets go)
function clickCity(c, e) {
  if (!c) { if (state.cityPick && state.tool === "select") selectCity(""); return; }
  if (c.id !== state.cityPick) return selectCity(c.id);
  const v = groundAt(e);
  if (!v) return;
  const [x, y] = toUE(v);
  const b = (c.blocks || []).find((k) => insidePoly(k[4], x, y));
  state.block = b ? { city: c.id, x: b[0], y: b[1] } : null;
  renderWorldCity();
  drawCityMark();
  needsDraw = true;
}
// The Lake tool. A click on a lake picks it (its dots and its width show); a click anywhere else adds a point to
// the lake being drawn, and a double-click or Enter finishes it. Nothing is dug until then, and nothing of a
// picked lake changes until its dot or its slider is let go; Escape cancels either.
function lakePointUnder(e) {
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  return ray.intersectObjects(lakeMark.children.filter((o) => o.userData.lakePt), false)[0]?.object.userData.lakePt || null;
}
function lakeAt(x, y) {  // the lake whose water this spot is in
  const far = (p, a, b) => {
    const dx = b[0] - a[0], dy = b[1] - a[1], t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
    return Math.hypot(p[0] - a[0] - dx * t, p[1] - a[1] - dy * t);
  };
  return lakesOf().find((l) => l.id && l.pts.some((p, i) => far([x, y], p, l.pts[Math.min(l.pts.length - 1, i + 1)]) < l.width_m * 50)) || null;
}
function lakeClick(e) {
  const v = groundAt(e);
  if (!v) return;
  const [x, y] = toUE(v);
  if (!lake.draft.length) {
    const hit = lakeAt(x, y);
    if (hit || lake.pick) {  // on a lake: it is picked; off it with one picked: that one is let go
      lake.pick = hit && hit.id !== lake.pick ? hit.id : hit ? lake.pick : "";
      drawLakes();
      return lakeBanner();
    }
  }
  lake.draft.push([x, y]);
  drawLakes();
  lakeBanner();
}
function saveLake(title, params) {  // saved and dug by the next rebuild (queueEdit), like a change to a city
  drawLakes();
  lakeBanner();
  queueEdit(title, async () => {
    const out = await rpc(params.remove ? "lake_delete" : "lake_save", { net_id: state.net, lake_id: params.lake_id || "",
      ...(params.remove ? {} : { points: params.points ? JSON.stringify(params.points) : "", width_m: params.width_m || 0 }) });
    if (out?.ok !== false && currentNet()?.generated) {
      currentNet().generated.lakes = out.lakes;
      if (!params.lake_id && state.tool === "lake") lake.pick = out.lake || "";
    }
    return out;
  });
}
function finishLake() {
  const pts = lake.draft.filter((p, i, all) => !i || Math.hypot(p[0] - all[i - 1][0], p[1] - all[i - 1][1]) > Math.max(500, orbit.dist));  // (a double-click's two clicks are one point)
  lake.draft = [];
  if (!pts.length || !currentNet()?.generated) { drawLakes(); return lakeBanner(); }
  currentNet().generated.lakes = [...lakesOf(), { id: "", pts, width_m: lake.width }];  // shown at once; its name comes back with the save
  saveLake("Digging the lake", { points: pts, width_m: lake.width });
}
function dragLakePoint(e) {
  const v = groundAt(e);
  if (!v) return;
  const [x, y] = toUE(v), d = drag.lakePt;
  d.lake.pts[d.k] = [x, y];
  d.moved = true;
  renderer.domElement.style.cursor = "grabbing";
  drawLakes();
}
function dropLakePoint(d) {
  saveLake("Reshaping the lake", { lake_id: d.lake.id, points: d.lake.pts });
}
function lakeBanner() {
  const banner = $("banner");
  if (state.tool !== "lake") return;
  const done = h("button", { type: "button", class: "rn-mini", onclick: () => setTool("select") }, "Done");
  const [lo, , hi] = [60, 200, 1500];  // citygen.LAKE_WIDTH_M
  const width = (value, onChange) => slider({ label: "Width", min: lo, max: hi, step: 10, value, fmt: (v) => `${v} m`, onChange,
    hint: "How wide the lake is. Nothing changes until you let go; Escape puts it back" }).el;
  const l = lakesOf().find((x) => x.id === lake.pick);
  if (l) {
    banner.replaceChildren(h("span", {}, "Lake: drag its dots to reshape it"),
      width(l.width_m, (v) => { if (v !== l.width_m) { l.width_m = v; saveLake("Changing the lake", { lake_id: l.id, width_m: v }); } }),
      h("button", { type: "button", class: "rn-mini", title: "Take this lake away: the ground comes back",
        onclick: () => { currentNet().generated.lakes = lakesOf().filter((x) => x !== l); lake.pick = ""; saveLake("Removing the lake", { lake_id: l.id, remove: true }); } },
      icon("trash", 12), h("span", {}, "Delete")), done);
  } else if (lake.draft.length) {
    banner.replaceChildren(h("span", {}, `${lake.draft.length} point${lake.draft.length > 1 ? "s" : ""}: double-click or Enter to finish`),
      width(lake.width, (v) => { lake.width = v; drawLakes(); }),
      h("button", { type: "button", class: "rn-mini", onclick: finishLake }, "Finish"),
      h("button", { type: "button", class: "rn-mini", onclick: () => { lake.draft = []; drawLakes(); lakeBanner(); }, "aria-label": "Cancel" }, icon("x", 12)));
  } else {
    banner.replaceChildren(h("span", {}, lakesOf().length ? "Click along where a new lake goes, or click a lake to change it" : "Click along where the lake goes; double-click the last point to finish"), done);
  }
  banner.hidden = false;
}
// Edit city: the handles on the picked city (drawCityMark). The white one turns the whole city round its centre;
// a dot on its edge pulls the edge in or out there (as far as the generator allows, state.shapeRange).
function handleUnder(e) {
  const c = state.cities.find((x) => x.id === state.cityPick);
  if (!c) return null;
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const hit = ray.intersectObjects(cityMark.children.filter((o) => o.userData.handle), false)[0];
  return hit ? { c, ...hit.object.userData } : null;
}
function turnCity(c, deg) {
  const d = deg - (c.turn_deg ?? 0);
  if (!d) return;
  c.spin = (c.spin || 0) + d;
  c.turn_deg = deg;
  if (c.districts?.industrial) c.districts.industrial.angle = (((c.districts.industrial.angle + d) % 360) + 360) % 360;
}
function dragHandle(e) {
  const v = groundAt(e);
  if (!v) return;
  const [x, y] = toUE(v), hnd = drag.handle, c = hnd.c;
  if (hnd.handle === "turn") {
    turnCity(c, Math.round((((Math.atan2(y - c.y, x - c.x) * 180 / Math.PI) % 360) + 360) % 360));
  } else if (hnd.handle === "core") {
    // the centre district, anywhere within 60 % of the way to the city's edge (citygen.CORE_MAX); back on the
    // city's own centre when let go within 20 m of it
    const dx = x - c.x, dy = y - c.y, dist = Math.hypot(dx, dy);
    const k = dist > 0 ? Math.min(1, 0.6 * c.radius * cityOutline(c, Math.atan2(dy, dx) * 180 / Math.PI) / dist) : 1;
    c.districts = { ...(c.districts || {}) };
    if (dist < 2000) delete c.districts.core; else c.districts.core = [Math.round(dx * k), Math.round(dy * k)];
  } else if (hnd.handle === "downtown" || hnd.handle === "normal") {
    // a ring's size: how far the pointer is from the centre district, as a share of the way to the edge there
    const core = cityCore(c) || [0, 0], px = x - c.x - core[0], py = y - c.y - core[1];
    const [ex, ey] = ringPoint(c, 1, Math.atan2(py, px) * 180 / Math.PI);
    const share = Math.hypot(px, py) / (Math.hypot(ex - c.x - core[0], ey - c.y - core[1]) || 1), d = c.districts, R = c.radius;
    const [lo, hi] = hnd.handle === "downtown" ? [0.1, 0.8] : [Math.max(0.2, d.downtown.radius / R + 0.05), 0.95];
    d[hnd.handle].radius = Math.round(R * Math.round(Math.max(lo, Math.min(hi, share)) * 100) / 100);
    if (d.normal.radius < d.downtown.radius + R * 0.05) d.normal.radius = Math.round(Math.min(R * 0.95, d.downtown.radius + R * 0.05));
  } else if (hnd.handle === "industry") {
    c.districts.industrial.angle = Math.round((((Math.atan2(y - c.y, x - c.x) * 180 / Math.PI) % 360) + 360) % 360);
  } else {
    const n = state.shapePoints || 16, [lo, hi] = state.shapeRange || [0.74, 1.26];
    // the first pull: the edge it has now becomes the hand-drawn one, handle for handle
    if (!c.shape?.length) c.shape = Array.from({ length: n }, (_, k) => Math.max(lo, Math.min(hi, cityOutline(c, k * 360 / n + (c.spin || 0)))));
    c.shape[hnd.k] = Math.max(lo, Math.min(hi, Math.hypot(x - c.x, y - c.y) / c.radius));
  }
  hnd.moved = true;
  renderer.domElement.style.cursor = "grabbing";
  if (state.view.districts) { clear(groups.districts); drawDistricts(); }
  drawCityMark();
  needsDraw = true;
}
function dropHandle(hnd) {
  if (!hnd.moved) return;
  const c = hnd.c, share = (ring) => Math.round(c.districts[ring].radius / c.radius * 1000) / 1000;
  if (hnd.handle === "turn") saveCity(c, { turn_deg: c.turn_deg });
  else if (hnd.handle === "core") saveCity(c, { core: cityCore(c) ? JSON.stringify(cityCore(c)) : "reset" });
  else if (hnd.handle === "industry") saveCity(c, { industry_deg: c.districts.industrial.angle });
  else if (hnd.handle === "downtown" || hnd.handle === "normal") {
    const moved = hnd.was?.districts?.normal?.radius !== c.districts.normal.radius;  // (downtown pushes the next ring out)
    saveCity(c, { features: JSON.stringify({ [hnd.handle]: share(hnd.handle), ...(moved ? { normal: share("normal") } : {}) }) });
  } else saveCity(c, { shape: JSON.stringify(c.shape.map((v) => Math.round(v * 1000) / 1000)) });
}
// Moving a city: it leaves its place at once (its roads, buildings and props go) and a light copy of its buildings
// rides with the pointer. After the drop the copy stays at the new place while the city is rebuilt there, and goes
// when the rebuilt city starts to come in.
const cityOfActor = (aslug) => state.cities.map((c) => c.id).filter((id) => aslug === id || aslug.startsWith(id + "-"))
  .sort((p, q) => q.length - p.length)[0] || "";
function inCity(c, x, y) {
  const dx = x - c.x, dy = y - c.y, d = Math.hypot(dx, dy);
  return d < c.radius * 1.35 && d < c.radius * 1.06 * cityOutline(c, Math.atan2(dy, dx) * 180 / Math.PI);
}
function liftCity(c) {
  clear(cityGhost);
  cityGhost.position.set(0, 0, 0);
  state.lifted = { id: c.id, x: c.x, y: c.y, radius: c.radius, seed: c.seed, spin: c.spin, shape: c.shape && [...c.shape], roads: true };
  const rows = [];
  for (const layer of Object.values(state.pcg || {})) {
    if (layer.kind !== "buildings" || layer.role === "park") continue;
    for (const p of layer.points || []) if (inCity(c, p[0], p[1])) rows.push(p);
  }
  if (rows.length) {
    const zx = state.view.zx || 1, dummy = new THREE.Object3D();
    const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ color: col("fg-dim"),
      roughness: 0.9, transparent: true, opacity: 0.6 }), rows.length);
    rows.forEach(([x, y, z, yaw, , hgt, , w, d], i) => {
      dummy.position.copy(toV(x, y, z));
      dummy.position.y += (hgt || 6) * zx / 2;
      dummy.rotation.set(0, -yaw * Math.PI / 180, 0);
      dummy.scale.set((w || 12) * 0.8, (hgt || 6) * zx, (d || 12) * 0.8);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    });
    cityGhost.add(mesh);
  }
  hideLifted();
}
function slideGhost(to) {
  const L = state.lifted;
  if (!L) return;
  const t = state.geo?.terrain || {}, zx = state.view.zx || 1;
  cityGhost.position.set((to[1] - L.y) / 100, ((sampleZ(t, to[0], to[1]) ?? 0) - (sampleZ(t, L.x, L.y) ?? 0)) * zx / 100, -(to[0] - L.x) / 100);
  needsDraw = true;
}
function hideLifted() {  // after every redraw too: the old city stays gone until the rebuilt one is in
  const L = state.lifted;
  if (!L) return;
  if (L.roads) {
    for (const o of groups.roads.children) if (o.userData.city === L.id) o.visible = false;
    for (const o of groups.pieces.children) if (cityOfActor(o.userData.actor || "") === L.id) o.visible = false;
  }
  const none = new THREE.Matrix4().makeScale(0, 0, 0);
  for (const o of groups.pcg.children) {
    const xy = o.userData.xy;
    if (!o.isInstancedMesh || !xy || o.userData.fresh) continue;  // (what the rebuild already put there stays)
    for (let i = 0; i < o.count; i++) if (inCity(L, xy[2 * i], xy[2 * i + 1])) o.setMatrixAt(i, none);
    o.instanceMatrix.needsUpdate = true;
  }
  needsDraw = true;
}
function dropLift() {  // the move was called off: the city is back where it was
  if (!state.lifted) return;
  state.lifted = null;
  clear(cityGhost);
  buildScene();
}
function settleCity() {  // the rebuilt city is coming in: the copy has done its job
  if (!cityGhost.children.length) return;
  clear(cityGhost);
  needsDraw = true;
}
// A rebuild comes in a few layers at a time and changes only what changed: of each layer, what stood before stays
// on screen exactly as it is (the other cities, the forests), what is gone goes, and only what is new rises out of
// the ground. Nothing is cleared first: the world never blinks empty.
const NO_POINTS = { points: [] };
async function revealLayers(layers, first) {
  if (first) settleCity();
  const old = state.pcg || {};
  const stay = {}, fresh = {};
  for (const [name, layer] of Object.entries(layers)) {
    if (live.sharing) shareLayer(name, layer, old[name]?.points);
    const had = old[name]?.points?.length ? new Set(old[name].points.map(spot)) : null;
    if (!had) { fresh[name] = layer; continue; }
    const s = [], f = [];
    for (const p of layer.points || []) (had.has(spot(p)) ? s : f).push(p);
    stay[name] = { ...layer, points: s };
    if (f.length) fresh[name] = { ...layer, points: f };
  }
  state.pcg = { ...old, ...layers };
  if (!state.view.pcg || !state.geo) return;
  for (const o of [...groups.pcg.children]) {
    if (!(o.userData.layer in layers)) continue;
    groups.pcg.remove(o);
    o.geometry?.dispose?.();
    o.material?.dispose?.();
  }
  const whole = (part) => Object.fromEntries(Object.keys(state.pcg).map((n) => [n, part[n] || NO_POINTS]));
  let before = new Set(groups.pcg.children);
  drawPcg({}, null, whole(stay));
  for (const o of groups.pcg.children) if (!before.has(o)) o.userData.fresh = true;
  before = new Set(groups.pcg.children);
  drawPcg({}, null, whole(fresh));
  const risen = groups.pcg.children.filter((o) => !before.has(o));
  for (const o of risen) o.userData.fresh = true;
  needsDraw = true;
  if (!risen.length) return;
  const fresh_ = risen;
  // one after another, each a moment behind the last: a batch of thirty kinds of thing takes over a second to
  // come up, so the city fills in as it is made
  const start = performance.now(), ms = 520, gap = Math.min(45, 900 / fresh_.length);
  fresh_.forEach((o) => { o.scale.y = 0.02; });
  const step = (now) => {
    let rising = false;
    fresh_.forEach((o, i) => {
      const k = Math.max(0, Math.min(1, (now - start - i * gap) / ms));
      o.scale.y = 0.02 + 0.98 * (1 - Math.pow(1 - k, 3));
      if (k < 1) rising = true;
    });
    needsDraw = true;
    if (rising) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
  await new Promise((done) => setTimeout(done, 170 + fresh_.length * gap));
}
function startCityDrag(c) {
  if (heldElsewhere(c)) { drag.cityCand = null; drag.cityMove = false; return; }
  const v = groundAt({ clientX: drag.x0, clientY: drag.y0 });
  if (!v) return;
  const [x, y] = toUE(v);
  drag.city = { c, dx: c.x - x, dy: c.y - y, to: [c.x, c.y] };
  liftCity(c);  // it leaves its place at once; a light copy of it rides with the pointer
  renderer.domElement.style.cursor = "grabbing";
  state.cityPick = c.id;
  renderCityList();  // only a ghost moves while dragging: nothing is rebuilt until the drop (Esc cancels)
  drawCityGhost(drag.city);
}
function dragCity(e) {
  const v = groundAt(e);
  if (!v) return;
  const [x, y] = toUE(v);
  drag.city.to = [x + drag.city.dx, y + drag.city.dy];
  drag.city.c.dragTo = drag.city.to;  // its name tag follows
  tellGroup();  // teammates see it move as it is dragged
  slideGhost(drag.city.to);
  drawCityGhost(drag.city);
}
function drawCityGhost(m) {
  clear(marks);
  clear(cityMark);
  const t = state.geo?.terrain || {};
  const ring = [], fan = [];
  for (let k = 0; k <= 72; k++) {
    const a = (k / 72) * Math.PI * 2;
    const x = m.to[0] + Math.cos(a) * m.c.radius, y = m.to[1] + Math.sin(a) * m.c.radius;
    ring.push(toV(x, y, (sampleZ(t, x, y) ?? 0) + 600));
  }
  const cz = (sampleZ(t, m.to[0], m.to[1]) ?? 0) + 600;
  for (let k = 0; k < 72; k++) fan.push(toV(m.to[0], m.to[1], cz), ring[k], ring[k + 1]);
  const accent = col("accent");
  marks.add(new THREE.Mesh(new THREE.BufferGeometry().setFromPoints(fan),
    new THREE.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.18, depthTest: false, side: THREE.DoubleSide })));
  marks.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(ring),
    new THREE.LineBasicMaterial({ color: accent, depthTest: false })));
  needsDraw = true;
}
function cancelDrag() {  // Esc: whatever is held goes back to where it was; true when something was held
  if (!drag) return false;
  if (drag.city) return cancelCityDrag();
  if (drag.handle) {  // turning or shaping a city
    const { c, was, moved } = drag.handle;
    drag = null;
    if (moved && was) {
      Object.assign(c, { turn_deg: was.turn_deg, spin: was.spin });
      if (was.shape) c.shape = was.shape; else delete c.shape;
      if (was.districts) c.districts = was.districts;
      if (c.districts?.industrial && was.angle != null) c.districts.industrial.angle = was.angle;
      if (state.view.districts) { clear(groups.districts); drawDistricts(); }
      renderWorldCity();
    }
    renderer.domElement.style.cursor = "";
    drawCityMark();
    needsDraw = true;
    return true;
  }
  if (drag.lakePt) {  // moving a point of a lake
    const { lake: l, k, was } = drag.lakePt;
    drag = null;
    l.pts[k] = was;
    renderer.domElement.style.cursor = "";
    drawLakes();
    return true;
  }
  if (drag.ptCand && drag.ptTo) {  // moving a road point
    drag = null;
    clear(marks);
    drawMarks();
    renderer.domElement.style.cursor = "";
    needsDraw = true;
    return true;
  }
  if (drag.drawing) { drag = null; clearNote(); return true; }  // drawing an area
  return false;
}
function cancelCityDrag() {  // Esc while dragging: the city stays where it was
  if (!drag?.city) return false;
  clear(marks);
  delete drag.city.c.dragTo;
  drag = null;
  tellGroup();
  dropLift();
  renderer.domElement.style.cursor = "";
  drawCityMark();
  needsDraw = true;
  return true;
}
async function dropCity(m) {
  clear(marks);
  delete m.c.dragTo;
  setTimeout(tellGroup, 80);  // the drag is over: teammates' copy of it goes
  needsDraw = true;
  if (Math.hypot(m.to[0] - m.c.x, m.to[1] - m.c.y) < 1000) { dropLift(); drawCityMark(); return; }  // barely moved: nothing to do
  await saveCity(m.c, { x: Math.round(m.to[0]), y: Math.round(m.to[1]) });
}
// Move tool: press on a road and drag. The nearest spline point of that road goes with the pointer (a junction
// takes its mouths with it); on the drop the point is saved, the roads are read again and the ground and the
// city are fitted to the road's new line. Generating the world again lays the roads out afresh.
function roadPointUnder(e) {
  const v = groundAt(e);
  if (!v || !state.geo) return null;
  const [x, y] = toUE(v);
  let best = null;
  for (const [aslug, actor] of Object.entries(state.geo.actors || {})) {
    for (const p of actor.pts) {
      const d = Math.hypot(p[1] - x, p[2] - y);
      if (d < Math.max(1200, (p[6] || 500) + 400, orbit.dist * 2) && (!best || d < best.d)) best = { aslug, p, d };
    }
  }
  return best;
}
function dragRoadPoint(e) {
  const v = groundAt(e);
  if (!v) return;
  drag.ptTo = toUE(v);
  const p = drag.ptCand.p, s = Math.max(4, orbit.dist / 80), c = col("accent");
  clear(marks);
  const dot = new THREE.Mesh(new THREE.SphereGeometry(s, 16, 12), new THREE.MeshBasicMaterial({ color: c, depthTest: false }));
  dot.position.copy(toV(drag.ptTo[0], drag.ptTo[1], drag.ptTo[2] + 200));
  marks.add(dot, new THREE.Line(new THREE.BufferGeometry().setFromPoints([toV(p[1], p[2], p[3] + 200), dot.position]),
    new THREE.LineBasicMaterial({ color: c, depthTest: false })));
  renderer.domElement.style.cursor = "grabbing";
  needsDraw = true;
}
async function dropRoadPoint(d) {
  clear(marks);
  needsDraw = true;
  const { aslug, p } = d.ptCand, [x, y] = d.ptTo, t = state.geo?.terrain || {};
  const dx = x - p[1], dy = y - p[2];
  if (Math.hypot(dx, dy) < 200) return;  // barely moved
  const lift = (qx, qy, qz) => (sampleZ(t, qx + dx, qy + dy) ?? qz) + (qz - (sampleZ(t, qx, qy) ?? qz));  // keeps its height over the ground
  const actor = state.geo.actors[aslug], byId = new Map(actor.pts.map((q) => [q[0], q]));
  // a junction moves whole: its mouths (the points its mesh-less links reach) go with it
  const mouths = p[7] ? actor.segs.filter((s) => !s[5] && (s[1] === p[0] || s[2] === p[0])).map((s) => byId.get(s[1] === p[0] ? s[2] : s[1])).filter(Boolean) : [];
  busy("Moving the road…");
  for (const q of [p, ...mouths]) {
    const out = await rpc("move_point", { net_id: state.net, actor: aslug, index: q[0], x: q[1] + dx, y: q[2] + dy, z: lift(q[1], q[2], q[3]) });
    if (out.ok === false) { busy(""); return showError(out.error); }
  }
  busy("");
  await loadGeometry(true);
  schedulePcgPreview(0);  // the ground is cut again along the new line, and the buildings keep clear of it
}
function hoverCursor(e) {  // the Pointer tool shows a grab hand over a city
  if ((state.tool !== "select" && state.tool !== "city") || !state.cities.length) return;
  const now = performance.now();
  if (now - (hoverCursor.t || 0) < 60) return;  // raycasts at most ~16 times a second
  hoverCursor.t = now;
  renderer.domElement.style.cursor = state.tool === "city" ? (handleUnder(e) ? "grab" : cityUnder(e) ? "move" : "")
    : cityUnder(e) ? "pointer" : "";
}
function drawMarks() {
  clear(marks);
  const c = col("accent");
  if (note.kind === "pin" && note.points[0]) {
    const [x, y, z] = note.points[0];
    const s = Math.max(6, orbit.dist / 60);
    const pin = new THREE.Mesh(new THREE.ConeGeometry(s * 0.35, s, 16), new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: 0.3 }));
    pin.rotation.x = Math.PI;
    pin.position.copy(toV(x, y, z)).add(new THREE.Vector3(0, s / 2, 0));
    marks.add(pin);
  } else if (note.points.length > 1) {
    const pts = note.points.map(([x, y, z]) => toV(x, y, z + 150));
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const mat = new THREE.LineBasicMaterial({ color: c, depthTest: false });
    marks.add(note.kind === "area" ? new THREE.LineLoop(geo, mat) : new THREE.Line(geo, mat));
  }
  needsDraw = true;
}
function clearNote() {
  note.kind = ""; note.points = [];
  drawMarks();
  renderTools();
}
// A pin, an area or the build badge: the chat card pops up with where it is typed in; the person finishes the
// sentence in the chat itself and sends it (no box of our own)
async function askChat(kind) {
  if (!state.net) { clearNote(); return showError("Open a world first."); }
  const got = await rpc("note_draft", { net_id: state.net, kind,
    points: JSON.stringify(note.points.map((p) => [p[0], p[1]])) });
  if (got.ok === false) return showError(got.error);
  showError("");
  await openChatDock(got.draft);
}
document.addEventListener("keydown", (e) => {  // Esc takes the pin or the area off the map
  if (e.key === "Escape" && (note.kind === "pin" || note.kind === "area")) clearNote();
});
// ------------------------------------------------------------------ chat: Ducky's real chat in a card over the map
// chat.open place "float" shows the chat itself (messages, composer, styles), never a copy, in a card that pops in
// above the round agent button; its header picker (choices) switches which agent's chat shows
const OWN_CHAT = "own";
let dockOpen = false;
const chatChoices = () => [...(state.view.ownChat ? [{ id: state.view.ownChat, title: "City Builder" }] : []),
  ...(note.chats || []).filter((c) => c.id !== state.view.ownChat).map((c) => ({ id: c.id, title: c.title }))];
function setDockState(s) {
  dockOpen = !!(s && s.open);
  const b = $("chatToggle");
  if (b) {
    b.setAttribute("aria-expanded", String(dockOpen));
    b.setAttribute("aria-label", dockOpen ? "Close the chat" : "Chat with an agent");
    b.title = dockOpen ? "Close the chat" : "Chat with an agent";
  }
  // the header picker swapped the chat: open that one next time
  if (s?.conv_id && dockOpen) {
    const pick = s.conv_id === state.view.ownChat ? OWN_CHAT : s.conv_id;
    if (pick !== (state.view.chat || OWN_CHAT)) { state.view.chat = pick; savePrefs(); }
  }
  if (s?.place && s.place !== "float") setTimeout(resize, 50);  // an older Ducky docks it beside the map
}
async function openChatDock(draft = "") {
  const pick = state.view.chat || OWN_CHAT;
  const world = state.nets.find((n) => n.id === state.net);
  const which = pick === OWN_CHAT
    ? { key: "main", title: "City Builder", brief: "The person is in City Builder (plugin road-network). Use the road-network "
        + `skill and the road_network_* tools. The open world is net_id '${state.net || ""}'${world?.name ? ` (${world.name})` : ""}.` }
    : { conv_id: pick, title: (note.chats || []).find((c) => c.id === pick)?.title || "Chat" };
  try {
    let s = await call("chat.open", { ...which, place: "float", expanded: true, size: 420, offset: 80, choices: chatChoices(),
      ...(draft ? { draft } : {}) });
    if (pick === OWN_CHAT && s?.conv_id && s.conv_id !== state.view.ownChat) {
      state.view.ownChat = s.conv_id;  // the plugin's own chat, so the picker can list it
      savePrefs();
      s = await call("chat.set", { choices: chatChoices() });
    }
    // a Ducky without the floating card docks it under the map: put it beside the map instead
    if (s && s.place !== "float") s = await call("chat.set", { place: "right", expanded: true, size: 420 });
    setDockState(s);
  } catch (e) {
    showError(e.message);
  }
}
async function closeChatDock() {
  setDockState(await call("chat.close").catch(() => null));
}
function renderChatBar() {
  // the launcher: a round agent button; its icon turns into a close cross while the chat is open
  const btn = h("button", { type: "button", class: "rn-launch", id: "chatToggle", "aria-expanded": "false",
    "aria-label": "Chat with an agent", title: "Chat with an agent" },
  h("span", { class: "rn-launch__icon rn-launch__icon--agent" }, icon("agent", 22)),
  h("span", { class: "rn-launch__icon rn-launch__icon--close" }, icon("x", 20)));
  btn.addEventListener("click", () => (dockOpen ? closeChatDock() : openChatDock()));
  $("chatBar").replaceChildren(btn);
  loadChats().then(() => { if (dockOpen) call("chat.set", { choices: chatChoices() }).catch(() => {}); });
  call("chat.state").then(setDockState).catch(() => {});
}
window.addEventListener("message", (ev) => {  // the card's own close button and chat picker report back here
  const e = ev.data?.channel === CHANNEL ? ev.data.event : null;
  if (e?.type === "chat.state") setDockState(e);
});


let chatsLoading = null;
function loadChats() {  // fetched once in the background (on start), refreshed after a send
  chatsLoading ||= rpc("chats").then((got) => { note.chats = got.ok === false ? [] : got.chats || []; chatsLoading = null; });
  return chatsLoading;
}

// ------------------------------------------------------------------ start
(async () => {
  const saved = await call("prefs.get", { id: "ui" }).catch(() => null);
  const v = saved?.value || {};
  Object.assign(state.gen, v.gen || {});
  Object.assign(state.view, v.view || {});
  Object.assign(state.tune, v.tune || {});
  state.tab = TABS.some((t) => t[0] === v.tab) ? v.tab : "world";
  state.live = !!v.live;
  const last = await call("prefs.get", { id: "last_net" }).catch(() => null);
  if (last?.value) state.net = last.value;
  themeColors();
  state.tool = "select";
  renderHeader();
  renderTabs();
  renderToolbar();
  renderTools();
  renderChatBar();
  initSplits();
  showVersion();
  setTimeout(loadChats, 1500);  // the Send to list is ready before anyone draws
  // the right panel's groups fold; long, rarely changed ones start folded
  foldedSections(state.view.folded ?? ["Waterfront and airports", "Preview"], (titles) => { state.view.folded = titles; savePrefs(); });
  buildPanes();
  openTab(state.tab);
  resize();
  placeCamera();
  loop();
  call("plugin.subscribe", { types: ["road_network_changed"] }).catch(() => {});
  liveReady = true;
  // a page that opens while the group is live catches up on cameras and the shared selection
  call("live.state").then((r) => { live.on = !!r?.on; if (live.on) { tellGroup(); relayHolds(true); } }).catch(() => {});
  setTimeout(refreshHolds, 2500);
  call("live.memberStates").then((r) => {
    const rows = Array.isArray(r) ? r : r?.states || r?.members || [];
    rows.forEach((m) => onMemberState(m.from, m.state));
  }).catch(() => {});
  call("live.shared").then((r) => {
    const v = r?.values?.selection ?? r?.selection;
    if (v) onSharedSelection(v.value ?? v, v.by);
  }).catch(() => {});
  await loadNets(false);
  const first = state.net || state.nets[0]?.id;
  first ? openNet(first) : buildScene();
})();
