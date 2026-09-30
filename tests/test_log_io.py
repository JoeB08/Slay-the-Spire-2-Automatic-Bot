"""Reading compressed logs has to be indistinguishable from reading plain ones.

`scripts/compress_logs.py` packed 396 MB of archived decision logs down to
13 MB, and those archives are not just history: `decision_snapshot.py` replays
every payload in them to prove a refactor changed no behaviour, and it numbers
those payloads **by position**. So the file list has to come back in the same
order whether a set is packed or not, or a baseline recorded last week compares
run 400 against run 12 and reports nonsense.
"""
import gzip
import importlib.util
import json
from pathlib import Path

from bot import log_io

ROOT = Path(__file__).resolve().parent.parent


def _write(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _pack(path: Path) -> Path:
    packed = Path(str(path) + ".gz")
    with open(path, "rb") as src, gzip.open(packed, "wb") as dst:
        dst.write(src.read())
    path.unlink()
    return packed


def test_a_packed_log_reads_back_exactly(tmp_path):
    rows = [{"event": "decision", "n": i} for i in range(50)]
    plain = tmp_path / "run_1.jsonl"
    _write(plain, rows)
    expected = list(log_io.rows(plain))

    assert list(log_io.rows(_pack(plain))) == expected == rows


def test_a_byte_order_mark_is_stripped_either_way(tmp_path):
    """A log re-saved by Windows PowerShell gains a BOM, which lands on the
    first row's opening brace and loses that one decision silently."""
    for name in ("run_bom.jsonl", "run_bom_packed.jsonl"):
        path = tmp_path / name
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"n": 1}).encode() + b"\n")
        if "packed" in name:
            path = _pack(path)
        assert list(log_io.rows(path)) == [{"n": 1}]


def test_a_truncated_last_line_is_skipped_not_fatal(tmp_path):
    """Most sets end by killing the bot process mid-write."""
    path = tmp_path / "run_cut.jsonl"
    path.write_text('{"n": 1}\n{"n": 2}\n{"n": 3', encoding="utf-8")
    assert list(log_io.rows(_pack(path))) == [{"n": 1}, {"n": 2}]


def test_the_file_list_is_ordered_as_if_nothing_were_packed(tmp_path):
    names = ["run_20260101_000000.jsonl", "run_20260202_000000.jsonl",
             "run_20260303_000000.jsonl"]
    for n in names:
        _write(tmp_path / n, [{"n": n}])
    before = log_io.log_glob(tmp_path / "run_*.jsonl")

    _pack(tmp_path / names[1])  # pack the middle one only
    after = log_io.log_glob(tmp_path / "run_*.jsonl")

    assert [log_io.display_name(p) for p in after] == names
    assert [log_io.display_name(p) for p in before] == names


def test_find_log_resolves_a_name_whose_file_has_since_been_packed(tmp_path):
    """`runs.jsonl` records the decision log each run wrote; the log may have
    been compressed since, and the story rebuilder still has to find it."""
    plain = tmp_path / "run_9.jsonl"
    _write(plain, [{"n": 1}])
    assert log_io.find_log(plain) == plain

    packed = _pack(plain)
    assert log_io.find_log(plain) == packed
    assert log_io.find_log(tmp_path / "run_nope.jsonl") is None


def test_display_name_hides_the_suffix(tmp_path):
    assert log_io.display_name(tmp_path / "run_1.jsonl.gz") == "run_1.jsonl"
    assert log_io.display_name(tmp_path / "run_1.jsonl") == "run_1.jsonl"


def test_the_compressor_keeps_the_original_until_it_has_verified_the_copy(tmp_path):
    """It deletes logs, so this is the test that matters: same bytes, or no
    delete. The archive is the only regression test this project has."""
    spec = importlib.util.spec_from_file_location(
        "compress_logs", ROOT / "scripts" / "compress_logs.py"
    )
    compress_logs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compress_logs)

    rows = [{"event": "decision", "n": i, "raw": {"state_type": "monster"}} for i in range(200)]
    path = tmp_path / "run_1.jsonl"
    _write(path, rows)
    before = path.read_bytes()

    saved = compress_logs.compress(path, dry_run=False)

    packed = tmp_path / "run_1.jsonl.gz"
    assert not path.exists() and packed.exists()
    assert saved > 0
    assert gzip.open(packed, "rb").read() == before
    assert list(log_io.rows(packed)) == rows

    # and back again
    compress_logs.decompress(packed, dry_run=False)
    assert path.read_bytes() == before and not packed.exists()


def test_an_empty_log_is_left_alone(tmp_path):
    """Scripts skip the logs of processes that never acted with `st_size > 0`,
    and gzip would give an empty file a header and a non-zero size."""
    spec = importlib.util.spec_from_file_location(
        "compress_logs", ROOT / "scripts" / "compress_logs.py"
    )
    compress_logs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compress_logs)

    empty = tmp_path / "run_empty.jsonl"
    empty.touch()

    assert compress_logs.compress(empty, dry_run=False) == 0
    assert empty.exists() and empty.stat().st_size == 0
    assert not (tmp_path / "run_empty.jsonl.gz").exists()
