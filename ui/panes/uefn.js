import { button, chip, h, iconHTML, section, slider } from "../controls.js";
import { $, alert, busy, call, esc, rpc, savePrefs, showError, state } from "../core.js";
import { currentNet, loadGeometry } from "./world.js";
import { frame, orbit, placeCamera } from "../view/camera.js";
import { loadExact } from "../view/pcg.js";
import { buildScene } from "../view/scene.js";

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


export { renderEditPane, renderStats, renderSelection, needActor, onTune, onRepair, onRescan, onDrape, renderUefnPane, renderWorldHeightmap, onHeightmap, onImportAll, onReimport, onCopy, renderToolbar, renderHud };
