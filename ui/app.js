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
};
const OPTION_KEYS = ["hill_km", "roughness", "detail", "ridges", "peaks", "flatten_m", "sea_m", "water_depth_m", "terrace_m", "block_m", "wobble",
  "prune", "downtown", "normal", "highway", "highway_curve", "core_roads", "neighbourhood_roads", "suburb_roads", "one_way_core",
  "interchange", "overpasses"];
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
];
function family(mesh) {
  for (const f of FAMILY) if (f[0].test(mesh)) return f;
  return [null, 500, "fg-dim", "Other"];
}
const savePrefs = () => call("prefs.set", { id: "ui", value: { gen: state.gen, view: state.view, tab: state.tab, tune: state.tune, live: state.live } }).catch(() => {});

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
function col(name) { const c = new THREE.Color(); c.setStyle(cssVar(name) || cssVar("fg") || "transparent"); return c; }

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
});
renderer.domElement.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
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
  if (drag && !drag.moved && drag.button === 0) pick(e);
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
  if (!g) { $("legend").innerHTML = ""; $("hud").innerHTML = ""; needsDraw = true; return; }
  const byColor = {}, legend = {}, box = new THREE.Box3();
  for (const [aslug, actor] of Object.entries(g.actors || {})) {
    const pts = new Map(actor.pts.map((p) => [p[0], p]));
    const dim = state.actor && state.actor !== aslug;
    for (const s of actor.segs) {
      const a = pts.get(s[1]), b = pts.get(s[2]);
      if (!a || !b || !s[5]) continue; // mesh-less links only join the graph
      const f = family(s[5]);
      const key = f[2] + (dim ? ":dim" : "");
      (byColor[key] ||= []).push(...ribbon(hermite(a, b, s[3], s[4], 6), f[1], f[2] === "purple" || f[2] === "yellow" ? 0 : 60));
      legend[f[3]] = f[2];
    }
    for (const p of actor.pts) {
      if (!p[7] || p[7] === "SM_Junc_Empty") continue;
      // a node wider than 80 m is broken data: drawn red at a capped size so it never hides the map
      const giant = (p[6] || 0) > 8000;
      const r = giant ? 40 : Math.max(6, (p[6] || 600) / 100);
      if (giant) legend["Broken node (run Check and repair)"] = "red";
      const disc = new THREE.Mesh(new THREE.CircleGeometry(r, 24), new THREE.MeshStandardMaterial({ color: col(giant ? "red" : "fg-dim"), roughness: 0.8, side: THREE.DoubleSide }));
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
    const [c, dim] = key.split(":");
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
  $("legend").innerHTML = Object.entries(legend).map(([k, c]) => `<span><span class="rn-sw" style="background: var(--${c})"></span>${esc(k)}</span>`).join("");
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
const PCG_COLORS = ["green", "amber", "yellow", "blue", "purple", "red", "accent"];
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
function drawPcg(legend) {
  const layers = state.pcg || {};
  const dummy = new THREE.Object3D();
  Object.entries(layers).forEach(([name, layer], k) => {
    const pts = layer.points || [];
    if (!pts.length) return;
    const colour = PCG_COLORS[k % PCG_COLORS.length];
    const building = layer.kind === "buildings";
    const [geo, lift] = building ? [new THREE.BoxGeometry(1, 1, 1), 0] : pcgShape(name, layer.kind);
    const mesh = new THREE.InstancedMesh(geo, new THREE.MeshStandardMaterial({ color: col(colour), roughness: building ? 0.6 : 0.85,
      metalness: building ? 0.15 : 0 }), pts.length);
    const zx = state.view.zx || 1;
    pts.forEach(([x, y, z, yaw, s, hgt], i) => {
      dummy.position.copy(toV(x, y, z));
      dummy.rotation.set(0, -yaw * Math.PI / 180, 0);
      if (building) {  // its real lot and height: depth faces the street, frontage runs along it
        const [w, d] = layer.size_m || [12, 12];
        dummy.scale.set(d * 0.9, hgt * zx, w * 0.9);
        dummy.position.y += hgt * zx / 2;
      } else {
        dummy.scale.setScalar(s);
        dummy.position.y += lift * s;
      }
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    });
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    groups.pcg.add(mesh);
    if (building && layer.driveway) {  // a pad from each lot's front to the kerb
      const [, d] = layer.size_m || [12, 12];
      const reach = (layer.setback_m || 2) + 0.5;
      const pads = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ color: col("fg-dim"), roughness: 0.95 }), pts.length);
      pts.forEach(([x, y, z, yaw], i) => {
        const r = yaw * Math.PI / 180, dist = (d / 2 + reach / 2) * 100;
        dummy.position.copy(toV(x + Math.cos(r) * dist, y + Math.sin(r) * dist, z + 15));
        dummy.rotation.set(0, -r, 0);
        dummy.scale.set(reach, 0.2, 3.5);
        dummy.updateMatrix();
        pads.setMatrixAt(i, dummy.matrix);
      });
      pads.receiveShadow = true;
      groups.pcg.add(pads);
    }
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
  busy("Previewing PCG layers…");
  for (const l of state.layers.filter((x) => x.enabled)) {  // one layer per reply keeps each one small
    const r = await rpc("pcg_preview", { net_id: net, layer_id: l.id });
    if (r.ok === false) { busy(""); return showError(r.error); }
    Object.assign(out, r.layers || {});
  }
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

// ------------------------------------------------------------------ tabs
const TABS = [["world", "World", "world"], ["terrain", "Terrain", "terrain"], ["roads", "Roads", "road"],
  ["cities", "Cities", "city"], ["pcg", "PCG", "layers"], ["edit", "Edit", "edit"], ["uefn", "UEFN", "upload"]];
const GEN_TABS = new Set(["world", "terrain", "roads"]);
function renderTabs() {
  $("tabs").innerHTML = "";
  for (const [id, label, ic] of TABS) {
    const b = h("button", { type: "button", class: "dk-tab", role: "tab", id: `tab-${id}`, "aria-controls": `pane-${id}`,
      "aria-selected": id === state.tab ? "true" : "false", tabindex: id === state.tab ? "0" : "-1", "data-live-id": `tab:${id}` }, icon(ic, 14), h("span", {}, label));
    b.addEventListener("click", () => openTab(id));
    $("tabs").append(b);
  }
  $("tabs").onkeydown = (e) => {
    const k = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (!k) return;
    const i = TABS.findIndex((t) => t[0] === state.tab);
    openTab(TABS[(i + k + TABS.length) % TABS.length][0]);
    $(`tab-${state.tab}`).focus();
  };
}
function openTab(id) {
  state.tab = id;
  for (const [t] of TABS) {
    $(`pane-${t}`).classList.toggle("is-open", t === id);
    $(`tab-${t}`)?.setAttribute("aria-selected", t === id ? "true" : "false");
    $(`tab-${t}`)?.setAttribute("tabindex", t === id ? "0" : "-1");
  }
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
    section("World", ctl.name.el, sl("cities", "Cities", 1, 12, 1, (v) => String(v)), sl("size_km", "Map size", 1, 20, 0.5, (v) => `${v} km`), ctl.seed.el),
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
    seg("interchange", "Where a highway meets a city", [["direct", "Direct", "route"], ["trumpet", "Trumpet", "refresh"],
      ["cloverleaf", "Cloverleaf", "mixed"], ["stack", "Stack", "layers"]]),
    seg("overpasses", "Where a highway crosses a street", [["yes", "Bridge over", "top"], ["no", "Cross at grade", "route"]]),
    h("div", { class: "rn-hint" }, "Trumpet uses the big loop piece, Stack rises onto the elevated deck and ramps, Cloverleaf needs " +
      "SM_Road_Freeway_Cloverleaf imported from the NeonSkyscrapers road kit first."));
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
  const place = button("Place in UEFN with AI", "upload", onPcgPlace, "primary",
    "Starts a chat that builds one PCG graph with every layer in the open UEFN level");
  place.style.width = "100%";
  pane.replaceChildren(
    section("What it's built from", pieces, h("div", { class: "rn-hint", style: "margin-top: 6px" },
      "The AI that builds it picks real pieces for every layer from this set (layers with meshes of their own keep them).")),
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
  if (!state.net) { state.layers = []; renderPcgPane(); return; }
  const [out, assets] = await Promise.all([rpc("pcg_layers", { net_id: state.net }), rpc("pcg_assets", { net_id: state.net })]);
  state.layers = out.ok === false ? [] : out.items || [];
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
  const ask = await rpc("pcg_place", { net_id: state.net });
  if (ask.ok === false && !/confirm=true/.test(ask.error || "")) return showError(ask.error);
  const ok = await call("ui.confirm", { title: "Place PCG layers in UEFN", confirm: "Start",
    message: (ask.error || "").replace(" Call again with confirm=true.", "") + " An AI chat builds the graph in the open UEFN level; it picks meshes for layers that have none." }).catch(() => null);
  if (!ok?.confirmed) return;
  busy("Starting the PCG build chat…");
  const out = await rpc("pcg_place", { net_id: state.net, confirm: true });
  busy("");
  if (out.ok === false) return showError(out.error);
  showError("");
  alert("PCG build started", `A chat is building ${out.layers.length} layers (${out.nodes} nodes). Follow it in the chat list.`);
}

function syncControls() {
  for (const [k, c] of Object.entries(ctl)) if (k in state.gen && c?.set) c.set(state.gen[k]);
  ctl.look?.set(state.view.look);
  ctl.shadows?.set(state.view.shadows);
  ctl.live?.set(state.live);
}

// ------------------------------------------------------------------ cities pane
const STYLE_OPTS = [["mixed", "Mixed", "mixed"], ["grid", "Grid", "grid"], ["organic", "Organic", "wave"]];
function renderCitiesPane() {
  const pane = $("pane-cities");
  if (!state.cities.length) {
    pane.replaceChildren(h("div", { class: "dk-empty" }, h("div", { class: "dk-empty__title" }, "No cities yet"), "Generate a world to get cities with their own roads and districts."));
    return;
  }
  const cards = state.cities.map((c) => {
    const style = segmented({ label: "Road style", value: c.style, options: STYLE_OPTS, onChange: (v) => saveCity(c, { style: v }) });
    const radius = slider({ label: "Radius", min: 150, max: 2000, step: 10, value: Math.round(c.radius / 100), fmt: m,
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
        ...sets.flatMap(([k, n]) => [h("span", {}, `${k[0].toUpperCase() + k.slice(1)} buildings`), h("span", {}, n ? `${n} meshes` : "Not set")])));
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
  const ok = await call("ui.confirm", { title: "Import into UEFN", danger: true, confirm: "Import", message: `${ask.error} Don't touch UEFN until it finishes.` }).catch(() => null);
  if (!ok?.confirmed) return;
  busy("Building junction pieces and pasting roads into UEFN…");
  const out = await rpc("import_all", { net_id: state.net, confirm: true });
  busy("");
  if (out.ok === false) return showError(out.error);
  showError("");
  alert("Imported", Object.entries(out.done).map(([k, v]) => `${k}: ${v.pieces} pieces`).join("\n"));
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
  if (!autoOpen) return;
  if (!state.net && state.nets[0]) openNet(state.nets[0].id);
  else if (!state.net) buildScene();
}
async function openNet(id) {
  state.net = id; state.framed = false; state.sel = null; state.actor = ""; state.pcg = {};
  call("prefs.set", { id: "last_net", value: id }).catch(() => {});
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
  if (g.ground === "net" && (!state.net || currentNet()?.generated)) {
    openTab("world");
    return showError("Pick a network pulled from UEFN on the left: its ground is what the cities sit on.");
  }
  const prev = state.nets.find((n) => n.name.toLowerCase() === name.toLowerCase())?.generated;
  // same world with the same city layout: keep its cities (their styles and sizes) instead of placing new ones
  const keep = prev && prev.cities === g.cities && prev.seed === g.seed && prev.size_km === g.size_km;
  busy("Generating terrain, cities and roads…");
  const out = await rpc("generate", { name, seed: g.seed, cities: keep ? "" : String(g.cities), size_km: g.size_km, relief_m: g.relief_m,
    ground_from: g.ground === "net" ? state.net : "", options: JSON.stringify(optionsOf(g)) });
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
  renderTabs();
  renderToolbar();
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
