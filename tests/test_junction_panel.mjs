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
const arm = (deg, half) => [0, Math.cos(deg * Math.PI / 180) * 5000, Math.sin(deg * Math.PI / 180) * 5000, 0, 0, 0, half];

// two streets with 3.5 m sidewalks crossing: mouths at 875 / tan(45) + 300 = 1175, as the mesh
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
junctionTris([0, 0, 0, 0], [arm(0, 1650), arm(90, 525), arm(180, 1650), arm(270, 525)], true, bare);
assert.equal(bare.sidewalk, undefined, "roads with no sidewalk: a junction with none");
const east = tris(bare.asphalt).flat().filter(([x, y]) => x > 800 && Math.abs(Math.abs(y) - 1650) < 1);
assert.ok(east.length, "the boulevard's mouth is boulevard wide");

// an elbow and an airport junction get no paint
const elbow = {};
junctionTris([0, 0, 0, 0], [arm(0, 525), arm(90, 525)], true, elbow);
const air = {};
junctionTris([0, 0, 0, 0], [arm(0, 900), arm(90, 900), arm(180, 900)], false, air);
assert.equal(elbow.fg, undefined);
assert.equal(air.fg, undefined);
console.log("panel junction ok:", deck.length, "deck tris,", walk.length, "paving tris,", paint.length / 2, "painted bars");
