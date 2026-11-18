import test from "node:test";
import assert from "node:assert/strict";
import { marchHeightfield } from "../ui/ground.js";

const field = (f, n = 64) => ({ grid: n, min: [-100000, -100000], max: [100000, 100000],
  heights: Array.from({ length: n }, (_, j) => Array.from({ length: n }, (_, i) => f(i, j))) });

test("a ray straight down lands on flat ground at its height", () => {
  const s = marchHeightfield([0, 500, 0], [0, -1, 0], field(() => 2000));  // ground 20 m up
  assert.ok(Math.abs(s - 480) < 0.01, s);
});

test("a slanted ray finds a slope where it first meets it", () => {
  // heights rise along UE x (three -z): 10 m per cell, linear in i
  const t = field((i) => i * 1000);
  const d = [0, -Math.SQRT1_2, -Math.SQRT1_2];
  const s = marchHeightfield([0, 400, 0], d, t);  // (from 400 m it meets the slope, not the clamped edge)
  assert.ok(s !== null);
  const p = [0, 400 + d[1] * s, d[2] * s];
  // the hit is on the surface: its height matches the field (cell centres every 3125 cm, 1000 cm apart)
  const ux = -p[2] * 100, fx = (ux + 100000) / 200000 * 64 - 0.5;
  assert.ok(Math.abs(p[1] - fx * 1000 / 100) < 0.05, `${p[1]} vs ${fx * 10}`);
});

test("a ray into the sky finds nothing; no terrain is null", () => {
  assert.equal(marchHeightfield([0, 100, 0], [0, 1, 0], field(() => 0)), null);
  assert.equal(marchHeightfield([0, 100, 0], [0, -1, 0], { grid: 0, heights: [] }), null);
});
