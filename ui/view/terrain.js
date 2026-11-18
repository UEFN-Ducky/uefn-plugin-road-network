import * as THREE from "../vendor/three@0.160.0/build/three.module.min.js";
import { marchHeightfield } from "../ground.js";
import { camera, col, hemi, renderer, scene, state, toV, tone } from "../core.js";
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

export { themeColors, detailTex, smooth, terrainMesh, terrainKey, sampleZ, groundMarch, groundAt };
