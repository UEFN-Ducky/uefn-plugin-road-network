import { h, icon } from "../controls.js";
import { $, busy, hist, rpc, rt, showError, state } from "../core.js";
import { edits } from "./cities.js";
import { renderTools } from "./tools.js";
import { currentNet, loadGeometry, loadNets } from "./world.js";
import { cancelDrag } from "../view/edit.js";

// ------------------------------------------------------------------ history: undo, redo, go back
// The backend keeps what every step changed (on this PC only) and puts it back exactly: the world's cities, roads,
// ground and layers as they were. UEFN is not touched; what is then waiting to be pushed shows as usual.
function historySoon() {
  clearTimeout(hist.timer);
  hist.timer = setTimeout(loadHistory, 400);
}
async function loadHistory() {
  if (!state.net) { hist.undo = []; hist.redo = []; renderHistory(); return; }
  const net = state.net, got = await rpc("history", { net_id: net });
  if (net !== state.net || got.ok === false) return;
  hist.undo = got.undo || [];
  hist.redo = got.redo || [];
  renderHistory();
}
async function stepHistory(back, steps = 1) {
  const list = back ? hist.undo : hist.redo;
  if (hist.busy || !state.net || !list.length || state.generating || edits.running || edits.list.length) return;
  hist.busy = true;
  const name = list[list.length - 1].label;
  busy(back ? "Undoing" : "Redoing", { text: steps > 1 ? `${steps} steps…` : `${name}…` });
  const got = await rpc(back ? "undo" : "redo", { net_id: state.net, steps });
  hist.busy = false;
  busy("");
  if (got.ok === false) { showError(got.error); return; }
  hist.undo = got.undo || [];
  hist.redo = got.redo || [];
  renderHistory();
  cancelDrag();
  await loadNets(false);  // (the step may have been the one that made the world, or its cities)
  if (currentNet()) await loadGeometry(true);
}
const ago = (at) => {
  const s = Math.max(0, Date.now() / 1000 - at);
  return s < 90 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
};
function renderHistory() {
  const host = $("historyList");
  if (host) {
    const row = (ent, cls, title, go) => {
      const b = h("button", { type: "button", class: `rn-hist__row ${cls}`, title }, icon(cls === "is-redo" ? "redo" : "clock", 14),
        h("span", { class: "rn-hist__name" }, ent.label), h("span", { class: "rn-hist__at" }, ago(ent.at)));
      b.addEventListener("click", go);
      return b;
    };
    const redo = hist.redo.map((ent, i) => row(ent, "is-redo", `Redo up to here: ${ent.label}`, () => stepHistory(false, hist.redo.length - i)));
    const undo = [...hist.undo].reverse().map((ent, i) => row(ent, "", `Go back to before this: ${ent.label}`, () => stepHistory(true, i + 1)));
    host.replaceChildren(...(redo.length || undo.length ? [...redo,
      ...(redo.length ? [h("div", { class: "rn-hist__now" }, "The world as it is now")] : []), ...undo]
      : [h("div", { class: "rn-hint" }, state.net ? "Nothing to go back to yet. Every change you make is listed here." : "Open a world.")]));
  }
  renderTools();
}

rt.historySoon = historySoon;  // core's rpc() asks for it after every change (core imports none of ours)

export { historySoon, loadHistory, stepHistory, ago, renderHistory };
