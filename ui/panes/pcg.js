import { button, chip, choices, field, h, icon, section, slider, toggle } from "../controls.js";
import { $, alert, busy, call, esc, rpc, showError, state } from "../core.js";
import { m, pct } from "./tabs.js";
import { renderLayerList } from "./world.js";
import { loadExact, loadPcgPreview, renderPcgCounts, schedulePcgPreview } from "../view/pcg.js";
import { buildScene } from "../view/scene.js";

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


export { KIND_OPTS, ASSET_OPTS, TREE_OPTS, WHERE_OPTS, BUILDING_FORMS, KIND_ICON, pcgSaveTimer, renderPcgPane, layerCard, loadLayers, onPcgPlace };
