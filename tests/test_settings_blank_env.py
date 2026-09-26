"""`.env.example` ke blank values settings ko todne na paayein.

`.env.example` copy karke `.env` banane par kuch keys KHAALI aati hain
(`DATABASE_URL=`, `ALLOWED_HOSTS=`). python-decouple ko key mil jaati hai, isliye
wo `config(...)` ka `default=` use NAHI karta - empty string deta hai. Isse pehle:

* `dj_database_url.parse('')` -> UnknownSchemeError: Scheme '://'  (app crash)
* `Csv()('')` -> [] -> `127.0.0.1` ALLOWED_HOSTS se gayab (http://127.0.0.1:8000 -> 400)

Settings module-level code hai (import par hi chal jaata hai), isliye is test mein
asli `hospital_system/settings.py` ko ek fresh subprocess mein blank env ke saath
import kiya jaata hai - yahi fresh checkout wala code path hai.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Yeh snippet settings ko import karke sirf do cheezein print karta hai.
# DJANGO_SETTINGS_MODULE set hone se django.conf.settings yahin se populate hota hai.
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
    """Asli settings module ko fresh process mein load karo, diye gaye env ke saath."""
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
        f"settings import fail hua (rc={result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    line = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("{")][-1]
    return json.loads(line)


class TestBlankEnvValues:
    """`.env.example` -> `cp` -> blank keys: app phir bhi chalna chahiye."""

    def test_blank_database_url_falls_back_to_sqlite(self):
        """`DATABASE_URL=` (khaali) -> SQLite, crash nahi."""
        loaded = _load_settings_with_env(DATABASE_URL="")
        assert loaded["engine"] == "django.db.backends.sqlite3", loaded
        assert str(BASE_DIR / "db.sqlite3") in loaded["name"], loaded

    def test_blank_allowed_hosts_keeps_localhost_and_127(self):
        """`ALLOWED_HOSTS=` (khaali) -> default hosts gayab na ho."""
        loaded = _load_settings_with_env(ALLOWED_HOSTS="")
        hosts = loaded["allowed_hosts"]
        assert "127.0.0.1" in hosts, hosts
        assert "localhost" in hosts, hosts
        # SAAS_ROOT_DOMAIN se wildcard bhi banta hai
        assert ".testserver" in hosts or ".localhost" in hosts, hosts

    def test_whitespace_only_values_bhi_tolerate_hote_hain(self):
        loaded = _load_settings_with_env(DATABASE_URL="   ", ALLOWED_HOSTS="  ")
        assert loaded["engine"] == "django.db.backends.sqlite3", loaded
        assert "127.0.0.1" in loaded["allowed_hosts"], loaded

    def test_explicit_postgres_url_still_wins(self):
        """Blank-tolerance real config ko override na kare."""
        loaded = _load_settings_with_env(
            DATABASE_URL="postgres://u:p@db-host:5432/mydb",
            ALLOWED_HOSTS="app.example.com",
        )
        assert loaded["engine"] == "django.db.backends.postgresql", loaded
        assert loaded["name"] == "mydb", loaded
        assert "app.example.com" in loaded["allowed_hosts"], loaded
