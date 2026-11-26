"""On a Mac UEFN runs on the paired PC: no local reading, so the guard never blocks and editor calls still run."""
import ctypes
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
import uefn_memory as um  # noqa: E402


def test_no_win32_means_no_reading_and_no_block(monkeypatch):
    monkeypatch.delattr(ctypes, "WinDLL", raising=False)  # what macOS's ctypes looks like
    monkeypatch.setattr(sys, "platform", "darwin")
    um._state.update(pid=0, trip=None)
    assert um.uefn_gb() is None and um.ram_gb() is None
    assert um.metered("road_network_count", lambda: 7) == 7
    assert um.LOG[-1]["grew_gb"] is None
    assert um.report()["uefn_gb"] is None
