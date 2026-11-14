# City Builder (UEFN Ducky plugin)

Build whole cities for UEFN: generate worlds with several cities, terrain and roads, lay out every building,
street light, sign and tree as PCG layers with a live 3D preview, pick the project's own kit or Fortnite pieces,
and build it in UEFN in one click.

Install it from the UEFN Ducky Store (City Builder), or load this folder as a plugin draft in UEFN Ducky.

## Layout

| Folder | What it holds |
|---|---|
| `backend/` | Python backend: world and city generation (`citygen.py`), PCG layers (`pcg_layers.py`), kit baking (`kit_bake.py`), road splines (`splines_text.py`), the tools the agent calls (`__init__.py`) |
| `listener/` | Code that runs inside UEFN through the Ducky listener: road and junction meshes, collision, LODs, saving (`road_io.py`) |
| `ui/` | The City Builder panel (plain HTML/JS, three.js preview) |
| `skills/road-network/` | The agent skill: how to drive City Builder, the city method, pieces and systems |
| `tests/`, `scripts/` | Tests, zip build and Store release scripts |

Most backend modules carry their own self-checks: `py -3 -m backend.splines_text`, for example.

## Contributing

Issues and pull requests are welcome. Keep changes small and include the self-check that covers them.

## License

MIT, see [LICENSE](LICENSE). `ui/vendor/three@0.160.0` keeps its own MIT licence.
