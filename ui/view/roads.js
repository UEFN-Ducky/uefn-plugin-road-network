import { MAT, PAINT_PERIOD, SLOT_MAT, toV } from "../core.js";

function roadMaterial(mesh) {
  if (/Path_Sidewalk/.test(mesh)) return [MAT.sidewalk, "Sidewalk"];
  if (/Path_Walkway/.test(mesh)) return [MAT.pavers, "Walkway"];
  if (/Path_Driveway/.test(mesh)) return [MAT.drive, "Driveway"];
  if (/Path_PrivateStreet/.test(mesh)) return [MAT.asphalt, "Private street"];
  return [MAT.asphalt, /Bridge|Elev_/.test(mesh) ? "Bridge / deck" : "Road"];
}
// a band along a curve between lateral offsets off-half..off+half at z0 (z1 === z0), or a wall at `off` from z0
// up to z1; dash = [on, off] cm paints it dashed
function stripe(curve, off, half, z0, z1, dash, out) {
  let s = 0;
  for (let k = 0; k + 1 < curve.length; k++) {
    const a = curve[k], b = curve[k + 1], dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1;
    const nx = -dy / l, ny = dx / l, spans = [];
    if (!dash) spans.push([0, l]);
    else {
      const P = dash[0] + dash[1];
      for (let m = Math.floor(s / P) * P; m < s + l; m += P) {
        const d0 = Math.max(m, s) - s, d1 = Math.min(m + dash[0], s + l) - s;
        if (d1 > d0) spans.push([d0, d1]);
      }
    }
    const at = (d, side, z) => { const t = d / l; return toV(a[0] + dx * t + nx * side, a[1] + dy * t + ny * side, a[2] + (b[2] - a[2]) * t + z); };
    for (const [d0, d1] of spans) {
      const q = z1 === z0 ? [at(d0, off - half, z0), at(d0, off + half, z0), at(d1, off + half, z0), at(d1, off - half, z0)]
        : [at(d0, off, z0), at(d0, off, z1), at(d1, off, z1), at(d1, off, z0)];
      for (const v of [q[0], q[1], q[2], q[0], q[2], q[3]]) out.push(v.x, v.y, v.z);
    }
    s += l;
  }
}
// a road piece drawn from its cross-section (backend/road_profiles: the same one its UEFN mesh is built from):
// every part's top and both its faces, the deck's underside, the paint 1 cm proud
// a piece on one point (road_profiles.CONNECTORS): its top-view outline turned to the point's yaw, the slab's
// sides down to its bottom, the paint 1 cm up; 2 cm over the road it lies on so the two never flicker
function drawPlan(p, plan, put) {
  const yaw = (p[5] || 0) * Math.PI / 180, c = Math.cos(yaw), s = Math.sin(yaw), LIFT = 62;
  const at = ([x, y], z) => toV(p[1] + c * x - s * y, p[2] + s * x + c * y, p[3] + z);
  const fan = (poly, z, out) => {
    for (let i = 1; i + 1 < poly.length; i++) for (const q of [poly[0], poly[i], poly[i + 1]]) { const v = at(q, z); out.push(v.x, v.y, v.z); }
  };
  const top = put(MAT.asphalt), o = plan.outline;
  fan(o, LIFT, top);
  o.forEach((q, i) => {
    const r = o[(i + 1) % o.length], lo = LIFT + plan.slab;
    for (const v of [at(q, lo), at(r, lo), at(r, LIFT), at(q, lo), at(r, LIFT), at(q, LIFT)]) top.push(v.x, v.y, v.z);
  });
  for (const [poly, slot] of plan.paint) fan(poly, LIFT + 1, put(SLOT_MAT[slot] || MAT.paint));
}
// one face of a swept cross-section: the edge (ya, za) -> (yb, zb) carried along the curve
function sweepEdge(curve, ya, za, yb, zb, out, skip) {
  for (let k = 0; k + 1 < curve.length; k++) {
    const a = curve[k], b = curve[k + 1], dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1;
    const nx = -dy / l, ny = dx / l;
    if (skip && skip((a[0] + b[0]) / 2 + nx * skip.y, (a[1] + b[1]) / 2 + ny * skip.y)) continue;
    const at = (p, y, z) => toV(p[0] + nx * y, p[1] + ny * y, p[2] + z);
    const q = [at(a, ya, za), at(a, yb, zb), at(b, yb, zb), at(b, ya, za)];
    for (const v of [q[0], q[1], q[2], q[0], q[2], q[3]]) out.push(v.x, v.y, v.z);
  }
}
// What the ramp ends open in the roads they meet (g.joins, backend/ramp_joins): a freeway's parapet along a merge
// lane, a street's sidewalk across a ramp's mouth. Returns part name -> (x, y) => true where that part is left out.
function joinSkips(joins) {
  if (!joins) return () => null;
  // ponytail: every gap point against every parapet strip (a few million distance checks a rebuild); a grid if
  // worlds grow past a few hundred ramps
  const gaps = joins.gores.flatMap((g) => g.rows.map((r) => [r[0], r[1]]));
  const parapet = (x, y) => gaps.some((g) => (x - g[0]) ** 2 + (y - g[1]) ** 2 < 420 ** 2);
  const sidewalk = (x, y) => joins.flares.some(([ex0, ey0, ux, uy, tx, ty, D, r, walk, , , A = 650]) => {
    const lx = (x - ex0) * ux + (y - ey0) * uy, ly = (x - ex0) * tx + (y - ey0) * ty;
    return walk > 0 && lx >= D - walk - 60 && lx <= D + 60 && Math.abs(ly) < A + r + 200;
  });
  return (part, mesh) => part === "parapet" && /Freeway_Lane|Elev_Deck/.test(mesh) ? parapet : part === "sidewalk" ? sidewalk : null;
}
// the ramp ends themselves: merge lanes and street mouths, built from g.joins the way the Blender models are
function drawJoins(joins, put) {
  const LIFT = 60, tri = (out, ...vs) => { for (const v of vs) out.push(v.x, v.y, v.z); };
  const quad = (out, a, b, c, d) => tri(out, a, b, c, a, c, d);
  for (const { deck, rows } of joins.gores) {
    const P = (r, k, dz) => toV(r[0] + r[2] * (r[4] - k), r[1] + r[3] * (r[4] - k), r[5] + LIFT + dz);  // k cm in from the lane's outer edge
    const I = (r, dz) => toV(r[0], r[1], r[5] + LIFT + dz);
    const asphalt = put(MAT.asphalt), wall = put(MAT.barrier), paint = put(MAT.paint), depth = deck ? 150 : 30;
    for (let i = 1; i + 1 < rows.length; i++) {
      const a = rows[i], b = rows[i + 1];
      if (a[4] < 2) break;
      quad(asphalt, I(a, 0), P(a, 0, 0), P(b, 0, 0), I(b, 0));
      quad(wall, P(a, 0, -depth), P(a, 0, 0), P(b, 0, 0), P(b, 0, -depth));
      if (deck) quad(wall, I(a, -depth), P(a, 0, -depth), P(b, 0, -depth), I(b, -depth));
      if (deck && Math.min(a[4], b[4]) > 80) {  // the parapet follows the lane's outer edge to the tip
        quad(wall, P(a, 35, 105), P(a, 0, 105), P(b, 0, 105), P(b, 35, 105));
        quad(wall, P(a, 35, 0), P(a, 35, 105), P(b, 35, 105), P(b, 35, 0));
        quad(wall, P(a, 0, 0), P(a, 0, 105), P(b, 0, 105), P(b, 0, 0));
      }
      const k0 = deck ? 80 : 45;
      if (Math.min(a[4], b[4]) > 250) quad(paint, P(a, k0 + 15, 1.5), P(a, k0, 1.5), P(b, k0, 1.5), P(b, k0 + 15, 1.5));
    }
  }
  for (const [ex0, ey0, ux, uy, tx, ty, D, r, walk, z0, z1, A = 650] of joins.flares) {  // A: a ramp's half width, or a private street's
    const W = (x, y, dz = 0) => toV(ex0 + ux * x + tx * y, ey0 + uy * x + ty * y, z0 + (z1 - z0) * Math.max(0, Math.min(1, x / D)) + LIFT + dz);
    const asphalt = put(MAT.asphalt), walkOut = put(MAT.sidewalk);
    quad(asphalt, W(0, -A), W(D, -A), W(D, A), W(0, A));
    quad(put(MAT.paint), W(D - 160, -A + 60, 1.5), W(D - 120, -A + 60, 1.5), W(D - 120, A - 60, 1.5), W(D - 160, A - 60, 1.5));  // stop line
    for (const sy of [-1, 1]) {
      const arc = (t0, n) => Array.from({ length: n + 1 }, (_, k) => { const th = t0 + (Math.PI / 2 - t0) * k / n; return [D - r + r * Math.sin(th), sy * (A + r - r * Math.cos(th))]; });
      const kerb = arc(0, 10);  // the kerb curving out of the ramp into the street
      for (let i = 0; i + 1 < kerb.length; i++) tri(asphalt, W(D, sy * A), W(...kerb[i]), W(...kerb[i + 1]));
      if (!walk) continue;
      const sl = arc(Math.asin(Math.max(0, (r - walk) / r)), 8), pivot = [D - walk, sy * (A + r)];
      for (let i = 0; i + 1 < sl.length; i++) {
        tri(walkOut, W(...pivot, 15), W(...sl[i], 15), W(...sl[i + 1], 15));
        quad(walkOut, W(...sl[i]), W(...sl[i], 15), W(...sl[i + 1], 15), W(...sl[i + 1]));  // the kerb's face
      }
      quad(walkOut, W(D - walk, sy * (A + r), 15), W(D, sy * (A + r), 15), W(D, sy * (A + r + 500), 15), W(D - walk, sy * (A + r + 500), 15));
    }
  }
}
function drawProfile(curve, prof, lift, put, skips) {
  if (prof.shapes) {  // the real cross-sections (Jersey barriers, chamfered kerbs, tapered decks), as modelled
    for (const [part, slot, poly] of prof.shapes) {
      const out = put(SLOT_MAT[slot] || MAT.barrier), skip = skips?.(part, prof.mesh);
      if (skip) skip.y = poly.reduce((s, p) => s + p[0], 0) / poly.length;  // where across the road this part lies
      poly.forEach((p, i) => { const q = poly[(i + 1) % poly.length]; sweepEdge(curve, p[0], lift + p[1], q[0], lift + q[1], out, skip); });
    }
  }
  for (const [name, slot, y0, y1, z0, z1] of prof.shapes ? [] : prof.parts) {
    const out = put(SLOT_MAT[slot] || MAT.barrier), mid = (y0 + y1) / 2, hw = (y1 - y0) / 2;
    stripe(curve, mid, hw, lift + z1, lift + z1, null, out);
    for (const y of [y0, y1]) stripe(curve, y, 0, lift + z0, lift + z1, null, out);
    if (name === "deck") stripe(curve, mid, hw, lift + z0, lift + z0, null, out);
  }
  for (const [y, w, dash, slot] of prof.paint) {
    stripe(curve, y, w / 2, lift + 1, lift + 1, dash ? [dash, PAINT_PERIOD - dash] : null, put(SLOT_MAT[slot]));
  }
}
// ------------------------------------------------------------------ geometry
function fwd(p) { // p = [i,x,y,z,pitch,yaw,w,mesh,raise]
  const cp = Math.cos(p[4] * Math.PI / 180), sp = Math.sin(p[4] * Math.PI / 180);
  const cy = Math.cos(p[5] * Math.PI / 180), sy = Math.sin(p[5] * Math.PI / 180);
  return [cp * cy, cp * sy, sp];
}
function hermite(a, b, ta, tb, n) {
  // start tangent = ta * fwd(a); end tangent = -tb * fwd(b) (UE connection convention)
  const fa = fwd(a), fb = fwd(b), out = [];
  for (let k = 0; k <= n; k++) {
    const t = k / n, t2 = t * t, t3 = t2 * t;
    const h00 = 2 * t3 - 3 * t2 + 1, h10 = t3 - 2 * t2 + t, h01 = -2 * t3 + 3 * t2, h11 = t3 - t2;
    out.push([0, 1, 2].map((i) => h00 * a[1 + i] + h10 * ta * fa[i] + h01 * b[1 + i] + h11 * (-tb) * fb[i]));
  }
  return out;
}
function ribbon(samples, half, lift) {
  const pos = [];
  for (let k = 0; k < samples.length - 1; k++) {
    const a = samples[k], b = samples[k + 1];
    const dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1;
    const nx = -dy / l * half, ny = dx / l * half;
    const q = [[a[0] + nx, a[1] + ny, a[2]], [a[0] - nx, a[1] - ny, a[2]], [b[0] + nx, b[1] + ny, b[2]], [b[0] - nx, b[1] - ny, b[2]]]
      .map((p) => toV(p[0], p[1], p[2] + lift));
    for (const v of [q[0], q[2], q[1], q[1], q[2], q[3]]) pos.push(v.x, v.y, v.z);
  }
  return pos;
}
// road_io._junction_geometry for our flat kit, drawn here exactly as the mesh is built (keep the two in step):
// the deck out to every arm's mouth, rounded corners between arms, a stop line and a zebra crossing on each
// arm of a 3+ way junction, and the sidewalk corners that join the arms' own sidewalks.
const WALK_OF = { 875: 350, 1400: 350, 650: 350, 2000: 350 };  // citygen.WALK by half width: how much of a road's half is sidewalk
function junctionTris(c, list, paint, byColor) {
  const TAU = Math.PI * 2, DEG = Math.PI / 180;
  const arms = list.map((m) => ({ a: (Math.atan2(m[2] - c[2], m[1] - c[1]) + TAU) % TAU, W: Math.max(272.5, m[6] || 500),
    D: Math.hypot(m[1] - c[1], m[2] - c[2]) })).sort((p, q) => p.a - q.a);
  const n = arms.length;
  if (n === 1) {  // a dead end's turnaround (cul-de-sac): a round deck of _BULB_MIN / _BULB_PAST, the road into it
    const W0 = arms[0].W, K0 = Math.max(0, Math.min(W0 - 150, WALK_OF[Math.round(W0)] || 0));
    const rb = Math.max(1000, W0 + 500), u = [Math.cos(arms[0].a), Math.sin(arms[0].a)], v = [-u[1], u[0]];
    const ring = (r) => Array.from({ length: 25 }, (_, i) => [r * Math.cos(i * Math.PI / 12), r * Math.sin(i * Math.PI / 12)]);
    const put1 = (key, z, ...ps) => {
      const arr = byColor[key] ||= [];
      for (const p of ps) { const q = toV(c[1] + p[0], c[2] + p[1], c[3] + z); arr.push(q.x, q.y, q.z); }
    };
    const outer = ring(rb), inner = ring(rb - K0), D = Math.max(arms[0].D, 100);
    for (let i = 0; i < 24; i++) put1(MAT.asphalt, 65, [0, 0], inner[i], inner[i + 1]);
    if (K0 > 0) for (let i = 0; i < 24; i++) put1(MAT.sidewalk, 75, inner[i], inner[i + 1], outer[i + 1], inner[i], outer[i + 1], outer[i]);
    const s = (a, b) => [u[0] * a + v[0] * b, u[1] * a + v[1] * b];
    put1(MAT.asphalt, 65, s(0, -W0), s(D, -W0), s(D, W0), s(0, -W0), s(D, W0), s(0, W0));
    return;
  }
  if (n < 2) return;
  const W = arms.map((r) => r.W), K = W.map((w) => Math.max(0, Math.min(w - 150, WALK_OF[Math.round(w)] || 0)));
  const U = arms.map((r) => [Math.cos(r.a), Math.sin(r.a)]), N = U.map(([x, y]) => [-y, x]);
  const gap = arms.map((r, k) => ((arms[(k + 1) % n].a - r.a + TAU) % TAU) || TAU);
  const need = new Array(n).fill(0);
  gap.forEach((g, k) => {
    const v = g < 179 * DEG ? Math.max(W[k], W[(k + 1) % n]) / Math.tan(g / 2) + 300 : 0;
    need[k] = Math.max(need[k], v);
    need[(k + 1) % n] = Math.max(need[(k + 1) % n], v);
  });
  // each arm's mouth is where its road really ends (the mouth point): the deck reaches exactly that far, so a
  // road never stops short of its junction. (The mesh builder moves the mouths to `need` when it builds.)
  const d = need.map((x, k) => arms[k].D > 100 ? arms[k].D : Math.max(700, Math.min(3000, x)));
  const pt = (k, along, side) => [U[k][0] * along + N[k][0] * side, U[k][1] * along + N[k][1] * side];
  const meet = (k, j, wk, wj) => {
    const p1 = [N[k][0] * wk, N[k][1] * wk], p2 = [-N[j][0] * wj, -N[j][1] * wj];
    const den = U[k][0] * U[j][1] - U[k][1] * U[j][0];
    if (Math.abs(den) < 1e-6) return null;
    const s = ((p2[0] - p1[0]) * U[j][1] - (p2[1] - p1[1]) * U[j][0]) / den;
    return [p1[0] + U[k][0] * s, p1[1] + U[k][1] * s];
  };
  const corner = (k, j, wk, wj) => {  // arm k's left edge round to arm j's right edge: 7 points
    const L = pt(k, d[k], wk), R = pt(j, d[j], -wj), g = gap[k], mid = [(L[0] + R[0]) / 2, (L[1] + R[1]) / 2];
    const square = g > 181 * DEG && n === 2;
    const C = (g < 179 * DEG || square) ? meet(k, j, wk, wj) || mid : mid;
    const chain = [L];
    for (let i = 1; i <= 5; i++) {
      const t = i / 6;
      if (square) {
        const u = Math.min(1, 2 * t), v = Math.max(0, 2 * t - 1);
        chain.push([L[0] + (C[0] - L[0]) * u + (R[0] - C[0]) * v, L[1] + (C[1] - L[1]) * u + (R[1] - C[1]) * v]);
      } else {
        const a = (1 - t) ** 2, b = 2 * (1 - t) * t, cc = t * t;
        chain.push([a * L[0] + b * C[0] + cc * R[0], a * L[1] + b * C[1] + cc * R[1]]);
      }
    }
    chain.push(R);
    return chain;
  };
  const put = (key, z, ...ps) => {
    const arr = byColor[key] ||= [];
    for (const p of ps) { const v = toV(c[1] + p[0], c[2] + p[1], c[3] + z); arr.push(v.x, v.y, v.z); }
  };
  const quad = (key, z, a, b, e, f) => put(key, z, a, b, e, a, e, f);
  const DECK = 65, WALK = 75, PAINT = 68;  // lifts over the ground, as the roads' own deck and sidewalks
  for (let k = 0; k < n; k++) {
    const j = (k + 1) % n, outer = corner(k, j, W[k], W[j]);
    put(MAT.asphalt, DECK, [0, 0], pt(k, d[k], -W[k]), pt(k, d[k], W[k]));
    for (let i = 0; i + 1 < outer.length; i++) put(MAT.asphalt, DECK, [0, 0], outer[i], outer[i + 1]);
    if (K[k] > 0 || K[j] > 0) {
      const inner = corner(k, j, W[k] - K[k], W[j] - K[j]);
      for (let i = 0; i + 1 < outer.length; i++) quad(MAT.sidewalk, WALK, inner[i], inner[i + 1], outer[i + 1], outer[i]);
    }
    const w = W[k] - K[k] - 60;
    if (paint && n >= 3 && w >= 150) {
      const bar = (a0, a1, s0, s1) => quad("fg", PAINT, pt(k, a0, s0), pt(k, a1, s0), pt(k, a1, s1), pt(k, a0, s1));
      bar(d[k] - 70, d[k] - 40, -w, w);  // stop line
      for (let s = -w; s + 50 <= w; s += 110) bar(d[k] - 420, d[k] - 120, s, s + 50);  // zebra
    }
  }
}

export { roadMaterial, stripe, drawPlan, sweepEdge, joinSkips, drawJoins, drawProfile, fwd, hermite, ribbon, WALK_OF, junctionTris };
