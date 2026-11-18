"""PCG placement layers for a road network world, and the PCG graph plan that builds them in UEFN.

A layer says WHAT goes WHERE: meshes (with weights) scattered on the ground, lined along the
roads, or set back from them as buildings; inside one district ring of every city, outside the
cities, or everywhere; avoiding the roads and the layers placed before it. ``plan()`` turns the
layers into one PCG graph, node by node in data-flow order, with the native node names and the
settings that worked in UEFN 43 (pcg skill, ``graph_layers``):

    sample -> Transform Points -> Bounds Modifier -> district mask -> Difference (Binary) against
    roads and earlier layers -> Self Pruning -> Static Mesh Spawner

Order matters: transform and bounds before pruning and differences (bad bounds = bad pruning),
and every Difference subtracts the *bounds-modified* points of the other set, never its spawner.

The package by system, every name still read as ``pl.X``: layers (what a layer is), samples (road
samples and sweeps), graph (district masks and plan), land (open land, paving, caps, district tests),
lots (a lot's ground), preview (offline placement). Self-check: ``py -3 -m backend.pcg_layers``.
"""

from .layers import *  # noqa: F401,F403
from .samples import *  # noqa: F401,F403
from .graph import *  # noqa: F401,F403
from .land import *  # noqa: F401,F403
from .lots import *  # noqa: F401,F403
from .preview import *  # noqa: F401,F403
# the private helpers, so every pl._X still resolves
from .layers import _bld, _guess_role, _lattice, _prop, _slug  # noqa: F401
from .samples import _Hash  # noqa: F401
from .graph import _in_wedge, _ring, _wedge  # noqa: F401
from .land import (_city_sig, _height, _in_where, _lot_hits_road, _rects_hit, _Rects, _u01,  # noqa: F401
                   _where_test)
from .lots import _lot_ground  # noqa: F401
