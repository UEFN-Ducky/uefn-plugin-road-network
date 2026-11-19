"""Terrain engine settings: KEYS (number sliders), CHOICES (pickers), PRESETS, and terrain_config() to merge them."""

# name: (default, min, max)
KEYS = {
    'mountain_m': (1200.0, 0.0, 2500.0),       # highest peak
    'mountain_cover': (0.2, 0.05, 0.8),        # share of the land in ranges
    'mountain_intensity': (0.5, 0.0, 1.0),     # how sharp and tall the ranges get
    'flatland': (0.5, 0.0, 1.0),               # 1 = every free cell is plain
    'hills': (0.5, 0.0, 1.0),
    'plateaus': (0.1, 0.0, 1.0),
    'coast_side_deg': (0.0, 0.0, 360.0),       # where the sea is: 0 = +x (east), 90 = +y
    'sea_level_m': (0.0, -50.0, 50.0),
    'erosion': (0.5, 0.0, 1.0),
    'thermal': (0.5, 0.0, 1.0),
    'rivers': (0.5, 0.0, 1.0),
    'lakes': (0.4, 0.0, 1.0),
    'temp_c': (14.0, -10.0, 35.0),             # sea-level mean temperature
    'rain': (0.5, 0.0, 1.0),
    'snowline_m': (1900.0, 300.0, 4000.0),
    'detail': (0.5, 0.0, 1.0),
    'hill_km': (2.0, 0.3, 10.0),               # hill spacing
    'relief_m': (60.0, 0.0, 250.0),            # hill height
}

# name: (default, allowed)
CHOICES = {
    'coast': ('one', ('none', 'one', 'two', 'island', 'archipelago')),
}

PRESETS = {
    'Coastal': {'mountain_intensity': 0.55, 'flatland': 0.6, 'hills': 0.55, 'coast': 'one',
                'mountain_m': 1600.0, 'mountain_cover': 0.25, 'plateaus': 0.05},
    'Plains': {'mountain_m': 300.0, 'mountain_cover': 0.05, 'mountain_intensity': 0.2, 'flatland': 0.9,
               'hills': 0.25, 'plateaus': 0.05, 'coast': 'none', 'relief_m': 30.0, 'hill_km': 4.0},
    'Mountainous': {'mountain_m': 2200.0, 'mountain_cover': 0.45, 'mountain_intensity': 0.85, 'flatland': 0.25,
                    'hills': 0.6, 'coast': 'none', 'relief_m': 120.0},
    'Alpine': {'mountain_m': 2500.0, 'mountain_cover': 0.5, 'mountain_intensity': 0.95, 'flatland': 0.2,
               'hills': 0.6, 'coast': 'none', 'snowline_m': 1400.0, 'temp_c': 8.0, 'lakes': 0.7, 'relief_m': 140.0},
    'Tropical': {'temp_c': 28.0, 'rain': 0.9, 'coast': 'two', 'mountain_m': 1200.0, 'mountain_cover': 0.15,
                 'snowline_m': 4000.0, 'rivers': 0.7},
    'Desert': {'temp_c': 30.0, 'rain': 0.1, 'coast': 'none', 'mountain_m': 900.0, 'mountain_cover': 0.12,
               'plateaus': 0.45, 'rivers': 0.2, 'lakes': 0.1, 'erosion': 0.3, 'snowline_m': 4000.0},
    'Island': {'coast': 'island', 'mountain_m': 1200.0, 'mountain_cover': 0.15, 'mountain_intensity': 0.6,
               'flatland': 0.55, 'temp_c': 22.0, 'rain': 0.65},
    'Mediterranean': {'coast': 'one', 'temp_c': 19.0, 'rain': 0.35, 'hills': 0.65, 'mountain_m': 1400.0,
                      'mountain_cover': 0.2, 'flatland': 0.45, 'coast_side_deg': 270.0},
    'Continental': {'coast': 'none', 'temp_c': 9.0, 'rain': 0.5, 'flatland': 0.7, 'mountain_m': 1000.0,
                    'mountain_cover': 0.12, 'lakes': 0.6},
    'Mixed': {},
    'Classic': {'classic': True},  # the old citygen path, byte for byte: callers delegate, the engine is not run
}

DEFAULT_PRESET = 'Coastal'


def _num(v, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return default if v != v else v  # NaN -> default


def terrain_config(opts: dict | None = None) -> dict:
    """Preset under the user's keys, every number clamped to its range. Unknown keys are ignored.
    Always has seed (int), preset (name) and classic (bool)."""
    opts = dict(opts or {})
    name = opts.get('preset') if opts.get('preset') in PRESETS else DEFAULT_PRESET
    merged = {**PRESETS[name], **{k: v for k, v in opts.items() if k in KEYS or k in CHOICES}}
    cfg = {}
    for k, (d, lo, hi) in KEYS.items():
        cfg[k] = min(max(_num(merged.get(k, d), d), lo), hi)
    for k, (d, allowed) in CHOICES.items():
        cfg[k] = merged.get(k) if merged.get(k) in allowed else d
    cfg['coast_side_deg'] %= 360.0
    cfg['seed'] = int(_num(opts.get('seed', 0), 0))
    cfg['preset'] = name
    cfg['classic'] = bool(PRESETS[name].get('classic'))
    return cfg
