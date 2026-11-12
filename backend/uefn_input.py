"""Drive UEFN's Landscape Splines tool with the mouse and keyboard (Windows only).

Landscape spline text can only be pasted by the Splines tool itself (Ctrl+V over the
viewport), so a reimport has to click it like a person would: bring UEFN forward,
open Landscape > Manage, make sure the Splines tool is on, hover the viewport and
press Ctrl+V. Clicks hover first: Slate ignores clicks that teleport in.

Positions are measured from the UEFN window's top-left corner with the Landscape panel
docked on the left. The Splines button sits at the end of the first tool row, or wraps
to a second row when the panel is narrow: both spots are tried, and the button counts
as on only when it is lit.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

MANAGE_TAB = (152, 130)
# Manage tools sit in a grid of 50 px buttons from (38, 180), rows 43 px apart, in this order:
# New, Import, Select, Add, Delete, Move, Resize, Splines. How many fit in a row depends on the
# panel's width, so the Splines button moves (end of row 1 on a wide panel, row 4 on a narrow one).
TOOL_X0, TOOL_Y0, TOOL_DX, TOOL_DY, SPLINES_INDEX = 38, 180, 50, 43, 7
VIEWPORT_PAST_PANEL = (250, 450)  # hover this far right of the panel's edge, this far down, to paste
TITLE = "Unreal Editor for Fortnite"

_MOUSE_DOWN, _MOUSE_UP = 0x0002, 0x0004
_KEY_UP = 0x0002
_VK_CONTROL, _VK_V, _VK_ESCAPE = 0x11, 0x56, 0x1B


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    return user32


def find_window() -> tuple:
    """(hwnd, (left, top, right, bottom)) of the UEFN editor window, or (0, None)."""
    user32 = _user32()
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.startswith(TITLE):
                found.append(hwnd)
        return True

    user32.EnumWindows(proto(each), 0)
    if not found:
        return 0, None
    return found[0], _rect(user32, found[0])


def _rect(user32, hwnd) -> tuple:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    # a maximized window's rect starts 8 px above the screen (its invisible frame); a normal window has no
    # such frame at the top, and every offset here (tab row, tools, viewport) is measured from that top
    top = rect.top + (8 if user32.IsZoomed(hwnd) else 0)
    return rect.left, top, rect.right, rect.bottom


def _click(user32, x: int, y: int) -> None:
    user32.SetCursorPos(x - 6, y)
    time.sleep(0.12)
    user32.SetCursorPos(x, y)
    time.sleep(0.2)
    user32.mouse_event(_MOUSE_DOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(_MOUSE_UP, 0, 0, 0, 0)


def _key(user32, *keys: int) -> None:
    for k in keys:
        user32.keybd_event(k, 0, 0, 0)
    for k in reversed(keys):
        user32.keybd_event(k, 0, _KEY_UP, 0)


def _lit(user32, x: int, y: int) -> bool:
    """True when the tool button at screen x, y has the blue accent fill of the active tool."""
    gdi = ctypes.WinDLL("gdi32")
    gdi.GetPixel.restype = wintypes.DWORD
    gdi.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    dc = user32.GetDC(0)
    try:
        for dx, dy in ((-14, -8), (14, -8), (-14, 8), (14, 8)):
            c = gdi.GetPixel(dc, x + dx, y + dy)
            r, g, b = c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF
            if b > 230 and r < 60 and 100 < g < 170:  # active tool fill is (14, 134, 255); viewport sky is not
                return True
        return False
    finally:
        user32.ReleaseDC(0, dc)


TAB_ROW_Y = 122  # a pixel row inside the Manage / Sculpt / Paint tab bar


def _rgb(user32, x: int, y: int) -> tuple:
    gdi = ctypes.WinDLL("gdi32")
    gdi.GetPixel.restype = wintypes.DWORD
    gdi.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    dc = user32.GetDC(0)
    try:
        c = gdi.GetPixel(dc, x, y)
    finally:
        user32.ReleaseDC(0, dc)
    return c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF


def landscape_tabs(user32, left: int, top: int):
    """(strip_start, strip_end, selected tab index 0=Manage 1=Sculpt 2=Paint) read from the tab bar's
    pixels (strip ~15 grey, selected tab ~56 grey, panel ~36 grey), or None when it isn't found."""
    xs = []
    for x in range(30, 480, 2):
        r, g, b = _rgb(user32, left + x, top + TAB_ROW_Y)
        # selected tab is a lighter grey (seen 56 and 87, depending on hover/focus); panel is 36
        kind = "strip" if max(r, g, b) <= 24 else ("sel" if 46 <= r <= 130 and abs(r - g) < 6 and abs(r - b) < 6 else "")
        xs.append((x, kind))
    start = next((n for n, (_, k) in enumerate(xs) if k), None)
    if start is None:
        return None
    end, gap = start, 0
    for n in range(start, len(xs)):  # follow the one continuous strip; stop after a 6 px break
        if xs[n][1]:
            end, gap = n, 0
        else:
            gap += 1
            if gap > 3:
                break
    x0, x1 = xs[start][0], xs[end][0]
    # the selected tab is the longest unbroken run of its grey: the other tabs' anti-aliased label pixels
    # fall in the same grey range and would pull the centre over to Sculpt
    runs, run = [], []
    for x, k in xs[start:end + 1]:
        if k == "sel":
            run.append(x)
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    sel = max(runs, key=len, default=[])
    if len(sel) < 5 or x1 - x0 < 120:
        return None
    centre = (sel[0] + sel[-1]) / 2
    return x0, x1, max(0, min(2, int((centre - x0) / ((x1 - x0) / 3))))


def panel_width(tabs) -> int:
    """The Landscape panel's width: its tab strip is centred in it, so the panel ends at strip start + strip end."""
    return tabs[0] + tabs[1]


def splines_spot(tabs) -> tuple:
    """Window-relative centre of the Splines button for a panel whose tab strip runs tabs[0]..tabs[1]."""
    cols = max(1, int((panel_width(tabs) - 13) // TOOL_DX))
    return TOOL_X0 + TOOL_DX * (SPLINES_INDEX % cols), TOOL_Y0 + TOOL_DY * (SPLINES_INDEX // cols)


def splines_active(user32, left: int, top: int, spot: tuple) -> bool:
    return _lit(user32, left + spot[0], top + spot[1])


def paste_in_splines_tool(toggle_splines: bool = True) -> dict:
    """Open Manage > Splines (only clicked when it isn't lit) and press Ctrl+V over the viewport."""
    try:
        user32 = _user32()
    except OSError:
        return {"ok": False, "error": "Mouse automation needs Windows."}
    # Real pixels on every monitor: without this a non-DPI-aware host gets scaled
    # coordinates on a scaled monitor and every click and pixel read lands elsewhere.
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    old_ctx = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    try:
        return _paste(user32, toggle_splines)
    finally:
        if old_ctx:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old_ctx))


def _paste(user32, toggle_splines: bool) -> dict:
    hwnd, _ = find_window()
    if not hwnd:
        return {"ok": False, "error": "UEFN's editor window was not found. Open the island in UEFN."}
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # only un-minimize: SW_RESTORE on a maximized window reflows the panels
    user32.GetForegroundWindow.restype = wintypes.HWND
    for _ in range(3):
        # Windows refuses SetForegroundWindow from a background process unless a key was just pressed
        user32.keybd_event(0x12, 0, 0, 0)  # Alt down
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(0x12, 0, _KEY_UP, 0)  # Alt up
        time.sleep(0.6)
        if user32.GetForegroundWindow() == hwnd:
            break
    else:
        return {"ok": False, "error": "UEFN couldn't be brought to the front; nothing was pasted."}
    left, top = _rect(user32, hwnd)[:2]
    _key(user32, _VK_ESCAPE)
    time.sleep(0.3)
    tabs = landscape_tabs(user32, left, top)
    if tabs is None:
        return {"ok": False, "error": "The Landscape panel's Manage/Sculpt/Paint tabs weren't found."}
    if tabs[2] != 0:  # Sculpt or Paint is open: Ctrl+V there pastes heights or paint, never splines
        manage_x = int(tabs[0] + (tabs[1] - tabs[0]) / 6)
        for _ in range(2):
            _click(user32, left + manage_x, top + MANAGE_TAB[1])
            time.sleep(1.0)
        tabs = landscape_tabs(user32, left, top)
        if tabs is None or tabs[2] != 0:
            return {"ok": False, "error": "Couldn't switch the Landscape panel to Manage; nothing was pasted."}
    spot = splines_spot(tabs)
    if not splines_active(user32, left, top, spot) and toggle_splines:
        _click(user32, left + spot[0], top + spot[1])
        time.sleep(1.0)
    if not splines_active(user32, left, top, spot):
        return {"ok": False, "error": f"The Splines tool didn't turn on (looked for it at {spot} in the window)."}
    tabs = landscape_tabs(user32, left, top)
    if tabs is None or tabs[2] != 0:  # last look before Ctrl+V: only ever paste on the Manage tab
        return {"ok": False, "error": "The Landscape panel isn't on Manage; nothing was pasted."}
    vx, vy = panel_width(tabs) + VIEWPORT_PAST_PANEL[0], VIEWPORT_PAST_PANEL[1]  # the viewport starts where the panel ends
    user32.SetCursorPos(left + vx - 10, top + vy)
    time.sleep(0.15)
    user32.SetCursorPos(left + vx, top + vy)
    time.sleep(0.3)
    _key(user32, _VK_CONTROL, _VK_V)
    return {"ok": True}


if __name__ == "__main__":
    # a narrow panel (2 buttons a row), the original wide one (all 8 in row 1) and the wrapped one (7 a row)
    assert splines_spot((10, 150, 0)) == (88, 309), splines_spot((10, 150, 0))
    assert splines_spot((10, 460, 0)) == (388, 180)
    assert splines_spot((10, 380, 0)) == (38, 223)
    assert splines_spot((155, 345, 0)) == (388, 180), "a 500 px panel with its tabs centred: all 8 in row 1"
    print("uefn_input ok")
