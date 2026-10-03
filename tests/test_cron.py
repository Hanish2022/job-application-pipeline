"""Cron helper scripts, exercised with a fake `crontab` binary so the real crontab is never touched."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "scripts" / "install_cron.sh"
CRON = ROOT / "scripts" / "cron_crawl.sh"


@pytest.fixture()
def fake_crontab(tmp_path):
    """A `crontab` shim that stores its table in a file."""
    table = tmp_path / "table"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "crontab"
    shim.write_text(f'''#!/usr/bin/env bash
T="{table}"
if [[ "$1" == "-l" ]]; then [[ -f "$T" ]] && cat "$T" || {{ echo "no crontab for user" >&2; exit 1; }}
elif [[ "$1" == "-r" ]]; then rm -f "$T"
else cat > "$T"; fi
''')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    return table, env


def sh(args, env, **kw):
    return subprocess.run(args, env=env, capture_output=True, text=True, **kw)


def test_print_does_not_modify_crontab(fake_crontab):
    table, env = fake_crontab
    r = sh([str(INSTALL), "--print", "--time", "06:45"], env)
    assert r.returncode == 0 and r.stdout.startswith("45 6 * * *") and "cron_crawl.sh" in r.stdout
    assert not table.exists()


def test_install_is_idempotent_preserves_other_entries_and_remove_cleans(fake_crontab):
    table, env = fake_crontab
    table.write_text("0 1 * * * /usr/bin/other-job\n")
    assert sh([str(INSTALL), "--install", "--time", "09:05"], env).returncode == 0
    assert sh([str(INSTALL), "--install", "--time", "10:15"], env).returncode == 0   # re-install replaces, not duplicates
    lines = table.read_text().strip().splitlines()
    assert "0 1 * * * /usr/bin/other-job" in lines
    ours = [l for l in lines if "jobs-pipeline daily crawl" in l]
    assert len(ours) == 1 and ours[0].startswith("15 10 * * *")
    assert sh([str(INSTALL), "--remove"], env).returncode == 0
    assert table.read_text().strip() == "0 1 * * * /usr/bin/other-job"


def test_install_rejects_bad_time_and_args(fake_crontab):
    _, env = fake_crontab
    for bad in ("25:00", "9:5", "noon", "12:60"):
        assert sh([str(INSTALL), "--install", "--time", bad], env).returncode == 2
    assert sh([str(INSTALL), "--bogus"], env).returncode == 2


def test_cron_script_skips_when_locked(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    env = dict(os.environ, JOBS_LOG_DIR=str(log_dir))
    holder = subprocess.Popen(["bash", "-c", f'exec 9>"{log_dir}/crawl.lock"; flock -n 9 && sleep 5'], env=env)
    try:
        import time
        time.sleep(0.5)
        r = sh([str(CRON)], env, timeout=20)
        assert r.returncode == 0
        assert "skipped: previous crawl still running" in (log_dir / "crawl.log").read_text()
    finally:
        holder.kill()
        holder.wait()
