"""Bounded purge of spent sign-in records and staging outbox files (P8-05B2).

Run as a scheduled job on the auth database role:

    python -m services.auth.maintenance [--batch 500] [--max-batches 20] [--outbox]

Each batch is its own short transaction, so a large backlog is drained over
several runs instead of holding locks. Candidate rows are not pre-locked
(that would need UPDATE, which the maintenance role must not hold); rows past
retention are rarely written by sign-in paths (only a mass sign-out can touch
an expired, unrevoked session), and a 2 s lock timeout bounds any wait; a
timed-out batch rolls back and is retried by the next run. Rows that still feed a throttle are
kept: challenges stay for the full rolling failed-attempt day plus a margin.
Browser sessions are deleted only once they can never authenticate again
(revoked, past absolute expiry or idle-expired) and a retention day passed.

Safe to rerun at any time: deletes are idempotent, every committed batch
stays committed, and a crash or timeout simply leaves work for the next run.
Only one run proceeds at a time (session-level advisory lock); a second run
exits with status 2 and changes nothing. Each statement has lock and statement
timeouts so it never stalls sign-ins. Intended for a dedicated maintenance
role holding only SELECT and DELETE on these tables (see the B3 doc).
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg

from services.auth.email_login import DirectoryMailbox
from services.auth.sessions import IDLE_LIFETIME, SessionUnavailable


CHALLENGE_RETENTION = timedelta(days=1, hours=1)
STATE_RETENTION = timedelta(hours=1)
SESSION_RETENTION = timedelta(days=1)
MAX_BATCH = 5000
RUN_LOCK = 'language-ai-auth-maintenance'


class PurgeAlreadyRunning(RuntimeError):
    pass


def purge_auth_records(dsn, now, *, batch=500, max_batches=20):
    """Delete at most ``batch * max_batches`` rows per table; return counts."""
    if not 1 <= batch <= MAX_BATCH or not 1 <= max_batches <= 1000:
        raise ValueError('Purge bounds out of range')
    dead = now - SESSION_RETENTION
    statements = {
        'email_challenges': ('DELETE FROM cloud_email_login_challenges WHERE challenge_id IN ('
                             'SELECT challenge_id FROM cloud_email_login_challenges WHERE created_at < %s '
                             'ORDER BY created_at LIMIT %s)', now - CHALLENGE_RETENTION),
        'oauth_states': ('DELETE FROM cloud_oauth_states WHERE state_hash IN ('
                         'SELECT state_hash FROM cloud_oauth_states WHERE expires_at < %s '
                         'ORDER BY expires_at LIMIT %s)', now - STATE_RETENTION),
        # A session past every lifetime check can never verify again (sessions.py
        # rejects revoked, absolutely expired and idle rows), so deleting it
        # cannot sign anyone out. Its OAuth states cascade.
        'browser_sessions': ('DELETE FROM cloud_browser_sessions WHERE session_id IN ('
                             'SELECT session_id FROM cloud_browser_sessions WHERE revoked_at < %(cutoff)s OR expires_at < %(cutoff)s '
                             'OR last_seen_at < %(idle)s ORDER BY created_at LIMIT %(batch)s)', dead),
    }
    counts = {}
    try:
        with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as connection:
            if not connection.execute('SELECT pg_try_advisory_lock(hashtextextended(%s, 0))', (RUN_LOCK,)).fetchone()[0]:
                raise PurgeAlreadyRunning('Another purge run holds the lock')
            try:
                for name, (sql, cutoff) in statements.items():
                    counts[name] = 0
                    params = ({'cutoff': cutoff, 'idle': cutoff - IDLE_LIFETIME, 'batch': batch}
                              if name == 'browser_sessions' else (cutoff, batch))
                    for _ in range(max_batches):
                        with connection.transaction():
                            connection.execute("SET LOCAL lock_timeout = '2s'")
                            connection.execute("SET LOCAL statement_timeout = '30s'")
                            deleted = connection.execute(sql, params).rowcount
                        counts[name] += deleted
                        if deleted < batch:
                            break
            finally:
                connection.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', (RUN_LOCK,))
    except psycopg.Error as exc:
        raise SessionUnavailable('Auth storage unavailable') from exc
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description='Purge spent sign-in records.')
    parser.add_argument('--batch', type=int, default=500)
    parser.add_argument('--max-batches', type=int, default=20)
    parser.add_argument('--outbox', action='store_true', help='also purge CLOUD_EMAIL_SANDBOX_DIR (staging only)')
    args = parser.parse_args(argv)
    dsn = os.getenv('CLOUD_SESSION_DATABASE_URL', '').strip()
    if not dsn:
        parser.error('CLOUD_SESSION_DATABASE_URL is not set')
    now = datetime.now(timezone.utc)
    try:
        counts = purge_auth_records(dsn, now, batch=args.batch, max_batches=args.max_batches)
    except PurgeAlreadyRunning:
        print('another purge run is active; nothing done', file=sys.stderr)
        return 2
    except SessionUnavailable as exc:
        print(f'purge failed: {exc}; committed batches are kept, rerun later', file=sys.stderr)
        return 1
    if args.outbox:
        directory = os.getenv('CLOUD_EMAIL_SANDBOX_DIR', '').strip()
        if not directory:
            parser.error('CLOUD_EMAIL_SANDBOX_DIR is not set')
        counts['outbox_files'] = DirectoryMailbox(directory).purge(now, limit=args.batch * args.max_batches)
    print(' '.join(f'{name}={count}' for name, count in counts.items()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
