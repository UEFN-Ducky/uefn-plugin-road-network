"""Who is working on which city: holds.

A person who picks a city in the panel has it until they let go; a ducky that changes a city has it for two
minutes; a teammate's hold (their pick, or their ducky's change) is what this PC's panel hears from the live group
and tells this module. While a city is held by someone else, nothing of it changes from here: not from the panel
and not from a ducky's tool call. A person's own ducky may change the city that person has open (it works for
them), and holds it while it does, so the person does not change it under the ducky's hands.

Everything is kept in memory on this PC: nothing is stored and nothing is synced, so holds cost a team nothing
and never show up as changes to send. Teammates hear of this PC's holds through the panel's live state.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

PERSON_S = 60.0   # a person's hold ends this long after the panel last said they still have the city
DUCKY_S = 120.0   # a ducky's change holds its city this long (each change starts it again)
PEER_S = 25.0     # a teammate's hold stands this long after the panel last heard it
PANEL_S = 40.0    # the panel says what teammates hold every ~10 s: no word for this long and it is closed


def label(actor: Mapping[str, Any] | None) -> str:
    """Who it is, in words: "Ilya", "Builder (Ilya's ducky)", "A ducky"."""
    a = actor or {}
    name, owner = str(a.get("name") or ""), str(a.get("for") or "")
    if a.get("kind") == "ducky":
        if name and owner == "You":  # signed out: there is no teammate to read it
            return f"{name} (your ducky)"
        if name and owner:
            return f"{name} ({owner}'s ducky)"
        return f"{name} (ducky)" if name else (f"{owner}'s ducky" if owner else "A ducky")
    return name or "Someone"


def same(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """The same actor: the same person, or the same ducky of the same person."""
    a, b = a or {}, b or {}
    key = lambda x: (x.get("kind"), x.get("user") or x.get("for") or x.get("name"), x.get("name") if x.get("kind") == "ducky" else "")
    return key(a) == key(b)


def _owner(actor: Mapping[str, Any]) -> str:
    """The person an actor is or works for."""
    return str(actor.get("user") or (actor.get("for") if actor.get("kind") == "ducky" else actor.get("name")) or "")


class Holds:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.rows: dict = {}   # (world, city) -> {by, task, since, touched, lease}: this PC's person and duckies
        self.peers: dict = {}  # world -> {city: {who, at}}: what teammates hold, as the panel last heard it
        self.panel: dict = {}  # world -> when the panel last told

    def held(self, net: str, city: str) -> dict | None:
        """The hold that stands on a city now, or None."""
        now = self.clock()
        row = self.rows.get((net, city))
        if row and now - row["touched"] < row["lease"]:
            return row
        self.rows.pop((net, city), None)
        peer = (self.peers.get(net) or {}).get(city)
        if peer and now - peer["at"] < PEER_S:
            return {"by": {"kind": "teammate", "name": peer["who"]}, "task": "", "since": peer["at"], "touched": peer["at"],
                    "lease": PEER_S}
        return None

    def blocks(self, net: str, city: str, me: Mapping[str, Any]) -> dict | None:
        """The hold that keeps `me` from changing this city, or None when it is free, mine, or my own person's."""
        h = self.held(net, city)
        if not h or same(h["by"], me):
            return None
        if me.get("kind") == "ducky" and h["by"].get("kind") == "person" and _owner(h["by"]) == _owner(me):
            return None  # its own person has the city open: their ducky works on it for them
        return h

    def take(self, net: str, city: str, me: Mapping[str, Any], task: str = "", minutes: float = 0.0, name: str = "") -> dict:
        """`me` takes the city, or keeps it a while longer. Refused while someone else has it."""
        h = self.blocks(net, city, me)
        if h:
            return {"ok": False, "error": refusal(h, name or city, self.clock()), "held_by": label(h["by"])}
        now, had = self.clock(), self.rows.get((net, city))
        mine = had if had and same(had["by"], me) else None
        lease = float(minutes) * 60 if minutes else (DUCKY_S if me.get("kind") == "ducky" else PERSON_S)
        self.rows[(net, city)] = {"by": dict(me), "task": str(task or (mine or {}).get("task") or "")[:80],
                                  "since": (mine or {}).get("since", now), "touched": now, "lease": lease}
        return {"ok": True, "city": city, "who": label(me), "for_s": round(lease), "new": mine is None}

    def release(self, net: str, city: str, me: Mapping[str, Any]) -> dict:
        row = self.rows.get((net, city))
        if row and same(row["by"], me):
            del self.rows[(net, city)]
            return {"ok": True, "released": True}
        return {"ok": True, "released": False}

    def said(self, net: str, rows: list) -> None:
        """The panel's word on what teammates hold right now ([{city, who}]): the whole list, every time."""
        now = self.clock()
        self.panel[net] = now
        self.peers[net] = {str(r["city"]): {"who": str(r.get("who") or "A teammate"), "at": now}
                           for r in rows if isinstance(r, dict) and r.get("city")}

    def panel_open(self, net: str) -> bool:
        return self.clock() - self.panel.get(net, -1e18) < PANEL_S

    def all(self, net: str, me: Mapping[str, Any]) -> list:
        """Every hold on the world's cities now: [{city, who, kind, mine, task, left_s}]."""
        cities = {c for n, c in self.rows if n == net} | set(self.peers.get(net) or {})
        out = []
        for city in sorted(cities):
            h = self.held(net, city)
            if h:
                out.append({"city": city, "who": label(h["by"]), "kind": h["by"].get("kind"), "mine": same(h["by"], me),
                            "task": h["task"], "left_s": max(0, round(h["lease"] - (self.clock() - h["touched"])))})
        return out


def refusal(h: Mapping[str, Any], name: str, now: float) -> str:
    left = max(1, round(h["lease"] - (now - h["touched"])))
    doing = f" ({h['task']})" if h.get("task") else ""
    return (f"{name} is {label(h['by'])}'s right now{doing}: nothing of it was changed. It frees when they let go, "
            f"or in about {left} s if they stop working on it. Work on another city, or ask them.")


def _demo() -> None:
    t = [1000.0]
    h = Holds(lambda: t[0])
    tas = {"kind": "person", "name": "Tas", "user": "u1"}
    mo = {"kind": "person", "name": "Mo", "user": "u2"}
    tas_ducky = {"kind": "ducky", "name": "Builder", "for": "Tas", "user": "u1"}
    mo_ducky = {"kind": "ducky", "name": "Planner", "for": "Mo", "user": "u2"}
    assert label(tas_ducky) == "Builder (Tas's ducky)" and same(tas, dict(tas)) and not same(tas, tas_ducky)
    # a person picks a city: theirs; a teammate and a teammate's ducky are refused, by name
    assert h.take("v", "northgate", tas, name="Northgate")["ok"]
    no = h.take("v", "northgate", mo, name="Northgate")
    assert not no["ok"] and "Northgate is Tas's right now" in no["error"] and no["held_by"] == "Tas"
    assert h.blocks("v", "northgate", mo_ducky) and not h.blocks("v", "riverton", mo_ducky)
    # their own ducky may change it, and then holds it: the person waits while it works
    assert not h.blocks("v", "northgate", tas_ducky)
    got = h.take("v", "northgate", tas_ducky, "making block 3 a park")
    assert got["ok"] and got["for_s"] == 120 and h.blocks("v", "northgate", tas)["task"] == "making block 3 a park"
    assert "Builder (Tas's ducky)'s right now (making block 3 a park)" in h.take("v", "northgate", tas, name="Northgate")["error"]
    t[0] += 121  # two minutes with no change from the ducky: free again
    assert h.take("v", "northgate", tas)["ok"] and h.all("v", tas)[0]["mine"]
    t[0] += 61  # a person's hold ends a minute after the panel last said so
    assert not h.held("v", "northgate") and not h.all("v", tas)
    # a hold let go is free at once, and only its holder can let it go
    h.take("v", "ashford", mo)
    assert not h.release("v", "ashford", tas)["released"] and h.release("v", "ashford", mo)["released"] and not h.held("v", "ashford")
    # what teammates hold reaches this PC through the panel: a ducky here is refused, until the panel stops saying so
    assert not h.panel_open("v")
    h.said("v", [{"city": "riverton", "who": "Ana"}, {"city": "ashford", "who": "Scout (Ana's ducky)"}])
    assert h.panel_open("v") and "Riverton is Ana's right now" in h.take("v", "riverton", tas_ducky, name="Riverton")["error"]
    assert h.blocks("v", "ashford", tas) and [r["who"] for r in h.all("v", tas)] == ["Scout (Ana's ducky)", "Ana"]
    h.said("v", [])
    assert h.take("v", "riverton", tas_ducky)["ok"]
    t[0] += 41
    assert not h.panel_open("v")
    print("holds ok")


if __name__ == "__main__":
    _demo()
