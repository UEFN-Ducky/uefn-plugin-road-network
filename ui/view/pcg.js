import * as THREE from "../vendor/three@0.160.0/build/three.module.min.js";
import { MAT, busy, col, groups, loading, rpc, rt, showError, state, toV, tone, vary } from "../core.js";
import { live, peers } from "../live.js";
import { cachedRpc, loadSync } from "../panes/world.js";
import { orbit } from "./camera.js";
import { layerInfo, pieceInfo, revealLayers, settleCity } from "./edit.js";
import { buildScene } from "./scene.js";
import { terrainKey } from "./terrain.js";

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

// A layer's instances in one InstancedMesh per 1 km tile instead of one for the whole world: three.js culls a
// whole object or nothing, so a world-wide mesh was drawn in full (every instance) wherever the camera looked.
// items: [matrix, colour or null, x, y (UE cm)]. info(i) gets the index into items. far: metres past which a
// tile of small things is not drawn at all (loop), 0 = always.
const CHUNK_CM = 100000;
function addChunks(geo, mat, items, { info = null, cast = true, receive = true, far = 0 } = {}) {
  const tiles = new Map();
  items.forEach((it, i) => {
    const k = `${Math.floor(it[2] / CHUNK_CM)}_${Math.floor(it[3] / CHUNK_CM)}`;
    (tiles.get(k) || tiles.set(k, []).get(k)).push(i);
  });
  for (const idx of tiles.values()) {
    const mesh = new THREE.InstancedMesh(geo, mat, idx.length);
    const xy = new Float32Array(idx.length * 2);
    idx.forEach((n, i) => {
      const [m, c, x, y] = items[n];
      mesh.setMatrixAt(i, m);
      if (c) mesh.setColorAt(i, c);
      xy[2 * i] = x; xy[2 * i + 1] = y;
    });
    mesh.computeBoundingSphere();
    mesh.userData.xy = xy;
    if (info) mesh.userData.info = (i) => info(idx[i]);
    if (far) mesh.userData.far = far;
    mesh.castShadow = cast;
    mesh.receiveShadow = receive;
    groups.pcg.add(mesh);
  }
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
  for (const [list, geo] of [[boxes, new THREE.BoxGeometry(1, 1, 1)], [cyls, new THREE.CylinderGeometry(0.5, 0.5, 1, 12)]]) {
    if (!list.length) continue;
    addChunks(geo, mat, list.map(([mtx, tint, p]) => [mtx, tint, p[0], p[1]]), { info: (i) => {
      const p = list[i][2];
      return layerInfo(name, layer, p, [["Form", forms[p[6]] || "box"], ["Lot", `${p[7] || "?"} m wide × ${p[8] || "?"} m deep, ${p[5] || "?"} m tall`],
        ["Built as", layer.build === "modular" ? "kit modules stacked by the grammar" : "one kit building stretched to the lot"],
        ["Its real pieces", "press Real pieces here on the Layers tab"]]);
    } });
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
    addChunks(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ color: colour, roughness: 0.95 }),
      list.map((m, i) => [m, null, at[i][0], at[i][1]]), { cast: false });
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
    const shown = !rt.hiddenLayers.has(entry[0]);
    for (let i = n0; i < groups.pcg.children.length; i++) Object.assign(groups.pcg.children[i], { visible: shown }).userData.layer = entry[0];
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
      const zx = state.view.zx || 1, items = [];
      pts.forEach(([x, y, z, yaw, sx, sy, sz]) => {
        if (layer.mesh === STAY) {
          stayBar(dummy, x, y, z + 60, yaw, sx, sz);
          dummy.updateMatrix();
          return items.push([dummy.matrix.clone(), null, x, y]);
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
        items.push([dummy.matrix.clone(), null, x, y]);
      });
      addChunks(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ color: col(tint), roughness: 0.9 }), items,
        { info: (i) => layerInfo(name, layer, pts[i]), cast: false });
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
      const items = pts.map(([x, y, z, yaw, s]) => {
        dummy.position.copy(toV(x, y, z));
        dummy.rotation.set(0, -yaw * Math.PI / 180, 0);
        dummy.scale.setScalar(s);
        dummy.position.y += lift * s;
        dummy.updateMatrix();
        return [dummy.matrix.clone(), leafy ? vary(base, x, y, 1.4) : base, x, y];
      });
      // small things (bushes, benches, signs, lamps) are not drawn past 2.5 km, trees past 6 km: specks there
      const small = layer.role === "bush" || /traffic|stop|sign|hydrant|bench|bin|trash|lamp|light|pole|post|grass|bush|shrub|flower/i.test(name);
      addChunks(geo, new THREE.MeshStandardMaterial({ roughness: 0.85 }), items,
        { info: (i) => layerInfo(name, layer, pts[i]), cast: !small, far: small ? 2500 : leafy ? 6000 : 0 });
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
let pcgLoading = "", pcgAgain = false;  // the world whose city is being placed
async function loadPcgPreview() {
  const net = state.net;
  // the same world: once more after this run. Another world: start now; the old run is stopped in the backend
  // (it sees a newer world asked for) and its replies are dropped here
  if (pcgLoading && pcgLoading === net) { pcgAgain = true; return; }
  pcgLoading = net; pcgAgain = false;
  try { await loadPcgPreviewNow(); } finally { if (pcgLoading === net) pcgLoading = ""; }
  if (pcgAgain && !pcgLoading && net === state.net) { pcgAgain = false; return loadPcgPreview(); }
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
    // again: the later calls read the placement the first one checked, without re-reading the whole world
    const r = await cachedRpc("pcg_preview", { net_id: net, layer_id: ls.map((l) => l.id).join(","), ...(i ? { again: 1 } : {}) });
    if (net !== state.net) return;  // (openNet took the card down; the new world's run owns it now)
    if (r.ok === false && ls[0].id !== "road-walls") { card(""); settleCity(); state.lifted = null; buildScene(); return showError(r.error, () => loadPcgPreview()); }
    if (r.ok !== false) {
      Object.assign(picked, r.layers || {});
      await revealLayers(r.layers || {}, i === 0);  // a few layers at a time, each rising out of the ground
      if (net !== state.net) return;
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

export { ROLE_COLOR, CHUNK_CM, addChunks, PCG_COLORS, pcgShape, FORM_STOREY, formParts, drawBuildings, FOUND, drawLotGround, drawPcg, inExact, STAY, UP, stayBar, EXACT_TINT, loadExact, drawExact, pcgPreviewTimer, schedulePcgPreview, PCG_BATCH, pcgLoading, pcgAgain, loadPcgPreview, loadPcgPreviewNow, renderPcgCounts };
