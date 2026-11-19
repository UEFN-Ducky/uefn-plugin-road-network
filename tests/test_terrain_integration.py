import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import citygen as cg  # noqa: E402

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


def test_engine_holds_keep_zones_and_digs_lakes():
    keep = cg.keep_zones([{"x0": -20000, "y0": -20000, "x1": 20000, "y1": 20000, "z": 500}])
    lakes = cg.lake_list([{"pts": [[100000, -200000], [200000, -200000]], "width_m": 300}])
    t = cg.terrain(3, 600000.0, 6000.0, CITIES, grid=48, opts={"preset": "Plains"}, keep=keep, lakes=lakes)
    assert abs(cg.imported_ground(t, res=129)(0.0, 0.0) - 500) < 300
    assert t["sea"] is not None  # a lake on a coast-less world brings its own water line
    j, i = int((-200000 + 300000) / 600000 * 48), int((150000 + 300000) / 600000 * 48)
    assert t["heights"][j][i] < t["sea"]
