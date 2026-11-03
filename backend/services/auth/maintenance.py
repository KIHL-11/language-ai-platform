"""Bounded purge of spent sign-in records and staging outbox files (P8-05B2).

Run as a scheduled job on the auth database role:

    python -m services.auth.maintenance [--batch 500] [--max-batches 20] [--outbox]

Each batch is its own short transaction, so a large backlog is drained over
several runs instead of holding locks. Rows that still feed a throttle are
kept: challenges stay for the full rolling failed-attempt day plus a margin.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg

from services.auth.email_login import DirectoryMailbox
from services.auth.sessions import SessionUnavailable


CHALLENGE_RETENTION = timedelta(days=1, hours=1)
STATE_RETENTION = timedelta(hours=1)
MAX_BATCH = 5000


def purge_auth_records(dsn, now, *, batch=500, max_batches=20):
    """Delete at most ``batch * max_batches`` rows per table; return counts."""
    if not 1 <= batch <= MAX_BATCH or not 1 <= max_batches <= 1000:
        raise ValueError('Purge bounds out of range')
    statements = {
        'email_challenges': ('DELETE FROM cloud_email_login_challenges WHERE challenge_id IN ('
                             'SELECT challenge_id FROM cloud_email_login_challenges WHERE created_at < %s '
                             'ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED)', now - CHALLENGE_RETENTION),
        'oauth_states': ('DELETE FROM cloud_oauth_states WHERE state_hash IN ('
                         'SELECT state_hash FROM cloud_oauth_states WHERE expires_at < %s '
                         'ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED)', now - STATE_RETENTION),
    }
    counts = {}
    try:
        with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as connection:
            for name, (sql, cutoff) in statements.items():
                counts[name] = 0
                for _ in range(max_batches):
                    with connection.transaction():
                        deleted = connection.execute(sql, (cutoff, batch)).rowcount
                    counts[name] += deleted
                    if deleted < batch:
                        break
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
    counts = purge_auth_records(dsn, now, batch=args.batch, max_batches=args.max_batches)
    if args.outbox:
        directory = os.getenv('CLOUD_EMAIL_SANDBOX_DIR', '').strip()
        if not directory:
            parser.error('CLOUD_EMAIL_SANDBOX_DIR is not set')
        counts['outbox_files'] = DirectoryMailbox(directory).purge(now, limit=args.batch * args.max_batches)
    print(' '.join(f'{name}={count}' for name, count in counts.items()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
