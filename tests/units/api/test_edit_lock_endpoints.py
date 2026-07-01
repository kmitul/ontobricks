"""Tests for the internal /domain/edit-lock router request plumbing.

The handlers delegate to ``EditLockService`` (covered in
``tests/units/registry/test_edit_lock_service.py``). These tests assert the
request wiring: body parsing, the admin-only ``force`` flag forwarding, and
that ``load-from-uc`` attaches a ``lock`` block on a successful load.
"""

import asyncio
import importlib
from types import SimpleNamespace

from unittest.mock import AsyncMock, MagicMock, patch

_domain = importlib.import_module("api.routers.internal.domain")
_svc = importlib.import_module("back.objects.registry.EditLockService")


def _run(coro):
    """Run a coroutine synchronously, robust to a running event loop.

    Mirrors ``tests/units/auth/test_permission_middleware._run`` — driving
    the async handlers from a sync test avoids the pytest-asyncio
    ``Runner.run() cannot be called from a running event loop`` clash that
    the bare ``async def`` endpoint tests in this suite hit under the full
    (event-loop-polluted) run.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _request(body=None, *, user_role="editor", user_email="alice@acme.com"):
    req = MagicMock()
    req.json = AsyncMock(return_value=body if body is not None else {})
    req.state = SimpleNamespace(user_role=user_role, user_email=user_email)
    req.headers = {}
    return req


def test_get_edit_lock_delegates_to_service():
    with patch.object(
        _svc.EditLockService, "status",
        return_value={"success": True, "mode": "edit"},
    ) as st:
        res = _run(_domain.get_edit_lock(
            _request(), session_mgr=MagicMock(), settings=MagicMock()
        ))
    assert res["mode"] == "edit"
    assert st.called


def test_acquire_forwards_force_flag():
    with patch.object(
        _svc.EditLockService, "acquire",
        return_value={"success": True, "mode": "edit"},
    ) as ac:
        _run(_domain.acquire_edit_lock(
            _request({"force": True}, user_role="admin"),
            session_mgr=MagicMock(), settings=MagicMock(),
        ))
    assert ac.call_args.kwargs["force"] is True


def test_acquire_defaults_force_false():
    with patch.object(
        _svc.EditLockService, "acquire",
        return_value={"success": True, "mode": "edit"},
    ) as ac:
        _run(_domain.acquire_edit_lock(
            _request({}), session_mgr=MagicMock(), settings=MagicMock()
        ))
    assert ac.call_args.kwargs["force"] is False


def test_release_delegates_to_service():
    with patch.object(
        _svc.EditLockService, "release",
        return_value={"success": True, "released": True},
    ) as rl:
        res = _run(_domain.release_edit_lock(
            _request(), session_mgr=MagicMock(), settings=MagicMock()
        ))
    assert res["released"] is True
    assert rl.called


def test_close_releases_lock_then_resets_session():
    domain_obj = MagicMock()
    with (
        patch.object(_domain, "get_domain", return_value=domain_obj),
        patch.object(
            _svc.EditLockService, "release_for_session"
        ) as rel,
    ):
        res = _run(_domain.close_domain(
            _request(), session_mgr=MagicMock(), settings=MagicMock()
        ))
    assert res["success"] is True
    assert rel.called
    domain_obj.reset.assert_called_once()
    domain_obj.clear_uc_metadata.assert_called_once()


def test_load_from_uc_attaches_lock_block_on_success():
    domain_obj = MagicMock()
    domain_obj.domain_folder = ""
    domain_obj.current_version = ""

    fake_p = MagicMock()
    fake_p.load_domain_from_uc.return_value = {"success": True, "version": "1"}

    with (
        patch.object(_domain, "get_domain", return_value=domain_obj),
        patch.object(_domain, "Domain", return_value=fake_p),
        patch.object(
            _svc.EditLockService, "on_domain_loaded",
            return_value={"mode": "edit", "is_self": True,
                          "holder_email": "alice@acme.com", "holder_name": "Alice"},
        ) as odl,
    ):
        req = _request({"domain": "acme", "version": "1"})
        res = _run(_domain.load_domain_from_uc(
            req, session_mgr=MagicMock(), settings=MagicMock()
        ))
    assert res["success"] is True
    assert res["lock"]["mode"] == "edit"
    assert odl.called


def test_load_from_uc_no_lock_block_on_failure():
    fake_p = MagicMock()
    fake_p.load_domain_from_uc.return_value = {"success": False, "error": "boom"}

    with (
        patch.object(_domain, "get_domain", return_value=MagicMock()),
        patch.object(_domain, "Domain", return_value=fake_p),
        patch.object(_svc.EditLockService, "on_domain_loaded") as odl,
    ):
        req = _request({"domain": "acme", "version": "1"})
        res = _run(_domain.load_domain_from_uc(
            req, session_mgr=MagicMock(), settings=MagicMock()
        ))
    assert res["success"] is False
    assert "lock" not in res
    odl.assert_not_called()