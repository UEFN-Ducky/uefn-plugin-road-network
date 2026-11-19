import * as THREE from "../vendor/three@0.160.0/build/three.module.min.js";
import { h, icon, slider } from "../controls.js";
import { $, busy, call, camera, col, groups, marks, renderer, rpc, rt, showError, state, toUE, toV } from "../core.js";
import { heldElsewhere, live, shareLayer, shareSelection, spot, tellGroup } from "../live.js";
import { openChatDock } from "../panes/chat.js";
import { insidePoly, queueEdit, renderCityList, renderWorldCity, saveCity, selectCity } from "../panes/cities.js";
import { onPcgPlace } from "../panes/pcg.js";
import { openTab } from "../panes/tabs.js";
import { note, renderTools, setTool } from "../panes/tools.js";
import { onReimport, renderSelection } from "../panes/uefn.js";
import { currentNet, loadGeometry } from "../panes/world.js";
import { orbit } from "./camera.js";
import { drawPcg, schedulePcgPreview } from "./pcg.js";
import { buildScene, cityCore, cityGhost, cityMark, cityOutline, clear, drawCityMark, drawDistricts, drawLakes, lake, lakeMark, lakesOf, ringPoint } from "./scene.js";
import { groundAt, sampleZ } from "./terrain.js";

// ------------------------------------------------------------------ picking + editing
const ray = new THREE.Raycaster();
function pick(e) {
  const r = renderer.domElement.getBoundingClientRect();
  const m = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(m, camera);
  ray.params.Points.threshold = orbit.dist / 400;
  if (moving) { moveHere(e); return true; }
  const hits = ray.intersectObjects([...groups.points.children, ...groups.pieces.children], false);
  if (!hits.length) return inspectAt();
  const hit = hits[0], aslug = hit.object.userData.actor;
  if (hit.object.isPoints) select(aslug, hit.object.userData.pts[hit.index]);
  else select(aslug, hit.object.userData.points?.[hit.instanceId] ?? hit.object.userData.point);
  openTab("edit");
  const label = state.geo?.actors?.[aslug]?.label || aslug;
  showInspect({ title: label, road: aslug, rows: [["Road", label], ["In UEFN", "live splines: rebuilt on their own, no tile"]] });
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
  return { title: name, tile, at: { layer: name, x: p[0], y: p[1] }, rows: [["Layer", name], ["Kind", layer.role || layer.kind || "prop"],
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
  const btn = (label, fn, title = "") => h("button", { type: "button", class: "rn-mini", style: "pointer-events: auto; margin: 6px 6px 0 0", title, onclick: fn }, label);
  if (info) el.replaceChildren(h("b", {}, info.title), h("div", { class: "rn-kv" }, ...info.rows.flatMap(([k, v]) => [h("span", {}, k), h("span", {}, String(v))])),
    h("div", {}, ...(info.at && state.net ? [
      btn("Delete", async (ev) => { if (await popAt(ev.clientX, ev.clientY, `Delete ${info.title}? It is rebuilt in UEFN without it.`)) editPiece({ op: "delete", ...info.at }, true); },
        "Delete this piece for good: every rebuild keeps it gone"),
      btn("Move", () => { moving = info.at; showInspect({ title: `Moving ${info.title}`, rows: [["Next", "click the ground where it goes"]] }); },
        "Move this piece for good: click it, then click where it goes"),
      btn("Copy", () => { moving = { ...info.at, op: "copy" }; showInspect({ title: `Copying ${info.title}`, rows: [["Next", "click the ground where the copy goes"]] }); },
        "Put another one of these somewhere: click where it goes")] : []),
    ...(info.tile && state.net ? [btn(`Rebuild tile ${info.tile} in UEFN`, () => onPcgPlace(info.tile), "Build just this tile again in UEFN; every other tile is left alone")] : []),
    ...(info.road && state.net ? [btn("Rebuild this road in UEFN", () => { state.actor = info.road; onReimport(); }, "Paste just this road into UEFN again as live splines")] : [])));
}
// a little OK / Cancel box just above the pointer: resolves true on OK (or Enter), false on Cancel (or Escape)
function popAt(x, y, text) {
  document.getElementById("rnPop")?.remove();
  return new Promise((done) => {
    const close = (v) => { box.remove(); document.removeEventListener("keydown", key, true); done(v); };
    const key = (e) => { if (e.key === "Escape" || e.key === "Enter") { e.preventDefault(); close(e.key === "Enter"); } };
    const box = h("div", { id: "rnPop", class: "rn-help", role: "dialog", "aria-label": text,
      style: `position: fixed; left: ${x}px; top: ${Math.max(8, y - 84)}px; bottom: auto; right: auto; width: auto; max-width: 280px; transform: translateX(-50%); z-index: 60; pointer-events: auto` },
      h("div", { style: "margin-bottom: 6px" }, text),
      h("button", { type: "button", class: "rn-mini", style: "margin-right: 6px; font-weight: 600", onclick: () => close(true) }, "OK"),
      h("button", { type: "button", class: "rn-mini", onclick: () => close(false) }, "Cancel"));
    document.body.appendChild(box);
    document.addEventListener("keydown", key, true);
    box.querySelector("button").focus();
  });
}
// the placed piece (building, slab, prop) under the pointer, for a drag: its instance and what it is
function pcgUnder(e) {
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const hit = ray.intersectObjects(groups.pcg.children, false).find((x) => x.object.userData.info && x.instanceId != null);
  const info = hit && hit.object.userData.info(hit.instanceId);
  return info?.at ? { mesh: hit.object, id: hit.instanceId, info, g0: groundAt(e)?.y } : null;
}
function dragPiece(e) {  // it follows the pointer over the ground; nothing is saved until OK
  const d = rt.drag.piece, v = groundAt(e);
  if (!v) return;
  if (!d.orig) { d.orig = new THREE.Matrix4(); d.mesh.getMatrixAt(d.id, d.orig); }
  renderer.domElement.style.cursor = "grabbing";
  const m = d.orig.clone(), y = new THREE.Vector3().setFromMatrixPosition(m).y;
  m.setPosition(v.x, y + v.y - (d.g0 ?? v.y), v.z);
  d.mesh.setMatrixAt(d.id, m);
  d.mesh.instanceMatrix.needsUpdate = true;
  d.to = toUE(v);
}
async function dropPiece(d, e) {
  const ok = await popAt(e.clientX, e.clientY, `Move ${d.info.title} here? It is rebuilt in UEFN where you put it.`);
  if (!ok) { d.mesh.setMatrixAt(d.id, d.orig); d.mesh.instanceMatrix.needsUpdate = true; return; }
  editPiece({ op: "move", ...d.info.at, to_x: d.to[0], to_y: d.to[1] }, true);
}
// The person's own deletes and moves: kept with the world (road_network_edit), applied after every rule
let moving = null;  // the piece a Move button picked: the next click on the ground is where it goes
async function editPiece(params, push = false) {
  const r = await rpc("edit", { net_id: state.net, ...params });
  if (r.ok === false) return showError(r.error);
  schedulePcgPreview(100);
  if (push) {  // OK in the little box: straight into UEFN, only the tiles it touched
    const tiles = r.tiles.join(", ");
    busy(`Rebuilding ${tiles} in UEFN…`);
    const out = await rpc("pcg_build", { net_id: state.net, confirm: true, force: true, tiles });
    busy("");
    if (out.ok === false) return showError(out.error + (out.note ? ` ${out.note}` : ""));
    showError("");
  }
  showInspect({ title: `${{ delete: "Deleted", move: "Moved", copy: "Copied" }[r.edit.op]}: ${r.piece.layer}`, tile: r.tiles.join(", "),
    rows: [["Kept", `every rebuild keeps it (${r.edits} edits on this world)`], ["In UEFN", `push ${r.tiles.join(", ")}`]] });
}
async function moveHere(e) {  // the ray is already set by pick()
  const piece = moving;
  moving = null;
  const hit = ray.intersectObjects(groups.terrain.children, true)[0];
  if (!hit) return showInspect({ title: "Move cancelled", rows: [["Why", "that click was not on the ground"]] });
  const [to_x, to_y] = toUE(hit.point);
  if (await popAt(e.clientX, e.clientY, `${piece.op === "copy" ? "Put a copy" : "Move it"} here? It is rebuilt in UEFN.`))
    editPiece({ op: "move", ...piece, to_x, to_y }, true);  // (piece.op: a copy)
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
  rt.needsDraw = true;
  queueEdit("Rebuilding the world", async () => {
    const out = await rpc("city_delete", { net_id: state.net, city: c.id, confirm: true });
    return out.ok === false && /^No city /.test(out.error || "") ? { ok: true, gone: true } : out;
  });
}
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
  rt.needsDraw = true;
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
  const [x, y] = toUE(v), d = rt.drag.lakePt;
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
  const [x, y] = toUE(v), hnd = rt.drag.handle, c = hnd.c;
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
  rt.needsDraw = true;
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
  rt.needsDraw = true;
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
  rt.needsDraw = true;
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
  rt.needsDraw = true;
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
  rt.needsDraw = true;
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
    rt.needsDraw = true;
    if (rising) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);  // (not waited on: the next batch loads while this one rises)
}
function startCityDrag(c) {
  if (heldElsewhere(c)) { rt.drag.cityCand = null; rt.drag.cityMove = false; return; }
  const v = groundAt({ clientX: rt.drag.x0, clientY: rt.drag.y0 });
  if (!v) return;
  const [x, y] = toUE(v);
  rt.drag.city = { c, dx: c.x - x, dy: c.y - y, to: [c.x, c.y] };
  liftCity(c);  // it leaves its place at once; a light copy of it rides with the pointer
  renderer.domElement.style.cursor = "grabbing";
  state.cityPick = c.id;
  renderCityList();  // only a ghost moves while dragging: nothing is rebuilt until the drop (Esc cancels)
  drawCityGhost(rt.drag.city);
}
function dragCity(e) {
  const v = groundAt(e);
  if (!v) return;
  const [x, y] = toUE(v);
  rt.drag.city.to = [x + rt.drag.city.dx, y + rt.drag.city.dy];
  rt.drag.city.c.dragTo = rt.drag.city.to;  // its name tag follows
  tellGroup();  // teammates see it move as it is dragged
  slideGhost(rt.drag.city.to);
  drawCityGhost(rt.drag.city);
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
  rt.needsDraw = true;
}
function cancelDrag() {  // Esc: whatever is held goes back to where it was; true when something was held
  if (!rt.drag) return false;
  if (rt.drag.city) return cancelCityDrag();
  if (rt.drag.handle) {  // turning or shaping a city
    const { c, was, moved } = rt.drag.handle;
    rt.drag = null;
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
    rt.needsDraw = true;
    return true;
  }
  if (rt.drag.lakePt) {  // moving a point of a lake
    const { lake: l, k, was } = rt.drag.lakePt;
    rt.drag = null;
    l.pts[k] = was;
    renderer.domElement.style.cursor = "";
    drawLakes();
    return true;
  }
  if (rt.drag.ptCand && rt.drag.ptTo) {  // moving a road point
    rt.drag = null;
    clear(marks);
    drawMarks();
    renderer.domElement.style.cursor = "";
    rt.needsDraw = true;
    return true;
  }
  if (rt.drag.drawing) { rt.drag = null; clearNote(); return true; }  // drawing an area
  return false;
}
function cancelCityDrag() {  // Esc while dragging: the city stays where it was
  if (!rt.drag?.city) return false;
  clear(marks);
  delete rt.drag.city.c.dragTo;
  rt.drag = null;
  tellGroup();
  dropLift();
  renderer.domElement.style.cursor = "";
  drawCityMark();
  rt.needsDraw = true;
  return true;
}
async function dropCity(m) {
  clear(marks);
  delete m.c.dragTo;
  setTimeout(tellGroup, 80);  // the drag is over: teammates' copy of it goes
  rt.needsDraw = true;
  if (Math.hypot(m.to[0] - m.c.x, m.to[1] - m.c.y) < 1000) { dropLift(); drawCityMark(); return; }  // barely moved: nothing to do
  await saveCity(m.c, { x: Math.round(m.to[0]), y: Math.round(m.to[1]) });
}
// Move tool: press on a road and rt.drag. The nearest spline point of that road goes with the pointer (a junction
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
  rt.drag.ptTo = toUE(v);
  const p = rt.drag.ptCand.p, s = Math.max(4, orbit.dist / 80), c = col("accent");
  clear(marks);
  const dot = new THREE.Mesh(new THREE.SphereGeometry(s, 16, 12), new THREE.MeshBasicMaterial({ color: c, depthTest: false }));
  dot.position.copy(toV(rt.drag.ptTo[0], rt.drag.ptTo[1], rt.drag.ptTo[2] + 200));
  marks.add(dot, new THREE.Line(new THREE.BufferGeometry().setFromPoints([toV(p[1], p[2], p[3] + 200), dot.position]),
    new THREE.LineBasicMaterial({ color: c, depthTest: false })));
  renderer.domElement.style.cursor = "grabbing";
  rt.needsDraw = true;
}
async function dropRoadPoint(d) {
  clear(marks);
  rt.needsDraw = true;
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
  rt.needsDraw = true;
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

export { ray, pick, xyz, NATURE_RE, tileOf, layerInfo, pieceInfo, showInspect, popAt, pcgUnder, dragPiece, dropPiece, moving, editPiece, moveHere, inspectAt, select, addCityAt, deleteCity, addAreaPoint, finishArea, placePin, cityUnder, clickCity, lakePointUnder, lakeAt, lakeClick, saveLake, finishLake, dragLakePoint, dropLakePoint, lakeBanner, handleUnder, turnCity, dragHandle, dropHandle, cityOfActor, inCity, liftCity, slideGhost, hideLifted, dropLift, settleCity, NO_POINTS, revealLayers, startCityDrag, dragCity, drawCityGhost, cancelDrag, cancelCityDrag, dropCity, roadPointUnder, dragRoadPoint, dropRoadPoint, hoverCursor, drawMarks, clearNote, askChat };
