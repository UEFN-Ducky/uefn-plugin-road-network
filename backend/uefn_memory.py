"""UEFN's memory, measured around every editor call City Builder makes (Windows only).

Why: UEFN frees a window's GPU swap chain (and what hangs off it) only when the frame ends, and one
editor call is one frame. Too much work in one call (16 mesh builds, 40 junction meshes, a big save)
stacks windows until UEFN runs out of memory: 122 GB and a D3D12 descriptor crash. The meter records
every call's growth so the step that leaks has a name, and stops the run before UEFN goes down.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

EXE = "unrealeditorfortnite"
GROW_GB = 6.0  # one call that adds this much trips the guard: the next call is refused until it settles
SETTLE_GB = 3.0  # tripped guard clears once UEFN is back within this of where the big call started
LIMIT_SHARE = 0.8  # never start a call with UEFN above this share of the PC's RAM (past it Windows pages)

LOG: list = []  # last calls: {cmd, before_gb, after_gb, grew_gb, secs, t}
_state = {"pid": 0, "trip": None}


class _Counters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _private_gb(pid: int):
    k32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x1000 | 0x0010, False, pid)  # QUERY_LIMITED_INFORMATION | VM_READ
    if not h:
        return None, ""
    try:
        name = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(520)
        k32.QueryFullProcessImageNameW(h, 0, name, ctypes.byref(size))
        c = _Counters()
        c.cb = ctypes.sizeof(c)
        if not psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
            return None, name.value
        return c.PrivateUsage / 2 ** 30, name.value
    finally:
        k32.CloseHandle(h)


def uefn_gb():
    """UEFN's private memory in GB, or None when it isn't running (or this isn't Windows)."""
    try:
        if _state["pid"]:
            gb, name = _private_gb(_state["pid"])
            if gb is not None and EXE in name.lower():
                return round(gb, 1)
        psapi = ctypes.WinDLL("psapi")
        pids = (wintypes.DWORD * 4096)()
        got = wintypes.DWORD()
        psapi.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids), ctypes.byref(got))
        for pid in pids[:got.value // ctypes.sizeof(wintypes.DWORD)]:
            gb, name = _private_gb(pid)
            if gb is not None and EXE in name.lower().rsplit("\\", 1)[-1]:
                _state["pid"] = pid
                return round(gb, 1)
    except (OSError, AttributeError):
        pass
    _state["pid"] = 0
    return None


def ram_gb():
    try:
        m = _MemStatus()
        m.dwLength = ctypes.sizeof(m)
        ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys / 2 ** 30
    except (OSError, AttributeError):
        return None


def check(before, ram) -> str:
    """'' when a call may start, else why not (pure: the guard's rules, testable without UEFN)."""
    trip = _state["trip"]
    if trip and before is not None and before <= trip["before"] + SETTLE_GB:
        _state["trip"] = trip = None
    if trip:
        return (f"Stopped: '{trip['cmd']}' grew UEFN by {trip['grew']} GB in one call (now {before} GB). "
                f"Let UEFN settle (or save and restart it) before more editor work.")
    if before is not None and ram and before > ram * LIMIT_SHARE:
        return (f"Stopped: UEFN is using {before} GB of {round(ram)} GB RAM. Save the level and restart UEFN "
                f"before more editor work, or Windows starts paging and UEFN freezes.")
    return ""


def metered(cmd: str, call):
    """Run one editor call between two memory readings; refuse it when the guard says UEFN is in trouble."""
    before = uefn_gb()
    why = check(before, ram_gb())
    if why:
        raise RuntimeError(why)
    t = time.time()
    try:
        return call()
    finally:
        after = uefn_gb()
        grew = round(after - before, 1) if before is not None and after is not None else None
        LOG.append({"cmd": cmd, "before_gb": before, "after_gb": after, "grew_gb": grew,
                    "secs": round(time.time() - t, 1), "t": round(t)})
        del LOG[:-400]
        if grew is not None and grew >= GROW_GB:
            _state["trip"] = {"cmd": cmd, "grew": grew, "before": before}


def report(last: int = 60) -> dict:
    rows = LOG[-last:]
    worst = sorted((r for r in LOG if r["grew_gb"] is not None), key=lambda r: -r["grew_gb"])[:10]
    return {"uefn_gb": uefn_gb(), "ram_gb": round(ram_gb() or 0), "tripped": _state["trip"],
            "worst": worst, "last": rows}


def _demo() -> None:
    _state["trip"] = None
    assert check(10.0, 64.0) == ""
    assert "of 64 GB" in check(60.0, 64.0), "above 80 % of RAM: refuse"
    LOG.clear()
    _state["pid"] = -1  # no such process: readings are None, nothing trips
    metered("road_network_count", lambda: 1)
    assert LOG[-1]["cmd"] == "road_network_count"
    _state["trip"] = {"cmd": "road_network_street_kit", "grew": 9.0, "before": 20.0}
    assert "street_kit" in check(29.0, 64.0), "a big jump blocks the next call"
    assert check(22.0, 64.0) == "" and _state["trip"] is None, "settled back: the guard clears"
    print("uefn memory ok", uefn_gb(), "GB now")


if __name__ == "__main__":
    _demo()
