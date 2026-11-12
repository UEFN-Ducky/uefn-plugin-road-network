// The panel's junction drawing against the mesh builder's numbers: node tests/test_junction_panel.mjs
// junctionTris is lifted out of ui/app.js (it needs the panel's THREE scene otherwise); toV is the identity
// here, so positions come back in UE cm. The expected values are the ones tests/test_junction_geometry.py
// checks on listener/road_io._junction_geometry: the two must draw the same junction.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("../ui/app.js", import.meta.url), "utf8");
const from = src.indexOf("const WALK_OF = "), to = src.indexOf("// citygen.outline()");
assert.ok(from > 0 && to > from, "junctionTris is in app.js");
const MAT = { asphalt: "asphalt", sidewalk: "sidewalk" };
const toV = (x, y, z) => ({ x, y, z });
const junctionTris = new Function("MAT", "toV", `${src.slice(from, to)}; return junctionTris;`)(MAT, toV);

const tris = (arr) => { const out = []; for (let i = 0; i < arr.length; i += 9) out.push([0, 3, 6].map((k) => arr.slice(i + k, i + k + 3))); return out; };
// an arm is its mouth point: [i, x, y, z, , , half width]; the deck reaches exactly to it
const arm = (deg, half, dist = 1175) => [0, Math.cos(deg * Math.PI / 180) * dist, Math.sin(deg * Math.PI / 180) * dist, 0, 0, 0, half];

// two streets with 3.5 m sidewalks crossing, mouths where the mesh puts them: 875 / tan(45) + 300 = 1175
const by = {};
junctionTris([0, 0, 0, 0], [0, 90, 180, 270].map((d) => arm(d, 875)), true, by);
const walk = tris(by.sidewalk), deck = tris(by.asphalt), paint = tris(by.fg);
assert.equal(walk.length, 4 * 6 * 2, "6 paving quads a corner");
const atMouth = [...new Set(walk.flat().filter(([x]) => Math.abs(x - 1175) < 1).map(([, y]) => Math.round(Math.abs(y))))].sort((a, b) => a - b);
assert.deepEqual(atMouth, [525, 875], "the paving starts where the road's own sidewalk runs");
assert.ok(walk.flat().every(([, , z]) => z > deck[0][0][2]), "sidewalks stand above the deck");
assert.ok(Math.max(...deck.flat().map(([x]) => x)) > 1174, "the deck reaches each mouth");
assert.ok(paint.length > 0 && paint.flat().every(([x, y]) => Math.min(Math.abs(x), Math.abs(y)) <= 525 - 60 + 1), "crossings stay on the carriageway");

// a boulevard and a lane with no sidewalks: no paving, each mouth as wide as its own road
const bare = {};
junctionTris([0, 0, 0, 0], [arm(0, 1650, 1950), arm(90, 525, 1950), arm(180, 1650, 1950), arm(270, 525, 1950)], true, bare);
assert.equal(bare.sidewalk, undefined, "roads with no sidewalk: a junction with none");
const east = tris(bare.asphalt).flat().filter(([x, y]) => x > 800 && Math.abs(Math.abs(y) - 1650) < 1);
assert.ok(east.length, "the boulevard's mouth is boulevard wide");
// a road that ends 15 m out (before any push) is met there: the deck never stops short of it
const far = {};
junctionTris([0, 0, 0, 0], [0, 90, 180, 270].map((d) => arm(d, 875, 1500)), true, far);
assert.ok(Math.abs(Math.max(...tris(far.asphalt).flat().map(([x]) => x)) - 1500) < 1, "the deck reaches the road's end");

// an elbow and an airport junction get no paint
const elbow = {};
junctionTris([0, 0, 0, 0], [arm(0, 525), arm(90, 525)], true, elbow);
const air = {};
junctionTris([0, 0, 0, 0], [arm(0, 900), arm(90, 900), arm(180, 900)], false, air);
assert.equal(elbow.fg, undefined);
assert.equal(air.fg, undefined);

// ramp ends (backend/ramp_joins): a merge lane as wide as its rows say, a street mouth that reaches the carriageway
const j0 = src.indexOf("function joinSkips("), j1 = src.indexOf("function drawProfile(");
assert.ok(j0 > 0 && j1 > j0, "drawJoins is in app.js");
const M2 = { asphalt: "asphalt", sidewalk: "sidewalk", barrier: "barrier", paint: "paint" };
const [joinSkips, drawJoins] = new Function("MAT", "toV", `${src.slice(j0, j1)}; return [joinSkips, drawJoins];`)(M2, toV);
const joins = { gores: [{ deck: true, rows: [[-600, 1650, 0, 1, 1600, 750], [0, 1650, 0, 1, 1600, 750], [600, 1650, 0, 1, 800, 750], [1200, 1650, 0, 1, 0, 750]] }],
  flares: [[0, 0, 1, 0, 0, 1, 1450, 600, 350, 0, 0]], done: [] };
const jb = {};
drawJoins(joins, (k) => jb[k] ||= []);
const lane = tris(jb.asphalt).flat();
assert.equal(Math.max(...lane.filter(([x, , z]) => z > 700).map(([, y]) => y)), 3250, "the merge lane starts as wide as the ramp's outer edge");
assert.equal(Math.max(...lane.filter(([, , z]) => z < 100).map(([x]) => x)), 1450, "the mouth reaches the street's carriageway");
assert.ok(tris(jb.sidewalk).length > 0 && jb.paint.length > 0, "kerb corners and a stop line");
const skips = joinSkips(joins);
assert.equal(skips("parapet", "SM_Elev_Deck20m")(300, 1650), true, "the freeway's parapet opens along the merge lane");
assert.equal(skips("parapet", "SM_Elev_Deck20m")(9000, 1650), false);
assert.equal(skips("sidewalk", "x")(1300, 0), true, "the street's sidewalk is cut across the mouth");
assert.equal(skips("sidewalk", "x")(1300, 3000), false);
assert.equal(skips("road", "x"), null);
console.log("panel junction ok:", deck.length, "deck tris,", walk.length, "paving tris,", paint.length / 2, "painted bars");
