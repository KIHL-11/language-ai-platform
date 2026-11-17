"""PR-I: P8-05B email login on the Cloud Phase 1 foundation.

0001-0009 apply through the pinned runner, preflight accepts the
least-privilege runtime role at 0009, and email login, sign-out, re-login,
re-authentication and per-account learning data all work with only the
runtime grants. Synthetic addresses and the in-memory sandbox mailbox; nothing
is sent.
"""
from __future__ import annotations

import pytest

from services.auth.email_login import EmailLoginService, SandboxMailbox, new_client_binding
from services.auth.identity import InvalidAuthentication
from services.auth.sessions import BrowserSessionRepository
from services.cloud import preflight
from services.library.postgres import FilesystemMediaObjectStore, PostgresOwnerScopedLibraryRepository
from test_cloud_db_foundation import cloud_db  # noqa: F401  (fixture)
from test_durable_ai_chunk_candidates import MutableClock
from test_owner_scoped_library import lesson

CODE_KEY = bytes(range(32))  # synthetic test key
EMAIL_A = "learner.a.p1@example.test"
EMAIL_B = "learner.b.p1@example.test"


def _login(service, mailbox, email):
    browser = new_client_binding()
    challenge = service.start(email, browser).challenge_id
    return service.verify(challenge, mailbox.latest(email).code, browser)


def test_email_login_lifecycle_runs_as_the_runtime_role(cloud_db):  # noqa: F811
    dsn = cloud_db.runtime(versions_target="0009")
    report = preflight.run_preflight(dsn, minimum_version="0009")
    assert report.ok, report.failures
    assert report.versions == [f"{i:04d}" for i in range(1, 10)]

    clock = MutableClock()
    sessions = BrowserSessionRepository(dsn, clock=clock)
    mailbox = SandboxMailbox()
    service = EmailLoginService(sessions, mailbox, code_key=CODE_KEY)

    first = _login(service, mailbox, EMAIL_A)
    assert first.created_account is True
    assert sessions.verify(first.session_token).user_id == first.owner_user_id

    sessions.revoke(first.session_token)
    with pytest.raises(InvalidAuthentication):
        sessions.verify(first.session_token)

    clock.advance(seconds=120)  # past the resend throttle
    again = _login(service, mailbox, EMAIL_A)
    assert again.created_account is False
    assert again.owner_user_id == first.owner_user_id

    clock.advance(seconds=120)
    reauth = service.start_reauth(again.session_token)
    service.verify_reauth(reauth.challenge_id, mailbox.latest(EMAIL_A).code, again.session_token)

    assert cloud_db.scalar("SELECT count(*) FROM cloud_users") == 1
    assert cloud_db.scalar("SELECT count(*) FROM cloud_external_identities") == 1
    assert preflight.run_preflight(dsn, minimum_version="0009").ok


def test_email_accounts_get_separate_owners_and_isolated_learning_data(cloud_db, tmp_path):  # noqa: F811
    dsn = cloud_db.runtime(versions_target="0009")
    clock = MutableClock()
    sessions = BrowserSessionRepository(dsn, clock=clock)
    mailbox = SandboxMailbox()
    service = EmailLoginService(sessions, mailbox, code_key=CODE_KEY)

    owner_a = _login(service, mailbox, EMAIL_A).owner_user_id
    owner_b = _login(service, mailbox, EMAIL_B).owner_user_id
    assert owner_a != owner_b

    library = PostgresOwnerScopedLibraryRepository(
        dsn, FilesystemMediaObjectStore(tmp_path / "cloud-media"), clock=clock)
    item_a = library.create_for_owner(owner_a, lesson(source_url="https://youtu.be/pr_i_a"))
    item_b = library.create_for_owner(owner_b, lesson(source_url="https://youtu.be/pr_i_b"))

    assert [i.id for i in library.list_for_owner(owner_a)] == [item_a.id]
    assert [i.id for i in library.list_for_owner(owner_b)] == [item_b.id]
    with pytest.raises(KeyError):
        library.get_for_owner(owner_b, item_a.id)
    with pytest.raises(KeyError):
        library.delete_for_owner(owner_b, item_a.id)
    assert library.get_for_owner(owner_a, item_a.id).id == item_a.id
    assert cloud_db.scalar("SELECT count(*) FROM cloud_users") == 2
