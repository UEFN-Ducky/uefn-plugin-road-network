"""Road Network: city road networks for UEFN, viewed and edited in 3D, reimported in one go.

Data (team-shared through api.data: small head docs, and a file per road actor and per city):
  project.<pid>                         {name, description}
  net.<nid>                             {name, project, actors, terrain_mode, ...}
  net.<nid>.actor.<slug>                {name, label, file, sha, points, segments, stats}
  world/<nid>/<slug>.json               file {pts: [...], segs: [...]}, a line per point / segment
  net.<nid>.scan                        {name, props, terrain}
  net.<nid>.city.<slug>                 {name, x, y, radius, style, districts, buildings, file, sha}  (generated worlds)
  world/<nid>/city/<slug>.json          file {private: {...}, lots, plan, ...}: the city's big fields
A world from before files keeps net.<nid>.actor.<slug>.p.<k> / .s.<k> chunk docs and city .private / .piece
docs until its next write.
"""

from __future__ import annotations

import functools
import json
import os
import re
import threading
import time

from . import citygen as cg
from . import editor_heal
from . import history
from . import holds as hd
from . import uefn_memory

CHUNK = 1000


STEPS = {  # what a step is called in the history, by the call that made it
    "lake_save": "Draw or change a lake", "lake_delete": "Remove a lake", "lake": "Change a lake",
    "generate": "Regenerate the world", "city_add": "Add a city", "city_delete": "Remove {city}",
    "pipeline": "Run the pipeline again", "pcg_layer_save": "Change a layer", "pcg_layer_delete": "Remove a layer",
    "pcg_preset": "Add layers", "move_point": "Move a road", "delete": "Delete the world",
}


# Calls that only push what the world already is into UEFN, or note what was pushed: no step of the world's history
NOT_STEPS = ("history", "undo", "redo", "sync", "import_all", "reimport", "heightmap", "pcg_build", "pcg_place",
             "build_pieces", "kit_materials", "hold", "release", "send_note", "note_draft")


def step_label(name: str, k: dict) -> str:
    """The words the history shows for one call: 'Move Northgate', 'Change a layer', 'Regenerate the world'."""
    name = name.replace("road_network_", "")
    city = str(k.get("city") or "").replace("-", " ").title() or "a city"
    if name == "city_save":
        what = ("Move" if k.get("x") is not None else "Turn" if k.get("turn_deg") is not None else "Reshape" if k.get("shape")
                else "Resize" if k.get("radius_m") else "Change a block of" if k.get("zones")
                else "Move the centre of" if k.get("core") else "Turn the industry of" if k.get("industry_deg") is not None
                else "Resize a district of" if any(s in str(k.get("features") or "") for s in cg.CITY_SHARES) else "Change")
        return f"{what} {city}"
    return (STEPS.get(name) or name.replace("_", " ").capitalize()).format(city=city)


class Ctx:
    """What register() hands each backend/tools_*.py: api, data, the caches and the helpers, by name. A part takes
    what it uses (take) and shares what it defines (share). A handler of a part registered later comes as a
    stand-in that calls it once it exists; check() says when one never came."""

    def __init__(self) -> None:
        self._stand_ins: dict = {}  # id -> (name, stand-in)

    def take(self, names: str) -> list:
        return [getattr(self, n) if hasattr(self, n) else self._later(n) for n in names.split()]

    def _later(self, name: str):
        def run(*a, **k):
            return getattr(self, name)(*a, **k)
        run.__name__ = name
        self._stand_ins[id(run)] = (name, run)
        return run

    def share(self, names: dict) -> None:
        for k, v in names.items():
            if k != "ctx" and id(v) not in self._stand_ins:
                setattr(self, k, v)

    def check(self) -> None:
        missing = sorted(n for n, _ in self._stand_ins.values() if not hasattr(self, n))
        if missing:
            raise NameError(f"backend/tools_*.py take names no part defines: {missing}")


def register(api) -> None:
    # everything this plugin stores goes through `data`: history.Recorder remembers what each step changed, on
    # this PC only (the plugin's own folder), so the panel can undo and redo it
    data = history.Recorder(api.data, lambda: os.path.join(str(api.local_dir() if callable(api.local_dir) else api.local_dir), "history"))
    pid_ = api.plugin_id

    def panel_rpc(name: str, fn) -> None:
        """A panel call. Whatever it changes in a world is one step of that world's history."""
        if name in NOT_STEPS:
            return api.register_panel_rpc(name, fn)

        @functools.wraps(fn)
        def run(*a, **k):
            # (a city edit is followed by the rebuild it asked for: the two are one step, history.Recorder.step)
            with data.step(step_label(name, k), opens=name in ("city_save", "city_add", "city_delete", "lake_save", "lake_delete"),
                           joins=name in ("generate", "pipeline")):
                return fn(*a, **k)
        return api.register_panel_rpc(name, run)

    def tool(**kw):
        """An agent tool, the same way: what a ducky changes can be undone like what the person changed."""
        def deco(fn):
            if fn.__name__.replace("road_network_", "") in NOT_STEPS:
                return api.tool(**kw)(fn)

            @functools.wraps(fn)
            def run(*a, **k):
                with data.step(step_label(fn.__name__, k)):
                    return fn(*a, **k)
            return api.tool(**kw)(run)
        return deco

    # ---------------------------------------------------------------- helpers
    def slug(text: str) -> str:
        return re.sub(r"[^a-z0-9-]+", "-", str(text or "").strip().lower()).strip("-")[:60]

    def safe(fn):
        def run(*a, **k):
            try:
                return fn(*a, **k)
            except PermissionError as exc:
                return {"ok": False, "error": str(exc)}
            except (KeyError, ValueError, TypeError, IndexError) as exc:
                # never a bare traceback: what failed, the plugin line it failed on, and where the names that do
                # exist are listed
                import traceback
                own = [f for f in traceback.extract_tb(exc.__traceback__) if "road-network" in f.filename
                       or "road_network" in f.filename or "backend" in f.filename]
                at = f" (at {os.path.basename(own[-1].filename)}:{own[-1].lineno} in {own[-1].name})" if own else ""
                return {"ok": False, "error": f"{fn.__name__}: {type(exc).__name__}: {exc}{at}",
                        "fix": "Check the arguments against road_network_describe(net_id): it lists the worlds, "
                               "cities, layers, roles, forms, build modes and stages that exist. Ids are "
                               "lower-case slugs; layer and options arguments are JSON strings."}
        run.__name__ = fn.__name__
        run.__doc__ = fn.__doc__
        return run

    def one_run_at_a_time(fn):
        """Full-city work runs once at a time. A caller that waited gets the answer of a run that started after
        it asked (fresh), so a pile of the same request costs at most two runs. The panel asks on every open
        and reload: these minutes-long runs side by side took Ducky to 13 GB (Nov 11 2026)."""
        lock, last, now_running = threading.RLock(), {}, {}
        what = {"generate": "rebuild of the world", "sync_status": "check against UEFN"}.get(fn.__name__, "build")

        def run(*a, **k):
            key, asked = json.dumps([a, k], sort_keys=True, default=str), time.perf_counter()
            if not lock.acquire(blocking=False):  # say so: the panel sat on 'Starting… 0 %' with no word why
                world = k.get("name") or k.get("net_id") or (a[0] if a and isinstance(a[0], str) else "")
                if world:
                    # ... and who: a teammate's ducky's rebuild is not the person's own stuck one
                    by, since = now_running.get("by") or "Someone", now_running.get("at") or time.monotonic()
                    push({"what": "progress", "id": slug(world), "step": 0, "of": 1,
                          "text": f"Waiting: {by} started a {what} {round(time.monotonic() - since)} s ago. "
                                  "Yours starts as soon as it is done."})
            else:
                lock.release()
            with lock:
                hit = last.get(key)
                if hit and hit[0] > asked:
                    return hit[1]
                started = time.perf_counter()
                try:
                    now_running.update(by=hd.label(actor()), at=time.monotonic())
                except Exception:
                    now_running.update(by="Someone", at=time.monotonic())
                out = fn(*a, **k)
                last.clear()  # ponytail: only the newest answer is kept
                last[key] = (started, out)
                return out
        run.__name__ = fn.__name__
        run.__doc__ = fn.__doc__
        return run

    _PREVIEW_CACHE: dict = {}  # the PCG preview of the world being looked at (generate reads it too)

    world_lock = threading.RLock()

    def holds_the_world(fn):
        """A rebuild holds the world for its whole run and a city edit (add, move, resize, delete) waits for it: a
        rebuild writes back the cities it started with, so an edit made during one was undone (Nov 11 2026)."""
        def run(*a, **k):
            with world_lock:
                return fn(*a, **k)
        run.__name__ = fn.__name__
        run.__doc__ = fn.__doc__
        return run

    # ---------------------------------------------------------------- holds: who is working on which city
    # (holds.py: in memory on this PC, nothing stored or synced). A person's pick in the panel, a ducky's change,
    # and what teammates hold as the panel hears it from the live group. Every change to a city asks city_guard.
    held_cities = hd.Holds()
    held_lock = threading.Lock()  # (their own: a pick is taken and kept while a rebuild holds the world)
    me_cache = {"at": -1e9, "name": "", "user": ""}

    def actor() -> dict:
        """Who is calling: the signed-in person, or their ducky (a ducky's call is the ducky's, never the person's)."""
        if time.monotonic() - me_cache["at"] > 30:
            try:
                from frontend.duckyos_account import get_status
                got = get_status()
                me_cache.update(name=str(got.get("display_name") or ""), user=str(got.get("user_id") or ""))
            except Exception:  # signed out, or run outside the app: holds still work on this PC
                pass
            me_cache["at"] = time.monotonic()
        try:
            from backend.uefn_plugins.scopes import caller
            kind, ducky = caller()
        except Exception:
            kind, ducky = "user", ""
        if kind == "agent":
            return {"kind": "ducky", "name": ducky, "for": me_cache["name"] or "You", "user": me_cache["user"]}
        return {"kind": "person", "name": me_cache["name"] or "You", "user": me_cache["user"]}

    def team_live() -> bool:
        """Is this PC live with teammates on this plugin's team copy right now?"""
        try:
            from backend.uefn_plugins import live as host_live
            return bool(host_live.is_live(pid_))
        except Exception:
            return False

    NO_PANEL = ("Your team is live on this world and City Builder is not open on this PC, so there is no telling "
                "which cities teammates are working on. Open City Builder here (or leave Live), then try again. "
                "Nothing was changed.")

    def city_guard(nid: str, cslug: str, name: str, what: str) -> dict | None:
        """Before a city changes: the refusal when someone else has it, else None. A ducky that goes ahead holds
        the city while it works (every screen shows whose ducky has it)."""
        who = actor()
        with held_lock:
            if who["kind"] == "ducky" and team_live() and not held_cities.panel_open(nid):
                return {"ok": False, "error": NO_PANEL}
            held = held_cities.blocks(nid, cslug, who)
            if held:
                return {"ok": False, "error": hd.refusal(held, name, held_cities.clock()), "held_by": hd.label(held["by"])}
            if who["kind"] == "ducky":
                held_cities.take(nid, cslug, who, what)
        if who["kind"] == "ducky":
            push({"what": "hold", "id": nid})
        return None

    def hold(net_id: str = "", city: str = "", task: str = "", minutes: float = 0.0) -> dict:
        nid, cslug = slug(net_id), slug(city)
        doc = data.get(f"net.{nid}.city.{cslug}") if nid and cslug else None
        if not doc:
            return {"ok": False, "error": f"No city {city!r} in {net_id!r}. List them with road_network_cities."}
        who = actor()
        with held_lock:
            if who["kind"] == "ducky" and team_live() and not held_cities.panel_open(nid):
                return {"ok": False, "error": NO_PANEL}
            got = held_cities.take(nid, cslug, who, task, minutes, doc.get("name") or city)
        if got.get("ok") and (who["kind"] == "ducky" or got.get("new")):
            push({"what": "hold", "id": nid})
        return got

    def release(net_id: str = "", city: str = "") -> dict:
        nid, cslug = slug(net_id), slug(city)
        who = actor()
        with held_lock:
            got = held_cities.release(nid, cslug, who)
        if got.get("released"):
            push({"what": "hold", "id": nid})
        return got

    def holds(net_id: str = "", teammates: str | None = None) -> dict:
        """Who holds which city. teammates (the panel only, never a tool): its word on what teammates hold right
        now, from the live group ([{city, who}], the whole list every time); a ducky can't say it, so a ducky
        can't clear a teammate's hold."""
        nid, who = slug(net_id), actor()
        with held_lock:
            if teammates is not None and who["kind"] == "person":
                try:
                    rows = json.loads(teammates) if str(teammates).strip() else []
                except ValueError:
                    rows = []
                held_cities.said(nid, rows if isinstance(rows, list) else [])
            return {"ok": True, "id": nid, "holds": held_cities.all(nid, who), "live": team_live(),
                    "panel_open": held_cities.panel_open(nid)}

    def record(command: str, kind: str, ident: str, before, inverse: list) -> None:
        api.changeset.record(command=command, kind=kind, ident=ident, facet="record", before=before,
                             inverse=inverse, slot=f"{pid_}://{kind}/{ident}/record")

    def push(event: dict) -> None:
        try:
            from frontend.ui_web.verse_editor.panel_events import push_agent_event
            push_agent_event({"type": "road_network_changed", **event})
        except Exception:
            pass

    def listener(command: str, params: dict | None = None, timeout: float = 120):
        # every editor call is metered: UEFN's memory before/after, refused when the last call blew up;
        # a popup or a dropped listener is cleared and a lost call sent once more (editor_heal)
        return editor_heal.retry_once(lambda: uefn_memory.metered(
            command, lambda: api.listener(command, params or {}, timeout=timeout)), api.call_tool)

    def listener_long(command: str, params: dict | None = None, timeout: float = 300) -> dict:
        """A slow editor call (a mesh build, a heavy slice). Ducky's bridge stops waiting after ~30 s but UEFN
        finishes the call anyway; then wait for UEFN to answer again (it runs one command at a time) instead of
        failing the whole job. Returns {"ok": True, "late": True} when the reply itself was lost."""
        try:
            return listener(command, params, timeout=timeout)
        except Exception as err:  # noqa: BLE001 - the bridge reports a lost reply in several wordings
            text = str(err).lower()
            if not any(w in text for w in ("timed out", "timeout", "not running")):
                raise
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(2)
            try:
                api.listener("road_network_count", {"label": ""}, timeout=20)  # answers once UEFN is free
                return {"ok": True, "late": True}
            except Exception:  # noqa: BLE001 - still busy
                continue
        raise TimeoutError(f"UEFN is still busy with {command} after {round(timeout)} s.")

    def epic(toolset: str, tool: str, arguments: dict, timeout: float = 120):
        # Same route as Ducky's own Epic callers: the pool keeps the nested arguments intact
        # (api.call_tool's dispatcher drops them for unreal__call_tool).
        from backend.mcp_plugins.client_pool import get_plugin_pool
        pool = get_plugin_pool()
        out = editor_heal.retry_once(lambda: uefn_memory.metered(f"{toolset.rsplit('.', 1)[-1]}.{tool}", lambda: pool.run_sync(
            pool.call_tool("unreal__call_tool", {"toolset_name": toolset, "tool_name": tool, "arguments": arguments}),
            timeout=timeout)), api.call_tool)
        if not isinstance(out, (str, dict)):
            out = getattr(out, "text", None) or str(out)
        if isinstance(out, str):
            try:
                out = json.loads(out)
            except ValueError:
                raise ValueError(out[:300]) from None
        if isinstance(out, dict) and out.get("ok") is False:
            raise ValueError(str(out.get("error") or out)[:300])
        return out

    # ---------------------------------------------------------------- the tools, panel RPCs and nodes
    # (imported here, not at the top: they import this module's constants); registered in this order
    from . import (tools_edits, tools_generate, tools_ground, tools_mcp, tools_notes, tools_panel, tools_pcg,
                   tools_pcg_mcp, tools_pieces, tools_pipeline, tools_worlds)
    ctx = Ctx()
    ctx.share(locals())
    for part in (tools_worlds, tools_ground, tools_pieces, tools_generate, tools_pcg, tools_edits, tools_pipeline,
                 tools_mcp, tools_notes, tools_pcg_mcp, tools_panel):
        part.register(ctx)
    ctx.check()
