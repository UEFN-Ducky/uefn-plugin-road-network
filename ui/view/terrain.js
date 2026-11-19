import * as THREE from "../vendor/three@0.160.0/build/three.module.min.js";
import { marchHeightfield } from "../ground.js";
import { camera, col, hemi, renderer, rpc, rt, scene, state, toV, tone } from "../core.js";
import { placeCamera } from "./camera.js";
import { ray } from "./edit.js";

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

// backend/terrain/climate.py BIOMES in id order (t.maps.biome holds the id), each as a theme colour
const BIOME_COLORS = [
  ["ocean", "mix:blue:bg:0.35"], ["beach", "mix:amber:fg:0.55"], ["wetland", "mix:blue:green:0.45"],
  ["grassland", "mix:green:amber:0.25"], ["meadow", "mix:green:yellow:0.3"], ["temperate_forest", "mix:green:bg:0.35"],
  ["conifer_forest", "mix:green:bg:0.55"], ["rainforest", "mix:green:blue:0.2"], ["savanna", "mix:yellow:amber:0.4"],
  ["desert", "mix:amber:yellow:0.35"], ["shrubland", "mix:amber:green:0.4"], ["rocky_slope", "mix:fg-dim:amber:0.15"],
  ["alpine_meadow", "mix:green:fg-dim:0.45"], ["tundra", "mix:fg-dim:blue:0.25"], ["snow", "fg"], ["cliff", "mix:fg-dim:bg:0.35"],
];
// the engine's maps the Debug looks show (backend debug_map / rpc terrain_map takes the name)
const DEBUG_VIEWS = [["height", "Height"], ["land", "Land"], ["coast_dist", "Coast distance"], ["plains", "Plains"],
  ["hills", "Hills"], ["foothills", "Foothills"], ["mountains", "Mountains"], ["plateaus", "Plateaus"], ["valleys", "Valleys"],
  ["wetlands", "Wetlands"], ["ridge", "Ridges"], ["passes", "Passes"], ["flow", "Flow direction"], ["accum", "Flow accumulation"],
  ["river", "Rivers"], ["lake", "Lakes"], ["temp", "Temperature"], ["moist", "Moisture"], ["biome", "Biome"], ["slope", "Slope"],
  ["build", "Buildability"], ["road_cost", "Road cost"], ["flood", "Flood risk"]];
// a map of the engine, flat (j * n + i) or rows like heights; null when the world has none
const mapAt = (m, n, j, i) => !m ? null : Array.isArray(m[0]) ? (m[j]?.[i] ?? null) : (m[j * n + i] ?? null);

// the debug PNGs, by world and view: a rebuild of the scene never fetches one again
const debugTex = new Map();
function drapeDebug(mat, t, name) {
  const key = `${state.net}|${terrainKey(t)}|${name}`;
  const have = debugTex.get(key);
  if (have) { mat.map = have; mat.needsUpdate = true; return; }
  rpc("terrain_map", { net_id: state.net, name }).then((out) => {
    const b64 = out?.png ?? out?.image ?? out?.data;
    if (!b64) return;
    new THREE.TextureLoader().load(String(b64).startsWith("data:") ? b64 : `data:image/png;base64,${b64}`, (tex) => {
      tex.flipY = false;  // row 0 of the PNG is the grid's first row (j = 0), as the mesh's v runs
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.magFilter = THREE.NearestFilter;
      debugTex.set(key, tex);
      mat.map = tex;
      mat.needsUpdate = true;
      rt.needsDraw = true;
    });
  });
}

// river lines in --blue: the backend's polylines (cm) when it sends them, else every river cell joined to its
// lowest neighbour (downhill, the way the water runs)
function riverLines(t) {
  const pts = [], lift = 150;
  if (Array.isArray(t.rivers) && t.rivers.length) {
    for (const line of t.rivers) for (let k = 1; k < line.length; k++) {
      const a = line[k - 1], b = line[k];
      const za = a[2] ?? sampleZ(t, a[0], a[1]) ?? 0, zb = b[2] ?? sampleZ(t, b[0], b[1]) ?? 0;
      pts.push(toV(a[0], a[1], za + lift), toV(b[0], b[1], zb + lift));
    }
  } else if (t.maps?.river) {
    const n = t.grid, H = t.heights, cw = (t.max[0] - t.min[0]) / n, ch = (t.max[1] - t.min[1]) / n;
    const at = (j, i) => toV(t.min[0] + cw * (i + 0.5), t.min[1] + ch * (j + 0.5), (H[j][i] ?? 0) + lift);
    for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
      if (!(mapAt(t.maps.river, n, j, i) > 0) || H[j][i] == null) continue;
      let best = null, bz = H[j][i];
      for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) {
        const z = H[j + dj]?.[i + di];
        if ((dj || di) && z != null && z <= bz) { bz = z; best = [j + dj, i + di]; }
      }
      if (best) pts.push(at(j, i), at(best[0], best[1]));
    }
  }
  if (!pts.length) return null;
  return new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: col("blue") }));
}

const smooth = (e0, e1, x) => { const t = Math.max(0, Math.min(1, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };

function terrainMesh(t) {
  const n = t.grid, pos = [], uv = [], idx = [];
  const H = t.heights;
  let lo = Infinity, hi = -Infinity;
  for (const row of H) for (const v of row) if (v !== null && v !== undefined) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  const look = state.view.look || "natural", debug = look.startsWith("debug:");
  const reps = debug ? 1 : Math.max(8, n / 6);  // a debug PNG covers the world once; the detail texture repeats
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = t.min[0] + (t.max[0] - t.min[0]) * (i + 0.5) / n, y = t.min[1] + (t.max[1] - t.min[1]) * (j + 0.5) / n;
    const v = toV(x, y, H[j][i] ?? lo);
    pos.push(v.x, v.y, v.z);
    uv.push((i + 0.5) / n * reps, (j + 0.5) / n * reps);
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
  // Biome: the engine's biome map in flat theme colours; Natural on an engine world: the same, toned like the outdoors
  const biome = t.maps?.biome, river = t.maps?.river;
  const bFlat = BIOME_COLORS.map(([, c]) => col(c));
  const bNat = bFlat.map((c) => { const hsl = {}; c.getHSL(hsl, THREE.SRGBColorSpace);
    return c.clone().setHSL(hsl.h, hsl.s * 0.45, 0.22 + hsl.l * 0.5, THREE.SRGBColorSpace); });
  const water = tone("blue", 0.6, 0.45), deep = tone("blue", 0.45, 0.2);
  const colors = new Float32Array(nrm.count * 3);
  const c = new THREE.Color();
  // biome colours blurred over their neighbours (2 box passes): one colour per cell read as a blocky grid
  let bc = null;
  if (biome && !debug && (look === "natural" || look === "biome" || look === "rivers")) {
    const pal2 = look === "biome" ? bFlat : bNat;
    bc = new Float32Array(n * n * 3);
    for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
      const p = pal2[Math.round(mapAt(biome, n, j, i) ?? 3)] ?? pal2[3], k = (j * n + i) * 3;
      bc[k] = p.r; bc[k + 1] = p.g; bc[k + 2] = p.b;
    }
    for (let pass = 0; pass < 2; pass++) {
      const src = bc.slice();
      for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
        let r = 0, g = 0, bl = 0, m = 0;
        for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) {
          const jj = j + dj, ii = i + di;
          if (jj < 0 || ii < 0 || jj >= n || ii >= n) continue;
          const q = (jj * n + ii) * 3;
          r += src[q]; g += src[q + 1]; bl += src[q + 2]; m++;
        }
        const k = (j * n + i) * 3;
        bc[k] = r / m; bc[k + 1] = g / m; bc[k + 2] = bl / m;
      }
    }
  }
  for (let k = 0; k < nrm.count; k++) {
    const j = Math.floor(k / n), i = k % n;
    const z = H[j][i] ?? lo;
    const tH = hi > lo ? (z - lo) / (hi - lo) : 0.3;
    const slope = 1 - nrm.getY(k);  // 0 flat .. 1 vertical
    const jitter = (Math.sin(i * 12.9898 + j * 78.233) * 43758.5453) % 1;
    const b = biome ? mapAt(biome, n, j, i) : null;
    if (debug) {
      c.setRGB(1, 1, 1);  // the PNG gives the colour
    } else if (look === "biome" && bc) {
      c.setRGB(bc[k * 3], bc[k * 3 + 1], bc[k * 3 + 2]);
    } else if (look === "height") {
      if (tH < 0.33) c.copy(pal.low).lerp(pal.mid, tH / 0.33);
      else if (tH < 0.66) c.copy(pal.mid).lerp(pal.high, (tH - 0.33) / 0.33);
      else c.copy(pal.high).lerp(pal.top, (tH - 0.66) / 0.34);
    } else if (state.view.look === "slope") {
      c.copy(pal.mid).lerp(pal.high, smooth(0.02, 0.12, slope)).lerp(pal.top, smooth(0.12, 0.3, slope));
    } else if (bc) {
      c.setRGB(bc[k * 3], bc[k * 3 + 1], bc[k * 3 + 2]);
      c.lerp(pal.rock, smooth(0.12, 0.3, slope) * 0.7);  // steep faces show bare rock whatever grows round them
      c.multiplyScalar(0.94 + 0.08 * Math.abs(jitter));
    } else {
      c.copy(pal.grassLo).lerp(pal.grassHi, smooth(0.2, 0.75, tH));
      c.lerp(pal.dirt, smooth(0.04, 0.1, slope) * 0.6);
      c.lerp(pal.rock, smooth(0.1, 0.22, slope));
      c.lerp(pal.snow, smooth(snowFrom, snowFrom + 15000, z) * (1 - smooth(0.15, 0.3, slope)));
      if (sea !== null) c.lerp(pal.sand, 1 - smooth(sea + 150, sea + 600, z));
      c.multiplyScalar(0.94 + 0.08 * Math.abs(jitter));
    }
    // under the sea: sand at the shore going dark with depth (the sea plane over it no longer shows land colours)
    if (sea !== null && z < sea && !debug && look !== "height" && look !== "slope") c.copy(pal.sand).lerp(deep, smooth(0, 3000, sea - z));
    if (look === "rivers" && !debug && mapAt(river, n, j, i) > 0) c.lerp(water, 0.85);
    colors[k * 3] = c.r; colors[k * 3 + 1] = c.g; colors[k * 3 + 2] = c.b;
  }
  geo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  const mat = new THREE.MeshStandardMaterial({ vertexColors: true, map: look === "natural" ? detailTex : null,
    roughness: 0.95, metalness: 0, side: THREE.DoubleSide });
  if (debug) drapeDebug(mat, t, look.slice(6));
  const mesh = new THREE.Mesh(geo, mat);
  if (["natural", "biome", "rivers"].includes(look)) { const r = riverLines(t); if (r) mesh.add(r); }
  mesh.receiveShadow = true;
  mesh.castShadow = true;
  return { mesh, lo, hi };
}

// the backend sends the ground packed (citygen panel_maps): heights int32 cm, biome and river bytes, base64.
// Back into the shape the view reads: heights[j][i] (null = no ground) and flat maps
const HOLE = -2147483648;
function unpackTerrain(t) {
  if (!t?.packed) return t;
  const bytes = (s) => Uint8Array.from(atob(s), (ch) => ch.charCodeAt(0));
  const n = t.grid, h = new Int32Array(bytes(t.packed.h).buffer), heights = [];
  for (let j = 0; j < n; j++) {
    const row = new Array(n);
    for (let i = 0; i < n; i++) { const v = h[j * n + i]; row[i] = v === HOLE ? null : v; }
    heights.push(row);
  }
  const maps = {};
  for (const k of ["biome", "river"]) if (t.packed[k]) maps[k] = Array.from(bytes(t.packed[k]));
  const { packed, ...rest } = t;
  return { ...rest, heights, ...(Object.keys(maps).length ? { maps } : {}) };
}
const terrainKey = (t) => t?.heights?.length ? `${t.grid}|${t.min}|${t.max}|${t.heights[0][0]}|${t.heights[t.grid >> 1]?.[t.grid >> 1]}|${t.heights[t.grid - 1]?.[t.grid - 1]}` : "";
function sampleZ(t, x, y) {
  const n = t.grid;
  if (!n || !t.heights?.length) return null;
  const i = Math.max(0, Math.min(n - 1, Math.floor((x - t.min[0]) / (t.max[0] - t.min[0]) * n)));
  const j = Math.max(0, Math.min(n - 1, Math.floor((y - t.min[1]) / (t.max[1] - t.min[1]) * n)));
  return t.heights[j]?.[i] ?? null;
}
function groundMarch(r) {  // the terrain under a view ray (ground.js), never a raycast of the terrain mesh
  const t = state.geo?.terrain;
  if (!state.view.terrain) return null;
  const o = r.origin, d = r.direction;
  const s = marchHeightfield([o.x, o.y, o.z], [d.x, d.y, d.z], t, state.view.zx || 1);
  return s === null ? null : r.at(s, new THREE.Vector3());
}
function groundAt(e) {
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const hit = groundMarch(ray.ray);
  if (hit) return hit;
  const out = new THREE.Vector3();
  return ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), out) ? out : null;
}

export { BIOME_COLORS, DEBUG_VIEWS, themeColors, detailTex, smooth, terrainMesh, terrainKey, unpackTerrain, sampleZ, groundMarch, groundAt };
