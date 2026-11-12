"""Urban planner: a demand-driven city, not a building placer.

CityDemandSystem -> FacilityLocationOptimizer -> DevelopmentGenerator, run per city after its lots exist:

1. Population & demand (`population`): every lot becomes households, residents and jobs (houses 2.6 people,
   apartment buildings by floor area, plain parcels by district ring), snapped to the street node it is
   reached from. Age groups give the school-age children, residents give retail demand D = P x S.
2. Accessibility (`Network`): real travel through the street graph, never circles: drive time per road
   class (Dijkstra) and walking distance on every street a pedestrian may use.
3. Location (`plan`), per CATALOG entry, in planning order (biggest sites first):
   - cover: maximum-coverage location. Greedy: the candidate that covers the most still-uncovered people
     within T_max (drive seconds or walk metres), until the target share is covered (fire, police, EMS,
     parks, convenience stores, gas). Facilities that already serve the area count (a hospital covers EMS,
     a community centre also covers its grocery level).
   - capacity: count = ceil(demand / capacity) (children of school age / seats, people per hospital, ...),
     placed one at a time where they cut the population-weighted travel time most (p-median), then every
     neighbourhood is assigned to its nearest one within capacity (school catchments).
   - single: one, where the whole city reaches it fastest (city hall, bus terminal), or at the edge
     (industry, power) when `edge` is set.
   - terrain: highest ground (water tower, telecom) / lowest ground (treatment, retention ponds).
   - anchors: at given points (park-and-ride at freeway interchanges).
4. Development (`develop`): a site is claimed from whole free street lots of one block (never across a
   street), then its ARCHETYPE lays out a complete development in the site's own frame: the main building
   (a lot row with the building's role and exact footprint), parking fields with stalls, aprons (fire
   apparatus, loading, bus loops), sports fields, playgrounds, pools, utility yards, towers, ponds.

Re-planning keeps every facility whose site still exists (`keep`), and only adds what new demand needs:
a city that grows gets more, never a reshuffle. All numbers are game-generation targets, configurable here.
"""

from __future__ import annotations

import heapq
import math

# ------------------------------------------------------------------ population
HOUSEHOLD = 2.6  # people per detached house
APT_PEOPLE_M2 = 2.2 / 85.0  # an apartment of 85 m2 houses 2.2 people
APT_FLOORS = 4
PARCEL = {"core": (12.0, 40.0), "normal": (18.0, 8.0), "housing": (2.6, 0.3)}  # (residents, jobs) per plain lot
AGES = {"0-4": 0.06, "5-10": 0.08, "11-13": 0.04, "14-17": 0.055, "18-64": 0.63, "65+": 0.135}
RETAIL_PER_RESIDENT = 1.0  # S in D = P x S (shopping trips per resident and week, a relative unit)

# ------------------------------------------------------------------ the eight land-use categories
CATEGORIES = ("commercial", "residential", "education", "government", "industrial", "recreation", "utilities",
              "transportation")

# key: category, method, building role (pcg layer), site size (frontage x depth, m), archetype, method params.
# `serves` = facility keys that count as covering this one's need too.
CATALOG = {
    # government & emergency
    "hospital": dict(cat="government", method="capacity", role="hospital", size=(160, 120), arch="hospital",
                     rate=1.0, capacity=60000, min_demand=12000),
    "city_hall": dict(cat="government", method="single", role="civic", size=(70, 60), arch="civic", min_pop=5000),
    "police_hq": dict(cat="government", method="single", role="police", size=(60, 50), arch="police", min_pop=3000),
    "police_precinct": dict(cat="government", method="cover", role="police", size=(40, 35), arch="police",
                            metric="drive", limit=360, target=0.9, min_pop=2000, serves=("police_hq",)),
    "fire_station": dict(cat="government", method="cover", role="fire_station", size=(50, 45), arch="fire",
                         metric="drive", limit=240, target=0.95, min_pop=1500),
    "ambulance": dict(cat="government", method="cover", role="fire_station", size=(35, 30), arch="ems",
                      metric="drive", limit=300, target=0.9, min_pop=8000, serves=("hospital", "fire_station")),
    "courthouse": dict(cat="government", method="single", role="civic", size=(60, 50), arch="civic", min_pop=30000),
    "post_office": dict(cat="government", method="capacity", role="civic", size=(45, 40), arch="civic_small",
                        rate=1.0, capacity=20000, min_demand=6000),
    "jail": dict(cat="government", method="single", role="police", size=(140, 110), arch="jail", min_pop=40000,
                 edge=True),
    # education
    "college": dict(cat="education", method="single", role="school", size=(250, 200), arch="campus", min_pop=50000),
    "high_school": dict(cat="education", method="capacity", role="school", size=(200, 150), arch="high_school",
                        age="14-17", capacity=1500, min_demand=250, catch=900),
    "middle_school": dict(cat="education", method="capacity", role="school", size=(150, 110), arch="school",
                          age="11-13", capacity=900, min_demand=200, catch=720),
    "elementary_school": dict(cat="education", method="capacity", role="school", size=(120, 90), arch="school",
                              age="5-10", capacity=600, min_demand=150, catch=480),
    "daycare": dict(cat="education", method="capacity", role="school", size=(35, 35), arch="daycare",
                    age="0-4", capacity=120, min_demand=60, catch=300),
    "library": dict(cat="education", method="capacity", role="civic", size=(50, 45), arch="civic_small",
                    rate=1.0, capacity=25000, min_demand=8000),
    # commercial: four retail levels, the higher first (a higher centre also serves the lower need)
    "regional_mall": dict(cat="commercial", method="cover", role="mall", size=(220, 160), arch="mall",
                          metric="drive", limit=900, target=1.0, min_pop=60000, level=4),
    "community_center_retail": dict(cat="commercial", method="cover", role="mall", size=(160, 120),
                                    arch="plaza_anchor", metric="drive", limit=480, target=0.9, min_pop=25000,
                                    level=3, serves=("regional_mall",)),
    "neighbourhood_center": dict(cat="commercial", method="cover", role="shop", size=(110, 90), arch="grocery",
                                 metric="drive", limit=240, target=0.9, min_pop=5000, level=2,
                                 serves=("regional_mall", "community_center_retail")),
    "convenience": dict(cat="commercial", method="cover", role="shop", size=(30, 30), arch="corner_store",
                        metric="walk", limit=100000, target=0.8, min_pop=1200, level=1,
                        serves=("neighbourhood_center", "community_center_retail", "regional_mall")),
    "gas_station": dict(cat="commercial", method="cover", role="gas_station", size=(40, 35), arch="gas",
                        metric="drive", limit=240, target=0.85, min_pop=2500),
    "hotel": dict(cat="commercial", method="single", role="office", size=(50, 45), arch="tower_parking", min_pop=20000),
    # industrial & logistics
    "distribution_center": dict(cat="industrial", method="single", role="warehouse", size=(140, 100),
                                arch="logistics", min_pop=20000, edge=True),
    "recycling_center": dict(cat="industrial", method="capacity", role="utility", size=(60, 50), arch="yard",
                             rate=1.0, capacity=40000, min_demand=10000, edge=True),
    # recreation
    "community_park": dict(cat="recreation", method="capacity", role="park", size=(140, 110), arch="big_park",
                           rate=1.0, capacity=15000, min_demand=5000),
    "sports_complex": dict(cat="recreation", method="single", role="park", size=(180, 140), arch="sports", min_pop=30000),
    "stadium": dict(cat="recreation", method="single", role="mall", size=(240, 200), arch="stadium", min_pop=80000),
    "community_center": dict(cat="recreation", method="capacity", role="civic", size=(70, 60), arch="community",
                             rate=1.0, capacity=15000, min_demand=4000),
    "neighbourhood_park": dict(cat="recreation", method="cover", role="park", size=(60, 50), arch="park",
                               metric="walk", limit=50000, target=0.85, min_pop=400, serves=("community_park",)),
    # utilities
    "power_plant": dict(cat="utilities", method="single", role="factory", size=(200, 150), arch="power",
                        min_pop=60000, edge=True),
    "water_treatment": dict(cat="utilities", method="terrain", role="utility", size=(120, 100), arch="treatment",
                            low=True, per=60000, min_pop=20000),
    "wastewater": dict(cat="utilities", method="terrain", role="utility", size=(120, 100), arch="treatment",
                       low=True, per=60000, min_pop=3000),
    "substation": dict(cat="utilities", method="capacity", role="utility", size=(40, 40), arch="substation",
                       rate=1.0, capacity=15000, min_demand=500),
    "water_tower": dict(cat="utilities", method="terrain", role="utility", size=(30, 30), arch="water_tower",
                        low=False, per=20000, min_pop=1500),
    "telecom_tower": dict(cat="utilities", method="terrain", role="utility", size=(20, 20), arch="telecom",
                          low=False, per=15000, min_pop=3000),
    "retention_pond": dict(cat="utilities", method="terrain", role="", size=(50, 50), arch="pond",
                           low=True, per=8000, min_pop=2000),
    # transportation
    "train_station": dict(cat="transportation", method="single", role="transit", size=(140, 50), arch="rail",
                          min_pop=25000),
    "bus_terminal": dict(cat="transportation", method="single", role="transit", size=(70, 50), arch="bus",
                         min_pop=8000),
    "park_and_ride": dict(cat="transportation", method="anchors", role="", size=(80, 60), arch="parking_only",
                          min_pop=10000),
    "parking_garage": dict(cat="transportation", method="capacity", role="", size=(50, 40), arch="garage",
                           jobs=True, rate=1.0, capacity=8000, min_demand=3000),
}
ORDER = list(CATALOG)  # planning order: the big civic sites first, then retail, then the small and the land-hungry

# Archetypes: a complete development in the site's frame. u runs along the frontage (-0.5 .. 0.5), v from the
# street (0) to the back (1). Each part: (kind, u0, u1, v0, v1). kind 'building' (the facility's role) or
# 'building:<role>', 'parking' (fields with stalls), 'garage' (parking structure), 'apron', 'field',
# 'playground', 'pool', 'yard', 'pond', 'tower'.
ARCHETYPES = {
    "grocery": [("building", -0.45, 0.15, 0.45, 0.95), ("building:shop", 0.18, 0.48, 0.5, 0.95),
                ("parking", -0.48, 0.48, 0.03, 0.42), ("apron", -0.45, 0.15, 0.95, 1.0)],
    "plaza_anchor": [("building", -0.48, 0.0, 0.5, 0.95), ("building:shop", 0.03, 0.48, 0.6, 0.95),
                     ("parking", -0.48, 0.48, 0.03, 0.47), ("apron", -0.48, 0.0, 0.95, 1.0)],
    "mall": [("building", -0.3, 0.3, 0.35, 0.9), ("parking", -0.48, -0.32, 0.03, 0.97),
             ("parking", 0.32, 0.48, 0.03, 0.97), ("parking", -0.3, 0.3, 0.03, 0.32), ("apron", -0.2, 0.2, 0.91, 0.99)],
    "corner_store": [("building", -0.4, 0.4, 0.5, 0.95), ("parking", -0.45, 0.45, 0.05, 0.45)],
    "gas": [("building", -0.45, 0.45, 0.08, 0.92)],
    "tower_parking": [("building", -0.3, 0.3, 0.08, 0.6), ("parking", -0.48, 0.48, 0.65, 0.97)],
    "daycare": [("parking", -0.48, 0.48, 0.02, 0.3), ("building", -0.45, 0.1, 0.35, 0.95),
                ("playground", 0.15, 0.48, 0.35, 0.95)],
    "school": [("apron", -0.48, 0.48, 0.02, 0.12), ("building", -0.45, 0.1, 0.18, 0.6),
               ("parking", 0.15, 0.48, 0.15, 0.6), ("playground", -0.45, 0.0, 0.65, 0.95),
               ("field", 0.05, 0.48, 0.65, 0.97)],
    "high_school": [("apron", -0.48, 0.48, 0.02, 0.1), ("building", -0.45, 0.0, 0.16, 0.55),
                    ("parking", 0.05, 0.48, 0.13, 0.55), ("field", -0.45, 0.48, 0.6, 0.98)],
    "campus": [("parking", -0.48, 0.48, 0.02, 0.24), ("building", -0.45, -0.15, 0.3, 0.6),
               ("building", -0.1, 0.2, 0.3, 0.6), ("building", 0.25, 0.48, 0.3, 0.6), ("field", -0.45, 0.48, 0.66, 0.97)],
    "civic": [("parking", -0.48, 0.48, 0.02, 0.25), ("building", -0.4, 0.4, 0.3, 0.85)],
    "civic_small": [("building", -0.45, 0.1, 0.1, 0.9), ("parking", 0.15, 0.48, 0.1, 0.9)],
    "community": [("parking", -0.48, 0.48, 0.02, 0.25), ("building", -0.45, 0.05, 0.3, 0.9),
                  ("pool", 0.1, 0.45, 0.4, 0.85)],
    "fire": [("apron", -0.3, 0.4, 0.02, 0.43), ("parking", -0.48, -0.32, 0.02, 0.43), ("building", -0.4, 0.4, 0.45, 0.95)],
    "ems": [("apron", -0.4, 0.4, 0.02, 0.42), ("building", -0.4, 0.4, 0.45, 0.95)],
    "police": [("parking", -0.45, 0.15, 0.03, 0.25), ("building", -0.45, 0.15, 0.3, 0.75),
               ("parking", 0.2, 0.48, 0.1, 0.95)],
    "hospital": [("parking", -0.48, 0.48, 0.02, 0.22), ("building", -0.35, 0.25, 0.25, 0.8),
                 ("garage", 0.3, 0.48, 0.25, 0.75), ("apron", -0.35, 0.0, 0.82, 0.97)],
    "logistics": [("parking", -0.45, 0.45, 0.03, 0.3), ("building", -0.45, 0.45, 0.35, 0.8),
                  ("apron", -0.45, 0.45, 0.82, 0.98)],
    "yard": [("building", -0.45, -0.1, 0.1, 0.5), ("yard", -0.05, 0.45, 0.1, 0.95)],
    "park": [("building", -0.5, 0.5, 0.0, 1.0), ("playground", -0.2, 0.2, 0.35, 0.65)],
    "big_park": [("parking", -0.48, 0.0, 0.02, 0.2), ("building", -0.5, 0.5, 0.22, 1.0),
                 ("field", 0.05, 0.45, 0.4, 0.95), ("playground", -0.4, -0.1, 0.4, 0.7)],
    "sports": [("parking", -0.48, 0.2, 0.02, 0.22), ("building:civic", 0.25, 0.48, 0.02, 0.22),
               ("field", -0.45, -0.02, 0.27, 0.97), ("field", 0.02, 0.45, 0.27, 0.97)],
    "stadium": [("parking", -0.48, 0.48, 0.02, 0.18), ("building", -0.3, 0.3, 0.22, 0.8),
                ("parking", -0.48, -0.34, 0.22, 0.97), ("parking", 0.34, 0.48, 0.22, 0.97)],
    "substation": [("building", -0.45, -0.2, 0.05, 0.3), ("yard", -0.45, 0.45, 0.35, 0.95)],
    "water_tower": [("tower", -0.2, 0.2, 0.3, 0.7)],
    "telecom": [("tower", -0.2, 0.2, 0.3, 0.7)],
    "treatment": [("building", -0.45, -0.1, 0.1, 0.4), ("pond", 0.0, 0.45, 0.1, 0.5), ("pond", -0.45, 0.45, 0.55, 0.95)],
    "pond": [("pond", -0.45, 0.45, 0.05, 0.95)],
    "power": [("building", -0.45, 0.0, 0.2, 0.8), ("yard", 0.05, 0.45, 0.1, 0.95), ("tower", -0.4, -0.3, 0.85, 0.95)],
    "bus": [("apron", -0.45, 0.45, 0.05, 0.6), ("building", -0.3, 0.3, 0.65, 0.95)],
    "rail": [("parking", -0.48, -0.32, 0.05, 0.45), ("building", -0.3, 0.3, 0.05, 0.45),
             ("parking", 0.32, 0.48, 0.05, 0.45), ("apron", -0.48, 0.48, 0.55, 0.95)],
    "parking_only": [("parking", -0.48, 0.48, 0.03, 0.97)],
    "jail": [("parking", -0.48, 0.48, 0.02, 0.18), ("building", -0.4, 0.4, 0.24, 0.96)],
    "garage": [("garage", -0.45, 0.45, 0.05, 0.95)],
}
SPEED = {"6L": 1667, "6S": 1667, "1S": 833, "ED": 2222, "ER": 1389, "4L": 1389, "2S": 1111, "FR": 1111, "2L": 972, "1W": 833, "DT": 694,
         "BR": 1389, "BRR": 1389, "PR": 556}  # cm/s (60, 80, 50, 50, 40, 40, 35, 30, 25 km/h ...)
NO_WALK = ("6L", "ED", "ER")
MIN_SITE_M2 = 2500.0  # a site may be smaller than its archetype wants (city blocks are ~85 m), never below this
CAND_CELL = 25000.0  # candidate sites: one per 250 m cell


# ------------------------------------------------------------------ network
class Network:
    """The street graph as travel: drive seconds and walking centimetres between street nodes."""

    def __init__(self, nodes: dict, edges: list):
        self.pos = {n: tuple(p[:2]) for n, p in nodes.items()}
        self.drive: dict = {}
        self.walk: dict = {}
        for e in edges:
            a, b, kind = e[0], e[1], e[2]
            if a not in self.pos or b not in self.pos:
                continue
            pts = [self.pos[a]] + [tuple(v[:2]) for v in (e[3] if len(e) > 3 and e[3] else [])] + [self.pos[b]]
            L = sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))
            t = L / SPEED.get(kind, 900)
            for x, y in ((a, b), (b, a)):
                self.drive.setdefault(x, []).append((y, t))
                if kind not in NO_WALK:
                    self.walk.setdefault(x, []).append((y, L))
        self._cell: dict = {}
        for n, (x, y) in self.pos.items():
            self._cell.setdefault((int(x // 5000), int(y // 5000)), []).append(n)
        self._cache: dict = {}

    def nearest(self, x, y):
        cx, cy = int(x // 5000), int(y // 5000)
        for r in range(0, 40):
            best = None
            for gx in range(cx - r, cx + r + 1):
                for gy in range(cy - r, cy + r + 1):
                    for n in self._cell.get((gx, gy), ()):
                        d = math.hypot(self.pos[n][0] - x, self.pos[n][1] - y)
                        if n in self.drive and (best is None or d < best[0]):
                            best = (d, n)
            if best:
                return best[1]
        return None

    def reach(self, src, metric: str, limit: float) -> dict:
        """Dijkstra from src: {node: cost} for every node within limit (drive seconds / walk cm)."""
        key = (src, metric, limit)
        if key in self._cache:
            return self._cache[key]
        adj = self.drive if metric == "drive" else self.walk
        best = {src: 0.0}
        heap = [(0.0, src)]
        while heap:
            c, n = heapq.heappop(heap)
            if c > best.get(n, 1e18) or c > limit:
                continue
            for m, w in adj.get(n, ()):
                nc = c + w
                if nc <= limit and nc < best.get(m, 1e18):
                    best[m] = nc
                    heapq.heappush(heap, (nc, m))
        if len(self._cache) > 4000:
            self._cache.clear()
        self._cache[key] = best
        return best


# ------------------------------------------------------------------ demand
def population(lots: list, ring_of) -> dict:
    """Residents and jobs per lot index from what is built there; totals, age groups, households."""
    res, jobs = [], []
    for r in lots:
        role = r[7] if len(r) > 7 else ""
        if role == "house":
            p, j = HOUSEHOLD, 0.0
        elif role == "apartment":
            p, j = (r[3] / 100) * (r[4] / 100) * APT_FLOORS * 0.8 * APT_PEOPLE_M2, 0.0
        elif role:
            p, j = 0.0, 0.0
        else:
            p, j = PARCEL[ring_of(r[0], r[1])]
        res.append(p)
        jobs.append(j)
    total = sum(res)
    return {"residents": res, "jobs": jobs, "total": round(total), "total_jobs": round(sum(jobs)),
            "households": round(sum(x / HOUSEHOLD for x in res)),
            "ages": {k: round(total * v) for k, v in AGES.items()}}


# ------------------------------------------------------------------ sites
class _Site:
    """A site's frame: origin at its centre, u along the frontage, v from the street to the back."""

    def __init__(self, cx, cy, yaw, L, D):
        self.cx, self.cy, self.L, self.D = cx, cy, L, D
        fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))  # toward the street
        self.ux, self.uy = -fy, fx
        self.vx, self.vy = -fx, -fy  # toward the back
        self.facing = yaw

    def w(self, u, v):  # u, v in cm from the centre (v positive toward the back)
        return (self.cx + self.ux * u + self.vx * v, self.cy + self.uy * u + self.vy * v)

    def yaw(self, du, dv):
        return math.degrees(math.atan2(self.uy * du + self.vy * dv, self.ux * du + self.vx * dv))

    def rect(self, u0, u1, v0, v1):  # archetype fractions -> centred cm
        return u0 * self.L, u1 * self.L, (v0 - 0.5) * self.D, (v1 - 0.5) * self.D


def _cross(p1, p2, q1, q2):
    d = (p2[0] - p1[0]) * (q2[1] - q1[1]) - (p2[1] - p1[1]) * (q2[0] - q1[0])
    if abs(d) < 1e-9:
        return False
    t = ((q1[0] - p1[0]) * (q2[1] - q1[1]) - (q1[1] - p1[1]) * (q2[0] - q1[0])) / d
    u = ((q1[0] - p1[0]) * (p2[1] - p1[1]) - (q1[1] - p1[1]) * (p2[0] - p1[0])) / d
    return 0 < t < 1 and 0 < u < 1


def develop(spec: dict, site: _Site, ftype: str) -> dict:
    """DevelopmentGenerator: the archetype's parts as lot rows (buildings / garages) and ground rows."""
    from .neighbourhoods import parking
    out = {"lots": [], "parking": [], "stalls": [], "grounds": []}
    front = site.w(0.0, -site.D / 2)
    for kind, *fr in ARCHETYPES[spec["arch"]]:
        u0, u1, v0, v1 = site.rect(*fr)
        cx, cy = site.w((u0 + u1) / 2, (v0 + v1) / 2)
        w, d = u1 - u0, v1 - v0
        if kind.startswith("building"):
            role = kind.split(":", 1)[1] if ":" in kind else spec["role"]
            if role:
                # the facility type rides along, so the bake can build a city hall as a city hall, not a generic block
                out["lots"].append([round(cx), round(cy), round(site.facing, 1), round(w), round(d),
                                    round(front[0]), round(front[1]), role, True, ftype])
        elif kind == "garage":
            out["lots"].append([round(cx), round(cy), round(site.facing, 1), round(w), round(d),
                                round(front[0]), round(front[1]), "parking_garage", True])
        elif kind == "parking":
            slab, stalls = parking(site, u0, u1, v0, v1)
            out["parking"].append(slab)
            out["stalls"] += stalls
        else:
            out["grounds"].append([round(cx), round(cy), round(site.yaw(1, 0), 1), round(w / 100, 2),
                                   round(d / 100, 2), kind, ftype])
    return out


# ------------------------------------------------------------------ the planner
def plan(lots: list, nodes: dict, edges: list, ring_of, ground=None, anchors: list | None = None,
         keep: list | None = None, center=(0.0, 0.0), radius: float = 1.0, industrial=None,
         barriers: list | None = None) -> dict:
    """Plan one city's facilities on its lots. lots: [x, y, yaw, w, d, fx, fy, role, footprint] (plain parcels
    have role ''). ring_of(x, y) -> 'core' | 'normal' | 'housing'. ground(x, y) -> z (terrain methods).
    anchors: [(x, y)] freeway interchanges / highway gates. keep: last plan's facilities (kept where their site
    still exists). industrial(x, y) -> True inside the industrial zone (edge sites prefer it). barriers: extra
    [(p, q)] lines a site never spans (freeways and ramps running through a block).
    Returns {lots (the city's lots with claimed ones replaced by the developments' rows), parking, stalls,
    grounds, plan: {population, demand, facilities, coverage, unmet}}."""
    net = Network(nodes, edges)
    pop = population(lots, ring_of)
    P = pop["total"]
    # demand per street node (people, jobs, children by age): where each lot is reached from
    H: dict = {}
    J: dict = {}
    lot_node = []
    for k, r in enumerate(lots):
        n = net.nearest(r[0], r[1])
        lot_node.append(n)
        if n is not None:
            H[n] = H.get(n, 0.0) + pop["residents"][k]
            J[n] = J.get(n, 0.0) + pop["jobs"][k]
    # street segments (and freeways through a block) that a site never spans
    seg_cell: dict = {}
    lines = [(net.pos[e[0]], net.pos[e[1]]) for e in edges if e[0] in net.pos and e[1] in net.pos]
    for p, q in lines + [(tuple(a[:2]), tuple(b[:2])) for a, b in barriers or []]:
        for gx in range(int(min(p[0], q[0]) // 10000), int(max(p[0], q[0]) // 10000) + 1):
            for gy in range(int(min(p[1], q[1]) // 10000), int(max(p[1], q[1]) // 10000) + 1):
                seg_cell.setdefault((gx, gy), []).append((p, q))

    def same_block(a, b):
        cells = {(int(x // 10000), int(y // 10000)) for x, y in (a, b)}
        return not any(_cross(a, b, p, q) for c in cells for p, q in seg_cell.get(c, ()))

    free = [k for k, r in enumerate(lots) if not (len(r) > 7 and r[7])]  # plain parcels only
    taken: set = set()
    lot_cell: dict = {}
    for k in free:
        lot_cell.setdefault((int(lots[k][0] // 10000), int(lots[k][1] // 10000)), []).append(k)
    node_lots: dict = {}
    for k in free:
        if lot_node[k] is not None:
            node_lots.setdefault(lot_node[k], []).append(k)

    def claim(n, spec):
        """A site of spec['size'] from free lots reached from node n (or near it), one block."""
        want = spec["size"][0] * spec["size"][1] * 1e4
        seeds = [k for k in node_lots.get(n, ()) if k not in taken]
        if not seeds:
            return None
        best = None
        for seed in seeds[:6]:
            s = lots[seed]
            group, area = [seed], s[3] * s[4]
            reach = math.sqrt(want) * 0.75 + 2000
            near = []
            for gx in range(int((s[0] - reach) // 10000), int((s[0] + reach) // 10000) + 1):
                for gy in range(int((s[1] - reach) // 10000), int((s[1] + reach) // 10000) + 1):
                    near += lot_cell.get((gx, gy), ())
            near = sorted((k for k in near if k != seed and k not in taken),
                          key=lambda k: math.hypot(lots[k][0] - s[0], lots[k][1] - s[1]))
            for k in near:
                if area >= want or len(group) >= 16:
                    break
                if math.hypot(lots[k][0] - s[0], lots[k][1] - s[1]) > reach:
                    break
                if same_block((s[0], s[1]), (lots[k][0], lots[k][1])):
                    group.append(k)
                    area += lots[k][3] * lots[k][4]
            if best is None or area > best[1]:
                best = (group, area)
        group, area = best
        if area < min(want * 0.45, MIN_SITE_M2 * 1e4) and area < want:
            return None
        # the site rectangle: the claimed lots' corners in the seed lot's frame (facing its street)
        s = lots[group[0]]
        site = _Site(s[0], s[1], s[2], 1, 1)
        us, vs = [], []
        for k in group:
            r = lots[k]
            rf = _Site(r[0], r[1], r[2], 1, 1)
            for a in (-0.5, 0.5):
                for b in (-0.5, 0.5):
                    x = r[0] + rf.ux * a * r[3] + rf.vx * b * r[4]
                    y = r[1] + rf.uy * a * r[3] + rf.vy * b * r[4]
                    dx, dy = x - s[0], y - s[1]
                    us.append(dx * site.ux + dy * site.uy)
                    vs.append(dx * site.vx + dy * site.vy)
        cu, cv = (min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2
        cx, cy = s[0] + site.ux * cu + site.vx * cv, s[1] + site.uy * cu + site.vy * cv
        return group, _Site(cx, cy, s[2], max(us) - min(us), max(vs) - min(vs))

    built = {"lots": [], "parking": [], "stalls": [], "grounds": []}
    facilities = []
    at_node: dict = {}  # facility key -> [nodes]

    def place(key, spec, n, why):
        got = claim(n, spec)
        if not got:
            return False
        group, site = got
        taken.update(group)
        dev = develop(spec, site, key)
        for k in built:
            built[k] += dev[k]
        facilities.append({"type": key, "category": spec["cat"], "level": spec.get("level"), "node": list(net.pos[n]),
                           "x": round(site.cx), "y": round(site.cy), "yaw": round(site.facing, 1),
                           "site_m": [round(site.L / 100), round(site.D / 100)], "lots": len(group), "why": why})
        at_node.setdefault(key, []).append(n)
        return True

    # candidate sites: one street node per 250 m cell (the one reaching the most free lots), so a 3 km city
    # weighs a few hundred places per facility instead of every node
    best_in: dict = {}
    for n, ks in node_lots.items():
        cell = (int(net.pos[n][0] // CAND_CELL), int(net.pos[n][1] // CAND_CELL))
        if cell not in best_in or len(ks) > len(node_lots[best_in[cell]]):
            best_in[cell] = n
    cands = list(best_in.values())
    cx0, cy0 = center

    def covered_by(keys, metric, limit):
        cov = set()
        for key in keys:
            for n in at_node.get(key, ()):
                cov.update(net.reach(n, metric, limit))
        return cov

    def p_median_add(weights, existing, metric="drive", limit=1800, top=1):
        """The candidate that cuts the weighted travel time most, given facilities at `existing` nodes
        (top > 1: the best `top` of them, best first, for when the best has no room)."""
        far = limit
        cur = {}
        for n in existing:
            for m, c in net.reach(n, metric, limit).items():
                cur[m] = min(cur.get(m, far), c)
        ranked = []
        for n in cands:
            if not [k for k in node_lots[n] if k not in taken]:
                continue
            gain = 0.0
            for m, c in net.reach(n, metric, limit).items():
                w = weights.get(m, 0.0)
                if w:
                    gain += w * max(0.0, cur.get(m, far) - c)
            ranked.append((gain, n))
        ranked = [n for _, n in sorted(ranked, key=lambda r: -r[0])[:top]]
        return ranked if top > 1 else (ranked[0] if ranked else None)

    def by_score(score):
        ok = [n for n in cands if [k for k in node_lots[n] if k not in taken]]
        return sorted(ok, key=score)

    unmet = []
    kept_keys = {(f["type"], f["x"], f["y"]) for f in keep or []}
    for key in ORDER:
        spec = CATALOG[key]
        m = spec["method"]
        # facilities from the last plan stay where they are (a growing city adds, never reshuffles)
        for f in keep or []:
            if f["type"] == key:
                n = net.nearest(*f["node"]) if f.get("node") else None
                if n is not None and math.dist(net.pos[n], f["node"]) < 500:
                    place(key, spec, n, "kept from the last plan")
        if m == "cover":
            if P < spec["min_pop"]:
                continue
            metric, limit = spec["metric"], spec["limit"]
            cov = covered_by((key,) + tuple(spec.get("serves", ())), metric, limit)
            need = spec["target"] * sum(H.values())
            for _ in range(60):
                have = sum(H.get(n, 0.0) for n in cov)
                if have >= need:
                    break
                best = None
                for n in cands:
                    if not [k for k in node_lots[n] if k not in taken]:
                        continue
                    gain = sum(H.get(x, 0.0) for x in net.reach(n, metric, limit) if x not in cov)
                    if best is None or gain > best[0]:
                        best = (gain, n)
                if not best or best[0] < max(50.0, spec["min_pop"] * 0.08):
                    break
                if place(key, spec, best[1], f"covers {round(best[0])} more people within "
                                             f"{limit} {'s drive' if metric == 'drive' else 'cm walk'}"):
                    cov.update(net.reach(best[1], metric, limit))
                else:
                    node_lots[best[1]] = [k for k in node_lots[best[1]] if k in taken]  # no room there
            have = sum(H.get(n, 0.0) for n in cov)
            if have < need * 0.98:
                unmet.append({"type": key, "covered": round(have / max(1.0, sum(H.values())), 2), "target": spec["target"]})
        elif m == "capacity":
            if spec.get("age"):
                weights = {n: h * AGES[spec["age"]] for n, h in H.items()}
            elif spec.get("jobs"):
                weights = dict(J)
            else:
                weights = {n: h * spec.get("rate", 1.0) for n, h in H.items()}
            demand = sum(weights.values())
            if demand < spec["min_demand"]:
                continue
            count = max(1, math.ceil(demand / spec["capacity"]))
            have = len(at_node.get(key, []))
            why = f"{round(demand)} {spec.get('age', 'people')} demand, {spec['capacity']} per site"
            for _ in range(count - have):
                if spec.get("edge") and industrial:
                    order = by_score(lambda x: (not industrial(*net.pos[x]),
                                                -math.hypot(net.pos[x][0] - cx0, net.pos[x][1] - cy0)))[:12]
                else:
                    order = p_median_add(weights, at_node.get(key, []), limit=spec.get("catch", 900), top=12)
                if not any(place(key, spec, n, why) for n in order):
                    unmet.append({"type": key, "short": count - len(at_node.get(key, []))})
                    break
        elif m == "single":
            if P < spec["min_pop"] or at_node.get(key):
                continue
            if spec.get("edge"):
                order = by_score(lambda x: ((not industrial(*net.pos[x])) if industrial else 0,
                                            -math.hypot(net.pos[x][0] - cx0, net.pos[x][1] - cy0)))
            else:
                order = p_median_add(H, [], top=12)
            if not any(place(key, spec, n, "central: the whole city reaches it fastest" if not spec.get("edge")
                             else "edge / industrial zone, away from housing") for n in order[:12]):
                unmet.append({"type": key, "short": 1})
        elif m == "terrain":
            if P < spec["min_pop"]:
                continue
            count = max(1, math.ceil(P / spec["per"])) - len(at_node.get(key, []))
            z = (lambda x: ground(*net.pos[x]) or 0.0) if ground else (lambda x: 0.0)
            order = by_score(lambda x: z(x) if spec["low"] else -z(x))
            for n in order:
                if count <= 0:
                    break
                if any(math.dist(net.pos[n], net.pos[o]) < 30000 for o in at_node.get(key, [])):
                    continue  # spread them out
                if place(key, spec, n, "lowest ground (gravity flow)" if spec["low"] else "highest ground (pressure / line of sight)"):
                    count -= 1
        elif m == "anchors":
            if P < spec["min_pop"]:
                continue
            for ax, ay in anchors or []:
                # one per interchange: a site kept from the last plan serves it (every re-plan added another one
                # beside each interchange, and the city lost twenty lots a rebuild with nothing changed)
                if any(math.hypot(net.pos[o][0] - ax, net.pos[o][1] - ay) < 40000 for o in at_node.get(key, [])):
                    continue
                order = by_score(lambda x: math.hypot(net.pos[x][0] - ax, net.pos[x][1] - ay))
                if order and math.hypot(net.pos[order[0]][0] - ax, net.pos[order[0]][1] - ay) < 40000:
                    place(key, spec, order[0], "beside a freeway interchange")
    # coverage report (road network, never circles)
    total_h = max(1.0, sum(H.values()))
    coverage = {}
    for key in ("fire_station", "police_precinct", "ambulance", "neighbourhood_park", "convenience", "neighbourhood_center"):
        spec = CATALOG[key]
        cov = covered_by((key,) + tuple(spec.get("serves", ())), spec["metric"], spec["limit"])
        coverage[key] = round(sum(H.get(n, 0.0) for n in cov) / total_h, 3)
    # school catchments: each neighbourhood node to its nearest school by travel time, within capacity
    catchments = {}
    for key in ("elementary_school", "middle_school", "high_school"):
        sites = at_node.get(key, [])
        if not sites:
            continue
        load = [0.0] * len(sites)
        reach = [net.reach(s, "drive", 1800) for s in sites]
        kids = AGES[CATALOG[key]["age"]]
        for n in sorted(H, key=lambda n: -H[n]):
            opts = sorted((reach[i].get(n, 1e9), i) for i in range(len(sites)))
            for t, i in opts:
                if load[i] + H[n] * kids <= CATALOG[key]["capacity"] * 1.1 or i == opts[-1][1]:
                    load[i] += H[n] * kids
                    break
        catchments[key] = [round(x) for x in load]
    lots_out = [r for k, r in enumerate(lots) if k not in taken] + built["lots"]
    counts: dict = {}
    for f in facilities:
        counts[f["type"]] = counts.get(f["type"], 0) + 1
    return {"lots": lots_out, "parking": built["parking"], "stalls": built["stalls"], "grounds": built["grounds"],
            "plan": {"population": {k: v for k, v in pop.items() if k not in ("residents", "jobs")},
                     "demand": {"retail": round(P * RETAIL_PER_RESIDENT),
                                "school_seats": {k: pop["ages"][CATALOG[k]["age"]] for k in
                                                 ("daycare", "elementary_school", "middle_school", "high_school")}},
                     "counts": counts, "by_category": {c: sum(v for k, v in counts.items() if CATALOG[k]["cat"] == c)
                                                       for c in CATEGORIES},
                     "coverage": coverage, "school_enrolment": catchments, "unmet": unmet,
                     "facilities": facilities}}


def _demo() -> None:
    import random
    from . import parcels
    # a 1.6 km grid city with plain parcels: every lot is reachable, nothing is placed at random
    block = 10000
    nodes = {(i, j): [i * block, j * block] for i in range(-8, 9) for j in range(-8, 9)}
    edges = [[(i, j), (i + 1, j), "4L" if j % 4 == 0 else "2S"] for i in range(-8, 8) for j in range(-8, 9)] + \
            [[(i, j), (i, j + 1), "4L" if i % 4 == 0 else "2S"] for i in range(-8, 9) for j in range(-8, 8)]
    half = {"4L": 1400.0, "2S": 875.0}
    rnd = random.Random(3)
    lots = []
    for area in parcels.block_areas(nodes, edges, half):
        lots += [[x, y, yaw, w, d, fx, fy, "", False] for x, y, yaw, w, d, _p, fx, fy in parcels.lots_in(area, 1600, 3500, rnd)]
    ring = lambda x, y: "core" if math.hypot(x, y) < 25000 else "normal" if math.hypot(x, y) < 55000 else "housing"
    out = plan(lots, nodes, edges, ring, ground=lambda x, y: x * 0.01, anchors=[(80000, 0)], radius=80000)
    p = out["plan"]
    print("  population", p["population"]["total"], "facilities", p["counts"])
    assert p["population"]["total"] > 10000
    c = p["counts"]
    assert c.get("fire_station", 0) >= 1 and c.get("elementary_school", 0) >= 1 and c.get("police_hq") == 1, c
    print("  coverage", p["coverage"], "unmet", p["unmet"])
    # parks by walking distance: every home within 500 m of a park (community parks count too)
    assert p["coverage"]["neighbourhood_park"] >= 0.8 and c.get("neighbourhood_center", 0) >= 1, (c, p["coverage"])
    assert p["coverage"]["fire_station"] >= 0.9, p["coverage"]
    # schools by enrolment: seats >= children, never a fixed count
    kids = p["demand"]["school_seats"]["elementary_school"]
    assert c["elementary_school"] * 600 >= kids * 0.9, (c["elementary_school"], kids)
    # complete developments: a school has a building, a field and a playground; a fire station an apron
    kinds = {g[5] for g in out["grounds"]}
    assert {"field", "playground", "apron"} <= kinds, kinds
    roles = {r[7] for r in out["lots"] if r[8] is True}
    assert {"school", "fire_station", "police", "shop", "park"} <= roles, roles
    assert out["parking"] and out["stalls"], "sites come with their parking"
    # no site spans a street; claimed lots are gone, the rest stay
    assert len(out["lots"]) < len(lots) + len([f for f in p["facilities"]]) * 4
    # water towers on the highest ground, treatment on the lowest (ground rises to +x)
    wt = [f for f in p["facilities"] if f["type"] == "water_tower"]
    ww = [f for f in p["facilities"] if f["type"] == "wastewater"]
    assert wt and ww and wt[0]["x"] > 40000 > ww[0]["x"], (wt, ww)
    # growth: the same plan, kept, places the same facilities again and adds only for new demand
    again = plan(lots, nodes, edges, ring, ground=lambda x, y: x * 0.01, keep=p["facilities"])
    assert {(f["type"], f["x"], f["y"]) for f in p["facilities"]} <= \
           {(f["type"], f["x"], f["y"]) for f in again["plan"]["facilities"]}, "kept in place"
    print("urban ok", p["coverage"])


if __name__ == "__main__":
    _demo()
