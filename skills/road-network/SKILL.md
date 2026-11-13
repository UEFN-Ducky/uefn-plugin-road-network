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

## Changing one city, one block, or the whole look
- One city: `road_network_city_save` moves it (x, y), turns it (`turn_deg`), draws its outline (`shape`) and
  gives one block a type (`zones`: its layout, its zone, the share of its lots left green). Then generate again
  with cities empty. The person does the same on the map with the Edit city tool.
- Street look for the world: options `wobble` (0 = dead straight, higher = smooth curves), `block_ratio` (square
  to long blocks), `green_lots`.
- A look worth keeping: `road_network_preset_save` (layers + options under a name), `road_network_pcg_preset` to
  use it on any world. `references/systems.md` has every field.
- An edit to one city rebuilds that city only: every other building and tree stays where it was. A city moved
  by hand stays where it was put, even against or over another city: the bigger one keeps the shared land, and
  cities closer than 600 m are joined by streets with houses along them.

## Working beside people
A city someone is working on is theirs: `road_network_holds(net_id)` lists who has which city. A change to a held
city is refused and says whose it is; pick another city or tell the person, never retry in a loop. Your own change
holds the city for two minutes so nobody edits under you; `road_network_release` when you are done sooner.

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
- What to build comes from the plugin, never from the chat: `road_network_build_brief(net_id)` lists every mesh
  the world uses with its `specs` (size, `bounds_cm` it must measure after import, `sidewalk_cm`, slots), the
  `rules` (one mesh per road with its kerbs and sidewalks, axes, pivot, import check), the `junctions` by type
  (City Builder builds those itself) and `road_fit`. Every city street has sidewalks in its own mesh: a district
  set to 2L / 1W / 6L is built as 2S / 1S / 6S (`citygen.CITY_STREET`).
- The ground is the plugin's: roads cut in first, lots levelled round them, re-cut on every change.
  `road_network_ground_fit(net_id)` must show 0 buried / floating buildings and lists the street points still off.
- Before any push: `road_network_pcg_exact(net_id, x, y, radius_m)` is what the push will place there (each
  piece's real box, the ground as the landscape has it). `ground_fit.buried` / `floating` must be 0 and `unknown`
  empty. The panel's "Real pieces here" draws the same thing. A world laid over another's landscape is fitted to
  the scanned ground; once `road_network_heightmap` is made for it, to that heightmap (import it, then push).
- A BuildingKit mesh in UEFN must measure `grammar.BOUNDS` (walls 0..512 on X, roof slab 0..512 on X and Y). An
  FBX import that mirrors Y puts roofs beside their buildings: fix the export, never the placement.
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
- The person wants no loose upright wall slabs anywhere. What holds a piece up is part of the piece: roads carry
  3 m side walls, every building its own foundation, a deck too low for piers its approach wall. An uphill cut is
  graded ground. Never add a layer of wall boxes to hide a step: fix the piece or the ground.
- How deep foundations come out is set by the landscape's pixel (about 10 m), not by the pad rule: levelling lots
  on the road-graded ground or at street level was measured and changed nothing. Don't retry those. The person
  chose the rule instead: nothing is built on a foundation deeper than 4 m, the lot is planted (planned sites
  and parks stay). A finer heightmap is the other lever and is still their call.
- Whole blocks turned into pits after a regenerate (Nov 12 2026): the preview cut the new ground to the LAST
  world's private streets, read from the stored '<City> Access' actors before the sidewalk stage had rewritten
  them, and kept that result (its key never saw those actors). The ground now takes private streets from each
  city's plan. Anything a preview needs must come from the plan or from actors `generate` has already written:
  never from Paths / Access actors, which are always one step behind inside a generate.
- The person wants the land left alone when a city moves: a city adapts to the terrain, the terrain is not
  remade for the city. 'Ground under the cities: Natural' never changes the land; 'Flatten' levels the land under a
  city but never its lakes or sea (the plateau comes up from the shore). Never fill or move water for a city.
- The person does not want a chequerboard (Nov 12 2026): main roads wander, and past downtown many streets
  between two neighbouring blocks are simply not built, so subdivisions lie side by side (in a Grid city too:
  only its streets stay ruled). Never bring back a ring of roads round every block to make a layout easier.
- "Nothing is generated here" has four different causes; find which before changing anything: land no street
  rings (it gets lots along the street's open side), the middle of a block of plain lots (planted), a lot no
  layer fits (planted; downtown and industrial lots are built by the tower, office and warehouse layers), a keep
  zone (left alone on purpose). `road_network_pcg_preview` summary `swept` lists what was taken off the roads.
- A forest layer covers the whole map. `reach` is how far from a city it is as dense as asked, not where it stops.
