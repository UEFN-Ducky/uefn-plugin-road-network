import base64
import hashlib
import math
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import citygen as cg  # noqa: E402
from backend.terrain import export as tex  # noqa: E402

CITIES = [{"x": -150000.0, "y": -150000.0, "radius": 60000.0}, {"x": 150000.0, "y": 150000.0, "radius": 60000.0}]
# terrain() before the engine was wired in (commit 81fae9f): sha1 of (heights, sea, z_scale)
CLASSIC = [(1, {}, "a3315e79eb415179f54055c6646d19d2777eebe2"),
           (7, {"mountain_m": 1500, "city_ground": "flatten"}, "107f553fdfb047d23cb6668710727603e309bb92"),
           (42, {"sea_m": -5, "island": "one"}, "9042ed388a4032689fb33e052f0690e09e43eccc")]


def _sig(t):
    return hashlib.sha1(repr((t["heights"], t["sea"], t["z_scale"])).encode()).hexdigest()


def test_classic_is_the_old_ground():
    for seed, opts, want in CLASSIC:
        assert _sig(cg.terrain(seed, 600000.0, 6000.0, CITIES, grid=48, opts=opts)) == want, seed
        assert _sig(cg.terrain(seed, 600000.0, 6000.0, CITIES, grid=48, opts={**opts, "preset": "Classic"})) == want
    assert cg.options({})["preset"] == "Classic"
    # an engine preset's own values fill the terrain keys nobody gave (they were all defaulted: presets looked alike)
    assert "mountain_m" not in cg.options({"preset": "Alpine"})
    assert cg.options({"preset": "Alpine", "mountain_m": 900})["mountain_m"] == 900
    a, b = (cg.terrain(2, 600000.0, 6000.0, CITIES, grid=32, opts={"preset": p})["heights"] for p in ("Alpine", "Plains"))
    assert a != b


def test_coastal_runs_the_engine_in_cm():
    t = cg.terrain(3, 600000.0, 6000.0, CITIES, grid=48, opts={"preset": "Coastal", "mountain_m": 2000})
    hs = [v for r in t["heights"] for v in r]
    assert t["grid"] == 48 and len(t["heights"]) == 48 and t["min"] == [-300000.0, -300000.0]
    assert max(hs) > 50000  # cm: mountains well over 500 m
    assert t["z_scale"] > 100 and cg.z_scale(t) == t["z_scale"]
    assert t["sea"] is not None  # Coastal has a coast
    assert set(t["maps"]) <= set(cg.ENGINE_MAPS) and "biome" in t["maps"]
    assert all(len(m) == 48 * 48 for m in t["maps"].values())
    png, settings = cg.heightmap_png(t, res=129)
    assert settings["scale"][2] == t["z_scale"]


def _decode(png):
    """(width, height, channels, rows of bytes) of an 8-bit PNG as export writes it (filter 0 rows)."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, bits, kind = struct.unpack(">IIBB", png[16:26])
    assert bits == 8
    ch = {0: 1, 2: 3}[kind]
    k, idat = 8, b""
    while k < len(png):
        size, tag = struct.unpack(">I4s", png[k:k + 8])
        idat += png[k + 8:k + 8 + size] if tag == b"IDAT" else b""
        k += 12 + size
    raw = zlib.decompress(idat)
    stride = 1 + w * ch
    return w, h, ch, [raw[r * stride + 1:(r + 1) * stride] for r in range(h)]


DEBUG_VIEWS = ("height land coast_dist plains hills foothills mountains plateaus valleys wetlands ridge passes flow "
               "accum river lake temp moist biome slope build road_cost flood").split()


def test_material_masks_and_debug_maps():
    t = cg.terrain(5, 600000.0, 6000.0, CITIES, grid=40, opts={"preset": "Alpine"})
    assert t["rivers"] and all(len(p) == 2 for line in t["rivers"] for p in line)
    pngs = tex.material_pngs(t, 65)
    assert sorted(pngs) == sorted(tex.LAYERS)
    rows = {k: _decode(v) for k, v in pngs.items()}
    assert all(r[:3] == (65, 65, 1) for r in rows.values())
    for j in range(65):
        for i in range(65):
            assert abs(sum(rows[k][3][j][i] for k in rows) - 255) <= 2
    assert tex.material_pngs(cg.terrain(5, 600000.0, 6000.0, CITIES, grid=40), 65) == {}  # Classic: no biomes
    full = {**t, "maps": cg.LAST_WORLD.maps}
    for name in DEBUG_VIEWS:
        assert _decode(tex.debug_map(full, name, 32))[:3] == (32, 32, 3), name


def test_terrain_map_rpc():
    from backend import Ctx
    from backend import tools_generate
    ter = cg.terrain(5, 600000.0, 6000.0, CITIES, grid=40, opts={"preset": "Coastal"})
    ctx = Ctx()
    ctx.scan_terrain = lambda nid: ter if nid == "valley" else {}
    ctx.slug = lambda s: str(s).strip().lower()
    ctx.holds_the_world = ctx.one_run_at_a_time = lambda fn: fn  # decorators used while registering
    tools_generate.register(ctx)
    for name in ("biome", "debug:river", "height"):
        out = ctx.terrain_map("Valley", name)
        assert out["ok"], out
        assert _decode(base64.b64decode(out["png"]))[:3] == (256, 256, 3)
    assert not ctx.terrain_map("Valley", "accum")["ok"]  # not kept in the stored world, no run this session
    assert not ctx.terrain_map("Nowhere", "biome")["ok"]


def test_rivers_and_lakes_are_wet_and_bridged():
    t = cg.terrain(5, 600000.0, 6000.0, CITIES, grid=40, opts={"preset": "Alpine"})
    wet = cg.wet_from(t)
    lake = t["maps"]["lake"]
    n = math.isqrt(len(lake))
    cell = 600000.0 / n
    k = next(k for k, v in enumerate(lake) if v > 0.5)
    assert wet(-300000.0 + cell * (k % n + 0.5), -300000.0 + cell * (k // n + 0.5))
    # a highway square across a river's middle, both ends on dry land, gets a bridge
    crossings = []
    for line in t["rivers"]:
        for a, b in zip(line, line[1:]):
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            ux, uy = (b[0] - a[0]), (b[1] - a[1])
            L = math.hypot(ux, uy) or 1.0
            p, q = (mx - uy / L * 20000, my + ux / L * 20000), (mx + uy / L * 20000, my - ux / L * 20000)
            if not wet(*p) and not wet(*q):
                crossings.append((p, q))
    assert crossings
    p, q = crossings[0]
    bridges = []
    nodes, edges = cg.over_water({"a": [*p, 0.0], "b": [*q, 0.0]}, [("a", "b", "4L")], wet, bridges=bridges)
    assert bridges and edges
    assert cg.wet_from(cg.terrain(5, 600000.0, 6000.0, CITIES, grid=40)) is None  # Classic, no sea: no water


def test_engine_holds_keep_zones_and_digs_lakes():
    keep = cg.keep_zones([{"x0": -20000, "y0": -20000, "x1": 20000, "y1": 20000, "z": 500}])
    lakes = cg.lake_list([{"pts": [[100000, -200000], [200000, -200000]], "width_m": 300}])
    t = cg.terrain(3, 600000.0, 6000.0, CITIES, grid=48, opts={"preset": "Plains"}, keep=keep, lakes=lakes)
    assert abs(cg.imported_ground(t, res=129)(0.0, 0.0) - 500) < 300
    assert t["sea"] is not None  # a lake on a coast-less world brings its own water line
    j, i = int((-200000 + 300000) / 600000 * 48), int((150000 + 300000) / 600000 * 48)
    assert t["heights"][j][i] < t["sea"]
