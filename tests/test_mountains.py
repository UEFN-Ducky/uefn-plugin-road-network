import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import citygen as cg  # noqa: E402

CITIES = [{"x": -150000.0, "y": -150000.0, "radius": 60000.0}, {"x": 150000.0, "y": 150000.0, "radius": 60000.0}]


def test_mountains_in_places_flat_elsewhere():
    flat = cg.terrain(7, 600000.0, 1000.0, CITIES, grid=80, opts={"mountain_m": 2000})
    hs = [v for r in flat["heights"] for v in r]
    assert max(hs) > 120000  # ranges well over the old 256 m cap
    assert sum(v < 2000 for v in hs) > len(hs) * 0.4  # most of the map keeps the +-10 m ground
    for c in CITIES:  # the towns stay on their low ground
        assert abs(cg.imported_ground(flat, res=129)(c["x"], c["y"])) < 1500
    # the import Z scale grows to hold the peaks, and the 16-bit heightmap reads back what it was given
    zs = cg.z_scale(flat)
    assert zs > 100 and max(abs(v) for v in hs) * 128 / zs <= 32767
    png, settings = cg.heightmap_png(flat, res=129)
    assert settings["scale"][2] == zs
    ground = cg.imported_ground(flat, res=129)
    j, i = max(((j, i) for j in range(80) for i in range(80)), key=lambda p: flat["heights"][p[0]][p[1]])
    x = flat["min"][0] + 600000.0 * (i + 0.5) / 80
    y = flat["min"][1] + 600000.0 * (j + 0.5) / 80
    assert math.isclose(ground(x, y), flat["heights"][j][i], rel_tol=0.25)


def test_terrain_seed_gives_new_ground():
    for preset in ("Classic", "Coastal"):
        base = {"preset": preset, "mountain_m": 1200}
        world = cg.terrain(7, 600000.0, 6000.0, CITIES, grid=40, opts=base)["heights"]
        assert world == cg.terrain(7, 600000.0, 6000.0, CITIES, grid=40, opts={**base, "terrain_seed": 0})["heights"]  # 0 = the world seed
        assert world != cg.terrain(7, 600000.0, 6000.0, CITIES, grid=40, opts={**base, "terrain_seed": 4242})["heights"]


def test_small_island_gets_its_mountains():
    # a 6 km island has no ground 8 km inland: the inland band shrinks to the land, so the sliders still raise ranges
    opts = {"preset": "Island", "mountain_m": 2500, "mountain_cover": 0.8, "mountain_intensity": 1.0, "flatland": 0.0}
    hs = [v for r in cg.terrain(7, 600000.0, 6000.0, CITIES, grid=64, opts=opts)["heights"] for v in r]
    assert max(hs) > 150000


def test_peaks_are_pointed_not_walled_domes():
    for inten in (0.1, 0.9):
        opts = {"preset": "Mountainous", "coast": "island", "mountain_m": 2500, "mountain_cover": 0.2, "mountain_intensity": inten}
        t = cg.terrain(420, 600000.0, 12000.0, [], grid=160, opts=opts)
        H, n = t["heights"], t["grid"]
        top = max(v for r in H for v in r)
        assert sum(v > top * 0.9 for r in H for v in r) < 300  # a summit, not a plateau (the dome had ~1200)
        steep = max(abs(H[j][i + 1] - H[j][i]) for j in range(n) for i in range(n - 1)) / (600000.0 / n)
        assert steep < 6.0  # no vertical walls (was 9.6)


def test_water_level_sets_the_sea_on_every_preset():
    for preset in ("Mountainous", "Coastal", "Classic"):
        assert cg.terrain(7, 600000.0, 6000.0, CITIES, grid=32, opts={"preset": preset, "sea_m": -85})["sea"] == -8500
    assert cg.terrain(7, 600000.0, 6000.0, CITIES, grid=32, opts={"preset": "Mountainous"})["sea"] is None  # Off, no coast


def test_no_mountains_keeps_old_ground():
    ter = cg.terrain(7, 600000.0, 6000.0, CITIES, grid=40)
    assert cg.z_scale(ter) == 100.0
    assert ter["heights"] == cg.terrain(7, 600000.0, 6000.0, CITIES, grid=40, opts={"mountain_m": 0})["heights"]
