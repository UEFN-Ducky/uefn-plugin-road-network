import * as THREE from "./vendor/three@0.160.0/build/three.module.min.js";
import { h, icon, iconHTML, slider, segmented, toggle, chip, choices, field, section, button } from "./controls.js";
import { syncPlan, parseLiveView } from "./sync.js";

// ------------------------------------------------------------------ bridge
const CHANNEL = "uefn-plugin-ui";
function call(method, params = {}) {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    function onMsg(ev) {
      const d = ev.data;
      if (!d || d.channel !== CHANNEL || d.id !== id) return;
      window.removeEventListener("message", onMsg);
      d.ok ? resolve(d.result) : reject(new Error(d.error || "bridge error"));
    }
    window.addEventListener("message", onMsg);
    parent.postMessage({ channel: CHANNEL, id, method, params }, "*");
  });
}
const rpc = (method, params = {}) => call("plugin.call", { method, params }).catch((err) => ({ ok: false, error: err.message }));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const $ = (id) => document.getElementById(id);
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue("--" + name).trim();
const alert = (title, message) => call("ui.alert", { title, message }).catch(() => {});

function showError(msg) {
  $("err").innerHTML = msg ? `<div class="dk-error"><div class="dk-error__title">Problem</div>${esc(msg)}</div>` : "";
}
function busy(msg) { $("busy").innerHTML = msg ? `<div class="dk-loading">${esc(msg)}</div>` : ""; }

// ------------------------------------------------------------------ state
const GEN_DEFAULTS = {
  name: "Valley", cities: 3, size_km: 6, relief_m: 60, seed: 7, ground: "new",
  hill_km: 2, roughness: 0.5, detail: 5, ridges: 0, peaks: 0, flatten_m: 300, sea_m: -1000, water_depth_m: 12, terrace_m: 0,
  block_m: 100, wobble: 0.28, prune: 0.22, downtown: 0.4, normal: 0.75, highway: "4L", highway_curve: 0.08,
  core_roads: "4L", neighbourhood_roads: "2S", suburb_roads: "2L", one_way_core: "no", interchange: "direct", overpasses: "yes",
  city_size: 1, city_gap_m: 300, freeways: "yes", waterfront: "yes", canals: "none",
};
const OPTION_KEYS = ["hill_km", "roughness", "detail", "ridges", "peaks", "flatten_m", "sea_m", "water_depth_m", "terrace_m", "block_m", "wobble",
  "prune", "downtown", "normal", "highway", "highway_curve", "core_roads", "neighbourhood_roads", "suburb_roads", "one_way_core",
  "interchange", "overpasses", "city_size", "city_gap_m", "freeways", "waterfront", "canals"];
const VIEW_DEFAULTS = { zx: 1, pcg: true, terrain: true, water: true, districts: true, points: false, look: "natural", sunAz: 135, sunEl: 38, shadows: true };
const state = {
  nets: [], net: "", actor: "", geo: null, sel: null, cities: [], tab: "world", framed: false, live: false,
  gen: { ...GEN_DEFAULTS }, view: { ...VIEW_DEFAULTS }, tune: { side: 8, end: 10, tol: 50 },
};
const ctl = {};  // control handles by key, so loading a world can set them

// road families by mesh name: half width (cm), colour variable, legend label
const FAMILY = [
  [/Freeway/, 1650, "accent", "Freeway"],
  [/FourLane/, 1400, "blue", "Four-lane"],
  [/TwoLaneSidewalk/, 875, "green", "Two-lane + pavement"],
  [/TwoLane|OneLane|Dirt/, 525, "amber", "Two-lane"],
  [/Elev_Deck/, 1650, "purple", "Elevated deck"],
  [/Elev_Ramp/, 650, "yellow", "Flyover ramp"],
  [/Bridge/, 925, "red", "Bridge"],
  [/Water_Canal/, 1200, "blue", "Canal"],
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
const swatch = (c) => {
  const s = String(c);
  if (!s.startsWith("mix:")) return `var(--${s})`;
  const [, a, b, t] = s.split(":");
  return `color-mix(in srgb, var(--${b}) ${Math.round((parseFloat(t) || 0) * 100)}%, var(--${a}))`;
};
// what the city is made of, so the preview reads like the place (road types are told apart by width, not colour)
const MAT = { asphalt: "mix:bg:fg:0.16", sidewalk: "mix:fg-dim:fg:0.35", pavers: "mix:fg-dim:amber:0.3",
  drive: "mix:bg:fg:0.26", deck: "mix:fg-dim:blue:0.2" };
// the asphalt part of a road whose mesh carries its own sidewalks (2S 8.75 m -> 5.25 m, 4L 14 m -> 10.5 m)
const ASPHALT_HALF = { 875: 525, 1400: 1050 };
function roadMaterial(mesh) {
  if (/Path_Sidewalk/.test(mesh)) return [MAT.sidewalk, "Sidewalk"];
  if (/Path_Walkway/.test(mesh)) return [MAT.pavers, "Walkway"];
  if (/Path_Driveway/.test(mesh)) return [MAT.drive, "Driveway"];
  if (/Path_PrivateStreet/.test(mesh)) return [MAT.asphalt, "Private street"];
  if (/Bridge|Elev_/.test(mesh)) return [MAT.deck, "Bridge / deck"];
  return [MAT.asphalt, "Road"];
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
  scene.fog = new THREE.Fog(bg, 1000, 8000);
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
  if (typeof sendMyCamera === "function" && liveReady) sendMyCamera();
}
let liveReady = false;  // set once the page has started (the live helpers below exist then)
let drag = null;
renderer.domElement.addEventListener("contextmenu", (e) => e.preventDefault());
renderer.domElement.addEventListener("pointerdown", (e) => {
  drag = { x: e.clientX, y: e.clientY, button: e.button, moved: false };
  renderer.domElement.setPointerCapture(e.pointerId);
  if (e.button === 0 && state.tool === "area") { drag.drawing = true; note.points = []; addAreaPoint(e); }
});
renderer.domElement.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
  if (drag.drawing) {  // drawing an area: a point every few pixels, no orbit
    if (Math.abs(dx) + Math.abs(dy) > 6) { drag.x = e.clientX; drag.y = e.clientY; addAreaPoint(e); }
    return;
  }
  drag.x = e.clientX; drag.y = e.clientY;
  if (drag.button === 2 || e.shiftKey) {
    const s = orbit.dist / 900;
    const right = new THREE.Vector3().subVectors(camera.position, orbit.target).cross(camera.up).normalize();
    const fwd = new THREE.Vector3(Math.sin(orbit.az), 0, Math.cos(orbit.az));
    orbit.target.addScaledVector(right, dx * s).addScaledVector(fwd, -dy * s);
  } else {
    orbit.az -= dx * 0.005;
    orbit.el = Math.max(0.05, Math.min(1.55, orbit.el + dy * 0.005));
  }
  placeCamera();
});
renderer.domElement.addEventListener("pointerup", (e) => {
  if (drag?.drawing) { drag = null; finishArea(); return; }
  if (drag && !drag.moved && drag.button === 0) {
    if (state.tool === "pin") placePin(e); else pick(e);
  }
  drag = null;
});
renderer.domElement.addEventListener("wheel", (e) => {
  e.preventDefault();
  orbit.dist = Math.max(30, Math.min(150000, orbit.dist * (e.deltaY > 0 ? 1.15 : 0.87)));
  placeCamera();
}, { passive: false });

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
function loop() {
  if (visible && needsDraw) { renderer.render(scene, camera); placeLabels(); needsDraw = false; }
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
  const known = (j, i) => H[j][i] !== null && H[j][i] !== undefined;
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
  const pal = { sand: col("amber").lerp(col("fg"), 0.35), grassLo: col("green-dim").lerp(col("green"), 0.35), grassHi: col("green").multiplyScalar(0.75),
    dirt: col("amber-dim").lerp(col("fg-dim"), 0.3), rock: col("fg-dim").multiplyScalar(0.85), snow: col("fg"),
    low: col("blue"), mid: col("green"), high: col("amber"), top: col("red") };
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
      c.lerp(pal.snow, smooth(0.82, 0.95, tH) * (1 - smooth(0.15, 0.3, slope)));
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
  const byColor = {}, legend = {}, box = new THREE.Box3();
  for (const [aslug, actor] of Object.entries(g.actors || {})) {
    const pts = new Map(actor.pts.map((p) => [p[0], p]));
    const dim = state.actor && state.actor !== aslug;
    for (const s of actor.segs) {
      const a = pts.get(s[1]), b = pts.get(s[2]);
      if (!a || !b || !s[5]) continue; // mesh-less links only join the graph
      const f = family(s[5]);
      const [m, label] = roadMaterial(s[5]);
      const curve = hermite(a, b, s[3], s[4], 6);
      const lift = f[2] === "purple" || f[2] === "yellow" ? 0 : 60;
      const inner = ASPHALT_HALF[f[1]];
      if (inner && m === MAT.asphalt) {  // a road with its own sidewalks: concrete to the full width, asphalt inside
        (byColor[MAT.sidewalk + (dim ? "|dim" : "")] ||= []).push(...ribbon(curve, f[1], lift - 4));
        legend["Sidewalk"] = MAT.sidewalk;
      }
      (byColor[m + (dim ? "|dim" : "")] ||= []).push(...ribbon(curve, inner && m === MAT.asphalt ? inner : f[1], lift));
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
      const widest = Math.max(...list.map((m) => m[6] || 500));
      const dirs = list.map((m) => { const dx = m[1] - c[1], dy = m[2] - c[2], l = Math.hypot(dx, dy) || 1; return [dx / l, dy / l, m]; });
      for (const [ux, uy, m] of dirs) {
        const from = [c[1] - ux * widest, c[2] - uy * widest, c[3]];
        (byColor[MAT.asphalt] ||= []).push(...ribbon([from, [m[1], m[2], m[3]]], ASPHALT_HALF[m[6]] || m[6] || 500, 65));
      }
      dirs.sort((a, b) => Math.atan2(a[1], a[0]) - Math.atan2(b[1], b[0]));
      dirs.forEach(([ux, uy, m], k) => {  // the sidewalk corner between this arm and the next
        const [vx, vy, n] = dirs[(k + 1) % dirs.length];
        const ha = (m[6] || 500) + 190, hb = (n[6] || 500) + 190;
        const cx = c[1] + ux * hb + vx * ha, cy = c[2] + uy * hb + vy * ha;
        (byColor[MAT.sidewalk] ||= []).push(...ribbon([[cx - ux * 190, cy - uy * 190, c[3]], [cx + ux * 190, cy + uy * 190, c[3]]], 190, 75));
      });
      legend["Road"] = MAT.asphalt;
      legend["Sidewalk"] = MAT.sidewalk;
    }
    for (const p of actor.pts) {
      if (!p[7] || p[7] === "SM_Junc_Empty") continue;
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
    const [c, dim] = key.split("|");
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(arr, 3));
    geo.computeVertexNormals();
    const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: dim ? col(c).multiplyScalar(0.45) : col(c), roughness: 0.75,
      side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2 }));
    mesh.receiveShadow = true;
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
      const water = new THREE.Mesh(new THREE.PlaneGeometry(d, w), new THREE.MeshStandardMaterial({ color: col("blue"), roughness: 0.15,
        metalness: 0.1, transparent: true, opacity: 0.72 }));
      water.rotation.x = -Math.PI / 2;
      water.position.copy(toV((t.min[0] + t.max[0]) / 2, (t.min[1] + t.max[1]) / 2, t.sea));
      water.receiveShadow = true;
      groups.water.add(water);
      legend["Lake"] = "blue";
    }
  }
  if (state.view.districts) drawDistricts();
  if (state.view.pcg) drawPcg(legend);
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
  needsDraw = true;
}
// citygen.outline(): a city's edge in a direction as a factor of its radius (keep the two in step)
function cityOutline(c, angleDeg) {
  const s = Math.trunc(c.seed || 1), a = angleDeg * Math.PI / 180;
  let f = 0;
  for (const [k, amp] of [[2, 0.11], [3, 0.08], [5, 0.05], [7, 0.025]]) {
    const phase = (((s * (k * 7919 + 13)) % 997 + 997) % 997) / 997 * 2 * Math.PI;
    f += amp * Math.sin(k * a + phase);
  }
  return 1 + f;
}

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
    case "gable": return [box(0, 0, 0, d, w, h * 0.62), box(0, 0, h * 0.62, d * 0.55, w * 0.92, h * 0.25), box(0, 0, h * 0.87, d * 0.2, w * 0.86, h * 0.13)];
    case "flat": return [box(0, 0, 0, d, w, Math.max(0.4, h))];
    default: return [box(0, 0, 0, d, w, h)];
  }
}
function drawBuildings(layer, colour) {
  const zx = state.view.zx || 1;
  const forms = layer.forms || [];
  const boxes = [], cyls = [];
  const dummy = new THREE.Object3D();
  for (const p of layer.points || []) {
    const [x, y, z, yaw, , hgt, fi, wv, dv] = p;
    const [w0, d0] = layer.size_m || [12, 12];
    const w = (wv || w0) * 0.92, d = (dv || d0) * 0.92;
    const r = yaw * Math.PI / 180, fxv = Math.cos(r), fyv = Math.sin(r);
    for (const part of formParts(forms[fi] || "box", w, d, hgt || 6)) {
      const ux = x + (part.fx * fxv - part.fy * fyv) * 100, uy = y + (part.fx * fyv + part.fy * fxv) * 100;
      dummy.position.copy(toV(ux, uy, z + part.z * 100));
      dummy.position.y += part.sz * zx / 2;
      dummy.rotation.set(0, -r, 0);
      dummy.scale.set(part.sx, Math.max(0.2, part.sz) * zx, part.sy);
      dummy.updateMatrix();
      (part.c ? cyls : boxes).push(dummy.matrix.clone());
    }
  }
  const mat = new THREE.MeshStandardMaterial({ color: col(colour), roughness: 0.55, metalness: 0.2 });
  for (const [list, geo] of [[boxes, new THREE.BoxGeometry(1, 1, 1)], [cyls, new THREE.CylinderGeometry(0.5, 0.5, 1, 20)]]) {
    if (!list.length) continue;
    const mesh = new THREE.InstancedMesh(geo, mat, list.length);
    list.forEach((mtx, i) => mesh.setMatrixAt(i, mtx));
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    groups.pcg.add(mesh);
  }
}
// Foundations where a hill falls away under a lot, and the walkways / driveways from each door to the kerb
// (row [.., foundation_m, paths [[width_m, [x, y, z, ...]]]]).
function drawLotGround(layer) {
  const zx = state.view.zx || 1;
  const dummy = new THREE.Object3D();
  const found = [], walks = [];
  for (const p of layer.points || []) {
    const [x, y, z, yaw, , , , w, d, depth, paths, walls] = p;
    for (const [wx, wy, wyaw, len, hgt] of walls || []) {  // retaining walls on uphill cuts
      dummy.position.copy(toV(wx, wy, z));
      dummy.position.y += hgt * zx / 2;
      dummy.rotation.set(0, -wyaw * Math.PI / 180, 0);
      dummy.scale.set(len, hgt * zx, 0.3);
      dummy.updateMatrix();
      found.push(dummy.matrix.clone());
    }
    if (depth > 0.3) {
      dummy.position.copy(toV(x, y, z - depth * 50));
      dummy.rotation.set(0, -yaw * Math.PI / 180, 0);
      dummy.scale.set(d * 0.94, depth * zx, w * 0.94);
      dummy.updateMatrix();
      found.push(dummy.matrix.clone());
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
      }
    }
  }
  for (const [list, colour] of [[found, col("fg-dim").multiplyScalar(0.7)], [walks, col("fg-dim").lerp(col("fg"), 0.5)]]) {
    if (!list.length) continue;
    const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ color: colour, roughness: 0.95 }), list.length);
    list.forEach((m, i) => mesh.setMatrixAt(i, m));
    mesh.receiveShadow = true;
    groups.pcg.add(mesh);
  }
}
function drawPcg(legend) {
  const layers = state.pcg || {};
  const dummy = new THREE.Object3D();
  Object.entries(layers).forEach(([name, layer], k) => {
    const pts = layer.points || [];
    if (!pts.length) return;
    const colour = ROLE_COLOR[layer.role] || PCG_COLORS[k % PCG_COLORS.length];
    if (layer.kind === "site") {  // a neighbourhood's pieces, pre-scaled: [x, y, z, yaw, sx, sy, sz] x box_m
      const [bx, by, bz] = layer.box_m || [1, 1, 1];
      const tint = layer.tint || (layer.mesh === "SM_Kit_ParkingStall" ? "mix:fg:bg:0.05" : layer.mesh === "SM_City_CT_MidRise" ? "fg-dim" : MAT.drive);
      const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ color: col(tint), roughness: 0.9 }), pts.length);
      const zx = state.view.zx || 1;
      pts.forEach(([x, y, z, yaw, sx, sy, sz], i) => {
        dummy.position.copy(toV(x, y, z + 20));
        dummy.position.y += bz * sz * zx / 2;
        dummy.rotation.set(0, -yaw * Math.PI / 180, 0);
        dummy.scale.set(by * sy, Math.max(0.05, bz * sz) * zx, bx * sx);
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
      });
      mesh.receiveShadow = true;
      groups.pcg.add(mesh);
      legend[`${name} · ${layer.count.toLocaleString()}`] = tint;
      return;
    }
    if (layer.kind === "walls") {  // stone retaining walls along cut / filled roads: [x, y, z, yaw, 1, length m, height m]
      const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ color: col("fg-dim").multiplyScalar(0.8), roughness: 1 }), pts.length);
      const zx = state.view.zx || 1;
      pts.forEach(([x, y, z, yaw, , len, hgt], i) => {
        dummy.position.copy(toV(x, y, z));
        dummy.position.y += hgt * zx / 2;
        dummy.rotation.set(0, -yaw * Math.PI / 180, 0);
        dummy.scale.set(len, hgt * zx, 0.5);
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
      });
      mesh.castShadow = true;
      groups.pcg.add(mesh);
      legend[`${name} · ${layer.count.toLocaleString()}`] = "fg-dim";
      return;
    }
    const building = layer.kind === "buildings";
    if (building) { drawBuildings(layer, colour); legend[`${name} · ${layer.count.toLocaleString()}`] = colour; }
    const [geo, lift] = building ? [null, 0] : pcgShape(name, layer.kind);
    if (!building) {
      const mesh = new THREE.InstancedMesh(geo, new THREE.MeshStandardMaterial({ color: col(colour), roughness: 0.85 }), pts.length);
      pts.forEach(([x, y, z, yaw, s], i) => {
        dummy.position.copy(toV(x, y, z));
        dummy.rotation.set(0, -yaw * Math.PI / 180, 0);
        dummy.scale.setScalar(s);
        dummy.position.y += lift * s;
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
      });
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      groups.pcg.add(mesh);
    }
    if (building) drawLotGround(layer);
    legend[`${name} · ${layer.count.toLocaleString()}`] = colour;
  });
}
let pcgPreviewTimer = 0;
function schedulePcgPreview(delay = 700) {
  clearTimeout(pcgPreviewTimer);
  pcgPreviewTimer = setTimeout(loadPcgPreview, delay);
}
async function loadPcgPreview() {
  if (!state.net || !(state.layers || []).length) { state.pcg = {}; buildScene(); return; }
  const net = state.net;
  const out = {};
  const todo = state.layers.filter((x) => x.enabled);
  for (const [i, l] of todo.entries()) {  // one layer per reply keeps each one small; the first call computes them all
    busy(i ? `Previewing PCG layers ${i + 1}/${todo.length}…` : "Placing every PCG layer…");
    const r = await rpc("pcg_preview", { net_id: net, layer_id: l.id });
    if (r.ok === false) { busy(""); return showError(r.error); }
    Object.assign(out, r.layers || {});
  }
  const walls = await rpc("pcg_preview", { net_id: net, layer_id: "road-walls" });  // stone walls from road cut / fill
  if (walls.ok !== false) Object.assign(out, walls.layers || {});
  busy("");
  if (net !== state.net) return;
  state.pcg = out;
  renderPcgCounts();
  buildScene();
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
    for (const [ring, colour] of [["downtown", "accent"], ["normal", "fg-dim"], ["housing", "muted"]]) {
      const r = c.districts?.[ring]?.radius;
      if (!r) continue;
      const pts = [];
      for (let k = 0; k <= 96; k++) {
        const a = k / 96 * Math.PI * 2;
        const rr = r * cityOutline(c, a * 180 / Math.PI);  // the same irregular edge the generator uses
        const x = c.x + Math.cos(a) * rr, y = c.y + Math.sin(a) * rr;
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
      const line = new THREE.Line(geo, new THREE.LineDashedMaterial({ color: col("amber"), dashSize: 24, gapSize: 10 }));
      line.computeLineDistances();
      groups.districts.add(line);
    }
  }
}
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
  box.getCenter(orbit.target);
  orbit.dist = box.getSize(new THREE.Vector3()).length() * 0.75;
  placeCamera();
}
// city name tags that follow the camera
function placeLabels() {
  const host = $("labels");
  if (!state.view.districts || !state.geo) { host.innerHTML = ""; return; }
  if (host.children.length !== state.cities.length) {
    host.innerHTML = state.cities.map((c) => `<div class="rn-label" data-live-id="city:${esc(c.id)}">${iconHTML("pin", 12)}${esc(c.name)}</div>`).join("");
  }
  const w = view.clientWidth, hh = view.clientHeight, t = state.geo.terrain || {};
  state.cities.forEach((c, k) => {
    const v = toV(c.x, c.y, (sampleZ(t, c.x, c.y) ?? 0) + 4000).project(camera);
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
  if (!hits.length) return;
  const hit = hits[0];
  if (hit.object.isPoints) select(hit.object.userData.actor, hit.object.userData.pts[hit.index]);
  else select(hit.object.userData.actor, hit.object.userData.point);
  openTab("edit");
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
    $(`pane-${t}`).classList.toggle("is-open", t === id);
    const b = $(`tab-${t}`);
    b?.setAttribute("aria-selected", t === id ? "true" : "false");
    b?.setAttribute("tabindex", t === id ? "0" : "-1");
    b?.toggleAttribute("aria-current", t === id);
  }
  $("paneTitle").textContent = (TABS.find((t) => t[0] === id) || [])[1] || "";
  $("foot").hidden = !GEN_TABS.has(id);
  document.body.dataset.liveView = `${state.net || "networks"}:${id}`;
  savePrefs();
}

// a generate setting changed: remember it, and rebuild right away when live preview is on
let liveTimer = 0;
function setGen(key, v) {
  state.gen[key] = v;
  savePrefs();
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
    ["new", "New terrain", "Hills made around the cities, with a heightmap to import", "terrain"],
    ["net", "Selected network's ground", "Cities sit on the open network's scanned terrain", "layers"]] });
  $("pane-world").replaceChildren(
    section("World", ctl.name.el, sl("cities", "Cities", 1, 12, 1, (v) => String(v)), sl("size_km", "Map size", 1, 20, 0.5, (v) => `${v} km`),
      sl("city_size", "City size", 0.3, 4, 0.05, (v) => `×${(+v).toFixed(2)}`, "Every city bigger or smaller (regenerating scales the cities you have)"),
      sl("city_gap_m", "Distance between cities", 0, 6000, 50, m, "Open country between two cities' edges, at least: cities closer than this are pushed apart"),
      ctl.seed.el),
    section("Selected city", h("div", { id: "worldCity" })),
    section("Where it goes", ctl.ground.el),
    section("This world", h("div", { id: "worldSummary" })));
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
    section("Shape", sl("relief_m", "Hill height", 0, 250, 5, m), sl("hill_km", "Hill size", 0.3, 10, 0.1, (v) => `${v.toFixed(1)} km`),
      sl("roughness", "Roughness", 0.2, 0.8, 0.02, pct), sl("detail", "Detail", 1, 8, 1, (v) => `${v} layers`),
      sl("ridges", "Mountain ridges", 0, 1, 0.05, pct, "0 = rolling hills, 100 % = sharp crests"),
      sl("peaks", "Peaks", 0, 1, 0.05, pct, "Wide low valleys with tall sharp peaks")),
    section("Water and steps", sl("sea_m", "Water level", -1000, 200, 5, (v) => v <= -1000 ? "Off" : m(v), "Ground below this is dug down into lake beds under the water"),
      sl("water_depth_m", "Lake depth", 0, 80, 1, m, "How far lake beds drop below the water line"),
      sl("terrace_m", "Terraces", 0, 50, 1, (v) => v ? m(v) : "Off"),
      sl("flatten_m", "City edge blend", 0, 2000, 25, m, "How far the flat city ground blends back into the hills")),
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
  const junctions = section("Highways and interchanges", ctl.highway.el,
    seg("interchange", "Where a highway meets a city", [["direct", "Direct", "route"], ["diamond", "Diamond", "grid"],
      ["trumpet", "Trumpet", "refresh"], ["cloverleaf", "Clover", "mixed"], ["stack", "Stack", "layers"]]),
    seg("overpasses", "Where a highway crosses a street", [["yes", "Bridge over", "top"], ["no", "Cross at grade", "route"]]),
    seg("freeways", "Urban freeways through big cities", [["yes", "Freeways", "layers"], ["no", "None", "route"]]),
    seg("waterfront", "Waterfront (quays, marinas, beaches, ports)", [["yes", "Develop", "wave"], ["no", "Leave natural", "terrain"]]),
    seg("canals", "Canals", [["none", "None", "route"], ["canal_city", "Canal city", "wave"]]),
    h("div", { class: "rn-hint" }, "Diamond, trumpet and cloverleaf are built from our ramp, bridge and road pieces: the highway " +
      "bridges over a cross road with slip ramps (diamond), one loop (trumpet) or four loops (cloverleaf). Stack rises onto the " +
      "elevated deck. You see every ramp in the 3D view."));
  $("pane-roads").replaceChildren(
    section("Streets", sl("block_m", "Block size", 50, 250, 5, m), sl("wobble", "Curvy streets", 0, 0.45, 0.01, pct, "How far organic streets drift off the grid"),
      sl("prune", "Side streets removed", 0, 0.5, 0.01, pct)),
    section("Districts", sl("downtown", "Downtown ring", 0.1, 0.8, 0.01, pct, "Four-lane grid inside this share of each city's radius"),
      sl("normal", "Normal city ring", 0.2, 0.95, 0.01, pct, "Streets with pavements up to here; housing beyond")),
    roadKinds, junctions, sl("highway_curve", "Highway curve", 0, 0.3, 0.01, pct));
  // ---- Footer
  ctl.live = toggle({ label: "Live preview", iconName: "play", value: state.live, onChange: (v) => { state.live = v; savePrefs(); } });
  $("foot").replaceChildren(ctl.live.el, button("Generate", "refresh", () => generate(), "primary", "Build the terrain, cities and roads (offline, nothing changes in UEFN)"));
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
const WHERE_OPTS = [
  ["outside", "Outside the cities", "Countryside between towns", "terrain"],
  ["everywhere", "Everywhere", "Whole map, cities too", "world"],
  ["cities", "In the cities", "Every district of every city", "city"],
  ["downtown", "Downtown", "Inner ring of each city", "grid"],
  ["normal", "Normal city", "Between downtown and housing", "mixed"],
  ["housing", "Housing", "Outer ring of each city", "pin"],
  ["industrial", "Industrial zone", "One wedge of each city's outer rings", "wrench"],
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
  const pieces = choices({ label: "Pieces", value: state.assetSet || "own", options: ASSET_OPTS, onChange: async (v) => {
    const out = await rpc("pcg_assets", { net_id: state.net, asset_set: v });
    if (out.ok === false) return showError(out.error);
    state.assetSet = out.asset_set;
  } }).el;
  const place = button("Push city to UEFN", "upload", onPcgPlace, "primary",
    "Builds the city as PCG in the open UEFN level, 400 m tile by tile: only the tiles you changed are rebuilt");
  place.style.width = "100%";
  pane.replaceChildren(
    section("What it's built from", pieces, h("div", { class: "rn-hint", style: "margin-top: 6px" },
      "Every layer is built from the project's own kit (layers with meshes of their own keep them).")),
    section("Placement layers", head, h("div", { class: "rn-hint", style: "margin: 8px 0" },
      "Layers place in order: each one keeps clear of the roads and the layers you tick under Keep clear of.")),
    ...(cards.length ? cards : [h("div", { class: "dk-empty" }, h("div", { class: "dk-empty__title" }, "No layers yet"), "Add the starter layers or a new one.")]),
    section("Build", h("div", { id: "pcgPlanInfo", class: "rn-hint", style: "margin-bottom: 8px" }),
      h("div", { class: "rn-stack" }, button("Refresh 3D preview", "refresh", () => loadPcgPreview()), place)));
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
  const enabled = toggle({ label: "On", value: l.enabled, onChange: (v) => save({ enabled: v }, true) });
  enabled.el.style.width = "auto";
  enabled.el.style.marginBottom = "0";
  const headBtn = h("button", { type: "button", class: "rn-card__head", style: "width: 100%; border: 0; background: none; color: inherit; font: inherit; padding: 0; cursor: pointer; margin: 0", "aria-expanded": open ? "true" : "false" },
    icon(KIND_ICON[l.kind] || "layers", 16), h("span", { class: "rn-card__name", style: "text-align: left" }, l.name),
    h("span", { class: "rn-tag" }, (WHERE_OPTS.find((w) => w[0] === l.where) || [, l.where])[1]),
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
    num("size_jitter", "Size variety", 0, 0.6, 0.05, pct, "How much each lot's width and depth differ from the next"));
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
  const [out, assets] = await Promise.all([rpc("pcg_layers", { net_id: state.net }), rpc("pcg_assets", { net_id: state.net })]);
  state.layers = out.ok === false ? [] : out.items || [];
  renderLayerList();
  state.assetSet = assets.ok === false ? "own" : assets.asset_set;
  const pane = $("pane-pcg");
  if (pane && pane.contains(document.activeElement) && document.activeElement !== document.body) {
    // the person is typing or dragging in a layer card: redraw once they leave it, never under their hands
    pane.addEventListener("focusout", () => setTimeout(renderPcgPane, 0), { once: true });
  } else {
    renderPcgPane();
  }
  schedulePcgPreview(100);
}
async function onPcgPlace() {
  if (!state.net) return;
  const ask = await rpc("pcg_build", { net_id: state.net });
  if (ask.ok !== false) return alert("Up to date", ask.note || "Nothing changed since the last push.");
  if (!/confirm=true/.test(ask.error || "")) return showError(ask.error);
  if (!ask.build?.length && !ask.remove?.length) return alert("Up to date", "Nothing changed since the last push: every tile is left as it is.");
  const ok = await call("ui.confirm", { title: "Push city to UEFN", confirm: "Push",
    message: (ask.error || "").replace(" Call again with confirm=true.", "") + " Only those tiles change in the open UEFN level. Don't touch UEFN until it finishes." }).catch(() => null);
  if (!ok?.confirmed) return;
  busy(`Pushing ${ask.build.length} changed tiles to UEFN…`);
  const out = await rpc("pcg_build", { net_id: state.net, confirm: true });
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
// the World section's city picker: pick a city, then size it (the same save as the Cities section)
function renderWorldCity() {
  const box = $("worldCity");
  if (!box) return;
  if (!state.cities.length) return box.replaceChildren(h("div", { class: "rn-hint" }, "Generate a world, then pick a city here to resize it."));
  const c = state.cities.find((x) => x.id === state.cityPick) || state.cities[0];
  state.cityPick = c.id;
  const picks = h("div", { class: "dk-row", style: "margin-bottom: 10px" }, ...state.cities.map((x) => chip({ label: x.name, iconName: "city",
    value: x.id === c.id, onChange: () => { state.cityPick = x.id; renderWorldCity(); } }).el));
  box.replaceChildren(picks,
    slider({ label: `${c.name} radius`, min: 150, max: 5000, step: 10, value: Math.round(c.radius / 100), fmt: m,
      hint: "Rebuilds this city at its new size; its neighbours move away if they would overlap",
      onChange: (v) => saveCity(c, { radius_m: v }) }).el,
    segmented({ label: "Road style", value: c.style, options: STYLE_OPTS, onChange: (v) => saveCity(c, { style: v }) }).el);
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
      cityPlan(c.plan), cityWater(c.waterfront));
  });
  pane.replaceChildren(section(`Cities · ${state.cities.length}`, ...cards),
    h("div", { class: "rn-hint" }, "Each city keeps its own roads actor, style and building sets. Changing one rebuilds the world with the same seed."));
}
async function saveCity(c, change) {
  busy(`Rebuilding ${c.name}…`);
  let out = await rpc("city_save", { net_id: state.net, city: c.id, ...change });
  if (out.ok !== false) out = await rpc("generate", { name: currentNet()?.name || state.net, seed: state.gen.seed, cities: "",
    size_km: state.gen.size_km, relief_m: state.gen.relief_m, options: JSON.stringify(optionsOf(state.gen)) });
  busy("");
  if (out.ok === false) return showError(out.error);
  showError("");
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
async function onHeightmap() {
  if (!state.net) return;
  busy("Writing the heightmap…");
  const out = await rpc("heightmap", { net_id: state.net });
  busy("");
  if (out.ok === false) return showError(out.error);
  showError("");
  $("hmInfo").innerHTML = `<div class="rn-card"><div class="rn-kv"><span>Resolution</span><span>${esc(out.resolution)}</span>
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
    viewChip("pcg", "PCG", "layers"), viewChip("points", "Points", "points"));
}
function renderHud() {
  const n = currentNet();
  const t = state.stats?.total;
  $("hud").innerHTML = n ? `<b>${esc(n.name)}</b><span>${state.cities.length ? `${state.cities.length} cities · ` : ""}${t ? `${((t.road_length_m || 0) / 1000).toFixed(1)} km of road` : ""}</span>` : "";
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
  state.net = id; state.framed = false; state.sel = null; state.actor = ""; state.pcg = {};
  call("prefs.set", { id: "last_net", value: id }).catch(() => {});
  renderWorldPick();
  setTimeout(loadSync, 400);  // after the world's own loads: the badge never holds up the view
  document.querySelectorAll("[data-net]").forEach((b) => b.toggleAttribute("aria-current", b.dataset.net === id));
  const g = currentNet()?.generated;
  if (g) {  // the generate tabs show this world's own settings
    Object.assign(state.gen, { name: currentNet().name, seed: g.seed ?? state.gen.seed, size_km: g.size_km ?? state.gen.size_km,
      relief_m: g.relief_m ?? state.gen.relief_m, cities: g.cities ?? state.gen.cities, ground: g.ground_from ? "net" : "new", ...(g.options || {}) });
    syncControls();
  }
  document.body.dataset.liveView = `${id}:${state.tab}`;
  renderUefnPane();
  await loadGeometry(false);
}
async function loadGeometry(keep) {
  if (!state.net) return;
  busy("Loading roads…");
  const [geo, st, cities] = await Promise.all([loadAllGeometry(state.net), rpc("stats", { net_id: state.net }), rpc("cities", { net_id: state.net })]);
  busy("");
  if (geo.ok === false) return showError(geo.error);
  state.geo = geo;
  state.cities = cities.ok === false ? [] : cities.items || [];
  renderActors(geo);
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
}
// A whole city is several MB: the terrain comes first, then each actor in pages of PAGE points
// and segments, so no single reply nears the panel bridge's 1 MB line.
const PAGE = 2500;
async function loadAllGeometry(net) {
  const [base, info] = await Promise.all([rpc("geometry", { net_id: net, actor: "-" })  /* "-" matches no actor: terrain and props only */, rpc("get", { net_id: net })]);
  if (base.ok === false) return base;
  if (info.ok === false) return info;
  const actors = {};
  await Promise.all(Object.keys(info.actors || {}).map(async (aslug) => {
    const a = actors[aslug] = { label: info.actors[aslug].label, pts: [], segs: [] };
    for (let offset = 0; ; offset += PAGE) {
      const page = await rpc("geometry", { net_id: net, actor: aslug, offset, limit: PAGE, terrain: false });
      if (page.ok === false) throw new Error(page.error);
      const part = page.actors?.[aslug];
      if (!part) break;
      a.pts.push(...part.pts);
      a.segs.push(...part.segs);
      if (offset + PAGE >= Math.max(part.points_total, part.segments_total)) break;
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
  // same world with the same city layout: keep its cities (their styles and sizes) instead of placing new ones
  const keep = prev && prev.cities === g.cities && prev.seed === g.seed && prev.size_km === g.size_km;
  busy("Generating terrain, cities and roads…");
  const out = await rpc("generate", { name, seed: g.seed, cities: keep ? "" : String(g.cities), size_km: g.size_km, relief_m: g.relief_m,
    ground_from: groundFrom, options: JSON.stringify(optionsOf(g)) });
  busy("");
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
  if (e.type === "panel.visibility") { visible = !!e.visible; needsDraw = true; }
  if (e.type === "appearance_theme") { themeColors(); buildScene(); }
  if (e.type === "plugin_scope_changed") {
    if (e.reason === "synced") applySync(e.keys);  // teammates' changes: reload only what changed, keep what's open
    else { state.net = ""; state.geo = null; state.cities = []; state.layers = []; state.pcg = {}; loadNets(); buildScene(); }
  }
  if (e.type === "road_network_changed" && (!e.id || e.id === state.net)) {
    if (e.what === "pcg") loadLayers(); else loadGeometry(true);
  }
  if (e.type === "live.memberState") onMemberState(e.from, e.state);
  if (e.type === "live.shared" && e.key === "selection") onSharedSelection(e.value, e.by);
});
window.addEventListener("ducky-live-goto", (ev) => {  // the group moved to another world or tab
  const { net, tab } = parseLiveView(ev.detail?.view || ev.detail?.record);
  if (net && net !== state.net && state.nets.some((n) => n.id === net)) openNet(net);
  if (tab && tab !== state.tab && TABS.some((t) => t[0] === tab)) openTab(tab);
});

async function applySync(keys) {
  const plan = syncPlan(keys, state.net);
  if (plan.nets) await loadNets(false);
  if (plan.geometry) await loadGeometry(true);  // keeps the selection and camera
  else if (plan.cities) {
    const c = await rpc("cities", { net_id: state.net });
    if (c.ok !== false) { state.cities = c.items || []; renderCitiesPane(); buildScene(); }
  }
  if (plan.layers && !plan.geometry) loadLayers();  // loadGeometry already reloads layers
}

// ------------------------------------------------------------------ live: cameras and the shared selection
// Each person's 3D camera is their own live state (teammates see where everyone is looking); the picked
// spline point is one shared value for the group.
const peers = {};
let camSent = 0, camTimer = 0;
function sendMyCamera() {
  clearTimeout(camTimer);
  const now = Date.now();
  if (now - camSent < 250) { camTimer = setTimeout(sendMyCamera, 250); return; }
  camSent = now;
  const t = orbit.target;
  call("live.setMyState", { state: { net: state.net, tab: state.tab,
    cam: { x: +t.x.toFixed(1), y: +t.y.toFixed(1), z: +t.z.toFixed(1), dist: Math.round(orbit.dist), az: +orbit.az.toFixed(3), el: +orbit.el.toFixed(3) } } }).catch(() => {});
}
function peerKey(from) { return String(from?.device || from?.user || from?.name || from || ""); }
function onMemberState(from, s) {
  const k = peerKey(from);
  if (!k) return;
  if (!s) delete peers[k];
  else peers[k] = { name: String(from?.name || "Teammate"), state: s };
  drawPeers();
}
const peerGroup = new THREE.Group();
scene.add(peerGroup);
function drawPeers() {
  clear(peerGroup);
  for (const p of Object.values(peers)) {
    const c = p.state?.cam;
    if (!c || p.state.net !== state.net) continue;
    const pos = new THREE.Vector3(c.x + c.dist * Math.cos(c.el) * Math.sin(c.az), c.y + c.dist * Math.sin(c.el),
      c.z + c.dist * Math.cos(c.el) * Math.cos(c.az));
    const size = Math.max(8, c.dist / 40);
    const eye = new THREE.Mesh(new THREE.ConeGeometry(size * 0.6, size * 1.6, 4), new THREE.MeshStandardMaterial({ color: col("accent") }));
    eye.position.copy(pos);
    eye.lookAt(c.x, c.y, c.z);
    eye.rotateX(Math.PI / 2);
    peerGroup.add(eye);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([pos, new THREE.Vector3(c.x, c.y, c.z)]),
      new THREE.LineDashedMaterial({ color: col("accent"), dashSize: size, gapSize: size / 2 }));
    line.computeLineDistances();
    peerGroup.add(line);
    p.pos = pos;
  }
  needsDraw = true;
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
  // City Builder is the blueprint: an AI builds it in UEFN with UEFN's own systems (landscape splines, PCG)
  $("headPush").replaceChildren(h("span", { id: "syncBadge" }),
    button("Build in UEFN with AI", "chat", () => openNote("build"), "primary",
      "Send this world to an AI chat that builds what changed in UEFN: roads as landscape splines, the city as PCG"));
  loadSync();
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
  b.addEventListener("click", () => s.in_sync ? loadSync() : openNote("build"));
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
      h("span", {}, l.name), h("span", { class: "rn-count", "data-pcg-count": l.name }, ""), icon("grip", 14));
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

// ------------------------------------------------------------------ sidebar: width grip and version
function initGrip() {
  const grip = $("leftGrip"), root = document.documentElement;
  const setW = (w) => { state.view.leftW = Math.max(180, Math.min(480, Math.round(w))); root.style.setProperty("--left-w", `${state.view.leftW}px`); };
  if (state.view.leftW) setW(state.view.leftW);
  grip.addEventListener("pointerdown", (e) => {
    const start = e.clientX, w0 = grip.parentElement.getBoundingClientRect().width;
    grip.setPointerCapture(e.pointerId);
    grip.classList.add("is-dragging");
    const move = (ev) => { setW(w0 + ev.clientX - start); resize(); };
    const up = () => { grip.classList.remove("is-dragging"); grip.removeEventListener("pointermove", move); savePrefs(); };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up, { once: true });
  });
  grip.addEventListener("keydown", (e) => {
    const k = { ArrowLeft: -16, ArrowRight: 16 }[e.key];
    if (k) { setW((state.view.leftW || 248) + k); resize(); savePrefs(); }
  });
}
async function showVersion() {
  const v = await rpc("version");
  $("version").replaceChildren(h("span", {}, "City Builder"), h("span", {}, v.version ? `v${v.version}` : ""));
}

// ------------------------------------------------------------------ map tools: point or draw, tell an AI
const note = { kind: "", points: [], chats: null };
function renderTools() {
  const tool = (id, label, ic, hint) => {
    const b = h("button", { type: "button", class: "rn-tool", "aria-pressed": state.tool === id ? "true" : "false", title: hint },
      icon(ic, 16), h("span", {}, label));
    b.addEventListener("click", () => setTool(id));
    return b;
  };
  const act = (label, ic, fn, hint) => {
    const b = h("button", { type: "button", class: "rn-tool", title: hint }, icon(ic, 16), h("span", {}, label));
    b.addEventListener("click", fn);
    return b;
  };
  $("tools").replaceChildren(
    tool("select", "Select", "pointer", "Click roads and junctions to edit them"),
    tool("pin", "Pin", "pin", "Click a spot, then tell an AI what to do there"),
    tool("area", "Area", "pen", "Draw round an area, then tell an AI what to do there"),
    h("span", { class: "rn-sep" }),
    act("Frame", "frame", () => frame(), "Show the whole world"),
    act("Clear", "x", clearNote, "Remove the pin or area"));
}
function setTool(id) {
  state.tool = id;
  $("view").classList.toggle("is-drawing", id !== "select");
  renderTools();
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
  openNote();
}
function placePin(e) {
  const v = groundAt(e);
  if (!v) return;
  note.kind = "pin";
  note.points = [toUE(v)];
  drawMarks();
  openNote();
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
  $("note").hidden = true;
}
async function openNote(mode) {
  if (!state.net) { clearNote(); return showError("Open a world first."); }
  if (mode === "build") { note.kind = "build"; note.points = []; drawMarks(); }
  const box = $("note");
  const where = note.kind === "build" ? `Build ${currentNet()?.name || "this world"} in UEFN`
    : note.kind === "pin"
    ? `A spot at x ${note.points[0][0]}, y ${note.points[0][1]}`
    : `An area of about ${Math.round(Math.abs(note.points.reduce((s, p, i) => {
        const q = note.points[(i + 1) % note.points.length]; return s + p[0] * q[1] - q[0] * p[1]; }, 0)) / 2 / 1e4).toLocaleString()} m²`;
  const text = h("textarea", { placeholder: note.kind === "pin" ? "What should go here? A school, a park, a bus stop…"
    : note.kind === "build" ? "Anything to add for the AI (optional)"
    : "What should happen in this area? More houses, a shopping street, a dense forest…", "aria-label": "Note for the AI" });
  // the box opens at once: the chat list fills in when it arrives (it can take a moment), never blocking
  const opts = () => [{ value: "new", label: "New chat" }, ...(note.chats || []).map((c) => ({ value: c.id, label: c.title }))];
  let target = note.target || "new";
  const drop = window.DuckyKit?.dropdown
    ? window.DuckyKit.dropdown({ label: "Send to", options: opts(), value: target, onChange: (v) => { target = v; note.target = v; } })
    : null;
  const pickChat = drop ? drop.el : h("span", { class: "dk-muted" }, "Sends to a new chat");
  if (!note.chats) loadChats().then(() => { drop?.setOptions?.(opts()); drop?.set?.(target); });
  const send = button(note.kind === "build" ? "Ask AI to build" : "Send to AI", "send", async () => {
    if (!text.value.trim() && note.kind !== "build") return text.focus();
    busy("Sending to the AI…");
    const out = await rpc("send_note", { net_id: state.net, chat_id: target, text: text.value, kind: note.kind,
      points: JSON.stringify(note.points.map((p) => [p[0], p[1]])) });
    busy("");
    if (out.ok === false) return showError(out.error);
    showError("");
    const wasBuild = note.kind === "build";
    note.target = out.chat;
    note.chats = null;  // a new chat shows up next time
    loadChats();
    clearNote();
    alert("Sent", wasBuild
      ? "The AI has the build job: roads as landscape splines, the city as PCG tiles, only what changed."
      : "The AI has the spot, what is there now, and your note. Its changes show up here when it saves layers.");
  }, "primary");
  const cancel = button("Cancel", "x", clearNote, "ghost");
  box.replaceChildren(h("div", { class: "rn-note__where" }, where), text, h("div", { class: "rn-note__row" }, pickChat, cancel, send));
  box.hidden = false;
  text.focus();
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("note").hidden) clearNote();
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
  initGrip();
  showVersion();
  setTimeout(loadChats, 1500);  // the Send to list is ready before anyone draws
  buildPanes();
  openTab(state.tab);
  resize();
  placeCamera();
  loop();
  call("plugin.subscribe", { types: ["road_network_changed"] }).catch(() => {});
  liveReady = true;
  // a page that opens while the group is live catches up on cameras and the shared selection
  call("live.memberStates").then((r) => {
    const rows = Array.isArray(r) ? r : r?.states || r?.members || [];
    rows.forEach((m) => onMemberState(m.from, m.state));
  }).catch(() => {});
  call("live.shared").then((r) => {
    const v = r?.selection ?? (Array.isArray(r?.items) ? r.items.find((x) => x.key === "selection") : null);
    if (v) onSharedSelection(v.value ?? v, v.by);
  }).catch(() => {});
  await loadNets(false);
  const first = state.net || state.nets[0]?.id;
  first ? openNet(first) : buildScene();
})();
