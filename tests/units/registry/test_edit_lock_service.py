"""Tests for the single-editor edit-lock orchestrator (EditLockService).

The Lakebase store and the session are mocked; the focus is the service
logic: DRAFT gating, edit/view mode shaping, admin-only ``force`` take-over
gating, and the ``blocking_holder`` middleware helper.
"""

import importlib

from unittest.mock import MagicMock, patch

from back.objects.registry.EditLockService import EditLockService

_mod = importlib.import_module("back.objects.registry.EditLockService")


def _request(email="alice@acme.com", role="editor", session_id="sess1"):
    req = MagicMock()
    req.state.user_email = email
    req.state.user_role = role
    req.state.session_id = session_id
    req.headers = {"x-forwarded-preferred-username": "Alice"}
    return req


def _domain(folder="acme", version="1", status="DRAFT"):
    d = MagicMock()
    d.domain_folder = folder
    d.current_version = version
    d.info = {"status": status}
    return d


def _patches(store, domain):
    return (
        patch.object(_mod, "get_domain", MagicMock(return_value=domain)),
        patch.object(_mod.EditLockService, "_store", MagicMock(return_value=store)),
    )


def _store(acquire=None, heartbeat=None, get=None):
    st = MagicMock()
    st.acquire_edit_lock.return_value = acquire or {
        "acquired": True,
        "is_self": True,
        "holder_email": "alice@acme.com",
        "holder_name": "Alice",
        "acquired_at": "2026-01-01T00:00:00",
        "stale": False,
    }
    st.heartbeat_edit_lock.return_value = heartbeat or {"held": True}
    st.get_edit_lock.return_value = get
    st.release_edit_lock.return_value = True
    return st


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------


def test_status_editor_gets_edit_mode():
    store = _store()
    p1, p2 = _patches(store, _domain())
    with p1, p2:
        res = EditLockService.status(_request(), MagicMock(), MagicMock())
    assert res["mode"] == "edit"
    assert res["is_self"] is True
    store.acquire_edit_lock.assert_called_once()
    # Non-forcing acquire on a plain status check.
    assert store.acquire_edit_lock.call_args.kwargs["force"] is False


def test_status_viewer_gets_view_mode_no_takeover_for_non_admin():
    store = _store(
        acquire={
            "acquired": False,
            "is_self": False,
            "holder_email": "bob@acme.com",
            "holder_name": "Bob",
            "acquired_at": "2026-01-01T00:00:00",
            "stale": False,
        }
    )
    p1, p2 = _patches(store, _domain())
    with p1, p2:
        res = EditLockService.status(
            _request(email="carol@acme.com", role="editor"), MagicMock(), MagicMock()
        )
    assert res["mode"] == "view"
    assert res["holder_name"] == "Bob"
    assert res["can_take_over"] is False


def test_status_viewer_admin_can_take_over():
    store = _store(
        acquire={
            "acquired": False,
            "is_self": False,
            "holder_email": "bob@acme.com",
            "holder_name": "Bob",
            "stale": False,
        }
    )
    p1, p2 = _patches(store, _domain())
    with p1, p2:
        res = EditLockService.status(
            _request(email="admin@acme.com", role="admin"), MagicMock(), MagicMock()
        )
    assert res["mode"] == "view"
    assert res["can_take_over"] is True


def test_status_non_draft_is_none_and_skips_store():
    store = _store()
    p1, p2 = _patches(store, _domain(status="PUBLISHED"))
    with p1, p2:
        res = EditLockService.status(_request(), MagicMock(), MagicMock())
    assert res["mode"] == "none"
    store.acquire_edit_lock.assert_not_called()


# ----------------------------------------------------------------------
# heartbeat
# ----------------------------------------------------------------------


def test_heartbeat_held():
    store = _store(heartbeat={"held": True, "holder_email": "alice@acme.com"})
    p1, p2 = _patches(store, _domain())
    with p1, p2:
        res = EditLockService.heartbeat(_request(), MagicMock(), MagicMock())
    assert res["held"] is True


def test_heartbeat_lost_when_taken_over():
    store = _store(heartbeat={"held": False})
    p1, p2 = _patches(store, _domain())
    with p1, p2:
        res = EditLockService.heartbeat(_request(), MagicMock(), MagicMock())
    assert res["held"] is False


# ----------------------------------------------------------------------
# acquire — force gating
# ----------------------------------------------------------------------


def test_acquire_force_ignored_for_non_admin():
    store = _store()
    p1, p2 = _patches(store, _domain())
    with p1, p2:
        EditLockService.acquire(
            _request(role="editor"), MagicMock(), MagicMock(), force=True
        )
    assert store.acquire_edit_lock.call_args.kwargs["force"] is False


def test_acquire_force_honoured_for_admin():
    store = _store()
    p1, p2 = _patches(store, _domain())
    with p1, p2:
        EditLockService.acquire(
            _request(role="admin"), MagicMock(), MagicMock(), force=True
        )
    assert store.acquire_edit_lock.call_args.kwargs["force"] is True


# ----------------------------------------------------------------------
# release
# ----------------------------------------------------------------------


def test_release_calls_store_with_email():
    store = _store()
    p1, p2 = _patches(store, _domain())
    with p1, p2:
        res = EditLockService.release(_request(), MagicMock(), MagicMock())
    assert res["released"] is True
    assert store.release_edit_lock.call_args.kwargs["holder_email"] == "alice@acme.com"


# ----------------------------------------------------------------------
# blocking_holder (middleware helper)
# ----------------------------------------------------------------------


def test_blocking_holder_returns_name_when_held_by_other():
    store = _store(
        get={"holder_email": "bob@acme.com", "holder_name": "Bob", "stale": False}
    )
    p1, p2 = _patches(store, _domain())
    with p1, p2:
        holder = EditLockService.blocking_holder(
            _request(email="carol@acme.com"), MagicMock(), MagicMock()
        )
    assert holder == "Bob"


def test_blocking_holder_empty_when_self():
    store = _store(
        get={"holder_email": "alice@acme.com", "holder_name": "Alice", "stale": False}
    )
    p1, p2 = _patches(store, _domain())
    with p1, p2:
        holder = EditLockService.blocking_holder(
            _request(email="alice@acme.com"), MagicMock(), MagicMock()
        )
    assert holder == ""


def test_blocking_holder_empty_when_stale():
    store = _store(
        get={"holder_email": "bob@acme.com", "holder_name": "Bob", "stale": True}
    )
    p1, p2 = _patches(store, _domain())
    with p1, p2:
        holder = EditLockService.blocking_holder(
            _request(email="carol@acme.com"), MagicMock(), MagicMock()
        )
    assert holder == ""


def test_blocking_holder_empty_on_non_draft():
    store = _store(
        get={"holder_email": "bob@acme.com", "holder_name": "Bob", "stale": False}
    )
    p1, p2 = _patches(store, _domain(status="IN-REVIEW"))
    with p1, p2:
        holder = EditLockService.blocking_holder(
            _request(email="carol@acme.com"), MagicMock(), MagicMock()
        )
    assert holder == ""
    store.get_edit_lock.assert_not_called()
