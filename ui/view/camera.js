import * as THREE from "../vendor/three@0.160.0/build/three.module.min.js";
import { $, FULL_DPR, camera, groups, renderer, rt, scene, state, view } from "../core.js";
import { heldElsewhere, live, placePeerLabels, sendMyCamera, stepGhosts } from "../live.js";
import { note, renderZoom } from "../panes/tools.js";
import { addAreaPoint, addCityAt, cityUnder, clickCity, dragCity, dragHandle, dragLakePoint, dragPiece, dragRoadPoint, dropCity, dropHandle, dropLakePoint, dropPiece, dropRoadPoint, finishArea, finishLake, handleUnder, hoverCursor, lakeClick, lakePointUnder, pcgUnder, pick, placePin, roadPointUnder, startCityDrag } from "./edit.js";
import { drawCityMark, drawLakes, lakesOf, placeLabels } from "./scene.js";

// orbit camera: left drag rotate, right drag (or shift) pan, wheel zoom
const orbit = { target: new THREE.Vector3(), dist: 3000, az: 0.6, el: 0.9 };
function placeCamera() {
  const { target, dist, az, el } = orbit;
  camera.position.set(target.x + dist * Math.cos(el) * Math.sin(az), target.y + dist * Math.sin(el), target.z + dist * Math.cos(el) * Math.cos(az));
  camera.lookAt(target);
  if (scene.fog) { scene.fog.near = dist * 1.4; scene.fog.far = dist * 5; }
  rt.needsDraw = true;
  if (rt.liveReady) { live.moved = performance.now(); sendMyCamera(); }
}
// Fly camera, like the Unreal viewport: hold the right mouse button to look around, W A S D to move, Q / E to go
// down / up, Shift to go faster, and the wheel (while held) to set the flying speed.
const fly = { on: false, keys: new Set(), speed: 1, last: 0 };
const FLY_KEYS = { w: 1, a: 1, s: 1, d: 1, q: 1, e: 1 };
function orbitOffset() {
  const { dist, az, el } = orbit;
  return new THREE.Vector3(dist * Math.cos(el) * Math.sin(az), dist * Math.sin(el), dist * Math.cos(el) * Math.cos(az));
}
function flyLook(dx, dy) {  // turn in place: the eye stays, the point it looks at swings round it
  const eye = camera.position.clone();
  orbit.az -= dx * 0.004;
  orbit.el = Math.max(-1.45, Math.min(1.55, orbit.el + dy * 0.004));
  orbit.target.copy(eye).sub(orbitOffset());
  placeCamera();
}
function flyStep(now) {
  if (!fly.on || !fly.keys.size) { fly.last = 0; return; }
  const dt = Math.min(0.1, fly.last ? (now - fly.last) / 1000 : 0.016);
  fly.last = now;
  const f = new THREE.Vector3().subVectors(orbit.target, camera.position).normalize();
  const r = new THREE.Vector3().crossVectors(f, camera.up).normalize();
  const v = new THREE.Vector3();
  if (fly.keys.has("w")) v.add(f);
  if (fly.keys.has("s")) v.sub(f);
  if (fly.keys.has("d")) v.add(r);
  if (fly.keys.has("a")) v.sub(r);
  if (fly.keys.has("e")) v.y += 1;
  if (fly.keys.has("q")) v.y -= 1;
  if (!v.lengthSq()) return;
  // speed 1 crosses the whole world in about a minute; the wheel takes it from a walk to a jet
  const world = state.box && !state.box.isEmpty() ? state.box.getSize(new THREE.Vector3()).length() : 20000;
  orbit.target.addScaledVector(v.normalize(), (world / 60) * fly.speed * (fly.shift ? 3 : 1) * dt);
  placeCamera();
}
function showFlySpeed() {
  const el = $("zoomPct");
  if (el) el.textContent = `Fly ×${fly.speed < 1 ? fly.speed.toFixed(2) : fly.speed.toFixed(1)}`;
}
renderer.domElement.addEventListener("contextmenu", (e) => e.preventDefault());
// Pointer tool (like the Workflows canvas): press on a city and drag it to move it; drag empty ground to orbit.
// Hand tool: drag to pan. Always: right drag orbits, Shift / middle drag pans, the wheel zooms.
renderer.domElement.addEventListener("pointerdown", (e) => {
  rt.drag = { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, button: e.button, moved: false };
  renderer.domElement.setPointerCapture(e.pointerId);
  if (e.button === 2) {  // flying: the pointer hides and stays put, as in a game
    fly.on = true; fly.keys.clear(); showFlySpeed();
    renderer.domElement.style.cursor = "none";
    try { renderer.domElement.requestPointerLock?.()?.catch?.(() => {}); } catch { /* no pointer lock here: the hidden cursor is enough */ }
  }
  if (e.button === 0 && state.tool === "area") { rt.drag.drawing = true; note.points = []; addAreaPoint(e); }
  if (e.button === 0 && state.tool === "select" && !e.shiftKey) {
    rt.drag.cityCand = cityUnder(e);  // a plain click picks it, then its blocks; the Pointer never moves a city
    rt.drag.piece = pcgUnder(e);  // ... but a building, slab or prop under it is dragged to a new place
  }
  if (e.button === 0 && state.tool === "city" && !e.shiftKey) {  // Edit city: one of its handles, else the city itself
    const hnd0 = handleUnder(e), hnd = hnd0 && heldElsewhere(hnd0.c) ? null : hnd0, hit = hnd0 ? null : cityUnder(e);
    if (hnd) {  // (as it stands now, for Escape)
      const c = hnd.c;
      hnd.was = { turn_deg: c.turn_deg, spin: c.spin, shape: c.shape && [...c.shape], angle: c.districts?.industrial?.angle,
        districts: c.districts && JSON.parse(JSON.stringify(c.districts)) };
      rt.drag.handle = hnd;
    }
    else if (hit) { rt.drag.cityCand = hit; rt.drag.cityMove = true; }
    if (hnd || hit) renderer.domElement.style.cursor = "grab";
  }
  if (e.button === 0 && state.tool === "lake" && !e.shiftKey) {  // Lake tool: a point of the picked lake
    const pt = lakePointUnder(e), l = pt && lakesOf().find((x) => x.id === pt.id);
    if (l) { rt.drag.lakePt = { lake: l, k: pt.k, was: [...l.pts[pt.k]] }; renderer.domElement.style.cursor = "grab"; }
  }
  if (e.button === 0 && state.tool === "move" && !e.shiftKey) {  // Move tool: a road under the pointer, else the city
    const pt = roadPointUnder(e), hit = pt ? null : cityUnder(e);
    if (pt) rt.drag.ptCand = pt;
    else if (hit) { rt.drag.cityCand = hit; rt.drag.cityMove = true; }
    if (pt || hit) renderer.domElement.style.cursor = "grab";
  }
});
renderer.domElement.addEventListener("pointermove", (e) => {
  if (!rt.drag) { hoverCursor(e); return; }
  const locked = document.pointerLockElement === renderer.domElement;  // a locked pointer reports its travel only
  const dx = locked ? e.movementX : e.clientX - rt.drag.x, dy = locked ? e.movementY : e.clientY - rt.drag.y;
  if (locked || Math.abs(e.clientX - rt.drag.x0) + Math.abs(e.clientY - rt.drag.y0) > 5) rt.drag.moved = true;
  if (rt.drag.handle) { if (rt.drag.moved) dragHandle(e); return; }  // turning or shaping a city: the camera stays
  if (rt.drag.piece) { if (rt.drag.moved) dragPiece(e); return; }  // moving one placed piece: the camera stays
  if (rt.drag.cityCand && rt.drag.cityMove && rt.drag.moved && !rt.drag.city) startCityDrag(rt.drag.cityCand);
  if (rt.drag.city) { dragCity(e); return; }  // moving a city: it follows the pointer, the camera stays
  if (rt.drag.ptCand && rt.drag.moved) { dragRoadPoint(e); return; }  // moving one part of a road
  if (rt.drag.lakePt) { if (rt.drag.moved) dragLakePoint(e); return; }  // moving one point of a lake
  if (rt.drag.drawing) {  // drawing an area: a point every few pixels, no orbit
    if (Math.abs(dx) + Math.abs(dy) > 6) { rt.drag.x = e.clientX; rt.drag.y = e.clientY; addAreaPoint(e); }
    return;
  }
  rt.drag.x = e.clientX; rt.drag.y = e.clientY;
  const pan = rt.drag.button === 1 || (e.shiftKey && rt.drag.button !== 2) || (rt.drag.button === 0 && state.tool === "hand");  // flying: Shift is speed
  if (pan) {
    const s = orbit.dist / 900;
    const right = new THREE.Vector3().subVectors(camera.position, orbit.target).cross(camera.up).normalize();
    const fwd = new THREE.Vector3(Math.sin(orbit.az), 0, Math.cos(orbit.az));
    orbit.target.addScaledVector(right, dx * s).addScaledVector(fwd, -dy * s);
  } else if (rt.drag.button === 2) {
    flyLook(dx, dy);  // right button: look around from where you are
    return;
  } else {
    orbit.az -= dx * 0.005;
    orbit.el = Math.max(0.05, Math.min(1.55, orbit.el + dy * 0.005));
  }
  placeCamera();
  renderZoom();
});
renderer.domElement.addEventListener("pointerup", (e) => {
  renderer.domElement.style.cursor = "";
  if (document.pointerLockElement) document.exitPointerLock();
  if (fly.on) { fly.on = false; fly.keys.clear(); fly.shift = false; renderZoom(); }
  if (rt.drag?.drawing) { rt.drag = null; finishArea(); return; }
  if (rt.drag?.handle) { const d = rt.drag; rt.drag = null; dropHandle(d.handle); return; }
  if (rt.drag?.city) { const d = rt.drag; rt.drag = null; dropCity(d.city); return; }
  if (rt.drag?.ptCand) { const d = rt.drag; rt.drag = null; if (d.ptTo) dropRoadPoint(d); return; }
  if (rt.drag?.lakePt) { const d = rt.drag; rt.drag = null; if (d.lakePt.moved) dropLakePoint(d.lakePt); return; }
  if (rt.drag?.piece?.to) { const d = rt.drag; rt.drag = null; dropPiece(d.piece, e); return; }
  if (rt.drag && !rt.drag.moved && rt.drag.button === 0) {
    if (state.tool === "pin") placePin(e);
    else if (state.tool === "lake") lakeClick(e);
    else if (state.tool === "addcity") addCityAt(e);
    else if (state.tool === "city") clickCity(rt.drag.cityCand, e);
    else if (state.tool === "select" && !pick(e)) clickCity(rt.drag.cityCand, e);
  }
  rt.drag = null;
});
renderer.domElement.addEventListener("wheel", (e) => {
  e.preventDefault();
  if (fly.on) {  // flying: the wheel is the throttle, as in Unreal
    fly.speed = Math.max(0.02, Math.min(20, fly.speed * (e.deltaY > 0 ? 0.8 : 1.25)));
    showFlySpeed();
    return;
  }
  orbit.dist = Math.max(30, Math.min(150000, orbit.dist * (e.deltaY > 0 ? 1.15 : 0.87)));
  placeCamera();
  renderZoom();
  if (state.tool === "city") drawCityMark();  // its handles keep their size on screen
  if (state.tool === "lake") drawLakes();
}, { passive: false });
renderer.domElement.addEventListener("dblclick", () => { if (state.tool === "lake") finishLake(); });
function resize() {
  const w = view.clientWidth || 1, hh = view.clientHeight || 1;
  renderer.setSize(w, hh, false);
  camera.aspect = w / hh;
  camera.updateProjectionMatrix();
  rt.needsDraw = true;
}
new ResizeObserver(resize).observe(view);
const lastCam = new THREE.Matrix4(), _far = new THREE.Vector3();
function cullFar() {  // tiles of small things (addChunks far) past their distance are not drawn
  for (const o of groups.pcg.children) {
    const far = o.userData.far, s = o.boundingSphere;
    if (!far || !s) continue;
    o.visible = camera.position.distanceTo(_far.copy(s.center).applyMatrix4(o.matrixWorld)) - s.radius < far;
  }
}
let shadowsStale = true;
// what a frame costs, in the corner of the view: frames a second while drawing, draw calls, triangles
const perf = Object.assign(document.createElement("div"), { className: "rn-perf" });
perf.style.cssText = "position:absolute;right:8px;bottom:8px;font:11px monospace;opacity:.7;pointer-events:none;color:var(--fg-dim)";
view.appendChild(perf);
let perfN = 0, perfT = 0, perfJs = 0;
// the graphics card the panel draws with: "SwiftShader" / "Basic Render" means no GPU (software WebGL)
const GPU = (() => {
  const gl = renderer.getContext(), ext = gl.getExtension("WEBGL_debug_renderer_info");
  return String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)).replace(/^ANGLE \(|\)$/g, "").slice(0, 60);
})();
function showPerf(now, js) {
  perfN++; perfJs += js;
  if (now - perfT < 500) return;
  const { calls, triangles } = renderer.info.render, c = renderer.domElement;
  perf.textContent = `${Math.round(perfN * 1000 / (now - perfT))} fps · js ${(perfJs / perfN).toFixed(1)} ms · ${calls} calls · `
    + `${(triangles / 1e6).toFixed(2)}M tris · ${c.width}×${c.height} · ${GPU}`;
  perfN = 0; perfJs = 0; perfT = now;
}
function loop(now) {
  flyStep(now || performance.now());
  const gliding = rt.liveReady && stepGhosts();  // teammates' bodies on their way to where they now look
  if (rt.visible && (rt.needsDraw || gliding || shadowsStale)) {
    const t0 = performance.now();
    camera.updateMatrixWorld();
    const moving = !camera.matrixWorld.equals(lastCam);
    lastCam.copy(camera.matrixWorld);
    if (FULL_DPR > 1 && renderer.getPixelRatio() !== (moving ? 1 : FULL_DPR)) renderer.setPixelRatio(moving ? 1 : FULL_DPR);
    if (moving || rt.needsDraw) cullFar();
    if (rt.needsDraw || gliding) shadowsStale = true;  // something may have changed: redraw them once at rest
    renderer.shadowMap.needsUpdate = shadowsStale && !moving;
    if (!moving) shadowsStale = false;
    renderer.render(scene, camera);
    placeLabels();
    if (rt.liveReady) placePeerLabels();
    showPerf(now || performance.now(), performance.now() - t0);
    rt.needsDraw = false;
  }
  requestAnimationFrame(loop);
}
// changes come in smoothly: the new roads and buildings fade in and the city rises out of the ground
function fadeIn(list, ms = 700) {
  const mats = new Set();
  list.forEach((g) => g.traverse((o) => { const m = o.material; (Array.isArray(m) ? m : m ? [m] : []).forEach((x) => mats.add(x)); }));
  const items = [...mats].map((m) => ({ m, t: m.transparent, o: m.opacity ?? 1 }));
  items.forEach(({ m }) => { m.transparent = true; m.opacity = 0; m.needsUpdate = true; });
  const start = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - start) / ms), e = 1 - Math.pow(1 - k, 3);
    items.forEach(({ m, o }) => { m.opacity = o * e; });
    groups.pcg.scale.y = 0.25 + 0.75 * e;
    rt.needsDraw = true;
    if (k < 1) requestAnimationFrame(step);
    else items.forEach(({ m, t, o }) => { m.transparent = t; m.opacity = o; m.needsUpdate = true; });
  };
  requestAnimationFrame(step);
}
// the camera glides to a place instead of jumping
function flyTo(target, dist, ms = 650) {
  const from = orbit.target.clone(), d0 = orbit.dist, start = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - start) / ms), e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    orbit.target.lerpVectors(from, target, e);
    orbit.dist = d0 + (dist - d0) * e;
    placeCamera();
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function frame(box) {
  box = box || state.box;
  if (!box || box.isEmpty()) return;
  flyTo(box.getCenter(new THREE.Vector3()), box.getSize(new THREE.Vector3()).length() * 0.75, state.framed ? 450 : 1);
  setTimeout(renderZoom, 500);
}

export { orbit, placeCamera, fly, FLY_KEYS, orbitOffset, flyLook, flyStep, showFlySpeed, resize, lastCam, _far, cullFar, shadowsStale, perf, perfN, perfT, showPerf, loop, fadeIn, flyTo, frame };
