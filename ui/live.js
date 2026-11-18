import * as THREE from "./vendor/three@0.160.0/build/three.module.min.js";
import { h, icon } from "./controls.js";
import { $, busy, call, camera, col, loading, rpc, rt, scene, state, toV, view } from "./core.js";
import { renderWorldCity } from "./panes/cities.js";
import { orbit } from "./view/camera.js";
import { revealLayers } from "./view/edit.js";
import { cityOutline, clear, drawCityMark } from "./view/scene.js";
import { sampleZ } from "./view/terrain.js";

// ------------------------------------------------------------------ live: bodies, holds, edits as they happen
// While the group is live everyone flies round the map as a ghost with their name under it: a body hovers over
// the spot each person looks at (your own shows while you move). The city a person has picked is theirs until
// they let go, as a Kanban card someone has open: the others see whose it is and can look, not change it. A city
// being dragged and a rebuild under way show on every screen as they happen; the change itself comes with sync
// (plugin_scope_changed) and is swapped in place, layer by layer.
// Everything here rides on each person's live state (live.setMyState): nothing is stored, nothing goes to a server.
const ME = Math.random().toString(36).slice(2, 10);  // this page, to tell its own pick from a teammate's
const live = { on: false, moved: 0, since: 0, idle: false, active: Date.now() };
const peers = {};  // device -> { name, tint, state, at (where its body is shown) }
const PEER_TINTS = ["blue", "green", "amber", "red"];
const tintOf = (name) => PEER_TINTS[[...String(name)].reduce((n, ch) => (n * 31 + ch.charCodeAt(0)) >>> 0, 7) % PEER_TINTS.length];
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
let camSent = 0, camTimer = 0;
function sendMyCamera() {  // my live state: where I look, the city I have, a city I am dragging, a rebuild I am running
  clearTimeout(camTimer);
  const now = Date.now(), every = rt.drag?.city ? 90 : 250;
  if (now - camSent < every) { camTimer = setTimeout(sendMyCamera, every); return; }
  camSent = now;
  const t = orbit.target, m = rt.drag?.city;
  call("live.setMyState", { state: { id: ME, net: state.net, tab: state.tab,
    city: live.idle ? "" : state.cityPick || "", since: live.since,
    busy: state.generating ? (loading.title || "Rebuilding") : "",
    ducky: (state.holds || []).filter((x) => x.kind === "ducky").map((x) => ({ city: x.city, who: x.who, task: x.task })),
    move: m ? { city: m.c.id, x: Math.round(m.to[0]), y: Math.round(m.to[1]) } : null,
    cam: { x: +t.x.toFixed(1), y: +t.y.toFixed(1), z: +t.z.toFixed(1), dist: Math.round(orbit.dist), az: +orbit.az.toFixed(3), el: +orbit.el.toFixed(3) } } }).catch(() => {});
}
const tellGroup = () => { if (rt.liveReady) sendMyCamera(); };
function peerKey(from) { return String(from?.device || from?.user || from?.name || from || ""); }
function onMemberState(from, s) {
  const k = peerKey(from);
  if (!k) return;
  if (!s) delete peers[k];  // they left: what they had picked is free again
  else {
    const p = peers[k] ||= { name: String(from?.name || "Teammate"), tint: tintOf(from?.name || k), at: null };
    p.name = String(from?.name || p.name);
    p.state = s;
    live.on = true;
    if (s.busy && s.net === state.net) live.quietUntil = Date.now() + 120000;  // their change comes in without a loading card here
  }
  drawPeers();
}
// The teammate who has a city (picked it first and still has it picked), or null when it is free or mine. Everyone
// works it out from the same live states, so every screen names the same person.
function holderOf(id) {
  if (!id) return null;
  const here = (state.holds || []).find((x) => x.city === id && x.kind === "ducky");  // a ducky on this PC is changing it
  if (here) return { since: 0, id: `ducky:${here.who}`, name: here.who, tint: "accent", task: here.task };
  for (const p of Object.values(peers)) {  // ... or a teammate's ducky
    const dk = p.state?.net === state.net && (p.state.ducky || []).find((x) => x.city === id);
    if (dk) return { since: 0, id: `ducky:${dk.who}`, name: String(dk.who || `${p.name}'s ducky`), tint: p.tint, task: dk.task };
  }
  let best = null;
  for (const p of Object.values(peers)) {
    const s = p.state;
    if (!s || s.net !== state.net || s.city !== id) continue;
    const since = Number(s.since) || 0;
    if (!best || since < best.since || (since === best.since && String(s.id) < best.id)) best = { since, id: String(s.id), name: p.name, tint: p.tint };
  }
  if (!best) return null;
  const mine = state.cityPick === id && !live.idle;
  return mine && (live.since < best.since || (live.since === best.since && ME < best.id)) ? null : best;
}
function heldElsewhere(c) {  // true, and says whose it is, when a teammate or a ducky has this city
  const hd = c && holderOf(c.id);
  if (hd) flash(`${hd.name} has ${c.name}${hd.task ? ` (${hd.task})` : ""}. It is yours when they let go.`);
  return !!hd;
}
// The backend keeps the holds too (holds.py), so a ducky's tool call obeys them and a ducky's own hold shows here:
// this page tells it the city I picked, hears which cities duckies on this PC hold, and passes on what teammates
// hold (from the live group), every few seconds, which also tells it the panel is open.
let myHold = "", toldPeers = "", toldAt = 0;
async function refreshHolds() {
  if (!state.net) return;
  const r = await rpc("holds", { net_id: state.net }).catch(() => null);
  if (!r || r.ok === false) return;
  const was = JSON.stringify(state.holds || []);
  state.holds = r.holds || [];
  if (JSON.stringify(state.holds) !== was) { heldSig = ""; drawPeers(); tellGroup(); }
}
async function keepHold() {  // the city I have picked is held for me while I have it; the one I had is let go
  const want = state.cityPick && !live.idle && !holderOf(state.cityPick) ? state.cityPick : "";
  if (myHold && myHold !== want) rpc("release", { net_id: state.net, city: myHold }).catch(() => {});
  myHold = want;
  if (!want) return;
  const r = await rpc("hold", { net_id: state.net, city: want }).catch(() => null);
  if (r && r.ok === false) { myHold = ""; await refreshHolds(); }  // someone got there first (a ducky on this PC)
}
function relayHolds(force) {  // what teammates hold, for the backend: on a change, and every 10 s as a sign of life
  if (!state.net || !(live.on || Object.keys(peers).length)) return;
  const rows = [];
  for (const c of state.cities) {
    const hd = holderOf(c.id);
    if (hd && !(state.holds || []).some((x) => x.city === c.id && x.kind === "ducky")) rows.push({ city: c.id, who: hd.name });
  }
  const text = JSON.stringify(rows);
  if (!force && text === toldPeers && Date.now() - toldAt < 10000) return;
  toldPeers = text;
  toldAt = Date.now();
  rpc("holds", { net_id: state.net, teammates: text }).catch(() => {});
}
setInterval(() => { if (rt.liveReady && rt.visible) { relayHolds(); if (state.cityPick) keepHold(); } }, 10000);
const peerBusy = () => Object.values(peers).find((p) => p.state?.net === state.net && p.state.busy) || null;
async function peersIdle(title) {  // one rebuild of a world at a time: mine starts when a teammate's is in
  let waited = false;
  for (let n = 0; n < 480; n++) {
    const p = peerBusy();
    if (!p) break;
    waited = true;
    busy(title, { text: `${p.name} is rebuilding (${String(p.state.busy).toLowerCase()}). Yours starts when theirs is in…` });
    await sleep(500);
  }
  if (waited) await sleep(2500);  // their change reaches this PC with sync
}
let flashTimer = 0;
function flash(text) {
  live.flash = text;
  renderLiveNote();
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { live.flash = ""; renderLiveNote(); }, 3200);
}
function renderLiveNote() {  // top of the map: what teammates are doing to this world right now
  const host = $("liveNote");
  if (!host) return;
  const rows = Object.values(peers).filter((p) => p.state?.net === state.net && (p.state.busy || p.state.move)).map((p) =>
    h("div", { class: "rn-live__row" }, h("span", { class: "rn-live__dot", style: `background: var(--${p.tint})` }),
      `${p.name}: ${p.state.busy || `moving ${state.cities.find((c) => c.id === p.state.move.city)?.name || "a city"}`}`));
  if (live.flash) rows.push(h("div", { class: "rn-live__row" }, icon("lock", 12), live.flash));
  host.replaceChildren(...rows);
}
// On the map: the edge of every city a teammate has, in their colour, and a city a teammate is dragging, where it
// is right now
const peerGroup = new THREE.Group();
scene.add(peerGroup);
let heldSig = "";
function drawPeers() {
  clear(peerGroup);
  const t = state.geo?.terrain || {};
  const ring = (c, x, y, tint, real, wash) => {
    const pts = [];
    for (let k = 0; k <= 90; k++) {
      const deg = k * 4, a = deg * Math.PI / 180, r = c.radius * (real ? cityOutline(c, deg) : 1);
      const px = x + Math.cos(a) * r, py = y + Math.sin(a) * r;
      pts.push(toV(px, py, (sampleZ(t, px, py) ?? 0) + 700));
    }
    peerGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color: col(tint), depthTest: false, transparent: true, opacity: 0.95 })));
    if (wash) {
      const mid = toV(x, y, (sampleZ(t, x, y) ?? 0) + 700), fan = [];
      for (let k = 0; k + 1 < pts.length; k++) fan.push(mid, pts[k], pts[k + 1]);
      peerGroup.add(new THREE.Mesh(new THREE.BufferGeometry().setFromPoints(fan),
        new THREE.MeshBasicMaterial({ color: col(tint), transparent: true, opacity: 0.14, depthTest: false, side: THREE.DoubleSide })));
    }
  };
  if (state.geo) {
    for (const p of Object.values(peers)) {
      const s = p.state;
      if (!s || s.net !== state.net) continue;
      const held = s.city && state.cities.find((c) => c.id === s.city);
      if (held && holderOf(held.id)?.id === String(s.id)) ring(held, held.x, held.y, p.tint, true, false);
      const moving = s.move && state.cities.find((c) => c.id === s.move.city);
      if (moving) ring(moving, s.move.x, s.move.y, p.tint, false, true);
    }
  }
  syncGhosts();
  renderLiveNote();
  // whose a city is shows in its name tag and its details: redrawn only when that changes
  const sig = state.cities.map((c) => `${c.id}:${holderOf(c.id)?.id || ""}`).join("|");
  if (sig !== heldSig) {
    heldSig = sig;
    relayHolds(true);
    const host = $("labels");
    if (host) host.replaceChildren();  // placeLabels writes them again, with the holders' names
    if (state.cityPick) { renderWorldCity?.(); drawCityMark(); }
  }
  rt.needsDraw = true;
}
// A ghost: a rounded body with two eyes, in its person's colour, hovering over the spot that person looks at and
// turned the way they look. It is drawn as big as it needs to be seen from here, near or far.
const ghostGroup = new THREE.Group();
scene.add(ghostGroup);
const ghosts = new Map();  // device ("me" for this page) -> its body
const GHOST_BODY = new THREE.LatheGeometry([[0.001, 1.5], [0.28, 1.46], [0.5, 1.28], [0.62, 0.95], [0.64, 0.3], [0.56, 0.0]]
  .map(([r, y]) => new THREE.Vector2(r, y)), 20);
const GHOST_EYE = new THREE.SphereGeometry(0.085, 10, 8);
function makeGhost(tint) {
  const g = new THREE.Group();
  const skin = new THREE.MeshStandardMaterial({ color: col(tint), emissive: col(tint), emissiveIntensity: 0.3, roughness: 0.55,
    transparent: true, opacity: 0.85, side: THREE.DoubleSide });
  g.add(new THREE.Mesh(GHOST_BODY, skin));
  for (const sx of [-0.2, 0.2]) {
    const eye = new THREE.Mesh(GHOST_EYE, new THREE.MeshBasicMaterial({ color: col("bg"), transparent: true }));
    eye.position.set(sx, 1.05, 0.55);
    g.add(eye);
  }
  g.userData.skin = skin;
  return g;
}
function syncGhosts() {
  const want = new Map(Object.entries(peers).filter(([, p]) => p.state?.cam && p.state.net === state.net).map(([k, p]) => [k, p.tint]));
  if (want.size) want.set("me", "accent");  // my own body: only with someone to fly with
  for (const [k, g] of ghosts) {
    if (want.has(k)) continue;
    ghostGroup.remove(g);
    g.userData.skin.dispose();
    ghosts.delete(k);
  }
  for (const [k, tint] of want) {
    if (ghosts.has(k)) continue;
    const g = makeGhost(tint);
    g.visible = false;
    ghosts.set(k, g);
    ghostGroup.add(g);
  }
}
// every frame: each body glides to where its person now looks; true while one is still on its way (keep drawing)
function stepGhosts() {
  if (!ghosts.size) return false;
  let moving = false;
  const put = (g, at, az, fade) => {
    const size = Math.max(3, Math.min(900, camera.position.distanceTo(at) * 0.024));
    g.position.set(at.x, at.y + size * 0.9, at.z);
    g.scale.setScalar(size);
    g.rotation.y = az + Math.PI;  // facing the way its person looks
    g.userData.size = size;
    g.visible = fade > 0.02;
    g.userData.skin.opacity = 0.85 * fade;
    for (const o of g.children) if (o.material !== g.userData.skin) o.material.opacity = fade;
  };
  for (const [k, g] of ghosts) {
    if (k === "me") {  // shown while I move, gone a moment after I stop: it never sits in the way of a click
      const still = performance.now() - live.moved, fade = Math.max(0, Math.min(1, 1 - (still - 900) / 600));
      put(g, orbit.target, orbit.az, fade);
      if (fade > 0 && fade < 1) moving = true;
      else if (fade === 1 && still < 900) moving = true;
      continue;
    }
    const p = peers[k], c = p?.state?.cam;
    if (!c) continue;
    const goal = new THREE.Vector3(c.x, c.y, c.z);
    if (!p.at || p.at.distanceTo(goal) > Math.max(4000, c.dist * 4)) p.at = goal.clone();  // (a jump across the map: no glide)
    else if (p.at.distanceTo(goal) > 0.05) { p.at.lerp(goal, 0.18); moving = true; }
    put(g, p.at, c.az, 1);
  }
  return moving;
}
function placePeerLabels() {  // each body's name, under it
  const host = $("peers");
  if (!host) return;
  const rows = [...ghosts].filter(([, g]) => g.visible).map(([k, g]) => [k, g, k === "me" ? "You" : peers[k]?.name || "Teammate", k === "me" ? "accent" : peers[k]?.tint || "accent"]);
  const sig = rows.map(([k, , name]) => `${k}:${name}`).join("|");
  if (host.dataset.sig !== sig) {
    host.dataset.sig = sig;
    host.replaceChildren(...rows.map(([, , name, tint]) => h("div", { class: "rn-label rn-label--peer" },
      h("span", { class: "rn-live__dot", style: `background: var(--${tint})` }), name)));
  }
  const w = view.clientWidth, hh = view.clientHeight;
  rows.forEach(([, g], i) => {
    const el = host.children[i], v = g.position.clone().project(camera);
    if (!el) return;
    el.style.display = v.z > 1 ? "none" : "";
    el.style.left = ((v.x + 1) / 2 * w) + "px";
    el.style.top = ((1 - v.y) / 2 * hh) + "px";
  });
}
// A person who stops touching the panel for a while lets go of the city they had (as a Kanban card ends its lock):
// it is theirs again, at the back of the line, the moment they come back to it.
const IDLE_MS = 90000;
function touched() {
  live.active = Date.now();
  if (!live.idle) return;
  live.idle = false;
  live.since = Date.now();
  tellGroup();
  keepHold();
}
for (const type of ["pointerdown", "keydown", "wheel"]) window.addEventListener(type, touched, { passive: true, capture: true });
setInterval(() => {
  if (!live.idle && state.cityPick && Date.now() - live.active > IDLE_MS) { live.idle = true; tellGroup(); keepHold(); }
}, 15000);
window.addEventListener("beforeunload", () => {
  call("live.setMyState", { state: null }).catch(() => {});
  if (myHold) rpc("release", { net_id: state.net, city: myHold }).catch(() => {});
});
// ---- a rebuild's result goes straight to teammates' screens
// The PC that rebuilt a city sends, layer by layer, what is new and what is gone (live.send: short messages from
// PC to PC, nothing stored). A teammate's page puts that in at once, as its own rebuild would, so they watch the
// change come in while their own PC places it in the background and then agrees (placement is the same on every
// PC: each piece comes from its own spot). A change too big to send (a whole new world) is left to each PC.
const spot = (p) => `${p[0]},${p[1]},${p[3]}`;
const SHARE_MAX = 90000, PART_BYTES = 12000;
const outbox = [];
let outTimer = 0;
function sendLive(topic, data) {  // at most ~22 a second: the link takes 30
  outbox.push([topic, data]);
  if (outTimer) return;
  outTimer = setInterval(() => {
    const m = outbox.shift();
    if (!m) { clearInterval(outTimer); outTimer = 0; return; }
    call("live.send", { topic: m[0], data: m[1] }).catch(() => {});
  }, 45);
}
function shareLayer(name, layer, oldPts) {
  const pts = layer.points || [], now = new Set(pts.map(spot)), had = new Set((oldPts || []).map(spot));
  const add = pts.filter((p) => !had.has(spot(p))), gone = [...had].filter((k) => !now.has(k));
  if (!add.length && !gone.length) return;
  live.shared = (live.shared || 0) + add.length + gone.length;
  if (live.shared > SHARE_MAX) return;
  const { points, ...meta } = layer;
  let part = { n: state.net, l: name, m: meta, a: [], g: [] }, size = JSON.stringify(meta).length + 80;
  const flush = (end) => {
    if (end) part.e = 1;
    sendLive("pcg", part);
    part = { n: state.net, l: name, a: [], g: [] };
    size = 80;
  };
  for (const p of add) {
    const bytes = JSON.stringify(p).length + 1;
    if (size + bytes > PART_BYTES && (part.a.length || part.g.length)) flush(false);
    part.a.push(p);
    size += bytes;
  }
  for (const k of gone) {
    if (size + k.length + 3 > PART_BYTES) flush(false);
    part.g.push(k);
    size += k.length + 3;
  }
  flush(true);
}
const inbox = {};  // layer -> what has come of it so far, until its last part
function onPeerPcg(d) {
  if (!d || d.n !== state.net || !d.l) return;
  live.quietUntil = Date.now() + 120000;
  const box = inbox[d.l] ||= { m: null, a: [], g: [] };
  if (d.m) box.m = d.m;
  for (const p of d.a || []) box.a.push(p);
  for (const k of d.g || []) box.g.push(k);
  if (!d.e) return;
  delete inbox[d.l];
  const old = state.pcg?.[d.l];
  if (!old && !box.m) return;
  const gone = new Set(box.g), kept = (old?.points || []).filter((p) => !gone.has(spot(p))), have = new Set(kept.map(spot));
  const points = kept.concat(box.a.filter((p) => !have.has(spot(p))));
  revealLayers({ [d.l]: { ...old, ...box.m, points, shown: points.length } }, false);
}
let remoteSel = null;
const selGroup = new THREE.Group();
scene.add(selGroup);
function shareSelection(aslug, p) {
  call("live.share", { key: "selection", value: p ? { net: state.net, actor: aslug, i: p[0] } : null }).catch(() => {});
}
function onSharedSelection(v, by) {
  remoteSel = v && v.net === state.net ? { ...v, by: String(by?.name || "Teammate") } : null;
  drawRemoteSel();
}
function drawRemoteSel() {
  clear(selGroup);
  const a = remoteSel && state.geo?.actors?.[remoteSel.actor];
  const p = a && a.pts.find((q) => q[0] === remoteSel.i);
  if (p) {
    const ring = new THREE.Mesh(new THREE.RingGeometry(14, 18, 32), new THREE.MeshBasicMaterial({ color: col("accent"), side: THREE.DoubleSide }));
    ring.rotation.x = -Math.PI / 2;
    ring.position.copy(toV(p[1], p[2], p[3] + 120));
    selGroup.add(ring);
  }
  rt.needsDraw = true;
}


export { ME, live, peers, PEER_TINTS, tintOf, sleep, camSent, camTimer, sendMyCamera, tellGroup, peerKey, onMemberState, holderOf, heldElsewhere, myHold, toldPeers, toldAt, refreshHolds, keepHold, relayHolds, peerBusy, peersIdle, flashTimer, flash, renderLiveNote, peerGroup, heldSig, drawPeers, ghostGroup, ghosts, GHOST_BODY, GHOST_EYE, makeGhost, syncGhosts, stepGhosts, placePeerLabels, IDLE_MS, touched, spot, SHARE_MAX, PART_BYTES, outbox, outTimer, sendLive, shareLayer, inbox, onPeerPcg, remoteSel, selGroup, shareSelection, onSharedSelection, drawRemoteSel };
