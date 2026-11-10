---
name: road-network
description: "City Builder (plugin id road-network): generate worlds with cities, terrain and roads, lay out every city layer (buildings, street lights, signs, furniture, foliage) as PCG with a 3D preview, choose own or Fortnite pieces, build it in UEFN; road_network_* tools."
---

# City Builder

The plugin's id and tool names stay `road-network` / `road_network_*` (renaming them would break
saved data and the Store listing); everything the person sees says **City Builder**.

## Pieces: own kit or Fortnite

`road_network_pcg_assets(net_id, asset_set)` = `own` (the project's WorldGen/City SM_City_*, WorldGen/Kit, and
the BuildingKit modular storeys from Blender: storey 3.84 m, Tower / Shop / House / Mansion / Modern / Mall / Sky
styles), `fortnite` (Fortnite's own props and buildings, found by search words per role) or `mixed`. Every layer
has a `role` (traffic_light, stop_sign, bench, skyscraper, factory, park...); the plan's `pieces[]` says where
each role's pieces come from. The AI building it picks real assets and saves them on the layer.

## A realistic city (how the layout is made)

- **Transect zoning** (urban-design skill): core (short-block strict grid, 4-lane), neighbourhoods (looser grid, streets
  with pavements), suburbs (curving streets, cul-de-sacs, 2-lane), one industrial wedge per city, countryside outside.
- **Irregular edges:** `citygen.outline(city, angle)` bends the city edge and every district ring (never a circle);
  the panel, preview and plan polygons all use it.
- **Skyline:** building heights fall off from the core outward and two neighbours within 25 m never differ by less
  than 18 % of the layer's range; give each building layer several meshes of different heights.
- **One click:** `road_network_pcg_preset(net_id, "full_city")` = 31 layers: 16 building types, 12 street layers
  (traffic lights / stop signs / street signs on junction corners via kind `at_junctions`, lamps, street trees, bus
  stops, benches, bins, hydrants, billboards, power poles) and 3 foliage layers. **Foliage** (trees, rocks, bushes) is
  never PCG and there is no grass: the plan's `foliage[]` goes to UEFN Foliage mode (Foliage Types, painted by mouse).

## Road types, bridges and interchanges

Our road kit (NeonSkyscrapers.blend RoadKit, in UEFN under `WorldGen/Roads`, elevated pieces in `Roads/Elevated`):
`1W` OneLane (one-way), `DT` Dirt, `2L` TwoLane (no sidewalk), `2S` TwoLaneSidewalk, `4L` FourLane (sidewalks),
`6L` Freeway_Lane20m (six lanes), `BR`/`BRR` Bridge deck / ramp, `ED`/`ER` elevated deck / ramp, point pieces
`SM_Road_Loop_Big` (trumpet) and `SM_Road_Freeway_Cloverleaf` (import from the kit first). Generate options:
`core_roads`, `neighbourhood_roads`, `suburb_roads`, `one_way_core` (yes = every other core side street is a
one-way lane), `highway` (2L/4L/6L), `interchange` (direct / trumpet / cloverleaf / stack = ramps up onto an
elevated deck into each city), `overpasses` (yes = a highway bridges over every street it crosses: 40 m ramp,
deck at 7.5 m, ramp down; lifted points and segments never shape the ground). Buildings with `driveway` (houses,
mansions, businesses) get a pad from the front to the kerb (role `driveway`).

## Roads
A city's roads live in UEFN as landscape spline actors (City Roads, Elevated Freeways,
Stack Interchange, ...). Python cannot read spline control points, so each actor travels
as its BEGIN_SPLINES clipboard text, parsed into control points and segments and stored
as team docs (chunks of 1000).

## Tools

- `road_network_pull(name, project?, actors?)`: read the open level's road actors into a
  network (also samples the terrain and the pillar props for the 3D view). Needs UEFN.
- `road_network_import_text(net_id, label, text)`: add an actor from text copied in the
  Splines tool (Ctrl+C) when the level was not built by this session.
- `road_network_list(project?)`, `road_network_get(net_id)`, `road_network_save(...)`,
  `road_network_delete(net_id)`: networks. `road_network_projects`,
  `road_network_project_save`, `road_network_project_delete`: projects.
- `road_network_stats(net_id)`: pieces used (road meshes, SM_Junc_* junction pieces, kit
  pieces), total length, live-terrain points, open ends.
- `road_network_geometry(net_id, actor?)`: the points and segments the 3D view draws.
- `road_network_move_point(net_id, actor, index, x, y, z)`: move one control point; the
  tangents of its segments follow. Revert in Changes undoes it.
- `road_network_scan_ground(net_id, grid=200)`: re-read the terrain heights (read-only traces
  that count only the landscape's own collision, never water or props). Run it while only
  the terrain is in the level.
- `road_network_drape(net_id, actor, smooth=4, all_points=false)`: put the actor's road points
  onto the stored terrain scan, smoothed along the roads, junction groups flat. Offline:
  never touches UEFN. Run it before a reimport when the terrain changed.
- `road_network_tune(net_id, actor, thin_tolerance_cm=50, side_falloff=800, end_falloff=1000,
  mesh_offset_cm=-1, height_tolerance_cm=150)`: drop every point the spline doesn't need. A point
  goes when the smooth spline between its neighbours (their own end directions, tangent = chord)
  still passes within the plan tolerance, so a straight road between two junctions is ONE segment,
  mouth to mouth, with no lead-in points (the end tangents enter the junctions square). Also sets
  falloff and, when given, Mesh Vertical Offset (our road set uses 50).
- `road_network_check(net_id, actor)` / `road_network_repair(net_id, actor)`: find and fix broken
  data before it reaches UEFN: junction nodes wider than 80 m (a live control point flattens
  the ground across its Width, so one 1 km node stamps a giant circle), mesh-less join links
  over 80 m, runaway tangents. Repair drops the long links and strips the giant junction
  piece (the node stays as a plain shared point). Offline.
- Reimport refuses an actor while Check finds anything.
- `road_network_copy_text(net_id, actor)`: the actor's text on the clipboard for a manual
  paste (Landscape mode > Manage > Splines, hover the viewport, Ctrl+V).
- `road_network_reimport(net_id, actor, terrain_mode?, confirm=true)`: removes the actor
  from the level, pastes the network's version through the Splines tool (mouse and
  keyboard on the UEFN window) and checks the piece count. Ask the person before running
  it and keep their hands off UEFN.

## Generated worlds (several cities, terrain, roads)

- `road_network_generate(name, seed=7, cities="3", size_km=6, relief_m=60, ground_from?, folder?)`:
  offline. Makes a network with one `<City> Roads` actor per city plus `Highways` (shortest tree
  joining the cities, 4-lane, one gentle curve each), a terrain (stored as the network's scan so
  the 3D view shows it) flat under every city, and a 16-bit heightmap PNG. Each city has a style
  (`mixed`: straight 4-lane downtown grid, wobbly suburbs; `grid`; `organic`) and district rings
  (downtown 40 %, normal 75 %, housing out to the radius). `cities` is a count, a JSON list
  `[{name, x, y, radius_m, style}]` (cm), or empty to rebuild the cities already there.
  `ground_from=<network>` puts the cities on that network's scanned ground instead (no PNG).
  Road meshes come from the project's own `…/WorldGen/Roads` folder (found from any stored network).
- `road_network_cities(net_id)`, `road_network_city_save(net_id, city, name?, style?, radius_m?, buildings?)`:
  each city's own settings and building sets (`{"downtown": [mesh paths], "normal": [...], "housing": [...]}`).
- `road_network_heightmap(net_id)`: PNG path and the Landscape import settings. The person imports
  it (Landscape > New > Import from File) as a NEW landscape; never write the existing terrain.
- `road_network_import_all(net_id, confirm=true)`: one click: Build pieces (junctions as wide as
  their roads) then Reimport for every actor. Ask first; hands off UEFN while it runs.
- Generated junctions are `SM_Junc_Gen` points until Build pieces makes `SM_JK_<net>_<actor>_<node>`.

## PCG placement layers

- `road_network_pcg_preset(net_id, preset="full_city")` adds starter layers (meshes empty): nature (Trees, Rocks,
  Bushes and grass), every building type (Skyscrapers and Office blocks downtown; Hospitals and schools, Apartments
  and Shops in the normal ring; Factories and Warehouses in each city's industrial wedge; Gas stations; Houses in
  housing) and Street trees and lamps placed last. Building layers have `width_m`, `depth_m`, `height_min_m`,
  `height_max_m` and always keep clear of every earlier building layer. `where` also takes `industrial`.
  `road_network_pcg_preview(net_id, layer_id)` places them offline (buildings drawn at real size and height). `road_network_pcg_layers`, `road_network_pcg_layer_save(net_id, layer
  JSON, layer_id)`, `road_network_pcg_layer_delete`: one doc per layer (`net.<nid>.pcg.<layer>`).
- A layer: `kind` scatter (Surface Sampler, `density` per 100 m2) | along_roads | buildings (Spline Sampler on
  the road actors, `spacing_m`, both sides at `setback_m`, facing the road); `where` outside | everywhere |
  cities | downtown | normal | housing (rings from each city's districts); `avoid_roads`, `avoid` = earlier
  layers it keeps clear of; `footprint` = bounds scale for pruning; `collision` off for dense scatter.
- `road_network_pcg_plan(net_id)` = ONE graph for every layer: native nodes in data-flow order with their
  settings, wires, graph params (`<Layer>Meshes` SoftObjectPath array, `<Layer>Seed`, `<Layer>Density`),
  the road tag (`<net>_roads`, City = `CityRoads`) and the volume. Build it with Epic PCGToolset following
  `skill_read_subskill("pcg", "graph_layers")`. `road_network_pcg_place(net_id, confirm=true)` starts a chat
  that does exactly that (the panel's Place in UEFN with AI).

## Rules

- NEVER write the terrain (no editor_apply_spline, no heightmap edits, no bakes into
  "Layer"). Roads are live splines with Raise/Lower on; the landscape's reserved
  **Splines** edit layer shapes the ground, so moving a point by hand reshapes it like any
  hand-made spline. A landscape without a Splines edit layer won't deform: tell the person.

## Rules

- Road points sit 40 cm under the road surface with Mesh Vertical Offset 50.
- Junction meshes have a 50 cm lift built in, so their points also sit 40 cm under the road.
- A spline tile mesh must keep every vertex inside 0..tile length on X.
- Panel: header button Road Network. The 3D view follows the person's Appearance theme.
