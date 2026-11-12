---
name: road-network
description: "City Builder (plugin id road-network): generate worlds with cities, terrain and roads, lay out every city layer (buildings, street furniture, foliage) as PCG with a 3D preview, build modular or kit buildings, push it all into UEFN; road_network_* tools."
---

# City Builder

You're the hands; the person is the director. City Builder already knows how a city is laid out (roads →
blocks → lots → buildings, zoning, interchanges, services). Don't invent your own layout: change the plan's
settings and let the plugin regenerate. Everything it makes is plain data the person can open and edit too.
The person sees **City Builder**; the ids stay `road-network` / `road_network_*`.

## Start here
Call `road_network_describe` (with the world's id once you have one). It lists what exists right now: worlds,
cities, layers, roles, forms, build modes, grammar styles and pipeline stages. Take names from it, not from
memory or from this file.

**Building a world in UEFN starts with `road_network_build_brief(net_id, check=true)`.** It returns:
- the pieces the world needs and which are missing from the project;
- which tools are around (UEFN, Epic MCP, Blender, AI 3D);
- what is waiting to be built;
- the `questions` to ask the person.

Tell them in a few lines, ask every question in **one** `ducky_ask_user`, and wait. Then follow the answers; the
brief's `how` maps each answer to a tool. No editor work before the answers.

## Doors open, everything collides
Two rules every build keeps (the brief's `doors` and `collision` say what this world needs):
- **Doors are Scene Graph entities that open.** House, garage, shop, office, revolving, grand, industrial and
  roller doors: one Entity Prefab per kind (the door leaf + a component that opens it on interact), never a
  fixed mesh. The door's wall module is an open doorway (frame only). **PCG places them**, where the building
  grammar put each door module and with its exact transform. If this UEFN's PCG spawn step can't take an Entity
  Prefab, PCG still decides where: place the prefabs at that step's points with the Scene Graph tools (the
  `scenegraph` skill), never at hand-picked spots, and tell the person.
- **Everything put into UEFN has collision.** Buildings, walls, floors, stairs, roofs, road and bridge pieces,
  props, rocks and tree trunks block players. Only grass, leaves, bushes and flat paint have none. Simple
  collision (boxes, convex hulls, UCX from Blender) by default; complex as simple only where boxes would be wrong
  (stairs, ramps, arches, doorways, curved decks), or everywhere if the person chose exact. A park or parking lot
  collides by its parts (trees, benches, kerbs, lamps), never one box over the whole site: that would be an
  invisible wall. Set it on each mesh before the PCG build, **tell the person which pieces got simple, complex as
  simple or none**, and playtest that nothing can be walked through.

## Gotchas worth knowing
- **One editor call is one frame.** UEFN frees its GPU windows only when a frame ends, so many mesh builds or
  saves in one call has crashed it. Build one mesh per call, save everything in one batch at the end.
- **Kit materials can't go on instanced meshes.** Let the plugin make its world-aligned instances before a PCG
  build. Never re-material a kit mesh itself (a full rebuild, gigabytes each).
- **Never write the terrain.** Roads are live landscape splines that shape the ground through the Splines edit
  layer. No heightmap edits, no bakes.
- **Python can't read spline points.** Hand-edited roads come back as copied spline text.
- **Stretched buildings smear.** One kit mesh scaled to a lot distorts its windows. Layers whose role has a
  modular style can build from BuildingKit storeys and wall modules instead (a layer setting).
- **Modular buildings have one facing to check.** Walls face outward on their −Y side in Blender. If the first
  modular building in UEFN comes out inside out, the FBX export flipped Y. There's one switch for that in the
  grammar module; fix it there, never per building.
- **Overlapping roads flicker.** Junctions are real meshes 2 cm above the deck. Don't stack road tiles at a
  crossing the way Epic's City Sample does.
- **Straight meshes on curves leave gaps.** Anything continuous along a bend (kerbs, walls, barriers) wants a
  spline mesh.
- **UEFN's PCG has traps.** The spline primitive returns nothing on landscape splines (use a plain Spline
  Sampler), and a 2D polygon used as a bounding shape gives 0 points. The `pcg` skill lists the tested ones.
- **Memory.** Read the memory report after every big step. If one call grew UEFN by gigabytes, stop and fix
  that step; restarting UEFN is the person's call.

## Check your work
A step isn't done until you've looked at the result. Preview a layer and compare its counts. Run the road
check before a reimport. Look at the pipeline status after a regenerate. Take a screenshot after a build in
UEFN. A blown-out or empty screenshot is a failure, not a pass.

## Examples
- The `full_city` preset's layers are worked examples of every building and street layer. Read one before
  writing a new layer.
- Any layer already saved on a world is a better example than a new guess. Copy its shape.
- The piece manifest is the spec for every mesh, including modules still wanted. Model in Blender from it.

## Going deeper
- `references/systems.md`: every system in detail (pipeline, parcels, neighbourhoods, freeways, urban planner,
  waterfront, shipyards, PCG tiles, push/pull).
- `references/pieces.md`: exact sizes, cross-sections and materials of every piece, and the checklist for a new
  one.
- `references/city-method.md`: how procedural cities are built in PCG (Epic's City Sample method, Polygon 2D,
  shape grammar), and how City Builder maps onto it.

## Lessons
When the person corrects you, or you find the plugin did something surprising, add one line here: the
situation and what to do. Keep it to what you couldn't have known.
- The person wants cities realistic up close: tree pits in paving, real stairs, no smeared facades.
- Every planned facility must look like what it is (city hall, jail, power plant...). If one comes out generic,
  its type was lost on the way to the bake: check the lot still carries it, don't add a mesh by hand.
- Check a new building type the way the person can see it: assemble it in Blender from the plugin's own bake
  (a cutaway with the roof off) before calling it done. That's how the stairs poking through roofs were found.
- Big editor jobs go in slices of ~10 per call and pick assets from the asset registry, never `load_asset` on a
  whole folder: kit_materials loading WorldGen/Kit's 256 leftover materials took UEFN past 100 GB and crashed it.
- FBX from BuildingKit.blend: rotate −90° about Z, forward X / up Z, FBX_SCALE_NONE, and save the imported
  materials at once (a crash before the save leaves every slot empty).
- `road_network_pcg_build(confirm=true)` runs in the background, a batch of tiles per editor script: call it again
  for progress (done / total), `stop=true` to stop. Trees, bushes and rocks sit in their own 1.6 km `n` tiles, so a
  nature setting never rebuilds buildings.
- PCG's Static Mesh Spawner places instances with NO collision unless its descriptor says so (`bodyInstance`:
  `collisionProfileName` AND `collisionEnabled`; the name alone does nothing). The build sets it per mesh and its
  result has `collision` (what blocks, what doesn't, what was made exact): read it out to the person, then count
  the components' collision in the level. Saying "collision is set" without that count was wrong once already.
- Deleting an asset costs UEFN ~6 s each. A full rebuild writes graphs to a fresh `CityPCG_G<n>` folder and deletes
  the old folder in one call (1.4 s a tile, not 7).
- Never trust a stored actor path to remove something from the level: it goes stale when UEFN reloads. Find the
  actor by its label in the level, remove that, and count what is left before saying it is gone.
- A piece taller than a storey, or wider than a roof cell, shows up as junk on the roof (machines through
  ceilings, solar arrays overlapping). Keep new interior and roof pieces inside those sizes.
