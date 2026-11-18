---
description: "Exact design of every City Builder piece: road cross-sections, bridges, elevated ramps, junction meshes, diamond / trumpet / cloverleaf / stack interchanges, street kit, buildings and their scaling, materials, paths, foundations and the PCG build. Numbers in cm."
metadata:
  label: "City Builder: how each piece is designed"
  load_condition: "Before making, changing, importing or placing any road, junction, interchange, street, building or material piece for City Builder"
---

# How each City Builder piece is designed

All numbers are centimetres, UE axes (X forward along a road, Y across, Z up). The code is the source of
truth; this page names where each number lives so a change stays in one place.

## 1. Road tiles (WorldGen/Roads, spline meshes)

A road is a landscape spline whose segments carry `SplineMeshes(0)=(Mesh=..., bScaleToWidth=False)`.

| Code | Mesh | Tile | Half width | Lanes / cross-section (from the centre line out) |
|---|---|---|---|---|
| `1W` | `SM_Road_OneLane_Straight10m` | 1000 | 300 | one lane, one way |
| `DT` | `SM_Road_Dirt_Straight10m` | 1000 | 375 | dirt, no paint |
| `2L` | `SM_Road_TwoLane_Straight10m` | 1000 | 525 | asphalt 0-525 at z 0; white edge line 485-505; yellow centre 0-10 |
| `2S` | `SM_Road_TwoLaneSidewalk_Straight10m` | 1000 | 875 | asphalt 0-480 (z 0), gutter 480-525 (z -2), curb 525-555 (z 17), paver band 555-615 (z 16), pavers 615-855 (z 16), back curb 855-875 (z 17) |
| `4L` | `SM_Road_FourLane_Straight10m` | 1000 | 1400 | asphalt 0-1005, gutter 1005-1050, curb 1050-1080, band 1080-1140, pavers 1140-1380, curb 1380-1400; white lane line 515-535, yellow 8-22 |
| `6L` | `SM_Road_Freeway_Lane20m` | 2000 | 1650 | asphalt 30-1590, concrete barriers 0-30 and 1590-1650 (85 tall); yellow 203-218, white 740-760 and 1280-1300 |

Rules every tile follows (`ROAD_TYPES` in `backend/citygen.py`, `OUR_FAMILIES` in `backend/splines_text.py`):
- Every vertex stays inside X 0..tile length, or the spline mesh smears at the joints.
- Deck top is z 0 at the centre line; a ground road's side walls (its skirt) go 3 m down from its underside
  (`road_profiles.SKIRT_CM`), so the road never floats on a slope and needs no separate wall piece.
- Road decks are drawn `SegmentMeshOffset` 6 cm up their points (`citygen.ROAD_OFFSET_CM`); junction decks sit at
  `road_io._OWN_DECK_Z` = 7 (the road's deck + 1 cm). Every push ends with `road_network_paint_ground`: Layer1 dirt
  under roads, junctions and building pieces (paint only; the layer is rewritten to exactly the current footprints, so moved roads leave no dirt).
- Materials: `M_Asphalt`, `M_PaintW`, `M_PaintY`, `M_Gutter`, `M_Curb`, `M_PaverBand`, `M_Pavers`, `M_Grate`, `M_BarrierConcrete`,
  slot order as listed in `OUR_FAMILIES[...]["materials"]`; road materials need **Used with Spline Meshes**.
- Points on a city street raise and lower the terrain (`bRaiseTerrain/bLowerTerrain=True`, SideFalloff 800, EndFalloff 1000).

## 2. Bridges and elevated roads

| Code | Mesh | Half width | Use |
|---|---|---|---|
| `BRR` | `SM_Road_Bridge_Ramp40m` | 925 | 40 m ramp from the ground up to the deck |
| `BR` | `SM_Road_Bridge_Straight20m` | 925 | 20 m deck |
| `ER` | `Elevated/SM_Elev_Ramp20m` | 650 | one-lane elevated ramp (loops, stack ramps) |
| `ED` | `Elevated/SM_Elev_Deck20m` | 1650 | elevated deck |

- **Overpass** (`overpass_via`): a highway crossing a street rises over 40 m (BRR) to a deck at `BRIDGE_LIFT_CM` = 750,
  holds it over the crossing (BR) and comes down over 40 m. Crossings closer than 400 m to either highway end are skipped.
- **Lifted** points and segments (on a ramp or deck) set `bRaiseTerrain/bLowerTerrain=False`: a bridge never shapes the ground.
- A segment's piece comes from the lift at its two ends: both up = deck, one up = ramp, none = the road's own tile.

## 3. Junction meshes (`SM_JK_<net>_<actor>_<node>`, WorldGen/Roads/Junctions/Kit)

Made by the listener (`_junction_geometry` in `listener/road_io.py`) with GeometryScript, one per node with 3+ arms:
1. Arms sorted by angle. For each gap between neighbouring arms: corner offset `c = W / tan(gap / 2) + 300`,
   W = the widest arm's half width (`half` = max mouth width). Each arm's mouth distance
   `d = clamp(max(c of its two gaps), 700, 3000)`.
2. Ring: at each mouth, the road's cross-section (9 profile points across the arm, scaled to W); between arms a
   quadratic fillet (6 steps) from one arm's edge through the corner point to the next arm's edge.
3. Fan triangles from the centre; a 50 cm skirt outward down to z -21 on every fillet (both faces) hides the edge on slopes.
4. **Own kit:** flat at z 2 (a hair above the deck so overlapping road ends never flicker), UVs = world X/Y / 400,
   material `WorldGen/Roads/M_Asphalt`, slot `Asphalt`. **Fortnite kit only:** Road1 crown (up to z 16), U strip 0.27 of
   `MI_Apollo_Roads_Straight_NO_RVT`. Never put the Fortnite material on our roads: its grass overlay shows through.
5. The node gets the piece (`place_generated`), every arm's mouth point moves to its distance `d` along the arm,
   tangents re-made. Rebuild in place after a change: `road_network_build_pieces(net, actor, rebuild=true)`.

## 4. Interchanges (`interchange()` in `backend/citygen.py`)

Local frame at each city end: `a` along the highway (toward the other city), `b` across. The crossing is at
`D` = 18000 (180 m) out from the highway's end; the highway is a 120 m deck (`deck` = 6000 each side) at 750 with
60 m ramps either side.

- **Cross road:** a `4L` from `b = -reach` to `+reach`, reach = loop offset + R + 6000. It is a **city street**:
  it goes in `<City> Roads` and both ends join the city's nearest street nodes with a `neighbourhood_roads` link.
  Never leave it in Highways (it dead-ends in a field).
- **Loop ramp** (cloverleaf: 4, trumpet: 1): radius `R` = 5500, 12 `ER` segments, 270 deg sweep. Centre at
  `a = i * (R + HALF[4L])`, `b = j * (R + HALF[highway])`, so the circle is tangent to both road edges.
  It starts on the highway edge (`b = j * HALF[highway]`) at the deck's height there
  (`lift * (1 - (|a| - deck) / deck)`, clamped 0..lift) and descends linearly to 0 where it meets the cross road edge
  (`a = i * HALF[4L]`). Sweep sign = `i * j` so it turns away from the crossing.
- **Slip ramp** (cloverleaf: 4, diamond: 4, trumpet: 1): `2L`, from the highway edge past the ramps
  (`a = i * (2 * deck + R)`, `b = j * (HALF[highway] + HALF[ER])`) to the cross road edge
  (`a = i * (HALF[4L] + HALF[ER])`, `b = j * (loop offset + R)`), bowed out through one mid point.
- **Stack:** from each end the highway runs `ER` ramp at 15 m (z 0) -> `ED` deck at 60 m (z 600) -> `ER` at 140 m
  (z 600) -> back on the highway at 220 m (z 0); the highway's own points within 260 m of the end give way.
- Only on highways longer than 900 m (cloverleaf / trumpet / diamond) or 700 m (stack).
- Ramps are in Highways, the cross road in the city: they meet at the road edges (no shared node). That is
  expected; a check that counts open ends will list the ramp ends.

## 5. Street kit (`_STREET_KIT` in `listener/road_io.py`, WorldGen/Kit/Street)

GeometryScript parts, origin at the base unless noted; slot materials in brackets.

| Mesh | Parts | Size |
|---|---|---|
| `SM_Kit_TrafficLight` | pole cyl r12 h560 [Metal]; arm box 380x15x15 at x 190, z 532 [Metal]; head box 40x40x110 at x 360, z 420 [Signal] | arm points +X: place it on the corner facing the road |
| `SM_Kit_StopSign` | pole cyl r4 h250 [Metal]; octagon (8-sided cyl r38, 4 thick, turned up) at z 252 [SignRed] | 2.9 m |
| `SM_Kit_StreetSign` | pole cyl r4 h300 [Metal]; two crossed plates 90x2x22 at z 290 / 314 [SignGreen] | 3.3 m |
| `SM_Kit_Billboard` | two posts 30x30x700 at y +-400 [Metal]; panel 30x1200x450 at z 700 [Panel, emissive] | 11.5 m |
| `SM_Kit_PowerPole` | cyl r18 h1100 [Wood]; crossbar 20x240x15 at z 1000 [Wood] | 11 m |
| `SM_Kit_ParkingLot` | box 100x100x8 [Asphalt] | scaled to the lot (X depth, Y width) |
| `SM_Kit_PathSlab` | box 100x100x8 [Pavers] | X = run length / 100, Y = path width (m), pitched along the slope |
| `SM_Kit_Plinth` | box 100x100x100 from z -100 to 0 [Plinth] | a building's foundation, two a building: the course (X/Y = building + 0.6 m, Z 0.5) and the wall under it (X/Y = building + 0.3 m, Z = foundation_m + 0.5) |
| `SM_Kit_ApproachWall` | box 100x100x100 from z 0 to 100 [Plinth] | under a deck or ramp too low for piers: X = 5.2 m along it, Y = its flat underside, Z = from 1 m in the ground up into the slab |

Simple collision comes from the parts. Sizes are mirrored in `KIT` in `backend/kit_bake.py`; change both together.

## 6. Buildings (`backend/kit_bake.py`)

- `KIT`: every kit mesh with its bounds (measured from the imported assets). Multi-part towers
  (`SM_City_DT_<Name>_P0..Pn`) share one pivot: P1+ start higher up, so **every part gets the same point**.
- `BUILDINGS[role]`: the kit buildings a role may use (skyscraper = the DT towers + Sky04..Sky16; office = MidRise,
  Tower3, SlabTower; apartment = Apartment, MidRise; shop = ShopRow/ShopRow2; house = HS_* + Bld_House_*;
  mansion = RC_Mansion parts, ModernVilla; hospital = Bld_Hospital; police / fire = Bld_Police; school = MidRise;
  factory = crane + containers; warehouse = containers).
- Per lot: the two buildings whose height is nearest the lot's `height_m`, one picked by position (a varied skyline);
  scale X = lot depth / mesh X, Y = lot width / mesh Y, Z = height / mesh height, each clamped to 0.5..2.0.
  The lot's yaw turns +X to face its street.
- `FLAT` roles (parking) stretch a flat piece to the lot and never up.
- `PROPS[role]`: street pieces keep the preview's scale; one of the role's meshes picked by position.
- Every building and flat lot gets its foundation (`foundation()`): the point's z is the floor, the course and
  the wall go down from it, `foundation_m` + 1 m deep. `foundation_m > 0.3` adds entrance steps; each wide path
  in `paths` becomes one slab per run. Parks are planted ground and get none.

## 6a. Interiors (`_interior()` in `backend/kit_bake.py`, meshes in `_STREET_KIT`)

Every building role in `INTERIOR` (not factories / warehouses) is filled per storey on one building-wide grid
(`STOREY_CM` 400 from the lot's base, so storeys run on through a tower's parts with no gap; the last storey under
the top part's roof is left out):
- **Slab** inset from the shell: 0.86 of the part's half size (round parts 0.66 as a circle, `SM_Sky*`, hospital,
  police 0.6), scaled from a 100 x 100 x 15 cm mesh with light panels (`Light` look, emissive 10) under it, so lit
  ceilings show through the glass. `SM_Kit_FloorPlate` (square well 30 % of the width in the middle),
  `SM_Kit_FloorRound` (16 radial boxes, round well radius 21 of 50), `SM_Kit_FloorSolid` (no well) when the well
  would be under `CORE_MIN_CM` 280.
- **Stairs + elevator** `SM_Kit_StairCore` in the well, one per storey, scaled 0.7-2.2x to the well (z never):
  400 x 400 x 400 cm, floor top at 15. Stairs on y < 10: floor landing x -200..-150, flight up +x
  (11 steps, 25 cm tread, 18.2 cm rise, strip y -190..-90), half landing at 215 (x 120..200), flight back -x
  (strip y -90..10) ending at 415 = the next storey's floor landing. Elevator shaft y 30..200, walls 10 cm, door
  opening 120 x 250 facing the stairs, lit indicator over it. `SM_Kit_ElevatorCar` (220 x 140 x 250, lit ceiling)
  once per building at the ground floor (it does not move: a working lift needs a Verse / device setup).
- **Furniture** on a ring 70 % of the way to the slab edge, about every 6 m, up to `SETS_PER_FLOOR` 8, facing the
  nearest window: `SM_Kit_DeskSet` (offices, towers, civic), `SM_Kit_SofaSet` (homes), `SM_Kit_ShopShelf` (shops).
- At most `MAX_FLOORS` 40 storeys per building (instance budget). Glass is `M_City_Glass` (section 7) so all of
  this shows from the street.

## 6b. Forests and parks

- Foliage layers (`Trees`, `Rocks`, `Bushes`) grow in **patches**: `patch(x, y)` (3-octave value noise, 600 m cells,
  shared by every foliage layer) below the layer's `cover` is forest, the rest meadow. Inside a patch `density` per
  100 m2 (Trees 8 = a tree about every 3.5 m, Bushes 4) out to `reach` 1.8 city radii around each city. Beyond
  that the forest goes on over the whole map, as dense as the cap leaves room for (never under a fifth). Up to
  `FOREST_LIMIT` (200 000) points per layer are kept and baked.
- All three are PCG layers (baked into the tiled 1.6 km nature tiles), from the Blender nature kit in
  `WorldGen/Nature` (BuildingKit.blend `BK_Nature`): the forest (foliage layers) is `kit_bake.PROPS["forest_tree"]`
  (giants 23-33 m, pines, birches, autumn trees, a dead tree); city trees (`tree`, `street_tree`) are the medium
  ones only. Bushes -> `SM_Kit_Bush_Round/Tall/Wide/Small`; Rocks -> 13 rock shapes, a boulder, two stumps and a
  fallen log. Trees collide on their bark only (leaf sections have collision off); bushes and ground plants none.
  Materials are instances of `M_BK_NatureFoliage_Master` / `M_BK_NatureSolid_Master` (scanned textures, wind in the
  vertex alpha). Every layer keeps off slopes over `max_slope_deg` and below the water.
- A park lot is a **grove**: a tree every 4 m on a jittered grid, 85 % filled, the middle row left open with a 2.5 m
  path slab along the lot.

## 7. Materials (`WorldGen/Materials`)

- `M_City_WorldPBR` (made once by the listener): triplanar world projection: WorldPosition / `TileCm`, three samples per
  map (YZ, XZ, XY) weighted by |vertex normal|^2 normalised; `Base` x `Tint` -> Base Colour, `Rough` (R) x `RoughMul`
  -> Roughness, `Metallic`, `Tint` x `Emissive` -> Emissive. Usage: **Instanced Static Meshes, Nanite, Spline Meshes**.
- `M_City_Glass` (window glass, every old material with "Glass" in its name): **Translucent**, lighting mode
  Surface Per-Pixel, two-sided, ISM usage; `Tint` -> Base Colour, `Opacity` 0.3, `Roughness` 0.05, `Metallic` 0.3.
  You see the floors through it and sun/sky light reaches inside (translucency casts no shadow-map shadow).
  Keep Opacity 0.2-0.4: lower looks like an empty frame, higher looks opaque again. Never Masked/Opaque for windows.
- Textures: the cinema PBR sets `Cinema/PBR/Textures/T_CinemaPBR_<Set>_{B,R}` (Concrete, Stone, Tile, Wood, Oak,
  Frame, Pavers, Rock, Asphalt...). World projection = a building stretched to fit its lot never smears.
- One `MI_City_<old>` per old flat-colour material (keeps the old colour as Tint); the look comes from the name
  (`_LOOKS`: Glass -> Tile, rough 0.15, metal 0.4; Brick -> Pavers; Siding -> Wood; Metal/Gold/Spire/Mullion ->
  Frame, metallic; Neon/LampLED -> emissive 6; default Concrete). Street kit: `MI_Street_<Look>`.
- A mesh spawned by PCG with a material lacking the ISM usage flag draws the grey checker.
- **Never change a kit mesh's own materials** (`StaticMesh.set_material`): it rebuilds the whole mesh, about 1.5 min
  and GBs each, all inside one editor frame (80 meshes froze UEFN for hours). The MIs go on as **PCG material
  overrides**: `road_network_kit_materials` returns `overrides` {mesh: [MI per slot]} and pcg_build puts them in
  each spawner's `templateDescriptor.descriptor.overrideMaterials`. New street-kit meshes get MIs when they are made.

## 8. The PCG build (`road_network_pcg_build`)

- One graph `WorldGen/CityPCG/PCG_<World>_T<key>` and one volume `City Builder <World> <key>` per tile: 400 m city
  tiles (`tx_ty`) and 1.6 km nature tiles (`ntx_ty`: trees, bushes, rocks), **Generate On Demand** (never on load).
- With Fortnite trees on (`road_network_pcg_assets trees="fortnite"`) the city's trees are not in these tiles:
  they are Fortnite tree props placed as actors after the PCG tiles (see systems: Pieces). The tree pits stay.
- Built in batches (`backend/pcg_batch.py`): one editor script per up to 8 tiles / 6000 points, run as a background
  job, memory metered per batch. A tile's old volume is found by its label in the level, never by a stored path.
- Per kit mesh: `Create Points` nodes (world space, cull off, at most 1500 points each, transform = x, y, z, pitch,
  yaw, scale) -> one `Static Mesh Spawner` with `{"meshSelector": {"meshes": [path], "templateDescriptor":
  {"descriptor": {"overrideMaterials": [MI per slot]}}}}`.
- Runs `road_network_kit_materials` first (street kit + materials): 10 meshes per editor call over WorldGen/City,
  Kit, Kit/Street, Kit/Facility and BuildingKit, meshes picked from the asset registry (never by loading every asset
  in a folder: 256 leftover materials there compiled shaders until UEFN passed 100 GB and crashed).

## 9. Adding a new piece (checklist)

1. Make it so its pivot is where it meets the ground (or the road's start for a tile), facing +X.
2. Road tile: keep X inside 0..tile, deck at z 0, its skirt as its profile gives it, add its row to `ROAD_TYPES` and `OUR_FAMILIES`.
3. Street piece or building: add its bounds to `KIT` (kit_bake) and its role to `PROPS` / `BUILDINGS`; a generated one
   also to `_STREET_KIT` (listener) with matching sizes.
4. Give every slot an `M_City_WorldPBR` instance (or another material with ISM usage) before PCG spawns it: when
   you make the mesh, or as PCG material overrides for an existing one. Never `set_material` on an existing mesh.
5. Save everything in one batch; never `save_loaded_asset` in a loop.
6. Run the self-checks (`py -3 -m backend.citygen`, `backend.pcg_layers`, `backend.kit_bake`) and `ducky_plugin_test`.
