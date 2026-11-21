# City Builder (UEFN Ducky plugin)

Build whole cities for UEFN: generate worlds with several cities, terrain and roads, lay out every building,
street light, sign and tree as PCG layers with a live 3D preview, pick the project's own kit or Fortnite pieces,
and build it in UEFN in one click.

Install it from the UEFN Ducky Store (City Builder), or load this folder as a plugin draft in UEFN Ducky.

## Layout

| Folder | What it holds |
|---|---|
| `backend/` | Python backend: world and city generation (`citygen/`), PCG layers (`pcg_layers.py`), kit baking (`kit_bake.py`), road splines (`splines_text.py`), the tools the agent calls (`__init__.py`) |
| `listener/` | Code that runs inside UEFN through the Ducky listener: road and junction meshes, collision, LODs, saving (`road_io.py`) |
| `ui/` | The City Builder panel (plain HTML/JS, three.js preview) |
| `skills/road-network/` | The agent skill: how to drive City Builder, the city method, pieces and systems |
| `tests/`, `scripts/` | Tests, zip build and Store release scripts |

Most backend modules carry their own self-checks: `py -3 -m backend.splines_text`, for example.

## citygen is one module in several files

`backend/citygen/` is one module cut by system (`_kit.py`, `_terrain.py`, …): `__init__.py` runs each file into its
own namespace, so every name is `citygen.X`. The Store build has no `.py` files to read, so Ducky's build engine
compiles the files named in the marked list (`_CG_PARTS = (...)  # ducky-build: one module`) into the module.

- A new part is a new file **and** its name in `_CG_PARTS`, in the order it must run.
- Every part starts with `from __future__ import annotations`, like `__init__.py`.
- Nothing else in the backend reads or runs a `.py` file: a Store build would not load, and the build refuses it.

`tests/test_citygen_parts.py` holds the first two.

## The worker process

City Builder's work runs in one process of its own on the PC (`backend/worker.py`), not inside Ducky's window:
a preview or a rebuild no longer slows the window, the chats and the other plugins, and the hundreds of MB a
placement holds are given back when the worker closes (3 minutes after the last call).

- Every Ducky process still registers the plugin, but each panel call, tool and workflow node is passed to the
  worker. The parts (`backend/tools_*.py`) run there, so their caches, locks and progress exist once.
- What belongs to Ducky stays with the process that asked, and is called back there on the thread that made
  the call: `data` (the store and its history), `api` (UEFN, Ducky's tools), `actor`, `team_live`, the holds,
  `push`, `record`, `epic_call`.

Rules when changing the backend:

1. A part never imports Ducky's own modules (`frontend.*`, `backend.*` of the app). Take what you need from `ctx`.
2. A new helper in `register()` that keeps state on this PC for the person (like the holds), or that imports
   Ducky's modules, belongs to the caller: add its name to both lists in `register()` (`api.caller(...)` and
   `cb_link.provide(...)`).
3. Arguments and answers of a panel call, a tool or a node are plain data (they already are: JSON).

`tests/test_worker.py` holds the plugin to this: it runs the whole backend in one process and through the worker
and compares every answer, the stored docs and files, and every call into Ducky. `CITY_BUILDER_WORKER=0` runs
everything in the caller, the old way.

## Contributing

Issues and pull requests are welcome. Keep changes small and include the self-check that covers them.

## License

MIT, see [LICENSE](LICENSE). `ui/vendor/three@0.160.0` keeps its own MIT licence.
