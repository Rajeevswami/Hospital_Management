"""Blank `.env.example` values must not break the settings.

When you copy `.env.example` to `.env`, some keys come EMPTY
(`DATABASE_URL=`, `ALLOWED_HOSTS=`). python-decouple finds the key, so it does
NOT use `config(...)`'s `default=` - it returns an empty string. Before this was fixed:

* `dj_database_url.parse('')` -> UnknownSchemeError: Scheme '://'  (app crash)
* `Csv()('')` -> [] -> `127.0.0.1` disappears from ALLOWED_HOSTS (http://127.0.0.1:8000 -> 400)

Settings is module-level code (it runs at import), so this test imports the
real `hospital_system/settings.py` in a fresh subprocess with a blank env -
this is exactly the fresh-checkout code path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# This snippet imports settings and prints just two things.
# With DJANGO_SETTINGS_MODULE set, django.conf.settings is populated from here.
_PROBE = """
import json
from django.conf import settings
print(json.dumps({
    "engine": settings.DATABASES["default"]["ENGINE"],
    "name": settings.DATABASES["default"]["NAME"],
    "allowed_hosts": settings.ALLOWED_HOSTS,
}))
"""


def _load_settings_with_env(**overrides) -> dict:
    """Load the real settings module in a fresh process with the given env."""
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.pop("ALLOWED_HOSTS", None)
    env.update(overrides)
    env["DJANGO_SETTINGS_MODULE"] = "hospital_system.settings"
    env.setdefault("SECRET_KEY", "test-key-for-settings-probe")
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(BASE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"settings import failed (rc={result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    line = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("{")][-1]
    return json.loads(line)


class TestBlankEnvValues:
    """`.env.example` -> `cp` -> blank keys: the app must still run."""

    def test_blank_database_url_falls_back_to_sqlite(self):
        """`DATABASE_URL=` (empty) -> SQLite, no crash."""
        loaded = _load_settings_with_env(DATABASE_URL="")
        assert loaded["engine"] == "django.db.backends.sqlite3", loaded
        assert str(BASE_DIR / "db.sqlite3") in loaded["name"], loaded

    def test_blank_allowed_hosts_keeps_localhost_and_127(self):
        """`ALLOWED_HOSTS=` (empty) -> the default hosts must not disappear."""
        loaded = _load_settings_with_env(ALLOWED_HOSTS="")
        hosts = loaded["allowed_hosts"]
        assert "127.0.0.1" in hosts, hosts
        assert "localhost" in hosts, hosts
        # the wildcard from SAAS_ROOT_DOMAIN is also created
        assert ".testserver" in hosts or ".localhost" in hosts, hosts

    def test_whitespace_only_values_are_tolerated(self):
        loaded = _load_settings_with_env(DATABASE_URL="   ", ALLOWED_HOSTS="  ")
        assert loaded["engine"] == "django.db.backends.sqlite3", loaded
        assert "127.0.0.1" in loaded["allowed_hosts"], loaded

    def test_explicit_postgres_url_still_wins(self):
        """Blank-tolerance must not override the real config."""
        loaded = _load_settings_with_env(
            DATABASE_URL="postgres://u:p@db-host:5432/mydb",
            ALLOWED_HOSTS="app.example.com",
        )
        assert loaded["engine"] == "django.db.backends.postgresql", loaded
        assert loaded["name"] == "mydb", loaded
        assert "app.example.com" in loaded["allowed_hosts"], loaded
