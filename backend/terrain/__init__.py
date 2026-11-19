"""World terrain engine (pure Python, metres): coast, regions, mountains, hills, erosion, rivers, biomes."""
from .grid import Grid
from .noise import Noise


def __getattr__(name):  # generate / PRESETS / terrain_config load on first use
    if name in ('generate', 'World'):
        from . import engine
        return getattr(engine, name)
    if name in ('PRESETS', 'KEYS', 'terrain_config'):
        from . import config
        return getattr(config, name)
    raise AttributeError(name)


__all__ = ['Grid', 'Noise', 'generate', 'World', 'PRESETS', 'KEYS', 'terrain_config']
