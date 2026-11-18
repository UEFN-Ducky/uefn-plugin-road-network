"""Editor calls that heal themselves: clear what blocks UEFN, then try once more.

A UEFN popup holds the editor thread (every call behind it times out) and a dropped listener resets the
connection (WinError 10054). Both are cleared through Ducky's own tools; a call that lost its connection is
sent once more. City Builder's editor calls rebuild in place (tiles, props by label, meshes by path), so a
second send is safe. Never more than one retry, never a UEFN restart.
"""
from __future__ import annotations

LATE = ("timed out", "timeout", "busy")  # the call may still finish in UEFN: clear popups, never resend
LOST = ("10054", "10061", "connection reset", "forcibly closed", "connection refused", "not running",
        "remote end closed", "connection aborted")  # the call is gone: clear, reload the listener, resend


def kind(err) -> str:
    text = str(err).lower()
    return "lost" if any(w in text for w in LOST) else "late" if any(w in text for w in LATE) else ""


def heal(err, call_tool) -> list:
    """Press the known button on every blocking UEFN popup; a lost connection also reloads the listener.
    Returns what it did ([] when nothing could be done)."""
    did = []
    try:
        pops = (call_tool("uefn_popups", {}) or {}).get("popups") or []
    except Exception:  # noqa: BLE001 - healing must never be the new failure
        pops = []
    for p in pops:
        known = p.get("known")
        button = known.get("button") if isinstance(known, dict) else known
        if p.get("blocking") and p.get("hwnd") and isinstance(button, int):
            try:
                call_tool("uefn_popup_press", {"hwnd": p["hwnd"], "button": button})
                did.append(f"pressed {button} on '{p.get('title') or 'popup'}'")
            except Exception:  # noqa: BLE001
                pass
    if kind(err) == "lost":
        try:
            call_tool("reload_listener", {})
            did.append("reloaded the UEFN listener")
        except Exception:  # noqa: BLE001
            pass
    return did


def retry_once(fn, call_tool, healed: list | None = None):
    """fn(); on a lost connection heal and send it once more, on a late reply only clear popups and re-raise
    (the caller's own wait, e.g. listener_long, picks it up). `healed` collects what was done."""
    try:
        return fn()
    except Exception as err:  # noqa: BLE001 - re-raised unless healed
        k = kind(err)
        if not k:
            raise
        did = heal(err, call_tool)
        if healed is not None:
            healed += did
        if k != "lost" or not did:
            raise
        return fn()
