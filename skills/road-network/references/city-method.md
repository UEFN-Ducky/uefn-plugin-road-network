---
description: "How procedural cities are built in PCG (Epic's City Sample method, Polygon 2D, shape grammar) and how City Builder maps onto it."
metadata:
  label: "City Builder: the PCG city method"
  load_condition: "Before designing PCG city graphs, shape-grammar buildings or comparing City Builder with Epic's City Sample"
---

# How procedural cities are built (and where City Builder differs)

Epic's PCG City Sample (UE 5.8) and City Builder follow the same idea: a few hand-drawn shapes are cut into
smaller and smaller shapes, and everything else is spawned along the cut lines.

## The method
1. **Hand-drawn guides:** island or terrain outline, city outline, main arteries, highways, a high-rise zone,
   and landmark footprints as keep-out zones.
2. **Cut, then cut again:** the city outline is cut by the arteries into districts, again into sub-districts,
   then filled with a block grid aligned to one edge. Every cut line becomes a road with a category (artery,
   collector, local), and each category has its own width.
3. **Lots:** a block minus the road footprint, pulled in by the sidewalk. The leftover pieces are lot outlines.
4. **Buildings from the lot outline:** floors stacked in whole storeys, each wall filled with fixed-width
   modules by a grammar.
5. **Everything else along offset lines:** lamps, parked cars and street trees go at intervals on the kerb line
   (the block outline pushed out). Leftover lots become parks or parking.
6. **Stages in order:** each stage reads only what came before it, so changing a late stage rebuilds little
   and changing the outline rebuilds everything. Epic splits this into 18 graphs; City Builder has pipeline
   stages that rerun downstream only.

City Builder does steps 1–3 in Python (more control, testable, no editor cost) and uses PCG only for spawning.
That's deliberate: keep it.

## Grammar, in one minute
A grammar is a short string per wall or per storey:

| Syntax | Meaning |
|---|---|
| `[A,B,C]` | Sequence, in order |
| `X*` | Repeat to fill (zero or more) |
| `{A:3,B:1}` | Weighted random pick; whole-number weights only |
| `<A,B>` | Priority: as many of the biggest as fit, then the next |

`[Lobby*,Door,Lobby*]` centres a door; `[{Window:5,Band:1}*]` is a curtain wall with the odd band. Fixed
items survive a short wall middle-first, so the door stays and the corners go. Storeys snap to whole modules,
so a building is never a fraction of a floor tall.

City Builder's modular build uses this exact syntax. The grammars per style are listed by the describe tool;
change a style there, not per building.

## Polygon 2D (PCG nodes, UE 5.7+, present in UEFN 43)
- **Create Polygon 2D:** from a closed spline. Sample the spline by distance first; sampling "on interior"
  adds a false diagonal.
- **Polygon Operation:**
  - Union, Difference and Intersection.
  - Pairwise intersection keeps the clip shapes separate.
  - Inner intersection keeps only the area every input shares.
  - Exclusive or: shapes in the first input never merge; shapes in the second do.
  - Cut with paths cuts a shape with open splines (arteries into districts).
- **Offset Polygon:** a path through a shape splits into two lines, one each side (both kerbs from one centre
  line); a negative offset shrinks the shape. Nobody has documented its Open / Close modes yet.
- **Surface From Polygon 2D + Surface Sampler:** clean points inside a shape; high density gives a grid.
- In UEFN, a 2D polygon as a bounding shape returns 0 points. Intersect with tall-bounds points instead.

## What Epic's sample gets wrong (don't copy these)
- Crossing roads are overlapping pieces stacked at slightly different heights: visible flicker up close.
- The outer edge uses ordinary meshes on a curve: gaps outside the bend, overlaps inside.
- Trees are planted straight into concrete. Give street trees a pit.
- Only flat-roof exteriors, no interiors. City Builder has interiors; keep them on the storey grid.

## Lessons from Epic's team on working with an AI
- Expect pivot, overlap and rotation mistakes. Check one piece before placing thousands.
- Small scenes gain little. The win is in big moves: a district swap, a full re-layout.
- The output must stay editable by hand. Nothing the AI makes may be a black box.
