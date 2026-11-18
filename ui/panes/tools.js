import * as THREE from "../vendor/three@0.160.0/build/three.module.min.js";
import { h, icon } from "../controls.js";
import { $, hist, rpc, rt, savePrefs, state } from "../core.js";
import { renderWorldCity } from "./cities.js";
import { stepHistory } from "./history.js";
import { currentNet } from "./world.js";
import { FLY_KEYS, fly, frame, orbit, placeCamera, resize } from "../view/camera.js";
import { addCityAt, cancelDrag, clearNote, finishLake, lakeBanner, showInspect } from "../view/edit.js";
import { drawCityMark, drawLakes, lake, lakeWorld } from "../view/scene.js";

// ------------------------------------------------------------------ side panels: resize handles and version
// Ducky's sidebar handle on both sides: drag, arrow keys, double-click for the starting width. Widths are kept.
const SPLITS = [
  { id: "splitLeft", key: "leftW", cssVar: "--left-w", min: 180, max: 480, start: 236, sign: 1 },
  { id: "splitRight", key: "rightW", cssVar: "--right-w", min: 260, max: 560, start: 328, sign: -1 },
];
function initSplits() {
  const root = document.documentElement;
  for (const s of SPLITS) {
    const handle = $(s.id);
    const setW = (w) => {
      state.view[s.key] = Math.max(s.min, Math.min(s.max, Math.round(w)));
      root.style.setProperty(s.cssVar, `${state.view[s.key]}px`);
      handle.setAttribute("aria-valuenow", state.view[s.key]);
      resize();
    };
    handle.setAttribute("aria-valuemin", s.min);
    handle.setAttribute("aria-valuemax", s.max);
    if (state.view[s.key]) setW(state.view[s.key]);
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const x0 = e.clientX, w0 = state.view[s.key] || s.start;
      handle.setPointerCapture(e.pointerId);
      handle.classList.add("is-dragging");
      document.body.classList.add("is-resizing");
      const move = (ev) => setW(w0 + (ev.clientX - x0) * s.sign);
      const up = () => {
        handle.classList.remove("is-dragging");
        document.body.classList.remove("is-resizing");
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        handle.removeEventListener("pointercancel", up);
        savePrefs();
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
      handle.addEventListener("pointercancel", up);
    });
    handle.addEventListener("dblclick", () => { setW(s.start); savePrefs(); });
    // the header button folds this panel away (view.leftHidden / view.rightHidden are kept)
    const side = s.key === "leftW" ? "left" : "right", fold = $(s.key === "leftW" ? "foldLeft" : "foldRight");
    const show = (open) => {
      state.view[`${side}Hidden`] = !open;
      document.body.classList.toggle(`rn-no-${side}`, !open);
      const tip = `${open ? "Hide" : "Show"} the ${side} panel`;
      fold.setAttribute("aria-expanded", String(open));
      fold.setAttribute("aria-label", tip);
      fold.title = tip;
      resize();
    };
    fold.replaceChildren(icon(side === "left" ? "panelLeft" : "panelRight", 16));
    fold.addEventListener("click", () => { show(!!state.view[`${side}Hidden`]); savePrefs(); });
    show(!state.view[`${side}Hidden`]);
    handle.addEventListener("keydown", (e) => {
      const k = { ArrowLeft: -16, ArrowRight: 16 }[e.key];
      if (!k) return;
      e.preventDefault();
      setW((state.view[s.key] || s.start) + k * s.sign);
      savePrefs();
    });
  }
}
async function showVersion() {
  const v = await rpc("version");
  $("version").replaceChildren(h("span", {}, "City Builder"), h("span", {}, v.version ? `v${v.version}` : ""));
}

// ------------------------------------------------------------------ map tools: point or draw, tell an AI
const note = { kind: "", points: [], chats: null };
// the map tool bar: the same bar as the Workflows canvas (icon buttons, tool tips, a zoom readout)
function renderTools() {
  const tool = (id, ic, hint, key) => {
    const b = h("button", { type: "button", class: "rn-tool", "aria-pressed": state.tool === id ? "true" : "false",
      title: key ? `${hint} (${key})` : hint, "aria-label": hint }, icon(ic, 16));
    b.addEventListener("click", () => setTool(state.tool === id && id !== "select" ? "select" : id));
    return b;
  };
  const act = (ic, fn, hint, key) => {
    const b = h("button", { type: "button", class: "rn-tool", title: key ? `${hint} (${key})` : hint, "aria-label": hint }, icon(ic, 16));
    b.addEventListener("click", fn);
    return b;
  };
  const zoom = h("button", { type: "button", class: "rn-zoom", id: "zoomPct", title: "Zoom: click to fit the whole world (F)",
    "aria-label": "Zoom, fit the world" }, "100%");
  zoom.addEventListener("click", () => frame());
  const hasNote = note.points.length > 0;
  $("tools").replaceChildren(
    tool("select", "pointer", "Pointer: click a city, then one of its blocks; nothing moves", "V"),
    tool("hand", "hand", "Hand: drag to pan the view", "H"),
    ...(currentNet()?.generated ? [tool("city", "city", "Edit city: drag it to move it, the white handle to turn it, the dots to shape its edge; the red, blue and green dots move and resize its districts; click a block to change it", "E")] : []),
    tool("move", "route", "Move road: press on a road and drag that part of it; the ground is cut again under it", "M"),
    h("span", { class: "rn-sep" }),
    tool("pin", "pin", "Pin a spot, then tell an AI what to do there", "P"),
    tool("area", "pen", "Draw round an area, then tell an AI what to do there", "A"),
    ...(lakeWorld() ? [tool("lake", "lake", "Lake: click along where it goes, double-click to finish, and the ground is dug there; click a lake to change its width, drag its dots, or delete it", "L")] : []),
    ...(currentNet()?.generated ? [tool("addcity", "plus", "Add a city: click where it goes", "C")] : []),
    ...(hasNote ? [act("x", clearNote, "Remove the pin or area")] : []),
    h("span", { class: "rn-sep" }),
    ...[["undo", true, hist.undo, "Undo", "Ctrl+Z"], ["redo", false, hist.redo, "Redo", "Ctrl+Y"]].map(([ic, back, list, word, key]) => {
      const b = act(ic, () => stepHistory(back), list.length ? `${word}: ${list[list.length - 1].label}` : `Nothing to ${word.toLowerCase()}`, key);
      b.disabled = !list.length;
      return b;
    }),
    h("span", { class: "rn-sep" }),
    act("frame", () => frame(), "Fit the whole world", "F"),
    zoom,
    act("top", () => topView(), "Top view", "T"),
    act("help", toggleHelp, "Controls", "?"));
  renderZoom();
}
// zoom readout: 100 % is the whole world in view
function renderZoom() {
  const el = $("zoomPct");
  if (!el || !state.box || state.box.isEmpty()) return;
  const fit = state.box.getSize(new THREE.Vector3()).length() * 0.75;
  el.textContent = `${Math.round((fit / orbit.dist) * 100)}%`;
}
function topView() {
  orbit.el = 1.55;
  placeCamera();
  renderZoom();
}
function toggleHelp() {
  const pop = $("toolHelp");
  if (pop && !pop.hidden) { pop.hidden = true; return; }
  const row = (keys, what) => h("div", { class: "rn-help__row" }, h("span", { class: "rn-help__keys" }, keys), h("span", {}, what));
  const box = pop || h("div", { class: "rn-help", id: "toolHelp", role: "dialog", "aria-label": "Map controls" });
  box.replaceChildren(h("div", { class: "rn-help__title" }, "Map controls"),
    row("Click a city", "Pick it; then click a block of it"), row("Edit city (E)", "Drag to move, white handle turns, dots shape its edge"),
    row("District dots", "Red middle: move the centre. Red / blue on a ring: its size. Green: industry's side"),
    row("Lake (L)", "Click along it, double-click to finish. Click a lake: its width, its dots, delete"),
    row("Left drag", "Orbit (Pointer) / pan (Hand)"),
    row("Hold right mouse", "Fly: look around"), row("+ W A S D, Q / E", "Fly: move, down / up (Shift: faster)"),
    row("+ Wheel", "Fly: speed"), row("Shift / middle drag", "Pan"), row("Wheel", "Zoom"),
    row("V H E M P A L C", "Pointer, Hand, Edit city, Move road, Pin, Area, Lake, Add city"), row("F / T", "Fit / top view"),
    row("Ctrl+Z / Ctrl+Y", "Undo / redo your last change"),
    row("Esc", "While dragging: cancel, nothing changes. Else back to Pointer"));
  box.hidden = false;
  if (!pop) $("view").parentElement.append(box);
}
function setTool(id) {
  state.tool = id;
  $("view").classList.toggle("is-drawing", id === "pin" || id === "area" || id === "addcity" || id === "lake");
  $("view").classList.toggle("is-hand", id === "hand");
  if (id !== "lake") { lake.draft = []; lake.pick = ""; }
  renderTools();
  renderWorldCity();
  drawCityMark();  // the Edit city tool shows the picked city's handles
  drawLakes();  // the Lake tool shows every lake's line and shore
  rt.needsDraw = true;
  const banner = $("banner");
  if (id === "lake") return lakeBanner();
  if (id === "city") {
    const c = state.cities.find((x) => x.id === state.cityPick);
    banner.replaceChildren(h("span", {}, c ? `Editing ${c.name}: drag it to move it, the white handle to turn it, the dots to shape its edge, the coloured dots to move and resize its districts` : "Click a city to edit it"),
      h("button", { type: "button", class: "rn-mini", onclick: () => setTool("select") }, "Done"));
    banner.hidden = false;
  } else if (id === "addcity") {  // placing a new city: click the map, or let City Builder find room
    banner.replaceChildren(h("span", {}, "Click the map where the new city goes"),
      h("button", { type: "button", class: "rn-mini", onclick: () => addCityAt(null) }, "Find a free spot"),
      h("button", { type: "button", class: "rn-mini", onclick: () => setTool("select"), "aria-label": "Cancel" }, icon("x", 12)));
    banner.hidden = false;
  } else banner.hidden = true;
}
window.addEventListener("keydown", (e) => {
  if (e.target.closest?.("input, textarea, [contenteditable]")) return;  // typing, not shortcuts
  // Escape while something is held (a city, a handle, a road point, an area being drawn): it goes back to where
  // it was and nothing is changed. Nothing is ever saved before the button is let go.
  if (e.key === "Escape") {
    if (cancelDrag()) return;
    if (state.tool === "lake" && (lake.draft.length || lake.pick)) { lake.draft = []; lake.pick = ""; drawLakes(); lakeBanner(); return; }  // the lake being drawn, or the pick
    showInspect(null);
    if (state.tool !== "select") setTool("select");
    $("toolHelp")?.setAttribute("hidden", "");
    return;
  }
  if (e.key === "Enter" && state.tool === "lake" && lake.draft.length) { finishLake(); return; }
  if ((e.ctrlKey || e.metaKey) && !e.altKey && ["z", "y"].includes(e.key.toLowerCase())) {
    e.preventDefault();
    stepHistory(e.key.toLowerCase() === "z" && !e.shiftKey);  // Ctrl+Z undo; Ctrl+Y or Ctrl+Shift+Z redo
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (fly.on) {  // flying: W A S D Q E move, never switch tools
    fly.shift = e.shiftKey;
    if (FLY_KEYS[k]) { fly.keys.add(k); e.preventDefault(); }
    return;
  }
  const tools = { v: "select", h: "hand", e: "city", m: "move", p: "pin", a: "area", c: "addcity", l: "lake" };
  if (tools[k] && ((k !== "c" && k !== "e") || currentNet()?.generated) && (k !== "l" || lakeWorld())) setTool(tools[k]);
  else if (k === "f") frame();
  else if (k === "t") topView();
  else if (k === "?") toggleHelp();
});

window.addEventListener("keyup", (e) => { fly.keys.delete(e.key.toLowerCase()); fly.shift = e.shiftKey; });
window.addEventListener("blur", () => { fly.on = false; fly.keys.clear(); });


export { SPLITS, initSplits, showVersion, note, renderTools, renderZoom, topView, toggleHelp, setTool };
