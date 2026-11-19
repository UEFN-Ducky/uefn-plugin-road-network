// The Terrain tab against the engine's settings: node tests/test_terrain_ui.mjs
// core.js keeps a copy of backend/terrain/config.py KEYS / CHOICES / PRESETS; this fails when they drift apart.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const read = (p) => readFileSync(new URL(`../${p}`, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const core = read("ui/core.js"), tabs = read("ui/panes/tabs.js"), controls = read("ui/controls.js"), py = read("backend/terrain/config.py");

// ---- the backend, parsed
const block = (src, start) => { const a = src.indexOf(start); assert.ok(a >= 0, start); return src.slice(a, src.indexOf("\n}\n", a) + 2); };
const KEYS = {};
for (const [, k, d, lo, hi] of block(py, "KEYS = {").matchAll(/'(\w+)': \(([-\d.]+), ([-\d.]+), ([-\d.]+)\)/g)) KEYS[k] = [+d, +lo, +hi];
const CHOICES = {};
for (const [, k, d, allowed] of block(py, "CHOICES = {").matchAll(/'(\w+)': \('(\w+)', \(([^)]*)\)\)/g))
  CHOICES[k] = [d, [...allowed.matchAll(/'(\w+)'/g)].map((x) => x[1])];
const PRESETS = JSON.parse(block(py, "PRESETS = {").slice("PRESETS = ".length)
  .replace(/#.*$/gm, "").replace(/'/g, '"').replace(/\bTrue\b/g, "true").replace(/\bFalse\b/g, "false").replace(/,(\s*})/g, "$1"));
assert.ok(Object.keys(KEYS).length >= 15 && CHOICES.coast && Object.keys(PRESETS).length === 11, "config.py parsed");

// ---- the panel's copy (the block of core.js from TERRAIN_KEYS to OPTION_KEYS)
const from = core.indexOf("const TERRAIN_KEYS"), to = core.indexOf("const VIEW_DEFAULTS");
assert.ok(from > 0 && to > from, "terrain block in core.js");
const ui = new Function(`${core.slice(from, to)}; return { TERRAIN_KEYS, TERRAIN_CHOICES, TERRAIN_PRESETS, presetValues, GEN_DEFAULTS, OPTION_KEYS };`)();

assert.deepEqual(ui.TERRAIN_KEYS, KEYS, "TERRAIN_KEYS = config.KEYS");
assert.deepEqual(ui.TERRAIN_CHOICES, CHOICES, "TERRAIN_CHOICES = config.CHOICES");
assert.deepEqual(ui.TERRAIN_PRESETS, PRESETS, "TERRAIN_PRESETS = config.PRESETS");
for (const [name, p] of Object.entries(PRESETS))
  for (const k of Object.keys(p)) assert.ok(k in KEYS || k in CHOICES || k === "classic", `${name}.${k} is a config key`);

// every option the panel sends has a default, every engine key it has is in range
for (const k of ui.OPTION_KEYS) if (k !== "interchange_models") assert.ok(k in ui.GEN_DEFAULTS, `${k} has a default`);
assert.equal(new Set(ui.OPTION_KEYS).size, ui.OPTION_KEYS.length, "OPTION_KEYS has no repeats");
const ENGINE = ["preset", "mountain_m", "mountain_cover", "mountain_intensity", "flatland", "hills", "plateaus", "coast", "coast_side_deg",
  "erosion", "rivers", "lakes", "temp_c", "rain", "snowline_m"];
for (const k of ENGINE) assert.ok(ui.OPTION_KEYS.includes(k) && k in ui.GEN_DEFAULTS, `${k} is sent`);
assert.ok(ui.GEN_DEFAULTS.preset in PRESETS, "default preset exists");

// the defaults are what choosing that preset fills in; a preset fills valid values
const coastal = ui.presetValues(ui.GEN_DEFAULTS.preset);
for (const [k, v] of Object.entries(coastal)) assert.equal(ui.GEN_DEFAULTS[k], v, `GEN_DEFAULTS.${k} = ${ui.GEN_DEFAULTS.preset} preset`);
for (const name of Object.keys(PRESETS)) {
  for (const [k, v] of Object.entries(ui.presetValues(name))) {
    if (KEYS[k]) assert.ok(v >= KEYS[k][1] && v <= KEYS[k][2], `${name}.${k} in range`);
    else assert.ok(CHOICES[k][1].includes(v), `${name}.${k} allowed`);
  }
}
assert.equal(ui.presetValues("Alpine").snowline_m, 1400);
assert.equal(ui.presetValues("Alpine").rivers, KEYS.rivers[0], "keys a preset leaves out go back to the default");
assert.deepEqual(ui.presetValues("Classic"), {}, "Classic leaves the sliders alone");
assert.ok(!("detail" in ui.presetValues("Mixed")), "the old generator's detail is not overwritten");

// the picker lists every preset once, with an icon controls.js has
const opts = [...tabs.slice(tabs.indexOf("const PRESET_OPTIONS"), tabs.indexOf("];", tabs.indexOf("const PRESET_OPTIONS")))
  .matchAll(/\["(\w+)", "[^"]+", "[^"]*", "(\w+)"\]/g)];
assert.deepEqual(opts.map((o) => o[1]).sort(), Object.keys(PRESETS).sort(), "PRESET_OPTIONS = PRESETS");
for (const [, name, ic] of opts) assert.ok(new RegExp(`\\n  ${ic}: "`).test(controls), `${name} icon ${ic} exists`);
// every engine slider is on the Terrain tab (Ground height stays on World)
for (const k of ENGINE.filter((k) => k !== "preset" && k !== "coast")) assert.ok(tabs.includes(`sl("${k}"`), `slider ${k}`);
assert.ok(tabs.indexOf(`sl("relief_m"`) < tabs.indexOf("// ---- Terrain"), "Ground height on World");

console.log("terrain ui ok");
