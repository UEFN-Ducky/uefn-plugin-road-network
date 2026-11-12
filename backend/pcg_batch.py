"""PCG tiles built in batches: ONE editor script per group of tiles instead of ~40 calls per tile.

Why: a tile is a graph, a spawner and point nodes per mesh, their wires, a volume and a generate. Sent one
call at a time that was a round-trip each (13 s a tile, half an hour for 130 tiles). The script runs the same
Epic tools inside UEFN (ProgrammaticToolset), so a batch costs one round-trip. Batches stay small on
purpose: UEFN frees what a call used only when the call ends, and one call for a whole city took it past
100 GB. The caller meters every batch and lets UEFN settle between them.
"""

from __future__ import annotations

import json

MAX_POINTS = 6000  # instances per batch: bounds what one editor call holds before UEFN can free it
MAX_TILES = 8  # sparse tiles ride together up to this many
CHUNK = 1500  # points per Create Points node: keeps each node's settings a sensible size

_SCRIPT = '''import json
JOBS = json.loads(__JOBS__)
CHUNK = __CHUNK__
P = 'PCGToolset.PCGToolset.'

async def t(name, args):
    r = await execute_tool(name, json.dumps(args))
    return r.get('returnValue') if hasattr(r, 'get') else r

async def build(j, placed):
    # the tile's old volume(s), found by label in the level as it is now: a stored actor path goes stale when
    # UEFN reloads the level, and removing by it silently left the old tile (and its instances) in place
    for path in placed.get(j['label'], []):
        await t('EditorToolset.LevelToolset.RemoveFromScene', {'actor': {'refPath': path}})
    for g in j['drop_graphs']:
        if await t('EditorToolset.AssetToolset.Exists', {'path': g}):
            await t('EditorToolset.AssetToolset.Delete', {'path': g})
    if not j['meshes']:
        return {'removed': True}
    graph = await t(P + 'CreateGraph', {'name': j['graph_name'], 'path': j['graph_folder']})
    for k, m in enumerate(j['meshes']):
        spawn = await t(P + 'AddNode', {
            'graph': graph, 'nativeNodeType': 'Static Mesh Spawner', 'nodeName': 'Spawn%d' % k,
            'nodeTitle': m['title'], 'xPositionIdx': 400, 'yPositionIdx': k * 160,
            'jsonParams': json.dumps({'meshSelector': m['selector']})})
        rows = m['rows']
        for c in range(0, len(rows), CHUNK):
            pts = [{'transform': {'location': {'x': r[0], 'y': r[1], 'z': r[2]},
                                  'rotation': {'pitch': r[7] if len(r) > 7 else 0, 'yaw': r[3], 'roll': 0},
                                  'scale': {'x': r[4], 'y': r[5], 'z': r[6]}}} for r in rows[c:c + CHUNK]]
            made = await t(P + 'AddNode', {
                'graph': graph, 'nativeNodeType': 'Create Points', 'nodeName': 'Lots%d_%d' % (k, c // CHUNK),
                'xPositionIdx': 0, 'yPositionIdx': k * 160 + c // CHUNK * 40,
                'jsonParams': json.dumps({'pointsToCreate': pts, 'coordinateSpace': 'World',
                                          'bCullPointsOutsideVolume': False})})
            await t(P + 'ConnectNodePins', {'fromNode': made, 'fromPinLabel': 'Out', 'toNode': spawn, 'toPinLabel': 'In'})
    await t(P + 'SetGraphDescription', {'graph': graph, 'description': j['description']})
    vol = await t(P + 'SpawnGraphInstance', {
        'graph': graph, 'name': j['label'],
        'transform': {'location': {'x': j['centre'][0], 'y': j['centre'][1], 'z': 0},
                      'rotation': {'pitch': 0, 'yaw': 0, 'roll': 0},
                      'scale': {'x': j['half'] / 100, 'y': j['half'] / 100, 'z': 200}}})
    # never regenerate on level load: the instances are saved with the level, rebuilt on demand only
    for prop in ('PCGComponent.GenerationTrigger', 'pCGComponent.generationTrigger'):
        try:
            await t('EditorToolset.ObjectToolset.SetProperties',
                    {'instance': vol['actor'], 'values': json.dumps({prop: 'GenerateOnDemand'})})
            break
        except Exception:
            continue
    msgs = await t(P + 'ExecuteGraphInstance', {'pCGVolume': vol['actor']})
    return {'volume': vol['actor']['refPath'], 'messages': [str(x)[:120] for x in (msgs or [])[:3]]}

async def run():
    out, placed = {}, {}
    for a in await t('EditorToolset.LevelToolset.FindActors', {'filter': {'name': JOBS[0]['find']}}) or []:
        p = a['actorPath']
        placed.setdefault(a['label'], []).append(p['refPath'] if hasattr(p, 'get') else p)
    for j in JOBS:
        try:
            out[j['key']] = await build(j, placed)
        except Exception as e:
            out[j['key']] = {'error': str(e)[:300]}
            break  # the caller stops here: finished tiles are kept, the rest wait for the next build
    return out
'''


def points(job: dict) -> int:
    return sum(len(m["rows"]) for m in job["meshes"])


def batches(jobs: list, max_points: int = MAX_POINTS, max_tiles: int = MAX_TILES) -> list:
    """Jobs grouped in order: a group closes when it holds max_tiles tiles or the next one would pass
    max_points. A tile bigger than max_points goes alone (it can't be split: it is one graph)."""
    out, cur, n = [], [], 0
    for j in jobs:
        p = points(j)
        if cur and (len(cur) >= max_tiles or n + p > max_points):
            out.append(cur)
            cur, n = [], 0
        cur.append(j)
        n += p
    if cur:
        out.append(cur)
    return out


def script(jobs: list) -> str:
    """The editor script that builds these tiles. The jobs travel as one JSON string literal."""
    return _SCRIPT.replace("__JOBS__", repr(json.dumps(jobs, separators=(",", ":")))).replace("__CHUNK__", str(CHUNK))


def _demo() -> None:
    import asyncio

    def job(key: str, n: int) -> dict:
        return {"key": key, "find": "City", "drop_graphs": ["/G/PCG_" + key], "graph_name": "PCG_" + key,
                "graph_folder": "/G", "description": "it's a \"tile\"", "label": "City " + key, "centre": [0, 0],
                "half": 25000.0, "meshes": [{"mesh": "/K/SM_A.SM_A", "title": "SM_A", "selector": {"meshes": ["/K/SM_A.SM_A"]},
                                             "rows": [[i, 2, 3, 90.0, 1, 1, 1] for i in range(n)]}]}
    assert [len(b) for b in batches([job(str(i), 10) for i in range(20)])] == [8, 8, 4]
    assert [len(b) for b in batches([job("a", 5000), job("b", 5000), job("c", 9000), job("d", 1)])] == [1, 1, 1, 1]
    calls = []

    async def execute_tool(name, args):
        a = json.loads(args)
        calls.append(name.rsplit(".", 1)[1])
        if name.endswith("FindActors"):  # one old volume of tile 0_0, a neighbour whose label only starts the same
            return {"returnValue": [{"label": "City 0_0", "actorPath": "/L.Old"}, {"label": "City 0_01", "actorPath": "/L.Other"}]}
        if name.endswith("RemoveFromScene"):
            assert a["actor"]["refPath"] == "/L.Old", a
        if name.endswith("Exists"):
            return {"returnValue": True}
        if name.endswith("SpawnGraphInstance"):
            return {"returnValue": {"actor": {"refPath": "/L.Vol_" + a["name"]}}}
        if name.endswith("SetProperties") and "PCGComponent." in a["values"]:
            raise RuntimeError("no such property")  # the first spelling fails, the second is tried
        if name.endswith("ExecuteGraphInstance"):
            return {"returnValue": ["ok"]}
        return {"returnValue": {"refPath": "/x"}}
    ns = {"execute_tool": execute_tool}
    exec(compile(script([job("0_0", 3200), {**job("9_9", 0), "meshes": []}]), "<tile script>", "exec"), ns)
    got = asyncio.run(ns["run"]())
    assert got == {"0_0": {"volume": "/L.Vol_City 0_0", "messages": ["ok"]}, "9_9": {"removed": True}}, got
    assert calls.count("AddNode") == 1 + 3 and calls.count("ConnectNodePins") == 3, calls  # 3200 points = 3 chunks
    assert calls.count("Delete") == 2 and calls.count("SetProperties") == 2 and calls[-3] == "ExecuteGraphInstance", calls
    assert calls.count("RemoveFromScene") == 1 and calls[0] == "FindActors", "only the exact label's volume goes"
    print("pcg batch ok", len(script([job("0_0", 3200)])), "chars for 3200 points")


if __name__ == "__main__":
    _demo()
