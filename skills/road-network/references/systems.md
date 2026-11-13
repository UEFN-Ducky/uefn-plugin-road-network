---
description: "Every City Builder system in detail: pipeline, parcels, neighbourhoods, freeways, urban planner, waterfront, shipyards, water and bridges, PCG tiles, push/pull, tools and rules."
metadata:
  label: "City Builder: every system in detail"
  load_condition: "When SKILL.md points to a system (freeways, urban planner, waterfront, shipyards, PCG layers, pipeline) and you need its details"
---

# City Builder: every system in detail

Read the section you need, when you need it (SKILL.md says which). Exact option names and defaults come from
the tools (`road_network_describe`, `road_network_generate_options`, `road_network_pcg_layers`): trust them over
any name written here.

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
at its corners' mean height (cut and fill) and `foundation_m` (how far the ground falls away under it); preview
rows add `foundation_m, paths`. The generated heightmap (road_network_heightmap) has the pads cut in; an
existing terrain is never edited, so there the foundations and paths do the work.

## The pipeline (separate systems, `backend/pipeline.py`, tool `road_network_pipeline`)
```text
CityGenerator
  TerrainAnalyzer      Heightmap, SlopeMap, ObstacleMap          terrain scan, slope shares, keep zones, water
  UrbanPlanner         Districts, RoadOrientation, Density       rings, grid angle, lots per city
  RoadGenerator        RoadGraph, Intersections, RoadProfiles    '<City> Roads' / 'Highways' (graded)
  BlockGenerator       BuildingParcels                           lots from blocks
  FoundationGenerator  BuildingPads, CutAndFill, Foundations     pads, foundations
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

Nothing in the city stands on air, and no loose wall slab holds anything up: every piece carries its own walls.
- **Ground roads**: a concrete skirt under each edge, 3 m down (`road_profiles.SKIRT_CM`), part of the road mesh.
- **Buildings and flat lots**: their own foundation, always (`kit_bake.foundation`): a plinth course round the
  foot and a wall under it, 1 m into the ground below the lowest corner (`foundation_m` + 1 m).
- **Decks and ramps too low for piers**: an approach wall under them, from 1 m in the ground up into the slab
  (`citygen.deck_fills`, the 'Approach walls' layer). Higher up they stand on pier bents; over water and over a
  road they cross they stay open.
- **Uphill cuts**: held by the graded ground. A road's own cells sit at the road and blend back to the hill over
  30 m; a lot's pad blends back over a pixel and a half.
- **No foundation deeper than 4 m** (`pl.FOUND_MAX_M`): a building that would need one is left out and its lot
  planted (`pl.leave_out_deep`, `pl.plant_lots`, into 'Infill trees'); the ground is cut again without its pad,
  and its driveway kerb and fences go with it. Parks stay, and so does a planned site (hospital, school, city
  hall...). `ground_fit.left_out` says how many went, how many planned sites were kept, how many trees came.
  The '<City> Paths' / 'Access' actors are built from the previewed lots: run the pipeline from `sidewalks`
  after a change like this, or driveways to lots that are gone stay in the stored world.
The ground is graded to the public roads first, then to each city's private streets as its PLAN has them, laid on
that ground (`private_plans`, `private_streets` in `pcg_preview`), never to the stored '<City> Access' actors: inside
a generate those are still the last world's. The sidewalk stage builds Paths and Access on the same public ground.
A foundation's depth is measured on the ground the heightmap leaves (`citygen.footings`, after the pads are cut and
the buildings settled). How deep they come out is set by the landscape's pixel (about 10 m on a 10 km world, with
16 m lots): neighbours' pads at different heights meet inside one pixel. Measured Nov 12 2026: levelling lots on the
road-graded ground, or at their street's level, changed the depths by under 5 %, so neither is done.
The built-in layers (piers, caps, abutments, approach walls, neighbourhood sites) come with
`pcg_preview(layer_id='road-walls')`; Push city bakes them with the tiles.
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

**A picked city.** Click a city on the map: the right side becomes that city's details (road style, radius,
turn, its picked block, what it has, districts, plan); the x closes it. Click again inside it to pick a block:
the block is washed and outlined, its lots are outlined, and the right side says its zone and layout and lets
the person change both. Pointer never moves a city. The **Edit city** tool (E) does: drag the city to move it,
the white handle to turn it, a dot on its edge to pull the edge in or out. While a city is dragged it leaves its
place at once and a light copy of its buildings rides with the pointer; after the drop the copy stays until the
rebuilt city starts to come in. A rebuilt city always comes in a few layers at a time, never all at once. The
map has no distance fog.

**Live with a team** (the app's Live menu on a team copy; the panel's part is in `ui/app.js`, "live: bodies,
holds, edits as they happen"). Nothing here is stored or goes to a server: it rides on each person's live state.
- Everyone flies round the map as a ghost with their name under it: a body hovers over the spot each person looks
  at, turned the way they look. Your own body shows while you move.
- The city a person has picked is theirs until they let go (pick something else, close it, leave, or stop
  touching the panel for 90 s), as a Kanban card someone has open. The others see its edge in that person's
  colour and their name on its tag; they can open it and read everything, but not move, turn, reshape, re-plan
  or delete it. The next person who picked it gets it when it is let go.
- A city being dragged shows on every screen where it is right now; a rebuild under way shows as a line at the
  top of the map. Only one rebuild of a world runs at a time: a second person's starts when the first is in.
- The change itself arrives with sync (`plugin_scope_changed`, reason `synced`): the panel re-reads only what
  the keys name and swaps each layer in place.
- Teammates do not wait for their own PC to place the change: the PC that rebuilt sends, layer by layer, the
  pieces that are new and the ones that are gone (`live.send` topic `pcg`, at most 90,000 pieces; a whole new
  world is left to each PC). Their page puts them in at once; their own PC then places the change in the
  background without a loading card and ends on the same result (placement is the same on every PC).
- **Holds are kept by the backend too** (`backend/holds.py`, in memory, nothing stored or synced), so a ducky's
  tool call obeys them:
  - `road_network_city_save`, `road_network_city_delete`, and a `road_network_generate` that changes the world's
    settings or cities, are refused while someone else holds a city, with the holder's name. Do not retry in a
    loop: work on another city, or tell the person who has it.
  - A ducky that changes a city holds it for two minutes from each change, and every screen shows whose ducky
    it is ("Builder (Ilya's ducky)"). `road_network_hold(net_id, city, task, minutes)` takes a city for a longer
    job, `road_network_release` lets go, `road_network_holds` lists who has what.
  - A person's own ducky may change the city that person has open; the person's controls for it are read-only
    until the ducky's hold ends.
  - What teammates hold reaches a PC through its open City Builder panel. While the team is live and the panel
    is closed on a PC, a ducky there changes no city: it says to open City Builder (or leave Live) first.

## One city by hand: turn, outline, blocks (`road_network_city_save`)
Everything the panel does to one city is this tool, so an AI can do the same:
- `turn_deg`: the whole city turns round its centre (street grid, outline, industrial side).
- `shape`: its outline drawn by hand, a JSON list of 16 numbers (how far it reaches in 16 directions round it,
  as a share of its radius, 0.74 to 1.26; the first is the city's own 0 degrees and turns with it). `"reset"`
  returns the outline its seed gives it.
- `zones`: one block gets a type. `{"x", "y"}` is any spot inside the block (cm). `kind` is how it is laid out
  (`road_network_cities` returns `block_kinds`: plain lots, each subdivision and complex type, `park` = trees and
  nothing built). `zone` is what is built on it (downtown / normal / housing / industrial): every layer then
  treats that block as that zone, whatever ring it lies in. `green` (0 to 1) is the share of its lots planted
  with trees instead of built on. A list changes several blocks; all three empty returns a block to the city's
  own plan.
- `road_network_cities(net_id)` lists each city's `blocks`: `[x, y, kind, zone, polygon, green share]`.
- Only that block is laid out again (it has a random of its own); the other blocks keep their lots. The planner
  may still move a service site if the block it had chosen lost its lots.
- A change is saved at once and shows after the next generate (`road_network_generate` with cities empty).

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
- **Ends at the shore:** a freeway crosses a river or a bay to the land beyond, but with only open water ahead it
  ends at the shore, back at grade (`freeways.shore_reach`, kept on the city as `fw_shore` by
  `citygen.shore_plan` before the streets are laid). No stack is built where its place is in the water.
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

## Urban planner: a city that functions (`backend/urban.py`, stage `demand`, option `planner`)
Facilities are never placed at random. After the lots exist, `urban.plan` runs:
1. **Population** from the lots: houses hold 2.6 people, apartment buildings are sized by floor area, and plain
   parcels are counted by district ring (core lots also carry jobs). Age groups follow (0-4, 5-10, 11-13, 14-17).
   Retail demand is D = P × S.
2. **Travel along the roads** (`Network`): drive time per road class (Dijkstra) and walking distance. Never circles.
3. **Location** (`CATALOG`, in eight categories: commercial, residential, education, government, industrial,
   recreation, utilities, transportation):
   - **Maximum coverage:** fire within 4 min, police 6, ambulance 5, parks within 500 m walk, corner shops within
     1 km walk, grocery centres within 4 min. Facilities that already serve an area count toward it.
   - **Capacity:** schools from enrolment / seats, hospitals, libraries, post offices, community centres and
     parks, substations, parking garages by jobs. These are p-median placed, and school catchments are assigned
     by travel time within capacity.
   - **Single central sites:** city hall, bus terminal, train station, hotel. **Edge / industrial sites:**
     distribution, power, recycling.
   - **Terrain:** water and telecom towers on high ground, treatment plants and retention ponds on low ground.
   - **Anchors:** park-and-ride at freeway interchanges.
4. **Complete developments** (`ARCHETYPES`): a site is whole free lots of one block. It never crosses a street or
   a freeway. Each site gets its building (a lot with the building's role and exact footprint), parking fields
   with stalls, and aprons, sports fields, playgrounds, pools, yards, ponds or towers. These appear as built-in
   site layers (Service aprons, Sports fields, Playgrounds, Pools, Utility yards, Ponds and tanks, Towers).
   The PCG layers for these roles (Schools, Hospitals, Police / Fire stations, Malls, Parks, Gas stations, Civic
   buildings, Utilities, Transit stations) build only on planned sites.
5. **Growth:** generating again keeps every facility whose site still exists and adds only what new demand needs.

`road_network_city_plan(net_id, city, facilities)` returns population, demand, counts by category, coverage,
school enrolment, unmet needs, and each facility with the reason it is there. The panel's Cities cards show the
same. To change the services, change population (city size, district rings) or the catalogue, then generate.

**Each facility is built for what it is.** The facility type travels with its lot from the planner through the
preview into the bake. There, a table of facilities gives each type a grammar style, the part of the lot its
building takes, and its own pieces:
- **Civic:** a city hall gets classical walls, a portico, a dome and flags; a courthouse a portico and a flag.
- **Jail:** slit-window walls, a sally-port gate, a security fence round the lot and four watchtowers.
- **Utilities:** a power plant gets an open industrial hall with machines, two cooling towers, a chimney and a
  transformer; treatment plants get storage tanks.
- **Health:** a hospital gets a helipad on the roof and an ambulance canopy.
- **Transport and sport:** stations get platform canopies; a stadium is a ring of bleachers with no shell.

Facility buildings are always modular, so they are hollow with floors and a stair core every storey (open
halls get one storey of machines). A raised plinth gets entrance steps. Planned sites are never thinned by a
layer's spacing; that spacing only applies to scattered lots.

**Up close, modular buildings carry roof gear and facade details by style:**
- **Roof gear:** AC units, exhaust fans, vent stacks, antennas, satellite dishes, solar arrays, skylights, ducts,
  hatches, and a water tank on apartments. One weighted pick per 4.5 m roof cell, never under a higher block.
- **Roof access:** every block with a stair core gets a roof-access hut over it.
- **Facade details:** window AC units on apartments, houses and shops; split AC units and vents; downpipes on
  every corner (not glass towers); a lamp each side of every door; cameras on jails and industrial buildings;
  a steel fire escape up one side of apartment blocks of 3+ storeys.
- **They never change the building:** the details use their own random stream, so a building keeps the same
  walls with or without them. Every piece fits its cell or storey; nothing stands through a roof or ceiling.

## Waterfront & marine development (`backend/waterfront.py`, options `waterfront`, `canals`)
- **ShorelineAnalyzer (`analyze`):** the water line S(t) comes from the terrain at the water level (marching
  squares), resampled every 20 m. Each sample carries:
  - tangent T and water-facing normal N = (-T_y, T_x), signed curvature;
  - shelter (rays from the water hitting land within 400 m);
  - depth 30 m and 80 m out, water width along N, land slope, flood exposure.
  Waterbodies are typed ocean / lake / river by size, contact with the map edge, and shape.
- **WaterfrontPlanner (`classify`, `segments_for`):** every 100 m of a city's shore gets one use from depth,
  slope, shelter, water type and district: natural, wetland, beach, residential, commercial, marina,
  industrial, public. The core is commercial. Outside it each 300 m block of shore draws from its district's
  mix (`MIX`: homes, beach, marina, park), so a city's shore is varied. Only a cliff stays natural: steeper
  than 0.6 in its first 30 m, or more than 18 m high 50 m inland. Steep banks are no longer left empty.
- **ShoreWorks, the made shore (`shores_for`, `profile`, `Made`, `citygen.plan_shores` / `cut_shores`):** where a
  city builds on its shore, the shore is shaped for what stands there, before the streets are laid. A stretch
  is made when it is at least 160 m long and no shipyard has it. `city['shores']` keeps each one
  ({use, water, bank, pts}).

  | Use | The ground it gets (cm above the water, by distance inland) | What stands on it |
  |---|---|---|
  | Commercial | quay wall at the water line, level at 200 for 50 m, paved to the back | restaurants facing the water, piers, ferry terminal on big water |
  | Marina | the same for 42 m | main pier, floating branches, slips with boats, gangway, launch, marina building; floating homes when sheltered |
  | Public | the same for 24 m, a 6 m promenade | fishing pier |
  | Beach | sand rising 1 in 40 from 20 m out in the water to 40 m inland, then a boardwalk at 200 | sand in 10 m bands, a low wall and the boardwalk, a rock groyne at each end, resort hotel, beach club and pier on a long one |
  | Residential | a low bulkhead at 120, a lawn rising to 450 at 30 m, level to 58 m | a row of homes square to the shore, a few private docks or one shared marina |
  | Industrial | quay for 24 m | container cranes; yard, stacks and warehouse behind |

  Behind every made shore the land comes back to its own height as a bank no steeper than 0.5 (`bank`). A
  stretch fades into the natural shore over its last 60 m. Every city on a lake or the sea gets at least one
  beach: with none drawn, its gentlest park or row of homes becomes one.
- **Streets and the made shore:** streets are laid on the made ground. Where the land was cut down by more than
  2 m for it, no street runs (it would stand on a ramp above the promenade): streets end at the top of the bank.
  A bridge keeps both its ends. No street-grid lot stays on a made shore, and no other layer places there.
- **Heights:** everything at the water stands where it was built and levels no ground: piers on piles, floating
  docks, boats, slips, floating homes, quay walls, breakwaters, sand, promenades (`OWN_HEIGHT`, `NO_PAD`).
  Earlier they were re-seated on the ground under them, which had been raised to them: a pier became a strip
  of land and a marina's boats stood metres above the water.
- **Foundations:** pilings from the bed to the deck (piers, stilt houses at 3.5 m flood elevation); floating at
  Z_water + freeboard (docks, homes); quay walls down to the bed, 20 m at most.
- **Pieces:** rows [x, y, z, yaw, sx, sy, sz, kind] become built-in site layers (Piers and boardwalks, Floating
  docks, Pilings, Boat slips, Boats, Seawalls and quays, Breakwaters, Beaches, Wetlands, Promenades, Port cranes,
  Container stacks, Port yards, Boat launches, Stilt houses, Floating homes, Bridge towers). The street kit
  builds them. Land buildings are footprint lots facing the water (roles shop, office, transit, warehouse).
- **Validator:** piers start on land, end in water and stay within a third of the width; piles reach the bed;
  land buildings are within 150 m of a street; waterfront buildings never overlap.
- **Canals (`canals: canal_city`):** every third inner street line becomes a `<City> Canals` spline (`CN`, laid at
  the 3 m channel bed so the landscape spline lowers the ground). Cross streets run over it on canal arches.
  Walkways line both quays, and no lot sits in a canal.
- **Bridges:** typed by the water they span. Canal arch 3 m, beam 7.5 m, navigable girder 15 m, cable-stayed 30 m,
  suspension 45 m clearance. Ramps are long enough for 6 %. A cable-stayed or suspension bridge stands on two
  towers astride its deck (`citygen.bridge_towers`: a leg outside each edge, three beams across, never a column
  in the roadway) with stay cables from each leg's top to the deck's edge; the deck they carry has no pier bents.
- **`road_network_waterfront(net_id, city)`** returns waterbodies, bridges, shoreline by use, developments and
  the validator result.

## Shipyards and marine industry (`backend/shipyard.py`, option `shipyards`)
Shipyards are **dedicated industrial districts**, not waterfront lots. `citygen.plan_shipyards` runs before the
streets: it uses the classified shoreline (`waterfront.segments_for`) and an estimated population. City streets
inside a campus are removed, and no city lot stays inside one.

**Every yard is one level rectangle at the water** (`shipyard.settle`, on worlds that make their own ground):
made ground with a straight quay, 2.5 m above the water, paved from edge to edge.
- It reaches out over the water by up to 30 % of the water's width and inland only to where the land has risen
  12 m above it. A yard under a hill is mostly made ground in front of the hill, not a pit dug into it.
- `citygen.cut_yards` cuts it into the terrain (`shipyard.shaped`): level on the platform, a basin dredged 9 m
  deep for 90 m along the quay, the land beside it cut back to a bank no steeper than 0.6. The yard keeps that
  bank as its own land (`bank`, up to 80 m): no street, lot or tree on it.
- The paving is PCG: 'Yard hardstand' slabs of up to 100 m (`shipyard.paving`), open only over the docks and
  slipways. Everything else of the yard stands on them at the platform's level, and keeps the height the yard
  built it at (`keep_z`): a quay wall stands on the bed, a ship floats, a gantry spans its dock.
- Each graving dock is cut with the platform as a straight-walled basin 12 m deep, never graded like a road.
- The rows lie one behind the other with a lane between (`shipyard.rows_of`): docks and assembly yard, 25 m,
  fabrication halls, store row (warehouse, steel stacks, machine shop), back row (administration, gate, tanks,
  substation, fire station). A production yard is at least 260 m deep so they fit; a dock is as long as the
  rows leave, up to 300 m. No hall stands on a dock, the rail siding or another hall.
- The halls are lots a plan drew (role factory, warehouse, civic, utility, fire_station, footprint true). A
  building layer of big sites (Factories, Warehouses, Office blocks) builds every such lot of its role first,
  then its sites along the roads. A lot row is width across its front, then depth.
- A neighbouring city keeps off the yard too (`shipyards_near`): no street, lot or waterfront piece of its own.
- Its edges: the quay wall along the water, a wall on any side where the platform stands above what lies
  beside it, a security fence wherever it meets the land (the gate stays open).
- On the hardstand: hull sections and container stacks in the assembly yard, boats laid up ashore in a yacht
  yard, cold boxes in a fishing yard. Each from the yard's own dice, so they stay put from run to run.
- To put more on a yard, add a layer with `where` shipyards (every yard's platform) or draw the layer an
  `area` on one. Every other layer keeps off the yard's land.

| Yard type | Picked when | What it gets |
|---|---|---|
| commercial | at least 600 m of deep (6 m+) industrial shore, 40k+ people | 1-3 graving docks, panel line, steel fabrication, paint and blasting, assembly yard, outfitting quay |
| naval | a second big stretch on the ocean, 80k+ people | the same behind a security fence, with secure berths |
| offshore | at least 400 m on the ocean, 30k+ people | huge hardstand, fabrication hall, jackets / foundations, quay |
| repair | at least 250 m of deep water | floating dry dock, repair workshops, paint hall; a graving dock if the stretch is long |
| yacht | a sheltered marina stretch, 15k+ people | slipways, yacht assembly and finishing halls, boat lift |
| fishing | at least 150 m | processing plant, cold storage, fish market, docks |
| recycling | shallow industrial shore | ships beached on slipways, scrap yard |

**Production-flow layout** (u along the shore, v inland), following the EPA shipbuilding layouts and
UFC 4-213-10:
1. Administration, gate and parking at the back.
2. Warehouses and steel storage, with a rail siding.
3. Fabrication halls.
4. Assembly yard (open hardstand with tower cranes).
5. Graving docks, each with a goliath gantry spanning it, crane rails, keel blocks, a hull under construction,
   a caisson gate and a pump house.
6. Outfitting quay: quay wall, portal cranes on rails, ships alongside.
7. Tug station. The launch basin in front of each dock is kept clear.

Supporting pieces: substation, yard fire station and fuel tanks.

- **Docks:** each graving dock is a straight DK spline in `<City> Docks`, laid 12 m under the platform so the
  landscape spline digs the basin. Each one runs from inland to 30 m past the quay.
- **Validator:** docks open onto water, launch basins are clear, the flow runs steel → fabrication → assembly →
  quay, administration is outside the heavy zone, and floating docks stay within the navigation share.
- **Pieces:** Ships, Goliath gantry cranes, Portal cranes, Tower cranes, Crane rails, Keel blocks, Yard hardstand,
  Steel storage, Rail sidings, Floating dry docks, Fuel tanks, Security fences, Boat lifts, Offshore jackets,
  Slipways, Scrap yards, Dock gates, Gatehouses. Halls are footprint lots: factory, warehouse, civic, utility,
  fire_station.
- **Results:** `road_network_waterfront` returns each city's `shipyards` (type, docks, site size, the
  platform's corners, how far it stands out over the water, production-flow distances, validator).

## Airports (`backend/airport.py`, options `airports`, `military_base`, `wind_deg`)
Airports are planned at world level **before the streets** (`citygen.plan_airports`).

| Type | City size | What it gets |
|---|---|---|
| general_aviation | 2k+ people | 900 m runway (RS), FBO, T-hangars, light aircraft, fuel |
| regional | 20k+ | 1.8 km runway (RW), linear terminal with 8 gates, cargo, maintenance, tower |
| international | 120k+ (one per world) | 2 parallel 3 km runways, pier terminal with 24 gates, cargo district, hotel, train station |
| military | `military_base: yes` | shelters, hangars, command, perimeter fence |

- **AirportSitePlanner:** samples candidate sites on a ring outside the city, with runways turned to wind_deg
  ±15°. Each site is scored on grading (deviation from level) and must be dry, inside the map, clear of keep
  zones and outside every city. The terminal side faces the city.
- **Airside (`<City> Airport` actor):** RW / RS runways split at every connector, a TW parallel taxiway, 90° end
  connectors, 30° rapid exits and holding positions, forming one connected graph with junction pieces where the
  edges meet. The actor is laid at one level so the landscape spline grades the airfield.
- **Stands first, then the terminal:** linear (gates along one face), pier (concourses into the apron, gates on
  both sides), FBO. Aircraft sit on every stand, with jet bridges and ground support vehicles.
- **Support:** cargo district (apron, freight terminal, cold storage and customs, truck yard, ULDs), maintenance
  hangars, engine shop, GSE depot, fire and rescue station at midfield on the taxiway, control tower, radar,
  operations centre, catering, fuel farm, substation, windsock, edge and approach lights, thresholds.
- **Landside (`<City> Airport Roads`):** departures on a deck above (ED), arrivals at grade, a loop, parking
  garages, a surface lot, rental cars, a hotel, and an access road to the nearest city street. Cargo trucks have
  their own road and never use the passenger curb.
- **Land use:** no city street inside the airport. In the noise zone (an ellipse round the runways) homes are
  dropped and plain parcels become warehouses.
- **Validator:** one connected taxiway graph, each runway reached by at least 2 connectors, every stand on the
  apron next to the taxiway, nothing tall in a runway's object-free area or approach, no water under a runway,
  cargo kept off the curb, and the landside connected to the city.
- **`road_network_airports(net_id)`** returns each airport's type, heading, grading, facilities, links, noise zone
  and validator result. The panel's city card shows the same.

Sizes are game approximations of FAA AC 150/5300-13B and 150/5360-13A, not certified designs.

## Generate again, any time
Regenerating is never blocked. A world on scanned ground keeps using that ground.

**A rebuild changes only what changed.** Generating again with nothing changed gives exactly the same world.
After an edit to one city, that city is placed again and everything else is put back as it stood:
- Every layer is placed unit by unit (`pcg_layers.preview`): a city's lots are one unit per city, scatter,
  roadside and junction pieces one unit per 500 m tile. A piece's size, height and shape come from its own spot
  (`_u01`), a tile's candidates from that tile's own random, a city's lots in an order of their own. Nothing
  depends on a random shared with another place.
- `pcg_layers.PreviewMemo` (one per world, kept by `pcg_preview`) remembers each unit and what it was placed
  from. The next run places again only the units within 100 m of a change: a road sample, a city that moved, a
  plan row (lot, block, private street) or a ground cell that is not what it was.
- The planner re-reads its last plan (`urban.plan` keep) and adds nothing when nothing changed.
- Still world-wide on every rebuild: the ground cut and fit, fences and the road stage. A move on a three-city
  10 km world takes about 30 s (it was about 110 s); nothing changed, about 6 s.
- The panel replaces each layer in place: what stood before stays on screen, only what is new rises.

**Cities close together or overlapping.**
- A city moved by hand (`road_network_city_save` with x, y) is `pinned`: it stays where it was put and pushes no
  other city away, however close (`citygen.spread`). Only cities never placed by hand are spread apart.
- Where two cities overlap, the bigger one keeps the land (`citygen.yield_plan`, `yielded`; the name settles a
  tie). The smaller city's streets, lots and freeways stop 30 m short of the bigger one's edge, so no street is
  laid on another and nothing is built twice. The layers read the shared land as the bigger city's districts.
- Cities whose edges are closer than 600 m are joined street to street (`citygen.joins`): five streets where
  they touch, one at 600 m, spread across the side where they face each other. Each joins the shortest pair of
  street corners a real street can run between: never through water or within 30 m of it, never through a keep
  zone, a ramp or an at-grade freeway of any city, never from a corner within 150 m of an interchange
  (`JOIN_OFF_IC_CM`), and never steeper than 8 % from one city's level to the other's (`JOIN_GRADE`). A slot
  with no such pair gets no street. No highway runs between two cities joined this way.
- Each city lays its own half of a joining street to the middle and grades it with its own roads, so
  `citygen.level_joins` then sets both halves' ends to one height: the street runs at one even grade from
  corner to corner. Two cities on different levels never leave a step in the middle.
- A city's streets give way to a neighbour's ramps, frontage roads and at-grade freeway where those reach over
  its land (`citygen.share_freeways` sets the city's `fw_near` from the neighbour's `Plan.low_points`), the
  same as to its own. A freeway ends before a neighbouring city's land (`citygen.shore_plan`): it never runs
  through the neighbour and takes its streets.
- The land along a joining street fills in with house lots (`citygen.join_lots`), and that corridor counts as
  housing for every layer (the city's `rezoned`), so no forest grows there.

The header badge
(`road_network_sync`) says whether UEFN is behind this world: road actors changed or never pushed, and
city tiles to build or remove. The next build in UEFN changes only those.

## City size, spacing and water
- **World tab:** `City size` (option `city_size`, ×0.3–4) scales every city, and regenerating scales the ones you
  have. `Distance between cities` (`city_gap_m`) is the open country between two cities' edges; cities closer
  than that are pushed apart (`citygen.spread`). Click a city on the map and set its radius (150 m – 5 km), road
  style or turn on the right side. That calls `road_network_city_save`, then regenerates.
- **Per-city features** (`citygen.FEATURES`, `city_options`): one city may differ from the world's choice for
  airports, shipyards, freeways, waterfront, canals and planner (city services). Use `road_network_city_save`
  with `features`, for example `{"airports": "none"}` or `{"shipyards": "auto"}`; null returns a feature to the
  world's choice. A city that asks for an airport gets at least an airfield. In the panel, the Cities list shows
  each city's features with x to remove one and + Add to add one.
- **Ground fit (1:1 with the imported landscape)**: a generated terrain is cut and filled to its roads
  (`citygen.grade_to_roads`). Every lot pad is cut into the heightmap a pixel wider than its building
  (`pad_margin`); where pads meet, the one a spot is deepest in wins.
  - `ground_fit` checks every building against the landscape exactly as UEFN builds it from the heightmap
    (`imported_ground`: the 16-bit samples, bilinear).
  - `settle` puts anything that would be buried or floating on the highest ground under it, with a foundation
    down to the lowest.
  - `road_network_pcg_preview(summary=true)`, the heightmap and the build brief report it as `ground_fit`:
    0 buried and 0 floating means it imports 1:1.
- **Water** (`citygen.over_water`, the terrain's water line `sea`): every road edge is sampled for water.
  - Highways and freeways always cross on a bridge: BRR ramp on land, then a BR / ED deck 7.5 m up, then a ramp.
  - `citygen.cross_water` runs first: a street whose junctions fall in the water becomes one edge from bank to
    bank. It follows the straightest street through the wet junctions, or else picks the nearest dry junction
    straight ahead. Then that edge is bridged.
  - Spans: 4L up to 1.5 km, 2S up to 600 m, 2L up to 200 m, 6L and freeways 3 km, any paved street a 60 m creek.
    A crossing wider than that is not built, so the street ends at its last dry junction.
  - When a junction sits close to the bank, the two ramps meet mid-span on a lower deck (8 % ramp, at least 3 m up).
  - A street into open water keeps its land part, cut 25 m inland of the water line (`SHORE_MARGIN_CM`).
  - Lots, parking and private streets are never in the water. There are no sidewalk splines on bridges.

## How a city is generated (the math, in order)
Roads define the city, blocks are what roads enclose, lots are cut from blocks, buildings fit lots.
1. **Planned grid** (`citygen.street_grid`, `city_graph`): one orientation per city; intersections on a block
   grid. A block is `block_m` (100) wide and `block_m` x `block_ratio` long (1 = square, up to 3 = long
   rectangles). Hierarchy: the two main axes are arterials, every `collector_every` (4th) line a collector, the
   rest local; `prune` (12 %) drops only local side streets (suburb cul-de-sacs allowed).
   - **Streets bow, they never jog** (`wobble`, 0 to 0.45 of a block, default 0.12; `_bow`): a street between
     two main roads follows one long seeded wave, so it bends as a whole and the streets beside it bend with it.
     0 = dead straight. A Grid city never bows, a Mixed one only past downtown, an Organic one everywhere.
   - Main roads (collectors, the axes) and the two streets under a freeway's viaduct stay dead straight:
     interchanges land on them exactly.
   - `green_lots` (0 to 0.6): that share of every block's lots is planted with trees instead of built on; one
     block can have its own share (`zones` with `green`).
2. **Grading** (`GRADE`, `st.drape(max_grade=...)`): steepest rise per road (6L 4 %, 4L 5 %, 2S 6 %, 2L / 1W
   8 %, dirt 10 %); steeper ground is cut and filled by the live splines. Bridges / ramps sit over the highest
   ground within 15 m.
3. **Blocks -> lots** (`parcels.py`, Vanegas 2012): faces of the street graph, inset by road half width +
   3.8 m sidewalk zone, cut into back-to-back strips then 16 m frontages (`LOT_W_CM` / `LOT_D_CM` 16 x 35 m);
   every lot fronts a street square on; corner lots front their short side; stored as each city's `lots`.
4. **Buildings**: layers narrower than 25 m (houses, shops, apartments, gas stations) fill lots, one per lot,
   set back from the front lot line; wider ones (offices, towers, civic, mansions, industry) take road frontage
   first and the lot fill keeps clear of them.
5. **Lot grading** (`_lot_ground`): level pad at the corners' mean, a foundation
   on every building (deeper where the ground falls away), the uphill side held by the graded ground, a
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
- **Saved presets** (a person's or an AI's own, kept with the plugin's data and usable on any world):
  `road_network_presets()` lists the built-in ones and the saved ones. `road_network_preset_save(name, net_id |
  layers, options, note)` makes or changes one: from a world's layers as they are now (`net_id`), or from a JSON
  list of layers; `options` keeps world options with it (for example `{"block_m": 80, "wobble": 0}`).
  `road_network_pcg_preset(net_id, preset="<name>", replace=true)` applies it; its `options` come back so the
  next `road_network_generate` can use them. `road_network_preset_delete(preset, confirm=true)` removes one.
  The Layers section of the panel lists them too (Use, delete, save this world's layers as one).
- A park block (`kind` park) is trees only: no layer builds on its road frontage either.
- A layer: `kind` scatter (Surface Sampler, `density` per 100 m2) | along_roads | buildings (Spline Sampler on
  the road actors, `spacing_m`, both sides at `setback_m`, facing the road); `where` outside | everywhere |
  cities | downtown | normal | housing (rings from each city's districts) | industrial | shipyards (every
  shipyard's paved platform); `avoid_roads`, `avoid` = earlier
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
