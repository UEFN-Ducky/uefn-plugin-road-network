"""A failed PCG tile gets one more try, then the build goes on: py -3 tests/test_pcg_retry.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
import pcg_batch  # noqa: E402


def tile(key):
    return {"key": key, "meshes": [{"rows": [0] * 10}]}


def build(fails: dict):
    """Run the build loop like pcg_build does; fails = {key: times it fails}."""
    jobs = [tile(k) for k in ("1_1", "1_2", "2_1")]
    failed, sent, healed = {}, [], []
    for batch in pcg_batch.with_retry(jobs, failed, lambda: healed.append(True)):
        for j in batch:
            sent.append(j["key"])
            if fails.get(j["key"], 0):
                fails[j["key"]] -= 1
                failed[j["key"]] = "boom"
    return failed, sent, healed


def test_fails_once_then_works():
    failed, sent, healed = build({"1_2": 1})
    assert failed == {} and sent == ["1_1", "1_2", "2_1", "1_2"] and healed == [True], (failed, sent)


def test_fails_twice_is_reported_and_rest_built():
    failed, sent, _ = build({"1_2": 5})
    assert failed == {"1_2": "boom"} and sent.count("1_2") == 2 and "2_1" in sent


def test_clean_build_no_retry():
    failed, sent, healed = build({})
    assert failed == {} and len(sent) == 3 and healed == []


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f()
    print("ok")
