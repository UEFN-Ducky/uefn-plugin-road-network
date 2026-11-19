import * as THREE from "../vendor/three@0.160.0/build/three.module.min.js";
import { iconHTML } from "../controls.js";
import { $, ASPHALT_HALF, MAT, camera, col, esc, family, groups, rt, scene, state, sun, toV, tone, view } from "../core.js";
import { drawPeers, drawRemoteSel, holderOf } from "../live.js";
import { lotsIn, pickedBlock } from "../panes/cities.js";
import { renderHud } from "../panes/uefn.js";
import { currentNet } from "../panes/world.js";
import { fadeIn, frame, orbit } from "./camera.js";
import { cityOfActor, hideLifted } from "./edit.js";
import { drawExact, drawPcg } from "./pcg.js";
import { drawJoins, drawPlan, drawProfile, hermite, joinSkips, junctionTris, ribbon, roadMaterial } from "./roads.js";
import { sampleZ, terrainKey, terrainMesh } from "./terrain.js";

function clear(group) {
  while (group.children.length) {
    const c = group.children.pop();
    c.geometry?.dispose?.();
    c.material?.dispose?.();  // the shared detail texture stays: materials never dispose their maps
  }
}

function buildScene() {
  Object.values(groups).forEach(clear);
  const g = state.geo;
  $("emptyView").innerHTML = g ? "" : `<div class="dk-empty"><div class="dk-empty__title">No world open</div>Generate one in the World tab, or pull roads from UEFN.</div>`;
  if (!g) { $("hud").innerHTML = ""; rt.needsDraw = true; return; }
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
    // every junction's disc in two InstancedMeshes per road actor (shown / click-only), never a mesh each:
    // a mesh per junction was thousands of draw calls, and the click-only ones were drawn at opacity 0 too
    const discs = { shown: [], hidden: [] }, disc = new THREE.Object3D();
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
      disc.rotation.set(-Math.PI / 2, 0, 0);
      disc.position.copy(toV(p[1], p[2], p[3] + 70));
      disc.scale.setScalar(r);
      disc.updateMatrix();
      discs[drawn ? "hidden" : "shown"].push([disc.matrix.clone(), col(giant ? "red" : "fg-dim"), p]);
      legend["Junction"] = "fg-dim";
    }
    for (const [kind, list] of Object.entries(discs)) {
      if (!list.length) continue;
      const mat = kind === "shown" ? new THREE.MeshStandardMaterial({ roughness: 0.8, side: THREE.DoubleSide })
        : new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
      const mesh = new THREE.InstancedMesh(new THREE.CircleGeometry(1, 24), mat, list.length);
      list.forEach(([m, c], i) => { mesh.setMatrixAt(i, m); if (kind === "shown") mesh.setColorAt(i, c); });
      mesh.computeBoundingSphere();
      mesh.visible = kind === "shown";  // the click-only discs are never drawn; the ray still finds them
      mesh.receiveShadow = true;
      mesh.userData = { actor: aslug, points: list.map((row) => row[2]), shared: true };
      groups.pieces.add(mesh);
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
  // each colour's triangles in 1 km tiles (one material shared): a world-wide mesh is never culled
  for (const [key, arr] of Object.entries(byColor)) {
    const [mat, city = ""] = key.split("#"), [c, dim] = mat.split("|");
    const material = new THREE.MeshStandardMaterial({ color: dim ? col(c).multiplyScalar(0.45) : col(c), roughness: 0.75,
      side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2 });
    const tiles = new Map();
    for (let k = 0; k < arr.length; k += 9) {
      const t = `${Math.floor(arr[k] / 1000)}_${Math.floor(arr[k + 2] / 1000)}`;
      const out = tiles.get(t) || tiles.set(t, []).get(t);
      for (let q = 0; q < 9; q++) out.push(arr[k + q]);
    }
    for (const part of tiles.values()) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(part, 3));
      geo.computeVertexNormals();
      geo.computeBoundingSphere();
      const mesh = new THREE.Mesh(geo, material);
      mesh.receiveShadow = true;
      mesh.userData.city = city;
      groups.roads.add(mesh);
    }
  }
  if (g.props?.length) {  // every prop in ONE InstancedMesh
    const hgt = 20, d = new THREE.Object3D();
    const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(2.6, hgt, 2.6), new THREE.MeshStandardMaterial({ color: col("muted"), roughness: 0.9 }), g.props.length);
    g.props.forEach((pr, i) => { d.position.copy(toV(pr.x, pr.y, pr.z - hgt * 50)); d.updateMatrix(); mesh.setMatrixAt(i, d.matrix); });
    mesh.computeBoundingSphere();
    mesh.castShadow = true;
    groups.props.add(mesh);
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
        roughness: 0.12, metalness: 0.1, transparent: true, opacity: 0.84,
        polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }));  // wins over seabed at the same height (no flicker stripes)
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
  if (rt.liveReady) { drawPeers(); drawRemoteSel(); }
  if (!state.framed && !box.isEmpty()) { frame(box); state.framed = true; }
  state.box = box;
  drawCityMark();
  if (state.fadeNext) { state.fadeNext = false; fadeIn([groups.roads, groups.pieces, groups.pcg]); }
  hideLifted();
  rt.needsDraw = true;
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
  rt.needsDraw = true;
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
// city name tags that follow the camera
function placeLabels() {
  const host = $("labels");
  if (!state.view.districts || !state.geo) { host.innerHTML = ""; return; }
  if (host.children.length !== state.cities.length) {
    // (a city a teammate has: their name on its tag)
    host.innerHTML = state.cities.map((c) => {
      const hd = rt.liveReady ? holderOf(c.id) : null;
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


export { clear, buildScene, cityMark, cityGhost, lakeMark, lake, lakesOf, lakeWorld, lakeShore, drawLakes, drawCityMark, cityOutline, DISTRICT_COLOR, cityCore, coreReach, ringPoint, drawDistricts, placeLabels };
