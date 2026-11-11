---
name: road-network
description: "City Builder (plugin id road-network): generate worlds with cities, terrain and roads, lay out every city layer (buildings, street lights, signs, furniture, foliage) as PCG with a 3D preview, choose own or Fortnite pieces, build it in UEFN; road_network_* tools."
---

# City Builder

The plugin's id and tool names stay `road-network` / `road_network_*` (renaming them would break
saved data and the Store listing); everything the person sees says **City Builder**.

**Making, changing or placing any piece** (road tile, bridge, junction, interchange, street kit, building,
material, PCG build): read `references/pieces.md` first. It has every piece's exact sizes, cross-sections,
offsets, materials and the checklist for adding a new one.

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
one-way lane), `highway` (2L/4L/6L), `interchange` (direct / diamond / trumpet / cloverleaf / stack). Diamond, trumpet and
cloverleaf are REAL geometry in the Highways actor, made of our pieces: the highway bridges (BRR ramp, BR deck at
7.5 m) over a 4-lane cross road 180 m out from each city end, with 4 straight 2-lane slip ramps (diamond), 1 loop
(trumpet) or 4 loop ramps of 12 `ER` segments each, 55 m radius, descending from the deck (cloverleaf); stack
rises onto `ED` decks via `ER` ramps. All of it shows in the 3D view, and the AI builds what it sees.
Building forms (`FORMS` in pcg_layers): box, slab, tower, podium_tower (10-storey podium + tower), stepped,
round, round_podium, twin, spire, L, courtyard, shed, gable, flat. Each building layer has `forms` {form: weight}
and `size_jitter`; the preview gives every lot `[x, y, z, yaw, scale, height_m, form, width_m, depth_m]` and the
plan's `pieces[].forms` says what each form is, so the city can be built to match.
Materials (read before spawning kit meshes): the kit's FBX materials are one flat colour each and are NOT
allowed on instanced meshes, so anything PCG spawns draws the grey checker (or a flat untextured colour).
`road_network_kit_materials` (pcg_build runs it first) makes an MI of `WorldGen/Materials/M_City_WorldPBR` for
every WorldGen/City + WorldGen/Kit material: world-aligned PBR (cinema PBR textures by world position, so meshes
stretched to fit a lot never smear), old colour as Tint, Used with Instanced Static Meshes + Nanite on. They go
on as PCG material overrides; the kit meshes are never edited (a material change rebuilds a mesh: ~1.5 min and
GBs each). Never put a mesh on PCG/ISM without a material that has the ISM usage flag. Junctions: with our own road kit
`road_network_build_pieces(rebuild=true)` regenerates every SM_JK_ piece flat at deck height in the roads'
`M_Asphalt` with planar UVs (the Fortnite Road1 crown + MI_Apollo_Roads material is only for the Fortnite kit).
Interchanges are tangent: each loop's centre is its radius plus the road's half width from the highway and the
cross road, it starts on the highway's edge at the deck's height there and ends on the cross road's edge; slip ramps
end on both road edges. The cross road is a city street (in '<City> Roads'), joined at both ends to the city's
nearest streets, so it never dead-ends in a field.
One editor call = one frame: UEFN frees a window's GPU swap chain only when the frame ends, and every mesh
build, collision build and save opens a progress window. Never build many meshes in one listener / Python call:
16 street-kit meshes in one call stacked 32 swap chains in one frame (8-20 s apart, nothing else logged), went
past 120 GB and crashed on "Hit D3D12 device limits on descriptors". Build one mesh per call (street kit) or
`JUNCTIONS_PER_CALL` 8 (junctions), then save everything in one separate `road_network_save_assets` call.
Signs in the log: many `LogD3D12RHI: Swap chain created` lines with the SAME frame number in `[...][NNN]`.
Memory meter: every listener / Epic call and every paste is measured (UEFN private GB before and after);
`road_network_memory` shows the log and the 10 calls that grew UEFN most. The guard refuses the next call
when one call grew UEFN 6+ GB (until it settles back) or UEFN is above 80 % of RAM: save and restart UEFN,
then `road_network_memory(reset_guard=true)`. Read it after every big step (import, pcg_build) and fix the
step that grows, never push on.
Saving in UEFN: never save assets one by one in a loop. Each save runs revision control's checkout prompt and
every failed checkout opens a modal dialog with its own swap chain (~14 s each); 50 saves took UEFN past 70 GB.
The listener saves everything it made in one `save_packages` batch.
Street kit: the kit had no traffic lights, signs, billboards, power poles, parking lots, path slabs or plinths;
`road_network_kit_materials` first builds them (GeometryScript, WorldGen/Kit/Street, in the same PBR master), so
every layer gets a piece and lots on hills get a plinth plus walkway / driveway slabs.
Push / pull, only what changed (panel buttons, no AI needed): **Push roads** (`import_all`) pastes only road
actors whose text changed since their last push (hash in the actor doc `pushed`) or that are missing from the
level; **Push city** (`pcg_build`) rebuilds only changed 400 m tiles. Pull: a road actor or a tile volume deleted
in the level is noticed on the next push and put back; hand-edited road splines come in with Ctrl+C in the
Splines tool -> `road_network_import_text` (Python can't read spline points). PCG instances are not edited by
hand in the level: change the plan in the panel and push.
PCG is tiled and incremental: `road_network_pcg_build(net_id, confirm=true)` cuts the baked city into 400 m tiles
(`kit_bake.tiles`, a sha1 per tile), one graph `WorldGen/CityPCG/PCG_<World>_T<tx>_<ty>` (Create Points ->
Static Mesh Spawner per kit mesh) + volume `City Builder <World> <tx>_<ty>` each, Generate On Demand. The tile
hashes live in `net.<nid>.pcg`; a build rebuilds only tiles whose hash changed, removes tiles gone from the
plan and leaves the rest (and the roads) untouched. So: edit the city plan, build again - never delete
everything. Without confirm it reports what would change; force=true rebuilds all. Each tile's execute is a
small editor call (memory-safe); a stopped build keeps finished tiles and resumes. The first tiled build removes
the old whole-world / whole-city volumes and graphs by exact name. Lots take the own-kit building nearest their
height (multi-part towers share one point), scaled to the lot; `no_kit_piece` lists layers with no kit mesh.
Foliage layers are left for painting. Order for a fresh world: generate (with keep zones) -> heightmap ->
Landscape New > Import (Location = the map's centre, not its corner) -> import_all -> pcg_build.
Keep-out zones: `options.keep = [{"name", "x0", "y0", "x1", "y1", "z"}]` (cm) protects something already built
(a cinema, a spawn island): the ground stays flat at `z` there, no city is placed on it, city streets stop short,
highways go round it and PCG lots keep 20 m clear. A world remembers its zones on later regenerations.
Interchange option detail: `overpasses` (yes = a highway bridges over every street it crosses: 40 m ramp,
deck at 7.5 m, ramp down; lifted points and segments never shape the ground). Buildings with `walkway` (all but
parks / parking) get a 1.6 m path from the door to the kerb; `driveway` ones (houses, mansions, businesses) also a
3.5 m driveway at one side, winding across the slope when steeper than 15 %. On hills every lot gets a level pad
at its corners' mean height (cut and fill) and `foundation_m` (plinth depth where the ground falls away); preview
rows add `foundation_m, paths`. The generated heightmap (road_network_heightmap) has the pads cut in; an
existing terrain is never edited, so there the foundations and paths do the work.

## The pipeline (separate systems, `backend/pipeline.py`, tool `road_network_pipeline`)
```text
CityGenerator
  TerrainAnalyzer      Heightmap, SlopeMap, ObstacleMap          terrain scan, slope shares, keep zones, water
  UrbanPlanner         Districts, RoadOrientation, Density       rings, grid angle, lots per city
  RoadGenerator        RoadGraph, Intersections, RoadProfiles    '<City> Roads' / 'Highways' (graded)
  BlockGenerator       BuildingParcels                           lots from blocks
  FoundationGenerator  BuildingPads, CutAndFill, RetainingWalls  pads, plinths, walls
  BuildingGenerator    Footprints, Buildings, Entrances          PCG layers -> kit meshes -> 400 m tiles
  SidewalkGenerator    PedestrianPaths, Corners, FrontageConn.   '<City> Paths' spline actor
  RoadMeshGenerator    Asphalt, Curbs, Intersections             junction meshes + paste (UEFN, Push roads)
```
`road_network_pipeline(net_id)` shows every stage's state; `run_from=<stage>, confirm=true` re-runs that stage and
everything downstream only (`buildings` keeps the roads, `sidewalks` rebuilds only the paths actor).
Sidewalks, walkways and driveways are SPLINES (road types `SW` 3.8 m, `WK` 1.6 m, `DW` 3.5 m; meshes
`SM_Path_*_10m` in WorldGen/Roads, built by the street kit): live like roads, graded (8 %, 8 %, 15 %), so they bend
and cut / fill the ground. Every city street whose mesh has no sidewalk (`6L`, `1W`) gets one both sides. Only
the wide frontage aprons (plazas) stay PCG slabs. PCG never lines street furniture up on the Paths actor.

Height-aware pieces (read from the GRADED road vs the original terrain, `pl.road_walls`): every 10 m along a
ground-level road, both sides, just past the sidewalk: ground > 1.2 m above the road (cut) -> a stone retaining
wall holding the hill; road > 2 m above the ground (fill) -> a stone wall holding the road up
(`SM_Kit_StoneWall`, 50 cm thick, capped, scaled to the 10 m stretch and the height). The preview shows them as
the built-in 'Road walls' layer (`pcg_preview(layer_id='road-walls')`); Push city bakes them with the tiles.
Every city street has a pavement: `2L`, `6L`, `1W` get sidewalk splines (dirt roads stay bare), and every
junction corner gets a `SM_Kit_SidewalkCorner` paving pad ('Sidewalk corners' layer, 4 per junction). A street that turns more than
30 degrees where only two streets meet (`CORNER_DEG`, the city's edge, cul-de-sac returns) gets an ELBOW piece,
built like a junction (2 arms): kerb return inside the turn, a square corner outside where the road edges meet.
A plain spline point there smoothed a 90 degree turn into a wide arc that looped out past the block. Junction
meshes are crossings, not discs: each arm out to its mouth with curved kerb returns between arms
(`_junction_geometry`); the panel draws them the same way.

## The panel
City Builder is the BLUEPRINT; an AI builds it in UEFN with UEFN's own systems (landscape splines for roads,
paths and access; PCG tiles for the city). The panel has no push buttons: the header's "Build in UEFN with AI"
sends the world to a chat (`road_network_send_note`, kind 'build') with the job: `road_network_import_all` then
`road_network_pcg_build` (both with confirm=true, only what changed), `road_network_memory` after each step.
Header: the world dropdown, New world, Build in UEFN with AI. Left sidebar (drag its edge to resize): sections
(World, Terrain, Roads, Cities, Layers, Edit roads), the layers numbered in the order they are placed (click
one to open it, drag to reorder), road actors, Pull from UEFN, and the version at the foot. Bottom of the
map: Select, Pin (click a spot) and Area (draw round an area), then a note box with "Send to" (a recent chat
or a new one): `road_network_send_note` sends the AI the spot / outline, the city and district, what is already
placed inside, and how to act (save a PCG layer with `"area": [[x, y], ...]` - a layer with an area places only
inside it - then preview and Push city). Colours in the preview are mixes of Appearance variables
(`mix:a:b:t`), never literals.

## Neighbourhoods and the private access network (`backend/neighbourhoods.py`)
Outside the core the grid has ~200 m super-blocks (`superblocks`). Each block >= 120 m becomes a neighbourhood,
picked from the city seed and weighted by its ring (`TYPES`): subdivisions `grid` (two through streets), `loop`
(U street, two entrances), `cul_de_sac` (stem + 10 m turnaround), `organic` (curved loop) - lots both sides of
their private streets, facing them, role 'house'; apartment complexes `garden` (ring road, parking along it, two
rows of buildings round a courtyard), `suburban` (buildings round a central lot), `urban` (two big blocks + a
parking garage) - footprints with role 'apartment' placed at their exact size; or `parcels` (plain street lots).
Parking: lot -> 17.5 m modules (5.5 stalls + 6.5 aisle + 5.5 stalls) -> 2.6 m stalls. Three networks:
public roads ('<City> Roads'), private access ('<City> Access': PR private streets, DW / DD driveways as cubic
Beziers garage -> kerb, double for wide houses), pedestrians ('<City> Paths': sidewalks, walkways). Built-in
site layers (preview + Push city): Complex parking, Parking stalls, Turnarounds, Parking garages.

## Freeways and interchanges (`backend/freeways.py`, option `freeways`)
Three layers on one road graph: regional (`Highways`, city to city), urban freeways (`<City> Freeways`, planned
BEFORE the blocks) and local streets (`<City> Roads`). Per city, `freeways.plan`:
- **Mainlines:** from 600 m radius a north-south freeway on the city grid, mid-block at the edge of downtown
  (never on a street); from 2 km radius an east-west one too, and a **directional stack** where they cross.
- **Levels from clearance, not offsets:** `LEVEL_CM` 7.5 m (5.5 m vehicles + deck). Streets level 0, freeway
  viaduct level 1 over the whole city (streets keep running under it, ED deck mesh), back to grade outside it at 4 %.
  At a stack the north-south freeway is level 2 and the left-turn flyovers are levels 3 and 4.
- **Service interchanges:** only on main roads (collectors, axes), at least 800 m apart and clear of the stack.
  Downtown gets a tight diamond (ramps between the freeway and the next parallel street), the normal city a
  partial cloverleaf (`parclo`: 2 loops + 2 diamond ramps), the suburbs a diamond whose ramps meet frontage roads
  (FR, on the parallel street). `interchange()` also builds a full `cloverleaf` (4 loops + 4 outer connectors) and
  a `trumpet`.
- **Ramps (RampGenerator):** cubic Beziers leaving tangent to the freeway edge. Curvature
  κ(t) = |x'y'' − y'x''| / (x'² + y'²)^1.5 and a 3-point radius check: loops ≥ 45 m, ramps ≥ 150 m, directional
  ≥ 120 m. Height runs smoothly between the two roads; flyovers hold their level within 500 m of the stack.
- **Right of way:** a street that a low ramp (< 7 m lift) or an at-grade freeway would cut is removed, and no lot,
  stall or pad lies within 15 m of a freeway or ramp edge (`Plan.blocked`).
- **RoadNetworkValidator** (`freeways.validate`, run by `road_network_generate` → `freeways` per city): plan
  crossings with less than 7 m between them, radius and grade per class, and ramp ends that touch no road. It must
  come back clean. Never hand-place a ramp; change the plan and regenerate.

## City size, spacing and water
- **World tab:** `City size` (option `city_size`, ×0.3–4) scales every city, and regenerating scales the ones you
  have. `Distance between cities` (`city_gap_m`) is the open country between two cities' edges; cities closer
  than that are pushed apart (`citygen.spread`). Under **Selected city**, pick a city and set its radius
  (150 m – 5 km) or road style. That calls `road_network_city_save`, then regenerates.
- **Water** (`citygen.over_water`, the terrain's water line `sea`): every road edge is sampled for water.
  - Highways and freeways always cross on a bridge: BRR ramp on land, then a BR / ED deck 7.5 m up, then a ramp.
  - Main roads (2S / 4L / 6L) bridge up to 400 m of water, and any paved street bridges a creek up to 60 m.
  - Anything else, or a road with an end in the water, is removed. Streets stop at the shore.
  - Lots, parking and private streets are never in the water. There are no sidewalk splines on bridges.

## How a city is generated (the math, in order)
Roads define the city, blocks are what roads enclose, lots are cut from blocks, buildings fit lots.
1. **Planned grid** (`citygen.city_graph`): one orientation per city; intersections on a block grid (`block_m`
   100), each moved as a whole by at most `wobble` (default 4 %, max 8 % of a block), so streets stay straight;
   hierarchy: the two main axes are arterials, every `collector_every` (4th) line a collector, the rest local;
   `prune` (12 %) drops only local side streets (suburb cul-de-sacs allowed).
2. **Grading** (`GRADE`, `st.drape(max_grade=...)`): steepest rise per road (6L 4 %, 4L 5 %, 2S 6 %, 2L / 1W
   8 %, dirt 10 %); steeper ground is cut and filled by the live splines. Bridges / ramps sit over the highest
   ground within 15 m.
3. **Blocks -> lots** (`parcels.py`, Vanegas 2012): faces of the street graph, inset by road half width +
   3.8 m sidewalk zone, cut into back-to-back strips then 16 m frontages (`LOT_W_CM` / `LOT_D_CM` 16 x 35 m);
   every lot fronts a street square on; corner lots front their short side; stored as each city's `lots`.
4. **Buildings**: layers narrower than 25 m (houses, shops, apartments, gas stations) fill lots, one per lot,
   set back from the front lot line; wider ones (offices, towers, civic, mansions, industry) take road frontage
   first and the lot fill keeps clear of them.
5. **Lot grading** (`_lot_ground`): level pad at the corners' mean, plinth where the ground falls away,
   retaining wall (`SM_Kit_RetainingWall`) on any edge where the ground stands > 0.5 m above the pad, a
   full-width frontage apron (shops, offices, apartments, civic) or walkway + driveway (houses) to the kerb.
6. **Pieces**: `road_network_piece_manifest` lists every mesh with size, pivot, slots and what places it, plus
   the WANTED ones; build them in Blender to that spec, import to the folder, add buildings to a layer's
   meshes with `size_cm` (they fit lots like kit buildings).

## Design rules (the concept comes from the plugin, not from the AI)
`road_network_generate_options().rules` is the list City Builder's code follows; an AI building with the plugin
follows the same list and never invents its own layout. In short: highways only join two cities; interchanges only
where such a highway meets a city (180 m from its gate, highway > 900 m), never alone, their cross road joined to
the city's streets; freeways bridge 7.5 m over streets they cross; bridges / ramps / decks sit over the highest
ground within 15 m and never shape the terrain; keep zones are never built on; lots are levelled with plinths,
walkways and driveways; forests are dense patches in a belt round each city; PCG is tiled and incremental; one
editor call does little.
Fully automatic for anyone who installs it: Generate (World tab) makes the cities, roads, terrain AND every
starter PCG layer (`full_city` preset, added when the world has none); Push roads, then Push city. No AI step.

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

### Your own layers (person or AI, no plugin code)
Layers are data (`net.<nid>.pcg.<layer>` docs), so anyone extends the city without touching the plugin:
1. `road_network_pcg_layers(net_id)` -> current layers plus `fields` (every setting, default/min/max), `kinds`,
   `where`, `roles` and an `example` layer.
2. `road_network_pcg_layer_save(net_id, layer='{...}')` with `meshes: [{path, weight}]` (Content Browser paths,
   `/Mount/Folder/SM_Name` is enough). A layer WITH meshes places exactly those (weighted, stable per spot);
   without meshes it uses the kit piece for its `role`. Panel: Placement layers > Add layer, then Meshes > Add.
3. `road_network_pcg_preview(net_id, layer_id)` to check the count and spots in the 3D view.
4. Push city (`road_network_pcg_build(net_id, confirm=true)`): only tiles the new layer touches are rebuilt.
   Before the build, `road_network_ism_ready` gives the layer meshes' base materials the instanced-mesh
   usage (project materials only; Fortnite `/Game` materials are listed as `materials_locked`).
Example: Flower beds in the suburbs: `{"name": "Flower beds", "kind": "scatter", "where": "housing",
"density": 0.5, "cover": 0.4, "meshes": [{"path": "/MyIsland/Garden/SM_Tulips", "weight": 3}],
"avoid": ["Houses"], "order": 20}`.

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
