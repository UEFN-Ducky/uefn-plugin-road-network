import { button, h, icon } from "../controls.js";
import { parseLiveView, syncPlan } from "../sync.js";
import { $, CHANNEL, OPTION_KEYS, busy, call, ctl, esc, groups, loading, rpc, rt, savePrefs, showError, state, swatch } from "../core.js";
import { drawPeers, flash, live, onMemberState, onPeerPcg, onSharedSelection, peerBusy, peers, refreshHolds, tellGroup } from "../live.js";
import { edits, renderCitiesPane, renderCityList } from "./cities.js";
import { loadHistory } from "./history.js";
import { loadLayers, renderPcgPane } from "./pcg.js";
import { TABS, openTab, syncControls, syncGenerate } from "./tabs.js";
import { renderSelection, renderStats, renderUefnPane } from "./uefn.js";
import { askChat, lakeBanner, settleCity } from "../view/edit.js";
import { ROLE_COLOR, renderPcgCounts } from "../view/pcg.js";
import { buildScene, drawLakes, lake, lakesOf } from "../view/scene.js";
import { themeColors } from "../view/terrain.js";

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
// the generate settings a saved world opens with. A world saved before the terrain engine has no preset in its
// options: it stays Classic, so regenerating it never moves its ground onto the engine (new worlds start on Coastal)
function worldGen(g, name, gen) {
  return { name, seed: g.seed ?? gen.seed, size_km: g.size_km ?? gen.size_km, relief_m: g.relief_m ?? gen.relief_m,
    cities: g.cities ?? gen.cities, ground: g.ground_from ? "net" : "new", preset: "Classic", ...(g.options || {}) };
}
async function openNet(id) {
  state.net = id; state.framed = false; state.sel = null; state.actor = ""; state.pcg = {}; state.exact = null;
  state.cityPick = ""; state.block = null;
  settleCity();
  if (loading.title === "Placing the city") busy("");  // the last world's: its run stops (loadPcgPreview)
  call("prefs.set", { id: "last_net", value: id }).catch(() => {});
  renderWorldPick();
  $("syncBadge")?.replaceChildren();  // filled after this world's city preview (loadPcgPreview)
  document.querySelectorAll("[data-net]").forEach((b) => b.toggleAttribute("aria-current", b.dataset.net === id));
  const g = currentNet()?.generated;
  if (g) {  // the generate tabs show this world's own settings
    Object.assign(state.gen, worldGen(g, currentNet().name, state.gen));
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
  if (!quiet) busy("Loading the world", { text: "Ground and water…", step: 0, of: 1 });
  // the card says what has come in so far (it sat on one line for the whole load)
  const step = quiet ? null : (done, of, text) => {
    if (net === state.net && loading.title === "Loading the world") busy("Loading the world", { text, step: done, of });
  };
  const [geo, st, cities] = await Promise.all([loadAllGeometry(net, step), rpc("stats", { net_id: net }), rpc("cities", { net_id: net })]);
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
// re-polled big reads (geometry, the city preview): send the last reply's etag, Ducky answers {unchanged} instead of the same MB again
const replyCache = new Map();  // key -> { etag, reply }; Map keeps insertion order, so the first key is the oldest
async function cachedRpc(method, params = {}) {
  const key = method + JSON.stringify(params);
  const hit = replyCache.get(key);
  const out = await rpc(method, { ...params, _etag: hit?.etag ?? "" });
  if (out?.unchanged && hit) { replyCache.delete(key); replyCache.set(key, hit); return { ...hit.reply }; }
  if (out?.ok !== false && out?.etag) {
    replyCache.delete(key);
    replyCache.set(key, { etag: out.etag, reply: out });
    if (replyCache.size > 40) replyCache.delete(replyCache.keys().next().value);
  }
  return { ...out };  // a copy: callers set ok/error on the reply
}
async function loadAllGeometry(net, step = null) {
  const [base, info] = await Promise.all([cachedRpc("geometry", { net_id: net, actor: "-" })  /* "-" matches no actor: terrain and props only */, rpc("get", { net_id: net })]);
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
  let done = 0;
  step?.(0, batches.length + 1, `Roads: 0 of ${batches.length} parts…`);
  await Promise.all(batches.map(async (slugs) => {
    for (let offset = 0; ; offset += PAGE) {
      if (net !== state.net) return;  // another world was opened: stop paging this one
      const page = await cachedRpc("geometry", { net_id: net, actor: slugs.join(","), offset, limit: PAGE, terrain: false });
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
    done += 1;
    step?.(done, batches.length + 1, `Roads: ${done} of ${batches.length} parts (${slugs.map((s) => actors[s].label).join(", ")})`);
  })).catch((err) => { base.ok = false; base.error = err.message; });
  step?.(batches.length, batches.length + 1, "Cities and their plans…");
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
    rt.visible = !!e.visible;
    rt.needsDraw = true;
    if (rt.visible && (reloads.geometry || reloads.layers)) reloadSoon();  // changes made while the tab was hidden
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
  if (!rt.visible || reloads.running) return;  // shown again / the running reload ends: it looks again
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
// the open world deleted for good (its roads, cities and layers here; UEFN keeps whatever was built)
async function deleteWorld() {
  const n = currentNet();
  if (!n) return;
  const ok = await call("ui.confirm", { title: `Delete ${n.name}`, danger: true, confirm: "Delete",
    message: `${n.name} and everything in it (terrain, cities, roads, layers) go from City Builder. Nothing in UEFN changes.` }).catch(() => null);
  if (!ok?.confirmed) return;
  const out = await rpc("delete", { net_id: n.id });
  if (out.ok === false) return showError(out.error);
  state.net = "";
  worldDrop = null;  // rebuilt with the worlds that are left
  await loadNets();
}
const deleteWorldButton = () => h("button", { type: "button", class: "rn-mini", style: "margin-left: 6px",
  title: "Delete this world", "aria-label": "Delete this world", onclick: deleteWorld }, icon("trash", 14));
function renderWorldPick() {
  const host = $("worldPick");
  if (!state.nets.length) { worldDrop = null; host.replaceChildren(h("span", { class: "dk-muted" }, "No worlds yet")); return; }
  const opts = state.nets.map((n) => ({ value: n.id, label: n.generated ? n.name : `${n.name} (from UEFN)` }));
  if (!window.DuckyKit?.dropdown) {  // the kit not loaded yet: plain buttons, never a native select
    host.replaceChildren(...state.nets.map((n) => button(n.name, n.generated ? "world" : "network", () => openNet(n.id),
      n.id === state.net ? "primary" : "ghost")), deleteWorldButton());
    return;
  }
  if (!worldDrop) {
    worldDrop = window.DuckyKit.dropdown({ label: "World", options: opts, value: state.net || opts[0].value,
      onChange: (v) => { if (v && v !== state.net) openNet(v); } });
    host.replaceChildren(worldDrop.el, deleteWorldButton());
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
      h("span", { class: "rn-name" }, l.name), h("span", { class: "rn-count", "data-pcg-count": l.name }, ""), eyeOf(l), icon("grip", 14));
    row.lastChild.classList.add("rn-grab");
    if (rt.hiddenLayers.has(l.name)) row.classList.add("is-hidden");
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
// the eye on a layer row: hides or shows that layer in the view only (kept across reloads)
function eyeOf(l) {
  const hidden = rt.hiddenLayers.has(l.name);
  const eye = h("span", { class: "rn-eye", role: "button", title: hidden ? `Show ${l.name} in the view` : `Hide ${l.name} in the view (nothing in UEFN changes)` },
    icon(hidden ? "eyeOff" : "eye", 14));
  eye.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!rt.hiddenLayers.delete(l.name)) rt.hiddenLayers.add(l.name);
    state.view.hidden = [...rt.hiddenLayers];
    savePrefs();
    for (const o of groups.pcg.children) if (o.userData.layer === l.name) o.visible = !rt.hiddenLayers.has(l.name);
    rt.needsDraw = true;
    renderLayerList();
  });
  return eye;
}


export { currentNet, optionsOf, loadNets, openNet, loadSeq, loadGeometry, PAGE, replyCache, cachedRpc, loadAllGeometry, renderActors, generate, pullName, pullBtn, reloads, RELOAD_PAUSE_MS, reloadSoon, runReloads, applySync, renderHeader, syncSeq, loadSync, worldDrop, deleteWorld, deleteWorldButton, renderWorldPick, newWorld, renderLayerList };
