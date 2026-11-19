// City Builder panel: the entry. core.js first (it imports no module of ours), then the systems.
import { foldedSections } from "./controls.js";
import { call, rt, savePrefs, state } from "./core.js";
import { live, onMemberState, onSharedSelection, refreshHolds, relayHolds, tellGroup } from "./live.js";
import { loadChats, renderChatBar } from "./panes/chat.js";
import { TABS, buildPanes, openTab, renderTabs } from "./panes/tabs.js";
import { initSplits, renderTools, showVersion } from "./panes/tools.js";
import { renderToolbar } from "./panes/uefn.js";
import { loadNets, openNet, renderHeader } from "./panes/world.js";
import { loop, placeCamera, resize } from "./view/camera.js";
import { buildScene } from "./view/scene.js";
import { themeColors } from "./view/terrain.js";

// ------------------------------------------------------------------ start
(async () => {
  // never wait forever on the saved look: a host that doesn't answer still gets a working panel
  const saved = await call("prefs.get", { id: "ui" }, 8000).catch(() => null);
  const v = saved?.value || {};
  Object.assign(state.gen, v.gen || {});
  Object.assign(state.view, v.view || {});
  rt.hiddenLayers = new Set(state.view.hidden || []);  // the layers hidden with the eye in Layer order
  Object.assign(state.tune, v.tune || {});
  state.tab = TABS.some((t) => t[0] === v.tab) ? v.tab : "world";
  state.live = !!v.live;
  const last = await call("prefs.get", { id: "last_net" }, 8000).catch(() => null);
  if (last?.value) state.net = last.value;
  themeColors();
  state.tool = "select";
  renderHeader();
  renderTabs();
  renderToolbar();
  renderTools();
  renderChatBar();
  initSplits();
  showVersion();
  setTimeout(loadChats, 1500);  // the Send to list is ready before anyone draws
  // the right panel's groups fold; long, rarely changed ones start folded
  foldedSections(state.view.folded ?? ["Waterfront and airports", "Preview"], (titles) => { state.view.folded = titles; savePrefs(); });
  buildPanes();
  openTab(state.tab);
  resize();
  placeCamera();
  loop();
  call("plugin.subscribe", { types: ["road_network_changed"] }).catch(() => {});
  rt.liveReady = true;
  // a page that opens while the group is live catches up on cameras and the shared selection
  call("live.state").then((r) => { live.on = !!r?.on; if (live.on) { tellGroup(); relayHolds(true); } }).catch(() => {});
  setTimeout(refreshHolds, 2500);
  call("live.memberStates").then((r) => {
    const rows = Array.isArray(r) ? r : r?.states || r?.members || [];
    rows.forEach((m) => onMemberState(m.from, m.state));
  }).catch(() => {});
  call("live.shared").then((r) => {
    const v = r?.values?.selection ?? r?.selection;
    if (v) onSharedSelection(v.value ?? v, v.by);
  }).catch(() => {});
  await loadNets(false);
  const first = state.net || state.nets[0]?.id;
  first ? openNet(first) : buildScene();
})();
