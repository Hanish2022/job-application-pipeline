from datetime import datetime

import pytest

from app import config
from app.cli import main
from app.schedule import is_due, last_scheduled, parse_hhmm
from app.store import Store


def local(y, mo, d, h=0, mi=0):
    """A timezone-aware *local* time, so the tests behave the same in any timezone."""
    return datetime(y, mo, d, h, mi).astimezone()


def iso(dt):
    return dt.isoformat(timespec="seconds")


@pytest.mark.parametrize("value", ["08:30", "00:00", "23:59"])
def test_parse_hhmm_ok(value):
    assert parse_hhmm(value) == tuple(int(x) for x in value.split(":"))


@pytest.mark.parametrize("value", ["25:00", "12:60", "8", "noon", "", "12:3x"])
def test_parse_hhmm_rejects(value):
    with pytest.raises(ValueError):
        parse_hhmm(value)


def test_last_scheduled_rolls_back_a_day_before_the_slot():
    assert last_scheduled(local(2026, 10, 4, 9, 0), "08:30") == local(2026, 10, 4, 8, 30)
    assert last_scheduled(local(2026, 10, 4, 7, 0), "08:30") == local(2026, 10, 3, 8, 30)
    assert last_scheduled(local(2026, 10, 4, 8, 30), "08:30") == local(2026, 10, 4, 8, 30)      # boundary: exactly on the slot
    assert last_scheduled(local(2026, 10, 4, 8, 29), "08:30") == local(2026, 10, 3, 8, 30)


def test_due_when_never_run():
    assert is_due(None, local(2026, 10, 4, 9), "08:30")[0] is True


def test_due_after_slot_until_todays_run_happens():
    now = local(2026, 10, 4, 9, 0)
    assert is_due(iso(local(2026, 10, 3, 8, 40)), now)[0] is True        # ran yesterday only
    assert is_due(iso(local(2026, 10, 4, 8, 35)), now)[0] is False       # already ran today
    assert is_due(iso(local(2026, 10, 4, 8, 29)), now)[0] is True        # ran just before the slot: today's run is still owed
    due, reason = is_due(iso(local(2026, 10, 4, 8, 35)), now)
    assert "already ran" in reason


def test_not_due_before_the_slot_if_yesterdays_run_happened():
    now = local(2026, 10, 4, 7, 0)
    assert is_due(iso(local(2026, 10, 3, 9, 0)), now)[0] is False
    assert is_due(iso(local(2026, 10, 2, 9, 0)), now)[0] is True         # missed a whole day => catch up


def test_catches_up_after_laptop_was_off_all_morning():
    # Slot 08:30 was missed; at the 11:00 hourly tick it is due, and after that run it is not.
    assert is_due(iso(local(2026, 10, 3, 8, 31)), local(2026, 10, 4, 11, 0))[0] is True
    assert is_due(iso(local(2026, 10, 4, 11, 1)), local(2026, 10, 4, 12, 0))[0] is False


def test_utc_timestamps_from_the_database_are_handled():
    utc = "2026-10-03T03:30:00+00:00"              # whatever that is locally, it must compare correctly
    assert is_due(utc, local(2026, 10, 10, 9, 0))[0] is True


# ------------------------------------------------------------------ `app.cli due`
@pytest.fixture()
def cli_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "cli.db")
    return Store(tmp_path / "cli.db")


def test_cli_due_exit_codes(cli_db, capsys):
    assert main(["due"]) == 0 and "due: no successful run yet" in capsys.readouterr().out
    rid = cli_db.start_run()
    cli_db.finish_run(rid, "failed", {})                       # a failed run does not count
    assert main(["due", "--at", "00:00"]) == 0
    rid = cli_db.start_run()
    cli_db.finish_run(rid, "ok", {})
    assert main(["due", "--at", "00:00"]) == 1 and "not due" in capsys.readouterr().out
    assert main(["due", "--at", "99:99"]) == 2
