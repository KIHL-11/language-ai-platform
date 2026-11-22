"""Fail the CI job unless the PostgreSQL 16 test server is really reachable.

Backend tests skip PostgreSQL cases when TEST_POSTGRES_DSN is unset, so CI must
prove the database is there before the suite runs. Exits non-zero when the DSN
is missing, the server cannot be reached within the retry window, or the major
version is not the expected one. Never prints the DSN.
"""
from __future__ import annotations

import os
import sys
import time

EXPECTED_MAJOR = os.environ.get("EXPECTED_POSTGRES_MAJOR", "16")
ATTEMPTS = int(os.environ.get("POSTGRES_WAIT_ATTEMPTS", "30"))


def main() -> int:
    dsn = os.environ.get("TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        print("::error::TEST_POSTGRES_DSN is not set; PostgreSQL-backed tests would be skipped. Refusing to continue.")
        return 2
    if os.environ.get("CLOUD_P1_REQUIRE_POSTGRES") != "1":
        print("::error::CLOUD_P1_REQUIRE_POSTGRES must be 1 in CI.")
        return 2
    try:
        import psycopg
    except ImportError:
        print("::error::psycopg is not installed.")
        return 2
    last_error = "no attempt"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with psycopg.connect(dsn, connect_timeout=3) as connection:
                version = connection.execute("SHOW server_version").fetchone()[0]
                number = int(connection.execute("SHOW server_version_num").fetchone()[0])
        except psycopg.Error as exc:
            last_error = type(exc).__name__
            time.sleep(2)
            continue
        if str(number // 10000) != EXPECTED_MAJOR:
            print(f"::error::PostgreSQL {version} found; major {EXPECTED_MAJOR} is required.")
            return 2
        print(f"PostgreSQL {version} reachable (attempt {attempt}).")
        return 0
    print(f"::error::PostgreSQL is unreachable after {ATTEMPTS} attempts ({last_error}).")
    return 2


if __name__ == "__main__":
    sys.exit(main())
