"""Waterfront & marine development: water is a first-class part of the city plan, like roads and terrain.

WaterfrontGenerator
  ShorelineAnalyzer (`analyze`): the water line S(t) of the terrain grid (marching squares at the water level),
      resampled every 20 m, with per sample: tangent T = S'/|S'|, water normal N = (-T_y, T_x) (flipped to
      point at the water), signed curvature, shelter (share of rays from the water that hit land within
      400 m: bays, lakes and rivers are sheltered, open coast is not), water depth 30 m and 80 m out, water
      width along N, land slope, flood exposure (land within 1 m of the water 50 m inland) - and every
      waterbody (connected wet cells) typed ocean / lake / river by size, edge contact and shape.
  WaterfrontPlanner (`plan`): every 100 m of a city's shoreline gets one development type from depth, slope,
      shelter, the waterbody and the district: natural, beach, residential, commercial, marina, industrial,
      public. Neighbouring stretches of one type join into segments.
  Generators per segment type (all data, no meshes; the street kit / PCG builds them):
      commercial  - concrete quay seawall, 8 m promenade, a pier every 300 m, restaurants facing the water,
                    a ferry terminal on the longest stretch of a big waterbody
      marina      - MarinaGenerator: entrance (+ rock breakwater on open water), main pier, floating dock
                    branches, boat slips (with boats), gangway, boat launch, marina building and parking,
                    floating homes on the last branch when sheltered
      beach       - sand, a boardwalk on piles, a resort hotel with pools and a beach club, a long pier
      residential - a shared community marina on long stretches (compact, not one dock per house), else
                    private docks; stilt houses on pilings along a walkway in shallow water (fishing village)
      industrial  - PortAndHarborGenerator: quay wall, container cranes, container stacks on a yard, warehouses
      public      - waterfront park: promenade, fishing pier, the natural edge kept
      natural     - left natural: wetlands on flat shallow shores, nothing elsewhere (no seawall everywhere)
  WaterfrontConnectivityValidator (`validate`): piers start on land and end in water, never reach past a third
      of the water's width (navigation), piles reach the bed, land buildings are reached from a street,
      structures keep clear of each other.

Foundations: ground (land buildings), pilings (piers, boardwalks, stilt houses: piles from the bed to the
deck, deck above the flood level), floating (floating docks and homes: Z = Z_water + freeboard), quay (seawall).
Rows are [x, y, z, yaw, sx, sy, sz, kind] (z = the piece's bottom, yaw = its length direction, metres-scaled
for unit pieces), land buildings are lot rows [x, y, yaw, w, d, fx, fy, role, True].
"""

from __future__ import annotations

import math
import zlib

STEP = 2000.0  # shoreline sample spacing (cm)
CHUNK = 5  # samples per classified stretch (100 m)
FLOOD_CM = 100.0
DECK_ABOVE = 150.0  # fixed pier / boardwalk deck above the water
STILT_FLOOR = 350.0  # stilt house floor above the water (flood elevation)
FREEBOARD = 40.0  # floating dock / home deck above the water
PIER_W = 400.0
SLIP_W, SLIP_L = 450.0, 1200.0
NAV_SHARE = 0.3  # a pier never reaches past this share of the water's width
SETBACK = 2500.0  # public waterfront: no private lot this close to the water line
# pieces: kind -> (mesh, size at scale 1 (m), preview tint, layer name)
KINDS = {
    "deck": ("SM_Kit_DockDeck", [1, 1, 0.3], "mix:amber:fg-dim:0.55", "Piers and boardwalks"),
    "float": ("SM_Kit_FloatDock", [1, 1, 0.5], "mix:fg-dim:bg:0.15", "Floating docks"),
    "pile": ("SM_Kit_Piling", [0.3, 0.3, 1], "mix:amber:bg:0.6", "Pilings"),
    "slip": ("SM_Kit_BoatSlip", [12, 4.5, 0.05], "fg", "Boat slips"),
    "boat": ("SM_Kit_Boat", [9, 3, 2], "mix:fg:blue:0.15", "Boats"),
    "seawall": ("SM_Kit_Seawall", [1, 0.6, 1], "mix:fg-dim:bg:0.25", "Seawalls and quays"),
    "riprap": ("SM_Kit_Riprap", [1, 1, 1], "fg-dim", "Breakwaters"),
    "sand": ("SM_Kit_Sand", [1, 1, 0.1], "mix:amber:fg:0.6", "Beaches"),
    "wetland": ("SM_Kit_Wetland", [1, 1, 0.6], "mix:green:amber:0.35", "Wetlands"),
    "promenade": ("SM_Kit_PathSlab", [1, 1, 0.08], "mix:fg:bg:0.25", "Promenades"),
    "crane": ("SM_Kit_PortCrane", [30, 20, 45], "mix:red:fg-dim:0.4", "Port cranes"),
    "containers": ("SM_Kit_ContainerStack", [12.2, 2.5, 7.8], "mix:blue:red:0.5", "Container stacks"),
    "yard": ("SM_Kit_UtilityYard", [1, 1, 0.05], "mix:fg-dim:bg:0.5", "Port yards"),
    "launch": ("SM_Kit_BoatLaunch", [1, 1, 0.2], "mix:fg-dim:bg:0.3", "Boat launches"),
    "stilt_house": ("SM_Kit_StiltHouse", [10, 8, 6], "mix:fg-dim:amber:0.45", "Stilt houses"),
    "float_home": ("SM_Kit_FloatHome", [10, 7, 5], "mix:fg:amber:0.3", "Floating homes"),
    # shipyards (shipyard.py)
    "ship_hull": ("SM_Kit_ShipHull", [100, 20, 24], "mix:red:fg-dim:0.55", "Ships"),
    "goliath": ("SM_Kit_GoliathCrane", [20, 70, 80], "mix:red:amber:0.3", "Goliath gantry cranes"),
    "portal_crane": ("SM_Kit_PortalCrane", [36, 12, 45], "mix:amber:fg:0.3", "Portal cranes"),
    "tower_crane": ("SM_Kit_TowerCrane", [53, 6, 60], "amber", "Tower cranes"),
    "crane_rail": ("SM_Kit_CraneRail", [1, 1, 0.2], "fg-dim", "Crane rails"),
    "keel": ("SM_Kit_KeelBlock", [2, 1.5, 1.5], "mix:amber:bg:0.5", "Keel blocks"),
    "hardstand": ("SM_Kit_Apron", [1, 1, 0.1], "mix:fg-dim:bg:0.4", "Yard hardstand"),
    "steel_stack": ("SM_Kit_SteelStack", [12, 3, 1.5], "mix:fg-dim:blue:0.2", "Steel storage"),
    "rail": ("SM_Kit_RailTrack", [1, 3, 0.2], "mix:fg-dim:amber:0.2", "Rail sidings"),
    "float_drydock": ("SM_Kit_FloatingDryDock", [200, 45, 18], "mix:fg-dim:red:0.3", "Floating dry docks"),
    "tank": ("SM_Kit_FuelTank", [20, 20, 12], "mix:fg:bg:0.2", "Fuel tanks"),
    "fence": ("SM_Kit_Fence", [1, 0.1, 2.5], "fg-dim", "Security fences"),
    "boat_lift": ("SM_Kit_BoatLift", [12, 8, 10], "mix:blue:fg:0.4", "Boat lifts"),
    "offshore_module": ("SM_Kit_OffshoreJacket", [20, 20, 60], "mix:amber:red:0.4", "Offshore jackets"),
    "slipway": ("SM_Kit_Apron", [1, 1, 0.1], "mix:fg-dim:amber:0.35", "Slipways"),
    "scrap": ("SM_Kit_ScrapPile", [16, 16, 5], "mix:red:bg:0.5", "Scrap yards"),
    "dock_gate": ("SM_Kit_Seawall", [1, 0.6, 1], "mix:red:fg-dim:0.5", "Dock gates"),
    "gatehouse": ("SM_Kit_Gatehouse", [8, 6, 4], "fg-dim", "Gatehouses"),
    "quay_wall": ("SM_Kit_Seawall", [1, 0.6, 1], "mix:fg-dim:bg:0.25", "Shipyard quay walls"),
    # a port's piers (shipyard.build): a deck out over the water, walled down to the bed on its three sides
    "pier_deck": ("SM_Kit_Apron", [1, 1, 0.1], "mix:fg-dim:bg:0.4", "Port piers"),
    "pier_wall": ("SM_Kit_Seawall", [1, 0.6, 1], "mix:fg-dim:bg:0.25", "Pier walls"),
    "tug": ("SM_Kit_Boat", [9, 3, 2], "mix:fg:blue:0.15", "Tugboats"),
    "tug_float": ("SM_Kit_FloatDock", [1, 1, 0.5], "mix:fg-dim:bg:0.15", "Tug stations"),
}
# a shipyard's own pieces keep the height the yard built them at: the cut ground under a piece is not where it
# stands (a quay wall on the bed, a ship afloat, a gantry over its dock, a fence on the platform's very edge)
YARD_KINDS = ("ship_hull", "goliath", "portal_crane", "tower_crane", "crane_rail", "keel", "hardstand", "steel_stack",
              "rail", "float_drydock", "tank", "fence", "boat_lift", "offshore_module", "slipway", "scrap", "dock_gate",
              "gatehouse", "quay_wall", "tug", "tug_float", "pier_deck", "pier_wall")
NO_PAD = ("hardstand", "crane_rail", "tug_float", "pier_deck", "pier_wall")  # ... and these level no ground under them (pcg_layers.pads)
TYPES = ("natural", "beach", "residential", "commercial", "marina", "industrial", "public")

# ------------------------------------------------------------------ ShoreWorks: the made shore
# Where a city builds on its shore, the shore is made for what stands there, the way real waterfronts are: a quay
# wall with a level promenade on top, a beach graded from the water up to a boardwalk, a lawn rising to a row of
# houses. Behind it the land comes back to its own height as a bank. The planner decides stretch by stretch
# (shores_for); citygen.cut_shores cuts the profile into the ground the landscape is made from, the streets are
# laid on it, and every piece is built at its design level (never re-seated on whatever lies under it).
YARD_DEPTH_CM = 8000.0  # a container port's paved yard, inland from its quay
QUAY_CM = 200.0  # a quay, its promenade and the boardwalk behind a beach stand this far above the water
STEEP = 0.6  # a shore steeper than this in its first 30 m stays as it lies (a cliff) ...
RISE_MAX_M = 18.0  # ... and so does one that stands this high 50 m inland: too much hill to cut for a promenade
BANK = 0.5  # behind a made shore the land comes back to its own height no steeper than this
BANK_MAX_CM = 6000.0
WALL_DEEP_CM = 2000.0  # a quay wall goes down to the bed, or this far under the water where the bed is deeper
TAPER = 3  # samples at each end of a stretch over which the made shore fades into the natural one
MADE_MIN = 8  # samples (160 m): a shorter stretch is left as it lies
# use -> how deep the made shore is, inland from the water line (cm)
DEPTH = {"commercial": 5000.0, "marina": 4200.0, "public": 2400.0, "industrial": 2400.0, "beach": 5400.0,
         "residential": 5800.0}
BEACH_IN, BEACH_OUT, BEACH_RISE = 4000.0, 2000.0, 0.025  # sand from 20 m out to 40 m inland, rising 1 in 40
# homes on the water: a low bulkhead at the water's edge, a lawn rising 30 m from it to the house row
LAWN_IN, LAWN_CM, HOUSE_CM = 3000.0, 120.0, 450.0
HOUSE_AT, HOUSE_LOT = 4300.0, (1700.0, 2400.0)  # the row's middle, and each lot (along the shore, deep)
# what a city makes of the shore outside its core, a 300 m block at a time (each block's own draw)
MIX = {"normal": ((0.35, "public"), (0.55, "beach"), (0.75, "marina"), (1.0, "residential")),
       "housing": ((0.5, "residential"), (0.68, "beach"), (0.8, "marina"), (1.0, "public"))}
BLOCK = 15  # samples in a block
# these stand where they were built (afloat, on piles, on the bed, on a made shore), and these level no ground
OWN_HEIGHT = ("deck", "float", "pile", "slip", "boat", "float_home", "stilt_house", "riprap", "seawall", "sand",
              "promenade", "launch")
NO_PAD += ("deck", "float", "riprap", "sand", "promenade", "launch")


def yard_spot(roads: list, x: float, y: float, tx: float, ty: float, nx: float, ny: float, length: float,
              within: bool = False):
    """Where a container yard goes: its spot (x, y), shore direction (tx, ty) and inland normal (nx, ny), length
    along the shore; shortened, made shallower and slid until no street (roads: (x0, y0, x1, y1, half width) cm)
    crosses its paving: the biggest yard that fits, the nearest its planned spot of those. The yard was laid behind
    the quay wherever that fell, and a street with its junction ran through the middle of the container stacks
    (Valley 2_1, Nov 17 2026). Planning (within=False) it may slide 60 m along the shore and 30 m inland, never
    toward the water (the bank is there). A yard already built (within=True) stays on its own levelled platform: the
    part of it no street crosses (moved off it, it stood on the bank 18 m above the ground, and the hill covered
    its inland side). Returns (x, y, length, depth), or None: no yard here."""
    near = [r for r in roads if min(math.hypot(r[0] - x, r[1] - y), math.hypot(r[2] - x, r[3] - y))
            < length + 2 * YARD_DEPTH_CM + math.hypot(r[2] - r[0], r[3] - r[1])]
    pts = []
    for x0, y0, x1, y1, h in near:  # every 3 m along each street, with its half width
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) // 300))
        pts += [(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n, h) for k in range(n + 1)]

    def clear(cx, cy, hl, hd):
        return not any(abs((px - cx) * tx + (py - cy) * ty) < hl + h + 300
                       and abs((px - cx) * nx + (py - cy) * ny) < hd + h + 300 for px, py, h in pts)
    best = None  # (area, -move, spot)
    for L in (length * s for s in (1.0, 0.8, 0.6, 0.45, 0.35)):
        for D in (YARD_DEPTH_CM * s for s in (1.0, 0.75, 0.5)):
            if L < 4000 or (best and L * D < best[0]):
                continue
            if within:  # (500 cm steps over the old footprint)
                sl, bk = (length - L) / 2, (YARD_DEPTH_CM - D) / 2
                slides = [s * 500.0 for s in range(-int(sl // 500), int(sl // 500) + 1)]
                backs = [b * 500.0 for b in range(-int(bk // 500), int(bk // 500) + 1)]
            else:
                slides = [s * 1000.0 for s in range(-6, 7)]
                backs = [0.0, 1500.0, 3000.0]
            for back in backs:
                for slide in slides:
                    cx, cy = x + tx * slide + nx * back, y + ty * slide + ny * back
                    move = math.hypot(slide, back)
                    if (not best or (L * D, -move) > best[:2]) and clear(cx, cy, L / 2, D / 2):
                        best = (L * D, -move, (cx, cy, L, D))
    return best[2] if best else None
def _dice(*key) -> float:
    """A draw in [0, 1) that belongs to its key: the same stretch of the same shore draws the same every run."""
    return zlib.crc32(repr(key).encode()) / 4294967296.0


def level_of(use: str, d: float) -> float:
    """A made shore's height above the water d cm inland from the water line (0 <= d <= DEPTH[use])."""
    if use == "beach":
        return d * BEACH_RISE if d <= BEACH_IN else QUAY_CM
    if use == "residential":
        return LAWN_CM + (HOUSE_CM - LAWN_CM) * min(1.0, d / LAWN_IN)
    return QUAY_CM


def profile(use: str, d: float, z: float, sea: float) -> float:
    """The ground of a made shore d cm inland from the water line (negative: out over the water), from what it
    was (z). A quay: its wall at the water line (the bed 3 m down along it), level behind. A beach: sand from
    BEACH_OUT in the water up to BEACH_IN, then the boardwalk a metre above it. Homes: a bulkhead at the water,
    a lawn up to the house row. Behind any of them the land as it was, brought back to its own height as a bank no steeper than BANK."""
    W = DEPTH[use]
    if d > W:
        if z < sea:
            return z  # (water behind a spit stays water)
        top = sea + level_of(use, W)
        return max(top - BANK * (d - W), min(top + BANK * (d - W), z))
    if d >= 0:
        return sea + level_of(use, d)
    if use == "beach":
        return sea + d * BEACH_RISE if d >= -BEACH_OUT else z
    if use == "residential":
        return min(z, sea - 150.0) if d > -1000.0 else z  # (water at the bulkhead)
    return min(z, sea - 300.0) if d > -1500.0 else z


class Made:
    """The made shores of some cities (city['shores']), to look a point up: which stretch it lies by, how far
    inland of that stretch's water line, and how much of the made profile applies there (a stretch fades into
    the natural shore over its last TAPER samples and past its ends)."""
    CELL = 15000.0

    def __init__(self, cities: list):
        self.grid: dict = {}
        self.rows: list = []
        for c in cities:
            for sh in c.get("shores") or []:
                pts, n = sh["pts"], len(sh["pts"])
                for k, p in enumerate(pts):
                    t = min(1.0, min(k, n - 1 - k) / TAPER)
                    row = (p[0], p[1], p[2], p[3], t * t * (3 - 2 * t), sh, p[4] if len(p) > 4 else 0.0)
                    self.rows.append(row)
                    self.grid.setdefault((int(p[0] // self.CELL), int(p[1] // self.CELL)), []).append(row)
        self.n = len(self.rows)

    def at(self, x: float, y: float):
        """(stretch, cm inland of its water line, share of the made profile) for the nearest made shore, or None."""
        if not self.n:
            return None
        cx, cy = int(x // self.CELL), int(y // self.CELL)
        best, bd = None, 1e18
        for i in (cx - 1, cx, cx + 1):
            for j in (cy - 1, cy, cy + 1):
                for q in self.grid.get((i, j), ()):
                    dd = (x - q[0]) ** 2 + (y - q[1]) ** 2
                    if dd < bd:
                        best, bd = q, dd
        if best is None:
            return None
        px, py, nx, ny, w, sh, clear = best
        dx, dy = x - px, y - py
        side = abs(-dx * ny + dy * nx)  # along the shore from that sample: past a stretch's end it fades out
        if side > 1500.0:
            w *= max(0.0, 1.0 - (side - 1500.0) / 2500.0)
        self.clear = clear
        return sh, -(dx * nx + dy * ny), w

    def no_street(self, x: float, y: float) -> bool:
        """Is this a place no street is laid: on a made shore where the land was cut down by more than 2 m for
        it? A street there would stand on a ramp above the promenade (its own grade can't follow the bank)."""
        got = self.at(x, y)
        return bool(got and got[2] > 0.3 and -500.0 <= got[1] <= self.clear)

    def level(self, x: float, y: float, z: float) -> float:
        """The ground at a point once the shores are made, from what it was (z)."""
        got = self.at(x, y)
        if not got:
            return z
        sh, d, w = got
        if w <= 0 or d < -2500.0 or d > DEPTH[sh["use"]] + sh.get("bank", 0.0) + 1500.0:
            return z
        return z + (profile(sh["use"], d, z, sh["sea"]) - z) * w

    def shelf(self, x: float, y: float, margin: float = 0.0):
        """The stretch whose made ground (water line to back edge) a point stands on, or None."""
        got = self.at(x, y)
        if got and got[2] > 0.5 and -margin <= got[1] <= DEPTH[got[0]["use"]] + margin:
            return got[0]
        return None


# ------------------------------------------------------------------ ShorelineAnalyzer
def _cell(ter):
    n = int(ter["grid"])
    lo, hi = ter["min"], ter["max"]
    return n, lo, (hi[0] - lo[0]) / n, (hi[1] - lo[1]) / n


def _contours(ter, sea):
    """Marching squares on the grid's cell centres at the water level -> polylines [(x, y)]."""
    n, lo, cx, cy = _cell(ter)
    h = ter["heights"]
    P = lambda i, j: (lo[0] + (i + 0.5) * cx, lo[1] + (j + 0.5) * cy)
    segs = []

    def edge(a, b, ha, hb):
        t = (sea - ha) / ((hb - ha) or 1e-9)
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    for j in range(n - 1):
        for i in range(n - 1):
            v = [h[j][i], h[j][i + 1], h[j + 1][i + 1], h[j + 1][i]]
            if None in v:
                continue
            p = [P(i, j), P(i + 1, j), P(i + 1, j + 1), P(i, j + 1)]
            pts = []
            for k in range(4):
                a, b = k, (k + 1) % 4
                if (v[a] < sea) != (v[b] < sea):
                    pts.append(edge(p[a], p[b], v[a], v[b]))
            if len(pts) == 2:
                segs.append(tuple(pts))
            elif len(pts) == 4:  # saddle: pair them in order
                segs += [(pts[0], pts[1]), (pts[2], pts[3])]
    # chain the segments into polylines
    key = lambda p: (round(p[0], 0), round(p[1], 0))
    ends: dict = {}
    for k, (a, b) in enumerate(segs):
        ends.setdefault(key(a), []).append((k, 0))
        ends.setdefault(key(b), []).append((k, 1))
    used = [False] * len(segs)
    lines = []
    for k in range(len(segs)):
        if used[k]:
            continue
        used[k] = True
        line = [segs[k][0], segs[k][1]]
        for forward in (True, False):
            while True:
                tip = line[-1] if forward else line[0]
                nxt = next(((m, e) for m, e in ends.get(key(tip), ()) if not used[m]), None)
                if not nxt:
                    break
                m, e = nxt
                used[m] = True
                p = segs[m][1 - e]
                if forward:
                    line.append(p)
                else:
                    line.insert(0, p)
        lines.append(line)
    return lines


def _resample(line, step=STEP):
    out = [line[0]]
    carry = 0.0
    for p, q in zip(line, line[1:]):
        L = math.dist(p, q)
        d = step - carry
        while d <= L:
            t = d / L
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
            d += step
        carry = (carry + L) % step if L else carry
        carry = L - (d - step) if L else carry
    if math.dist(out[-1], line[-1]) > step * 0.3:
        out.append(line[-1])
    for _ in range(2):  # smooth the grid's staircase
        out = [out[0]] + [((a[0] + 2 * b[0] + c[0]) / 4, (a[1] + 2 * b[1] + c[1]) / 4)
                          for a, b, c in zip(out, out[1:], out[2:])] + [out[-1]]
    return out


def bodies(ter, sea) -> tuple:
    """Connected wet cells -> (label grid, {id: {type, area_m2, width_m, cells}})."""
    n, lo, cx, cy = _cell(ter)
    h = ter["heights"]
    lab = [[-1] * n for _ in range(n)]
    info = {}
    nid = 0
    for j in range(n):
        for i in range(n):
            if lab[j][i] >= 0 or h[j][i] is None or h[j][i] >= sea:
                continue
            todo, cells, edge, border = [(i, j)], 0, 0, False
            lab[j][i] = nid
            while todo:
                a, b = todo.pop()
                cells += 1
                if a in (0, n - 1) or b in (0, n - 1):
                    border = True
                for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    x, y = a + da, b + db
                    if not (0 <= x < n and 0 <= y < n):
                        continue
                    if h[y][x] is not None and h[y][x] < sea:
                        if lab[y][x] < 0:
                            lab[y][x] = nid
                            todo.append((x, y))
                    else:
                        edge += 1
            area = cells * cx * cy / 1e4
            perim = max(1.0, edge * cx / 100)
            width = 2 * area / perim
            if cells < 6:  # a puddle in the grid, not a waterbody: nothing is planned on it
                nid += 1
                continue
            if border and cells > n * n * 0.15:
                kind = "ocean"
            elif width < 250 and perim > 6 * width:
                kind = "river"
            else:
                kind = "lake"
            info[nid] = {"id": nid, "type": kind, "area_m2": round(area), "width_m": round(width), "cells": cells}
            nid += 1
    return lab, info


def analyze(ter: dict) -> dict:
    """ShorelineAnalyzer: waterbodies and shorelines (samples with geometry and site properties)."""
    from . import splines_text as st
    sea = (ter or {}).get("sea")
    if sea is None or not (ter or {}).get("heights"):
        return {"sea": None, "bodies": [], "shores": []}
    ground = lambda x, y: st.sample_grid(ter, x, y)
    wet = lambda x, y: (ground(x, y) if ground(x, y) is not None else sea + 1) < sea
    lab, info = bodies(ter, sea)
    n, lo, cx, cy = _cell(ter)

    def body_at(x, y):
        i, j = int((x - lo[0]) / cx), int((y - lo[1]) / cy)
        return lab[j][i] if 0 <= i < n and 0 <= j < n else -1
    shores = []
    for line in _contours(ter, sea):
        pts = _resample(line)
        if len(pts) < 4:
            continue
        samples = []
        for k, p in enumerate(pts):
            a, b = pts[max(0, k - 1)], pts[min(len(pts) - 1, k + 1)]
            tx, ty = b[0] - a[0], b[1] - a[1]
            L = math.hypot(tx, ty) or 1.0
            tx, ty = tx / L, ty / L
            nx, ny = -ty, tx
            if not wet(p[0] + nx * 1500, p[1] + ny * 1500) and wet(p[0] - nx * 1500, p[1] - ny * 1500):
                nx, ny = -nx, -ny
            # signed curvature: turning per length (+ when the shore bends round the water: a bay)
            if 0 < k < len(pts) - 1:
                ax, ay = p[0] - a[0], p[1] - a[1]
                bx, by = b[0] - p[0], b[1] - p[1]
                turn = math.atan2(ax * by - ay * bx, ax * bx + ay * by)
                curv = turn / (STEP / 100) * (1 if (bx - ax) * nx + (by - ay) * ny < 0 else -1)
            else:
                curv = 0.0
            # shelter: rays from a point 60 m out over the water that meet land within 400 m
            ox, oy = p[0] + nx * 6000, p[1] + ny * 6000
            hits = 0
            for r in range(12):
                ang = r * math.pi / 6
                dx, dy = math.cos(ang), math.sin(ang)
                if any(not wet(ox + dx * d, oy + dy * d) for d in range(5000, 40001, 5000)):
                    hits += 1
            width = 0.0
            for d in range(1000, 300001, 1000):
                if not wet(p[0] + nx * d, p[1] + ny * d):
                    break
                width = d
            gz = lambda d: ground(p[0] + nx * d, p[1] + ny * d)
            land = gz(-3000)
            inland = gz(-5000)
            d30, d80 = gz(3000), gz(8000)
            samples.append({"x": round(p[0]), "y": round(p[1]), "tx": round(tx, 4), "ty": round(ty, 4),
                            "nx": round(nx, 4), "ny": round(ny, 4), "curv": round(curv, 4),
                            "shelter": round(hits / 12, 2), "width_m": round(width / 100),
                            "depth30_m": round((sea - d30) / 100, 1) if d30 is not None else 0.0,
                            "depth80_m": round((sea - d80) / 100, 1) if d80 is not None else 0.0,
                            "slope": round(((land if land is not None else sea) - sea) / 3000, 3),
                            "rise_m": round(((inland if inland is not None else sea) - sea) / 100, 1),
                            "flood": bool(inland is not None and inland - sea < FLOOD_CM)})
        b = body_at(samples[len(samples) // 2]["x"] + samples[len(samples) // 2]["nx"] * 3000,
                    samples[len(samples) // 2]["y"] + samples[len(samples) // 2]["ny"] * 3000)
        if b not in info and len(samples) < 10:
            continue  # the rim of a puddle
        shores.append({"id": len(shores), "body": b, "type": info.get(b, {}).get("type", "lake"),
                       "length_m": round(len(samples) * STEP / 100), "samples": samples})
    return {"sea": sea, "bodies": list(info.values()), "shores": shores}


# ------------------------------------------------------------------ WaterfrontPlanner
def classify(s: dict, water_type: str, ring: str, industrial: bool, pop: int, dice: float = 0.5) -> str:
    """One stretch's development type from its site and the district (the planner's rules). dice: the draw of
    the 300 m block of shore it lies in, which picks from the district's mix (MIX) outside the core."""
    gentle, cliff = s["slope"] < 0.06, s["slope"] > STEEP or s.get("rise_m", 0.0) > RISE_MAX_M
    deep, shallow = s["depth80_m"] >= 4.0, s["depth30_m"] < 2.0
    sheltered = s["shelter"] >= 0.5 or water_type in ("lake", "river")
    if ring == "outside":
        return "wetland" if gentle and shallow else "natural"
    if industrial and deep and water_type in ("ocean", "river", "lake") and pop >= 8000:
        return "industrial"
    if cliff:
        return "natural"
    if water_type == "ocean" and gentle and shallow and not sheltered:
        return "beach"
    if ring == "core":
        return "commercial"
    kind = next((k for share, k in MIX.get(ring, MIX["housing"]) if dice < share), "public")
    if kind == "marina" and not (sheltered and s["depth80_m"] >= 2.5 and s["width_m"] >= 120):
        kind = "public"
    if kind == "beach" and (water_type == "river" or s["width_m"] < 150):
        kind = "public"  # (a beach looks out over open water)
    return kind


class _Out:
    def __init__(self):
        self.rows: list = []
        self.lots: list = []
        self.devs: list = []
        self.checks: list = []  # (what, ok, detail)

    def row(self, x, y, z, yaw, sx, sy, sz, kind):
        self.rows.append([round(x), round(y), round(z), round(yaw, 1), round(sx, 2), round(sy, 2), round(sz, 2), kind])


def _yaw(dx, dy):
    return math.degrees(math.atan2(dy, dx))


def _piles(out, ground, x0, y0, dx, dy, length, half, deck_z, step=600.0):
    """Two rows of piles under a deck from (x0, y0) along (dx, dy), down to the bed."""
    nx, ny = -dy, dx
    for k in range(int(length // step) + 1):
        for side in (-1, 1):
            px, py = x0 + dx * k * step + nx * side * half, y0 + dy * k * step + ny * side * half
            bed = ground(px, py)
            bed = deck_z - 300 if bed is None else bed
            if bed < deck_z - 20:
                out.row(px, py, bed, 0, 1, 1, (deck_z - bed) / 100, "pile")


def pier(out, ground, sea, s, length, width=PIER_W, z_top=None, why="pier", back=0.0):
    """A fixed pier on piles straight out along the water normal, starting `back` cm inland of the water line
    (a beach pier leaves from the boardwalk and crosses the sand)."""
    nav = max(2000.0, s["width_m"] * 100 * NAV_SHARE)
    length = min(length, nav)
    z = (sea + DECK_ABOVE) if z_top is None else z_top
    x0, y0 = s["x"] - s["nx"] * back, s["y"] - s["ny"] * back
    total = length + back
    out.row(x0 + s["nx"] * total / 2, y0 + s["ny"] * total / 2, z - 30, _yaw(s["nx"], s["ny"]), total / 100,
            width / 100, 1, "deck")
    _piles(out, ground, s["x"], s["y"], s["nx"], s["ny"], length, width / 2 - 30, z)
    out.checks.append(("pier", length <= nav + 1, f"{why}: {round(length / 100)} m of {s['width_m']} m water"))
    return length


def marina(out, ground, sea, s, water_type, sheltered, slips_wanted=60):
    """MarinaGenerator: main pier, floating branches with slips and boats, gangway, launch, breakwater."""
    nx, ny, tx, ty = s["nx"], s["ny"], s["tx"], s["ty"]
    L = min(14000.0, s["width_m"] * 100 * NAV_SHARE)
    if L < 4000:
        return 0
    x0, y0 = s["x"] + nx * 800, s["y"] + ny * 800  # the gangway lands the pier past the shallows
    out.row(s["x"] + nx * 400, s["y"] + ny * 400, sea + DECK_ABOVE - 30, _yaw(nx, ny), 8, 2.5, 1, "deck")  # gangway
    out.row(x0 + nx * L / 2, y0 + ny * L / 2, sea + FREEBOARD - 50, _yaw(nx, ny), L / 100, 3, 1, "float")  # main pier
    slips, k, branch = 0, 0, 2000.0
    while branch < L - 500 and slips < slips_wanted:
        bx, by = x0 + nx * branch, y0 + ny * branch
        for side in (-1, 1):
            arm = 3600.0
            out.row(bx + tx * side * (arm / 2 + 150), by + ty * side * (arm / 2 + 150), sea + FREEBOARD - 50,
                    _yaw(tx, ty), arm / 100, 2, 1, "float")
            for m in range(int(arm // SLIP_W)):
                along = 150 + (m + 0.5) * SLIP_W
                for off in (-1, 1):  # slips both sides of the branch
                    sx = bx + tx * side * along + nx * off * (100 + SLIP_L / 2)
                    sy = by + ty * side * along + ny * off * (100 + SLIP_L / 2)
                    out.row(sx, sy, sea, _yaw(nx, ny), 1, 1, 1, "slip")
                    slips += 1
                    if (slips * 7) % 10 < 7:  # ~70 % of slips taken
                        out.row(sx, sy, sea - 40, _yaw(nx, ny), 1, 1, 1, "boat")
        branch += 2 * SLIP_L + 400
        k += 1
    # floating homes on the outer branch side when the water is calm
    if sheltered:
        for m in range(4):
            hx = x0 + nx * (L - 600) + tx * (m - 1.5) * 1400
            hy = y0 + ny * (L - 600) + ty * (m - 1.5) * 1400
            out.row(hx, hy, sea + FREEBOARD - 50, _yaw(tx, ty), 12, 9, 1, "float")
            out.row(hx, hy, sea + FREEBOARD, _yaw(tx, ty), 1, 1, 1, "float_home")
    elif water_type == "ocean":  # open water: a rock breakwater shelters the entrance
        for m in range(9):
            a = m / 8
            bx = x0 + nx * (L + 1500) + tx * (a - 0.5) * 9000
            by = y0 + ny * (L + 1500) + ty * (a - 0.5) * 9000
            bed = ground(bx, by)
            bed = sea - 400 if bed is None else bed
            out.row(bx, by, bed, _yaw(tx, ty), 11, 5, (sea + 200 - bed) / 100, "riprap")
    # boat launch beside it
    lx, ly = s["x"] + tx * 3500, s["y"] + ty * 3500
    out.row(lx + nx * 500, ly + ny * 500, sea - 100, _yaw(nx, ny), 15, 5, 1, "launch")
    out.checks.append(("marina", L <= s["width_m"] * 100 * NAV_SHARE + 1, f"marina pier {round(L / 100)} m, {slips} slips"))
    return slips


def segments_for(city: dict, analysis: dict, ring_of, industrial, population: int = 0) -> list:
    """The city's shoreline in classified stretches (shipyard.sites uses them before the streets exist)."""
    if analysis.get("sea") is None:
        return []
    R = city["radius"]
    near = lambda s: math.hypot(s["x"] - city["x"], s["y"] - city["y"]) < R * 1.25
    segments = []
    for sh in analysis["shores"]:
        S = sh["samples"]
        if not any(near(s) for s in S):
            continue
        for k in range(0, len(S), CHUNK):
            part = S[k:k + CHUNK]
            mid = part[len(part) // 2]
            inside = math.hypot(mid["x"] - city["x"], mid["y"] - city["y"]) < R * 1.05
            ring = ring_of(mid["x"], mid["y"]) if inside else "outside"
            kind = classify(mid, sh["type"], ring, bool(industrial and industrial(mid["x"], mid["y"])), population,
                            _dice(city.get("seed"), city.get("name"), sh["id"], k // BLOCK))
            if segments and segments[-1]["shore"] == sh["id"] and segments[-1]["type"] == kind:
                segments[-1]["samples"] += part
            else:
                segments.append({"shore": sh["id"], "water": sh["type"], "type": kind, "samples": list(part)})
    for seg in segments:
        seg["length_m"] = round(len(seg["samples"]) * STEP / 100)
    return segments


def shores_for(city: dict, analysis: dict, ring_of, industrial, population: int = 0, ground=None,
               taken: set | None = None) -> list:
    """ShoreWorks planner: the stretches of the city's shore that are made for what is built on them, as
    [{use, water, sea, bank, pts: [[x, y, nx, ny], ...]}] (city['shores']). A stretch is made when its use builds
    (DEPTH), it is MADE_MIN samples long and no shipyard has it; marinas past the city's count become waterfront
    park. bank: how far behind the made shore the land needs to come back to its own height (from `ground`).
    taken: samples another city has already made (and the ones made here are added)."""
    from . import shipyard
    sea = analysis.get("sea")
    if sea is None:
        return []
    yards = (city.get("shipyards") or []) + (city.get("shipyards_near") or [])
    free = lambda s: not any(shipyard.reserved(y, s["x"], s["y"], 4000) for y in yards) and \
        (taken is None or (s["x"], s["y"]) not in taken)
    out, marinas = [], 0
    segs = segments_for(city, analysis, ring_of, industrial, population)
    for seg in sorted(segs, key=lambda g: (-g["length_m"], g["shore"], g["samples"][0]["x"], g["samples"][0]["y"])):
        use = seg["type"]
        if use not in DEPTH:
            continue
        runs, cur = [], []
        for s in seg["samples"]:  # the longest run of it no yard or neighbour has
            if free(s):
                cur.append(s)
            else:
                runs.append(cur)
                cur = []
        run = max(runs + [cur], key=len)
        if len(run) < MADE_MIN:
            continue
        if use == "marina":
            if marinas >= max(1, population // 25000):
                use = "public"
            else:
                marinas += 1
        out.append({"use": use, "water": seg["water"], "sea": sea, "bank": 0.0, "run": run})
        if taken is not None:
            taken.update((s["x"], s["y"]) for s in run)
    # every city on open water has a beach: with none drawn, its gentlest park or row of homes becomes one
    if out and not any(sh["use"] == "beach" for sh in out):
        can = [sh for sh in out if sh["use"] in ("public", "residential") and sh["water"] != "river"
               and sorted(s["width_m"] for s in sh["run"])[len(sh["run"]) // 2] >= 150]
        if can:
            min(can, key=lambda sh: (sum(s["slope"] for s in sh["run"]) / len(sh["run"]), -len(sh["run"])))["use"] = "beach"
    for sh in out:
        use, run = sh["use"], sh.pop("run")
        W, top = DEPTH[use], sea + level_of(use, DEPTH[use])
        made_at = lambda d: sea + level_of(use, d) if d <= W else top + BANK * (d - W)
        clear = [0.0] * len(run)
        if ground:
            def high(b):
                zs = [ground(s["x"] - s["nx"] * (W + b), s["y"] - s["ny"] * (W + b)) for s in run]
                return max([abs(z - top) for z in zs if z is not None and z >= sea] + [0.0])
            while sh["bank"] < BANK_MAX_CM and high(sh["bank"]) > BANK * sh["bank"] + 50:
                sh["bank"] += 500.0
            for k, s in enumerate(run):  # how far in the land is cut down by more than 2 m: no street there
                for d in range(0, int(W + sh["bank"]) + 1, 500):
                    z = ground(s["x"] - s["nx"] * d, s["y"] - s["ny"] * d)
                    if z is not None and z - made_at(d) > 200:
                        clear[k] = d + 1500.0
        sh["pts"] = [[s["x"], s["y"], s["nx"], s["ny"], clear[k]] for k, s in enumerate(run)]
    return out


def plan(city: dict, analysis: dict, lots: list, ring_of, industrial, ground, nodes: dict | None = None,
         population: int = 0, roads: list | None = None) -> dict:
    """WaterfrontPlanner + generators for one city: classified segments, structures (rows), land buildings
    (lot rows), the lots kept, and the validator report. Everything is built on the city's made shores
    (city['shores'], planned before the streets; worked out here when a caller has none), at their design
    levels. The city's shipyards (city['shipyards']) are built here too: they take their stretch of shore."""
    from . import shipyard
    sea = analysis.get("sea")
    out = _Out()
    if sea is None:
        return {"lots": lots, "rows": [], "segments": [], "developments": [], "report": None, "shipyards": []}
    segments = segments_for(city, analysis, ring_of, industrial, population)
    yards = city.get("shipyards") or []
    off_limits = yards + (city.get("shipyards_near") or [])  # its own yards and a neighbouring city's
    made = city.get("shores")
    if made is None:
        made = shores_for(city, analysis, ring_of, industrial, population, ground)
    look = Made([{"shores": made}])
    sample = {(s["x"], s["y"]): s for sh in analysis["shores"] for s in sh["samples"]}
    allp = [s for seg in segments for s in seg["samples"]]
    gall: dict = {}
    for s in allp:  # 50 m cells: a lot only looks at the shore samples near it
        gall.setdefault((int(s["x"] // 5000), int(s["y"] // 5000)), []).append(s)

    def too_close(x, y, d):
        r = int(d // 5000) + 1
        cx, cy = int(x // 5000), int(y // 5000)
        return any(math.hypot(s["x"] - x, s["y"] - y) < d
                   for i in range(cx - r, cx + r + 1) for j in range(cy - r, cy + r + 1) for s in gall.get((i, j), ()))
    # no lot of the street grid on a made shore (what stands there is planned here), nor at the water's very edge
    keep = [r for r in lots if not look.shelf(r[0], r[1], max(r[3], r[4]) / 2 + 300)
            and not too_close(r[0], r[1], 1000 + max(r[3], r[4]) / 2)]
    devs = []
    claims = []  # container yards: (centre x, y, along x, y, half length, half depth) cm, no city lot on them

    def building(s, back, w, d, role, why):
        """A land building facing the water, `back` cm inland from the water line."""
        x, y = s["x"] - s["nx"] * back, s["y"] - s["ny"] * back
        yaw = _yaw(s["nx"], s["ny"])  # faces the water
        out.lots.append([round(x), round(y), round(yaw, 1), round(w), round(d), round(s["x"]), round(s["y"]), role, True])
        devs.append({"type": why, "x": round(x), "y": round(y)})
        if nodes:
            far = min(math.hypot(p[0] - x, p[1] - y) for p in nodes.values())
            out.checks.append(("access", far <= 15000, f"{why}: street {round(far / 100)} m away"))

    def along(S, every):
        return S[1:-1:max(1, int(every // STEP))] or S[len(S) // 2:len(S) // 2 + 1]

    def edge(a, b):  # between two neighbouring samples: the middle, the way the shore runs, how long
        return ((a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2, _yaw(b["x"] - a["x"], b["y"] - a["y"]),
                math.dist((a["x"], a["y"]), (b["x"], b["y"])))

    def quay(S, wide, top=QUAY_CM, deep=300.0):  # the wall at the water line, and the paving on top of it
        for a, b in zip(S, S[1:]):
            mx, my, yaw, L = edge(a, b)
            bed = ground(mx + a["nx"] * 500, my + a["ny"] * 500)
            bed = max(min(bed if bed is not None else sea - deep, sea - deep), sea - WALL_DEEP_CM)
            out.row(mx, my, bed, yaw, L / 100 * 1.05, 1, (sea + top + 10 - bed) / 100, "seawall")
            if wide:
                out.row(mx - a["nx"] * (wide / 2 + 60), my - a["ny"] * (wide / 2 + 60), sea + QUAY_CM, yaw,
                        L / 100 * 1.08, wide / 100, 1, "promenade")

    marinas = 0
    ferry_done = False
    for sh in sorted(made, key=lambda g: -len(g["pts"])):
        S = [sample[(p[0], p[1])] for p in sh["pts"] if (p[0], p[1]) in sample]
        S = S[TAPER:len(S) - TAPER]  # where the whole made profile stands
        if len(S) < 2:
            continue
        t, W, B, length_m = sh["use"], DEPTH[sh["use"]], sh.get("bank", 0.0), round(len(S) * STEP / 100)
        mid = S[len(S) // 2]
        if t == "commercial":
            quay(S, W - 400)  # paved from the wall to the back of the shops: a city's waterfront is a plaza
            for s in along(S, 30000):
                pier(out, ground, sea, s, 6000, 600, z_top=sea + QUAY_CM, why="public pier")
            ferry = None
            if not ferry_done and population >= 15000 and sh["water"] in ("ocean", "river", "lake") and len(S) >= 6:
                ferry = mid
                building(ferry, 3000, 4000, 2500, "transit", "ferry terminal")
                L = pier(out, ground, sea, ferry, 5000, 800, z_top=sea + QUAY_CM, why="ferry pier")
                out.row(ferry["x"] + ferry["nx"] * (L + 600), ferry["y"] + ferry["ny"] * (L + 600), sea + FREEBOARD - 50,
                        _yaw(ferry["tx"], ferry["ty"]), 30, 6, 1, "float")
                ferry_done = True
            for s in along(S, 6000)[:12]:
                if not ferry or math.hypot(s["x"] - ferry["x"], s["y"] - ferry["y"]) > 4500:
                    building(s, 2600, 1600, 1400, "shop", "waterfront restaurant")
            devs.append({"type": "waterfront shopping district", "length_m": length_m})
        elif t == "marina":
            quay(S, W - 400)
            s = max(S, key=lambda q: q["depth80_m"] + q["shelter"] * 5)
            n_slips = marina(out, ground, sea, s, sh["water"], s["shelter"] >= 0.5 or sh["water"] != "ocean")
            if n_slips:
                marinas += 1
                building(s, 2700, 2400, 1600, "shop", "marina building (office, boat rental, cafe)")
                devs.append({"type": "marina village", "slips": n_slips, "x": s["x"], "y": s["y"]})
            else:
                devs.append({"type": "waterfront park", "length_m": length_m})
        elif t == "beach":
            for a, b in zip(S, S[1:]):
                mx, my, yaw, L = edge(a, b)
                for dc in (-500.0, 500.0, 1500.0, 2500.0, 3500.0):  # sand in 10 m bands, each at the beach's height there
                    out.row(mx - a["nx"] * dc, my - a["ny"] * dc, sea + dc * BEACH_RISE - 10, yaw, L / 100 * 1.12, 10, 1, "sand")
                # a low wall holds the boardwalk a metre above the sand
                out.row(mx - a["nx"] * BEACH_IN, my - a["ny"] * BEACH_IN, sea + BEACH_IN * BEACH_RISE - 20, yaw, L / 100 * 1.1, 1,
                        (QUAY_CM + 30 - BEACH_IN * BEACH_RISE) / 100, "seawall")
                out.row(mx - a["nx"] * (BEACH_IN + 650), my - a["ny"] * (BEACH_IN + 650), sea + QUAY_CM, yaw,
                        L / 100 * 1.12, 12, 1, "promenade")
            for s in (S[0], S[-1]):  # a rock groyne at each end holds the sand
                for k in range(5):
                    dd = 1200.0 - k * 900
                    out.row(s["x"] - s["nx"] * dd, s["y"] - s["ny"] * dd, sea - 250, _yaw(s["nx"], s["ny"]), 9, 4, 3.3, "riprap")
            if len(S) >= 8:
                building(mid, W + B + 2600, 3600, 2400, "office", "beach resort hotel")
                if len(S) >= 14:
                    pier(out, ground, sea, mid, 9000, 600, z_top=sea + QUAY_CM, why="beach pier", back=BEACH_IN)
                    building(S[len(S) // 4], W + B + 1800, 1400, 1200, "shop", "beach club")
            devs.append({"type": "beach resort" if len(S) >= 8 else "beach", "length_m": length_m})
        elif t == "residential":
            quay(S, 0, LAWN_CM, 150.0)  # a low bulkhead holds the lawn at the water's edge
            shared = len(S) >= 18 and marinas < 2 and sh["water"] != "ocean"
            homes, last = 0, None
            for s in S:  # a row of homes square to the shore, each with its lawn down to the water
                x, y = s["x"] - s["nx"] * HOUSE_AT, s["y"] - s["ny"] * HOUSE_AT
                if last and math.hypot(x - last[0], y - last[1]) < HOUSE_LOT[0] + 50:
                    continue  # (a bay turns them toward each other: one fewer)
                last, homes = (x, y), homes + 1
                front = HOUSE_AT - HOUSE_LOT[1] / 2
                out.lots.append([round(x), round(y), round(_yaw(s["nx"], s["ny"]), 1), round(HOUSE_LOT[0]), round(HOUSE_LOT[1]),
                                 round(s["x"] - s["nx"] * front), round(s["y"] - s["ny"] * front), "house", False])
                if not shared and homes % 3 == 2:  # a few private docks, never one per house
                    pier(out, ground, sea, s, 1200, 250, z_top=sea + 100, why="private dock")
            if shared:
                s = max(S, key=lambda q: q["depth80_m"])
                n_slips = marina(out, ground, sea, s, sh["water"], True, slips_wanted=30)
                if n_slips:  # one compact community marina instead of a dock behind every house
                    marinas += 1
                    devs.append({"type": "community marina", "slips": n_slips})
            devs.append({"type": "homes on the water", "count": homes, "length_m": length_m})
        elif t == "industrial":
            quay(S, 0)
            for s in along(S, 6000):  # container cranes straddle the quay edge
                out.row(s["x"] - s["nx"] * 800, s["y"] - s["ny"] * 800, sea + QUAY_CM, _yaw(mid["tx"], mid["ty"]), 1, 1, 1, "crane")
            back = W + B + 4500
            spot = yard_spot(roads or [], mid["x"] - mid["nx"] * back, mid["y"] - mid["ny"] * back,
                             mid["tx"], mid["ty"], -mid["nx"], -mid["ny"], min(300, length_m) * 0.9 * 100)
            if spot is None:  # streets all over the land behind this quay: cranes and wall, no yard
                devs.append({"type": "container quay", "length_m": length_m})
                continue
            yx, yy, yard_l, yard_d = spot
            yz = ground(yx, yy)
            yz = sea + QUAY_CM if yz is None or yz < sea + QUAY_CM else yz
            out.row(yx, yy, yz, _yaw(mid["tx"], mid["ty"]), yard_l / 100, yard_d / 100, 1, "yard")
            claims.append((yx, yy, mid["tx"], mid["ty"], yard_l / 2, yard_d / 2))
            # the stacks stay on the paving: as many 14 m bays each way as the yard is long (at most 3), and as
            # many 10 m rows as it is deep (at most 5)
            na = max(0, min(3, int((yard_l / 2 - 700) // 1400)))
            nb = max(0, min(2, int((yard_d / 2 - 600) // 1000)))
            for a in range(-na, na + 1):
                for b in range(-nb, nb + 1):
                    out.row(yx + mid["tx"] * a * 1400 + mid["nx"] * b * 1000, yy + mid["ty"] * a * 1400 + mid["ny"] * b * 1000,
                            yz, _yaw(mid["tx"], mid["ty"]), 1, 1, 1, "containers")
            building(mid, back + 7000, 6000, 3000, "warehouse", "port warehouse")
            devs.append({"type": "container port", "length_m": length_m})
        elif t == "public":
            quay(S, 600)
            pier(out, ground, sea, mid, 4000, 300, z_top=sea + QUAY_CM, why="fishing pier")
            devs.append({"type": "waterfront park", "length_m": length_m})
    for seg in segments:  # outside the city, flat shallow shores stay marsh
        if seg["type"] == "wetland":
            for a in seg["samples"][::2]:
                out.row(a["x"], a["y"], sea - 40, _yaw(a["tx"], a["ty"]), STEP * 2 / 100, 20, 1, "wetland")
    # shipyards: dedicated industrial districts (their land is theirs, no city lot inside), and nothing else of
    # the waterfront stands on one or in the basin along its quay
    if off_limits:
        theirs = lambda x, y: any(shipyard.reserved(s, x, y, 1000) or shipyard.zone(s, *shipyard.uv(s, x, y)) == "basin"
                                  for s in off_limits)
        out.rows = [r for r in out.rows if not theirs(r[0], r[1])]
        out.lots = [b for b in out.lots if not theirs(b[0], b[1])]
        keep = [q for q in keep if not any(shipyard.reserved(s, q[0], q[1], 4000) for s in off_limits)]
    yard_reports = []
    for y in yards:
        got = shipyard.build(y, ground, sea, nodes)
        out.rows += got["rows"]
        out.lots += got["lots"]
        # (a lot's own pad would stand in the bank's way: 40 m clear of the yard's land)
        keep = [q for q in keep if not shipyard.reserved(y, q[0], q[1], 4000)]
        yard_reports.append({"type": y["type"], "docks": len(y.get("docks") or []), "site_m": [round(y["L"] / 100),
                             round(y["D"] / 100)], "flow_m": got["flow"], "report": got["report"],
                             **({"platform": shipyard.outline(y), "out_over_water_m": round(y.get("out", 0) / 100)}
                                if y.get("z") is not None else {})})
        devs.append({"type": f"{y['type']} shipyard", "docks": len(y.get("docks") or [])})
    # claimed land: no lot under a new waterfront building, nor on a container yard
    for b in out.lots:
        r = max(b[3], b[4]) / 2 + 800
        keep = [q for q in keep if math.hypot(q[0] - b[0], q[1] - b[1]) > r]
    for cx, cy, tx, ty, hl, hd in claims:
        keep = [q for q in keep if not (abs((q[0] - cx) * tx + (q[1] - cy) * ty) < hl + max(q[3], q[4]) / 2 + 500
                                        and abs((q[1] - cy) * tx - (q[0] - cx) * ty) < hd + max(q[3], q[4]) / 2 + 500)]
    report = validate(out, segments)
    counts: dict = {}
    for seg in segments:
        counts[seg["type"]] = counts.get(seg["type"], 0) + seg["length_m"]
    made_m: dict = {}
    for sh in made:
        made_m[sh["use"]] = made_m.get(sh["use"], 0) + round(len(sh["pts"]) * STEP / 100)
    return {"lots": keep + out.lots, "rows": out.rows,
            "segments": [{"type": g["type"], "water": g["water"], "length_m": g["length_m"],
                          "from": [g["samples"][0]["x"], g["samples"][0]["y"]],
                          "to": [g["samples"][-1]["x"], g["samples"][-1]["y"]]} for g in segments],
            "developments": devs, "shoreline_m": counts, "made_m": made_m, "report": report, "shipyards": yard_reports}


def validate(out: _Out, segments: list) -> dict:
    """WaterfrontConnectivityValidator: navigation, access, foundations, collisions."""
    bad = [d for what, ok, d in out.checks if not ok]
    piles = [r for r in out.rows if r[7] == "pile"]
    if any(r[6] <= 0 for r in piles):
        bad.append("a pile does not reach the bed")
    tall = [r for r in out.rows if r[7] in ("deck", "float", "stilt_house", "float_home")]
    clash = 0
    for k, a in enumerate(out.lots):  # two waterfront buildings on one spot
        for b in out.lots[k + 1:]:
            if math.hypot(a[0] - b[0], a[1] - b[1]) < (min(a[3], a[4]) + min(b[3], b[4])) / 2:
                clash += 1
    if clash:
        bad.append(f"{clash} waterfront buildings overlap")
    return {"ok": not bad, "problems": bad[:30], "checked": len(out.checks), "structures": len(tall),
            "piles": len(piles)}


def layers(cities: list) -> dict:
    """Built-in PCG site layers for every city's waterfront rows."""
    by: dict = {}
    for c in cities:
        for r in ((c.get("private") or {}).get("water") or []):
            by.setdefault(r[7], []).append(r[:7])
    out = {}
    for kind, rows in by.items():
        mesh, box, tint, name = KINDS[kind]
        out[name] = {"kind": "site", "role": "site", "mesh": mesh, "box_m": box, "tint": tint, "count": len(rows),
                     "shown": len(rows), "points": rows, "meshes": [], "row": "x, y, z, yaw, sx, sy, sz",
                     # (a yard's paving and crane rails level nothing: citygen.cut_yards made the whole platform)
                     **({"pad": False} if kind in NO_PAD else {}),
                     **({"keep_z": True} if kind in YARD_KINDS or kind in OWN_HEIGHT else {})}
    return out


def bridge_kind(span_cm: float, kind: str) -> tuple:
    """BridgeGenerator: (type, deck lift cm) from the span and the road; navigation needs clearance."""
    if span_cm <= 4000:
        return ("footbridge" if kind in ("SW", "WK") else "canal arch"), 300.0
    if span_cm <= 15000:
        return "beam bridge", 750.0
    if span_cm <= 60000:
        return "girder bridge (navigable)", 1500.0
    if span_cm <= 150000:
        return "cable-stayed bridge", 3000.0
    return "suspension bridge", 4500.0


def got_rows(got: dict, kind: str) -> list:
    return [r for r in got["rows"] if r[7] == kind]


def _demo() -> None:
    # a 6 km terrain: ocean to the east (x > 1.5 km, shallow beach shelf in the south, deep in the north), a
    # sheltered lake in the west, land rising inland
    n, size = 120, 600000
    lo = [-size / 2, -size / 2]
    heights = []
    for j in range(n):
        row = []
        for i in range(n):
            x, y = lo[0] + (i + 0.5) * size / n, lo[1] + (j + 0.5) * size / n
            if x > 150000:  # ocean: deep north, shallow shelf south
                h = -300 - (x - 150000) * 0.08 if y > 0 else -(x - 150000) * 0.008
            else:
                h = 100 + (150000 - x) * 0.004
            if math.hypot(x + 20000, y - 60000) < 40000:  # lake
                h = -1200
            row.append(round(h, 1))
        heights.append(row)
    ter = {"min": lo, "max": [size / 2, size / 2], "grid": n, "heights": heights, "sea": 0.0}
    a = analyze(ter)
    kinds = sorted({b["type"] for b in a["bodies"]})
    assert "ocean" in kinds and "lake" in kinds, kinds
    assert a["shores"], "shorelines found"
    lake_shore = [sh for sh in a["shores"] if sh["type"] == "lake"]
    assert lake_shore and min(s["shelter"] for s in lake_shore[0]["samples"]) >= 0.5, "a lake is sheltered"
    for sh in a["shores"]:  # every normal points at the water
        for s in sh["samples"][1:-1]:
            from . import splines_text as st
            z = st.sample_grid(ter, s["x"] + s["nx"] * 2500, s["y"] + s["ny"] * 2500)
            assert z is None or z < 50, ("normal points to land", s)
    from . import splines_text as st
    ground = lambda x, y: st.sample_grid(ter, x, y)
    city = {"name": "Port", "x": 120000, "y": 0, "radius": 160000}
    ring = lambda x, y: "core" if math.hypot(x - 120000, y) < 60000 else "normal" if math.hypot(x - 120000, y) < 110000 else "housing"
    industrial = lambda x, y: y > 90000
    lots = [[x, y, 0.0, 1600, 3500, x + 1750, y, "", False] for x in range(-80000, 150000, 4000) for y in range(-150000, 150001, 4000)]
    nodes = {k: [x, y] for k, (x, y) in enumerate((x, y) for x in range(-80000, 150000, 10000) for y in range(-150000, 150001, 10000))}
    got = plan(city, a, lots, ring, industrial, ground, nodes, 30000)
    types = {g["type"] for g in got["segments"]}
    print("  segments", got["shoreline_m"], "developments", sorted({d["type"] for d in got["developments"]}))
    assert {"commercial", "beach", "industrial"} <= types, types
    assert {"marina", "residential"} & types, types
    kinds = {r[7] for r in got["rows"]}
    assert {"seawall", "pile", "deck", "sand"} <= kinds, kinds
    assert "crane" in kinds and "containers" in kinds, "a container port on deep industrial water"
    # the containers stand on their yard, and no city lot does
    for yd in [r for r in got["rows"] if r[7] == "yard"]:
        tx_, ty_ = math.cos(math.radians(yd[3])), math.sin(math.radians(yd[3]))
        inside = lambda x, y, m=0.0: (abs((x - yd[0]) * tx_ + (y - yd[1]) * ty_) <= yd[4] * 50 + m
                                      and abs((y - yd[1]) * tx_ - (x - yd[0]) * ty_) <= yd[5] * 50 + m)
        near = [r for r in got["rows"] if r[7] == "containers" and math.hypot(r[0] - yd[0], r[1] - yd[1]) < 8000]
        assert near and all(inside(r[0], r[1], -600) for r in near), "every stack on the paving"
        assert not [q for q in got["lots"] if q[8] is not True and inside(q[0], q[1])], "no city lot on a container yard"
    assert {"float", "slip", "boat"} <= kinds, "a marina with slips"
    assert got["report"]["ok"], got["report"]
    # no seawall where the shore is natural or a beach
    walls = [r for r in got["rows"] if r[7] == "seawall"]
    beach = [g for g in got["segments"] if g["type"] == "beach"]
    assert walls and beach
    # floating pieces sit on the water, pilings reach the bed, decks above the flood line
    assert all(abs(r[2] - (FREEBOARD - 50)) < 2 for r in got["rows"] if r[7] == "float")
    assert all(r[6] > 0 for r in got["rows"] if r[7] == "pile")
    # every pier leaves from land and stays inside its navigation share
    assert not [p for p in got["report"]["problems"] if "m water" in p]
    # buildings face the water and replaced the lots under them
    wb = [r for r in got["lots"] if r[8] is True]
    assert wb and len(got["lots"]) < len(lots) + len(wb)
    # the made shore. Every stretch that is built on is long enough to make and knows how far in no street goes
    made = shores_for(city, a, ring, industrial, 30000, ground)
    uses = {sh["use"] for sh in made}
    assert {"commercial", "beach", "industrial"} <= uses and all(len(sh["pts"]) >= MADE_MIN and len(sh["pts"][0]) == 5 for sh in made), uses
    assert shores_for(city, a, ring, industrial, 30000, ground, {(p[0], p[1]) for sh in made for p in sh["pts"]}) == [], "a neighbour has them"
    # ... its profile: a quay level with 3 m of water at the wall, a beach from under the water up to its boardwalk,
    # a lawn up to the homes, and behind each the land brought back as a bank
    assert profile("commercial", 2500, 900.0, 0.0) == QUAY_CM and profile("commercial", -500, -40.0, 0.0) == -300.0
    assert profile("commercial", 5400, 900.0, 0.0) == QUAY_CM + BANK * 400 and profile("commercial", 5400, 250.0, 0.0) == 250.0
    assert profile("commercial", 5100, 50.0, 0.0) == QUAY_CM - BANK * 100 and profile("commercial", 5400, -50.0, 0.0) == -50.0
    assert [profile("beach", d, 700.0, 0.0) for d in (-2400, -2000, 0, 4000, 4100)] == [700.0, -50.0, 0.0, 100.0, QUAY_CM]
    assert [profile("residential", d, 700.0, 0.0) for d in (-1100, -100, 0, 1500, 3000, 5000)] == [700.0, -150.0, LAWN_CM, 285.0, HOUSE_CM, HOUSE_CM]
    # ... looked up from any point: how far in, and fading out past the ends of the stretch
    look = Made([{"shores": [{"use": "public", "sea": 0.0, "bank": 0.0, "pts": [[0, y, 1.0, 0.0, 0.0] for y in range(0, 20001, 2000)]}]}])
    spot = look.at(-1200, 10100)
    assert spot[1] == 1200 and spot[2] == 1.0 and look.shelf(-1200, 10100) and not look.shelf(-2500, 10100) and not look.shelf(300, 10100)
    assert look.at(-1200, 100)[2] == 0.0 and 0 < look.at(-1200, 2000)[2] < 1 and look.at(-1200, 24500)[2] == 0.0
    assert look.level(-1200, 10100, 900.0) == QUAY_CM and look.level(-1200, 100, 900.0) == 900.0 and Made([]).level(0, 0, 5.0) == 5.0
    # everything at the water stands at its own height: promenades on the quay, sand at the beach's height band by
    # band, boats and floating docks afloat, and none of them levels any ground
    got = plan(city, a, lots, ring, industrial, ground, nodes, 30000)
    assert {r[2] for r in got_rows(got, "promenade")} == {round(QUAY_CM)}
    sand = sorted({r[2] for r in got_rows(got, "sand")})
    assert sand == [round(d * BEACH_RISE - 10) for d in (-500, 500, 1500, 2500, 3500)], sand
    assert {r[2] for r in got_rows(got, "boat")} == {-40} and {r[2] for r in got_rows(got, "slip")} == {0}
    tops = {round((r[2] + r[6] * 100) / 10) * 10 for r in got_rows(got, "seawall")}
    assert tops == {QUAY_CM + 10, LAWN_CM + 10}, ("every wall ends at its promenade or its lawn", tops)
    flags = layers([{"private": {"water": got["rows"]}}])
    assert all(flags[n].get("keep_z") for n in ("Boats", "Floating docks", "Piers and boardwalks", "Beaches", "Promenades", "Seawalls and quays"))
    assert all(flags[n].get("pad") is False for n in ("Piers and boardwalks", "Floating docks", "Beaches", "Promenades"))
    # homes on the water stand in a row square to the shore, a lot apart, and no street-grid lot is left on a made shore
    homes = [r for r in got["lots"] if r[7] == "house"]
    assert len(homes) >= 8 and all(r[8] is False and r[3:5] == [1700, 2400] for r in homes)
    for r in homes:
        s_ = min((q for sh in a["shores"] for q in sh["samples"]), key=lambda q: math.hypot(q["x"] - r[5], q["y"] - r[6]))
        assert abs((r[2] - _yaw(s_["nx"], s_["ny"]) + 180) % 360 - 180) < 12, "a home faces the water"
    assert min(math.hypot(p[0] - q[0], p[1] - q[1]) for k, p in enumerate(homes) for q in homes[k + 1:]) >= HOUSE_LOT[0]
    grid_lots = [r for r in got["lots"] if r[7] == ""]
    look = Made([{"shores": made}])
    assert grid_lots and not any(look.shelf(r[0], r[1]) for r in grid_lots)
    # a city on a lake alone still gets its beach, whatever its blocks drew
    lake_city = {"name": "Lakeside", "x": -20000, "y": 60000, "radius": 80000, "seed": 1}
    for seed in range(4):
        lake_made = shores_for({**lake_city, "seed": seed}, a, lambda x, y: "housing", None, 9000, ground)
        assert lake_made and "beach" in {sh["use"] for sh in lake_made}, (seed, [sh["use"] for sh in lake_made])
    # shipyards: planned on the deep industrial shore before the streets, built as a district with docks
    from . import shipyard
    city["shipyards"] = shipyard.sites(city, segments_for(city, a, ring, industrial, 90000), 90000)
    types = {y["type"] for y in city["shipyards"]}
    assert "commercial" in types, ("800 m of deep industrial ocean shore in a 90k city: a commercial yard", types)
    yard = plan(city, a, lots, ring, industrial, ground, nodes, 90000)
    assert all(y["report"]["ok"] for y in yard["shipyards"]), yard["shipyards"]
    kinds = {r[7] for r in yard["rows"]}
    assert {"goliath", "ship_hull", "portal_crane", "keel", "dock_gate"} <= kinds, kinds
    for y in city["shipyards"]:
        assert not any(shipyard.inside(y, r[0], r[1], 0) for r in yard["lots"] if r[8] is not True), \
            "no city lot inside a yard"
    print("  shipyards", [(y["type"], y["docks"], y["site_m"]) for y in yard["shipyards"]])
    t, lift = bridge_kind(90000, "4L")
    assert t == "cable-stayed bridge" and lift == 3000.0
    # a yard behind its quay: shore along x, inland +y. No street: it stays put, whole. A street across it, or
    # one ending at a junction in its middle from inland (the Valley 2_1 one): the biggest yard clear of it.
    # Streets everywhere: no yard
    assert yard_spot([], 0, 0, 1, 0, 0, 1, 20000) == (0, 0, 20000, YARD_DEPTH_CM)
    for road in ([(0, -20000, 0, 20000, 500.0)], [(0, 0, 0, 20000, 600.0), (-20000, 9000, 20000, 9000, 600.0)]):
        x, y, L, D = yard_spot(road, 0, 0, 1, 0, 0, 1, 20000)
        assert not any(abs(px - x) < L / 2 + h and abs(py - y) < D / 2 + h
                       for x0, y0, x1, y1, h in road for k in range(41)
                       for px, py in [(x0 + (x1 - x0) * k / 40, y0 + (y1 - y0) * k / 40)]), ("a street in the yard", road)
        assert L * D >= 9000 * 4000, ("still a yard", L, D)
    # the Valley's: two streets crossing the yard on the slant, meeting at its inland edge; the land left is the
    # wedge on the water side (local cm: along, inland)
    vee = [(-9100, -2400, -1000, 3800, 1400.0), (-2300, 4700, 8700, -700, 875.0)]
    x, y, L, D = yard_spot(vee, 0, 0, 1, 0, 0, 1, 14400, within=True)
    assert y < 0 and L * D >= 5000 * 4000, ("a yard on the water side of the streets", x, y, L, D)
    assert abs(x) + L / 2 <= 7200 and abs(y) + D / 2 <= YARD_DEPTH_CM / 2, ("on its own platform", x, y, L, D)
    grid = [(gx, -20000, gx, 20000, 500.0) for gx in range(-30000, 30001, 2500)]
    assert yard_spot(grid, 0, 0, 1, 0, 0, 1, 20000) is None
    print("waterfront ok", len(got["rows"]), "pieces", len(wb), "waterfront buildings")


if __name__ == "__main__":
    _demo()
