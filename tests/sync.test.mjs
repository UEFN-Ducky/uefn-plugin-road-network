// node --test tests/sync.test.mjs : a teammates' sync reloads only what changed, so what the person
// has open (world, tab, layer card, selection, camera) is kept.
import { test } from "node:test";
import assert from "node:assert/strict";
import { syncPlan, parseLiveView } from "../ui/sync.js";

test("a teammate's layer edit reloads only the layers of the open world", () => {
  assert.deepEqual(syncPlan(["net.valley.pcg.trees"], "valley"), { nets: false, geometry: false, cities: false, layers: true });
});

test("road edits reload geometry and the preview, never the world list", () => {
  const p = syncPlan(["net.valley.actor.highways.p.0", "net.valley.actor.highways"], "valley");
  assert.equal(p.geometry, true);
  assert.equal(p.layers, true);
  assert.equal(p.nets, false);
});

test("another world's change only refreshes the list", () => {
  assert.deepEqual(syncPlan(["net.city.actor.city-roads.p.3"], "valley"), { nets: false, geometry: false, cities: false, layers: false });
  assert.equal(syncPlan(["net.city"], "valley").nets, true);
});

test("no keys means re-read everything", () => {
  assert.deepEqual(syncPlan(undefined, "valley"), { nets: true, geometry: true, cities: true, layers: true });
});

test("the group's screen opens the world and tab", () => {
  assert.deepEqual(parseLiveView("valley:pcg"), { net: "valley", tab: "pcg" });
  assert.deepEqual(parseLiveView("networks:world"), { net: "", tab: "world" });
});

test("a world's files: a road actor's reloads geometry, a city's the cities; another world's nothing", () => {
  const road = syncPlan(["world/valley/kingsport-roads.json"], "valley");
  assert.deepEqual(road, { nets: false, geometry: true, cities: false, layers: true });
  const city = syncPlan(["world/valley/city/kingsport.json"], "valley");
  assert.deepEqual(city, { nets: false, geometry: false, cities: true, layers: true });
  assert.deepEqual(syncPlan(["world/city/city-roads.json"], "valley"), { nets: false, geometry: false, cities: false, layers: false });
});
