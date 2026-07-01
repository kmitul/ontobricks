"""Single-editor edit lock for DRAFT domain versions.

Only one browser may *edit* a given ``(domain, version)`` at a time; later
openers land read-only with a banner naming the current editor. The lock
lives in Lakebase (``domain_edit_locks``) so it is consistent across app
replicas.

The lock has **no TTL / heartbeat**: it is held until the holder explicitly
*closes* the domain (:meth:`release`, wired to the "Close" button), an
app-admin *takes over* a held lock (``force``), or the version leaves DRAFT
(:meth:`force_release`). A genuinely stuck lock (e.g. a crashed browser
that never closed the domain) is recovered by admin take-over, not by a
timeout.

Only DRAFT versions are lockable — IN-REVIEW / PUBLISHED versions are
already read-only for everyone via the lifecycle gate.

This service is a thin orchestrator over the Lakebase store methods
(``acquire_edit_lock`` / ``release_edit_lock`` / ``get_edit_lock`` /
``force_release_edit_lock``). It resolves the Lakebase store directly via
:class:`RegistryFactory` (same path as
:meth:`SettingsService.check_lakebase_permissions`) and returns
``{"success": False, ...}`` when Lakebase / psycopg is unavailable so the
UI degrades to "no lock" rather than breaking domain loads.

Identity is taken from the request: the proxy-forwarded e-mail
(``request.state.user_email``), the display name
(``x-forwarded-preferred-username``) and the browser session id
(``request.state.session_id``, set by
:class:`back.objects.session.FileSessionMiddleware`). The lock is keyed by
**e-mail**, so the same user across two tabs shares one lock.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from back.core.logging import get_logger
from back.objects.registry import RegistryCfg, ROLE_ADMIN
from back.objects.registry.version_lifecycle import STATUS_DRAFT
from back.objects.session import SessionManager, get_domain

logger = get_logger(__name__)

# Mode reported to the client for the loaded (domain, version).
MODE_EDIT = "edit"
MODE_VIEW = "view"
MODE_NONE = "none"


class EditLockService:
    """Stateless orchestrator for the DRAFT single-editor lock."""

    # ------------------------------------------------------------------
    # Public API (consumed by the /domain/edit-lock endpoints)
    # ------------------------------------------------------------------

    @staticmethod
    def status(
        request, session_mgr: SessionManager, settings
    ) -> Dict[str, Any]:
        """Lock status for the session's loaded ``(folder, version)``.

        Performs a non-forcing acquire so the editor who just loaded (or
        reloaded) the page keeps / re-takes the lock when it is free, while
        a second opener is reported as a read-only viewer. Returns a
        ``mode`` of ``"none"`` for non-DRAFT versions or when the lock
        backend is unavailable.
        """
        folder, version, lifecycle = EditLockService._loaded(session_mgr)
        is_admin = EditLockService._is_admin(request)
        if not folder or not version or lifecycle != STATUS_DRAFT:
            return EditLockService._none(is_admin)

        store = EditLockService._store(session_mgr, settings)
        if store is None:
            return EditLockService._none(is_admin)

        email, name, sess = EditLockService._identity(request)
        res = store.acquire_edit_lock(
            folder,
            version,
            holder_email=email,
            holder_name=name,
            holder_session=sess,
            force=False,
        )
        return EditLockService._shape(res, is_admin=is_admin)

    @staticmethod
    def acquire(
        request,
        session_mgr: SessionManager,
        settings,
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Explicitly (re)acquire the lock. ``force`` is admin-only take-over."""
        folder, version, lifecycle = EditLockService._loaded(session_mgr)
        is_admin = EditLockService._is_admin(request)
        if not folder or not version or lifecycle != STATUS_DRAFT:
            return EditLockService._none(is_admin)
        # Defence in depth: force is only honoured for admins (the endpoint
        # also gates it, but never trust a single check for take-over).
        force = bool(force) and is_admin
        store = EditLockService._store(session_mgr, settings)
        if store is None:
            return EditLockService._none(is_admin)
        email, name, sess = EditLockService._identity(request)
        res = store.acquire_edit_lock(
            folder,
            version,
            holder_email=email,
            holder_name=name,
            holder_session=sess,
            force=force,
        )
        return EditLockService._shape(res, is_admin=is_admin)

    @staticmethod
    def release(
        request, session_mgr: SessionManager, settings
    ) -> Dict[str, Any]:
        """Release the current user's lock on the loaded ``(folder, version)``."""
        folder, version, _ = EditLockService._loaded(session_mgr)
        if not folder or not version:
            return {"success": True, "released": False}
        store = EditLockService._store(session_mgr, settings)
        if store is None:
            return {"success": False, "released": False}
        email, _, _ = EditLockService._identity(request)
        released = store.release_edit_lock(
            folder, version, holder_email=email
        )
        return {"success": True, "released": bool(released)}

    # ------------------------------------------------------------------
    # Helpers wired into other flows (load-from-uc, reset, status change)
    # ------------------------------------------------------------------

    @staticmethod
    def release_prev(
        request,
        session_mgr: SessionManager,
        settings,
        folder: str,
        version: str,
    ) -> bool:
        """Release the current user's lock on a specific ``(folder, version)``.

        Used to *close* the previously-open domain — freeing its edit-lock —
        **before** a different domain is loaded, so a user never holds two
        DRAFT locks at once. Holder-scoped and best-effort: it never raises
        into the load path and no-ops when the backend is unavailable or the
        caller is not the lock holder.
        """
        if not folder or not version:
            return False
        try:
            store = EditLockService._store(session_mgr, settings)
            if store is None:
                return False
            email, _, _ = EditLockService._identity(request)
            return bool(
                store.release_edit_lock(folder, version, holder_email=email)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("release_prev edit-lock skipped: %s", exc)
            return False

    @staticmethod
    def on_domain_loaded(
        request,
        session_mgr: SessionManager,
        settings,
        *,
        prev_folder: str = "",
        prev_version: str = "",
    ) -> Dict[str, Any]:
        """Acquire the lock for a freshly loaded DRAFT version.

        Releases any lock the session previously held on a *different*
        ``(folder, version)`` first, then acquires the newly loaded one
        (when DRAFT). Returns a small ``lock`` block for an immediate
        client toast: ``{mode, holder_email, holder_name, is_self}``.
        Best-effort — never raises into the load path.
        """
        try:
            folder, version, lifecycle = EditLockService._loaded(session_mgr)
            store = EditLockService._store(session_mgr, settings)
            if store is None:
                return {"mode": MODE_NONE}
            email, name, sess = EditLockService._identity(request)
            # Drop a stale lock from the previously loaded version.
            if (
                prev_folder
                and prev_version
                and (prev_folder, prev_version) != (folder, version)
            ):
                try:
                    store.release_edit_lock(
                        prev_folder, prev_version, holder_email=email
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("prev edit-lock release skipped: %s", exc)
            if not folder or not version or lifecycle != STATUS_DRAFT:
                return {"mode": MODE_NONE}
            res = store.acquire_edit_lock(
                folder,
                version,
                holder_email=email,
                holder_name=name,
                holder_session=sess,
                force=False,
            )
            shaped = EditLockService._shape(
                res, is_admin=EditLockService._is_admin(request)
            )
            return {
                "mode": shaped["mode"],
                "holder_email": shaped["holder_email"],
                "holder_name": shaped["holder_name"],
                "is_self": shaped["is_self"],
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("on_domain_loaded edit-lock skipped: %s", exc)
            return {"mode": MODE_NONE}

    @staticmethod
    def release_for_session(
        request, session_mgr: SessionManager, settings
    ) -> None:
        """Best-effort release of the loaded version's lock (reset-session)."""
        try:
            EditLockService.release(request, session_mgr, settings)
        except Exception as exc:  # noqa: BLE001
            logger.debug("release_for_session edit-lock skipped: %s", exc)

    @staticmethod
    def blocking_holder(request, session_mgr: SessionManager, settings) -> str:
        """Display name of *another* user holding the loaded version's lock.

        Returns ``""`` when the request must **not** be blocked: the lock is
        free, held by the requesting user, the version is not DRAFT, or the
        backend is unavailable. Used by the authoritative
        :class:`PermissionMiddleware` edit gate — admins are not exempt, so
        they must "take over" before editing (preserving single-writer).
        """
        try:
            folder, version, lifecycle = EditLockService._loaded(session_mgr)
            if not folder or not version or lifecycle != STATUS_DRAFT:
                return ""
            store = EditLockService._store(session_mgr, settings)
            if store is None:
                return ""
            lock = store.get_edit_lock(folder, version)
            if not lock:
                return ""
            email, _, _ = EditLockService._identity(request)
            holder_email = lock.get("holder_email") or ""
            if holder_email and holder_email.lower() != (email or "").lower():
                return lock.get("holder_name") or holder_email
            return ""
        except Exception as exc:  # noqa: BLE001
            logger.debug("blocking_holder check skipped: %s", exc)
            return ""

    @staticmethod
    def force_release(
        session_mgr: SessionManager, settings, folder: str, version: str
    ) -> None:
        """Unconditionally drop a lock (a version left DRAFT). Best-effort."""
        if not folder or not version:
            return
        try:
            store = EditLockService._store(session_mgr, settings)
            if store is not None:
                store.force_release_edit_lock(folder, version)
        except Exception as exc:  # noqa: BLE001
            logger.debug("force_release edit-lock skipped: %s", exc)

    # ------------------------------------------------------------------
    # Admin overview (Settings › Locks)
    # ------------------------------------------------------------------

    @staticmethod
    def list_all(session_mgr: SessionManager, settings) -> Dict[str, Any]:
        """All active edit locks across the registry (admin Locks panel).

        Returns ``{"success": True, "locks": [...]}``. Degrades to an empty
        list (still ``success``) when the lock backend is unavailable so the
        admin UI shows "no locks" rather than an error.
        """
        store = EditLockService._store(session_mgr, settings)
        if store is None:
            return {"success": True, "locks": []}
        try:
            locks = store.list_all_edit_locks()
        except Exception as exc:  # noqa: BLE001
            logger.debug("list_all edit-locks failed: %s", exc)
            locks = []
        return {"success": True, "locks": locks}

    @staticmethod
    def admin_release(
        session_mgr: SessionManager, settings, folder: str, version: str
    ) -> Dict[str, Any]:
        """Admin force-unlock of a specific ``(folder, version)``.

        Unconditional (unlike :meth:`release`, which only drops the caller's
        own lock). Returns ``{"success": True, "released": bool}``.
        """
        if not folder or not version:
            return {"success": False, "released": False}
        store = EditLockService._store(session_mgr, settings)
        if store is None:
            return {"success": False, "released": False}
        try:
            released = store.force_release_edit_lock(folder, version)
        except Exception as exc:  # noqa: BLE001
            logger.debug("admin_release edit-lock failed: %s", exc)
            return {"success": False, "released": False}
        return {"success": True, "released": bool(released)}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _store(session_mgr: SessionManager, settings):
        """Resolve the Lakebase registry store, or ``None`` when unavailable."""
        rcfg = RegistryCfg.from_session(session_mgr, settings)
        if not rcfg.is_configured:
            return None
        try:
            from back.objects.registry.store import RegistryFactory

            return RegistryFactory.lakebase(
                registry_cfg=rcfg,
                schema=rcfg.lakebase_schema,
                database=rcfg.lakebase_database,
            )
        except ImportError:
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("edit-lock store unavailable: %s", exc)
            return None

    @staticmethod
    def _loaded(session_mgr: SessionManager) -> Tuple[str, str, str]:
        """Return ``(folder, version, lifecycle_status)`` from the session."""
        try:
            domain = get_domain(session_mgr)
            folder = getattr(domain, "domain_folder", "") or ""
            version = getattr(domain, "current_version", "") or ""
            status = ((domain.info or {}).get("status") or STATUS_DRAFT).upper()
            return folder, version, status
        except Exception as exc:  # noqa: BLE001
            logger.debug("could not resolve loaded domain: %s", exc)
            return "", "", STATUS_DRAFT

    @staticmethod
    def _identity(request) -> Tuple[str, str, str]:
        """Return ``(email, display_name, session_id)`` for the request."""
        email = (
            getattr(request.state, "user_email", "")
            or request.headers.get("x-forwarded-email", "")
            or ""
        )
        name = (
            request.headers.get("x-forwarded-preferred-username", "")
            or email
            or "Someone"
        )
        session_id = getattr(request.state, "session_id", "") or ""
        return email, name, session_id

    @staticmethod
    def _is_admin(request) -> bool:
        return (getattr(request.state, "user_role", "") or "") == ROLE_ADMIN

    @staticmethod
    def _none(is_admin: bool) -> Dict[str, Any]:
        return {
            "success": True,
            "mode": MODE_NONE,
            "holder_email": "",
            "holder_name": "",
            "acquired_at": "",
            "is_self": False,
            "is_admin": is_admin,
            "can_take_over": False,
        }

    @staticmethod
    def _shape(res: Dict[str, Any], *, is_admin: bool) -> Dict[str, Any]:
        """Map a store ``acquire_edit_lock`` result to the API lock block."""
        holder_email = res.get("holder_email") or ""
        is_self = bool(res.get("is_self") or res.get("acquired"))
        # Only report a read-only VIEW when the lock is genuinely held by
        # *someone else* — i.e. we did not acquire it AND a real holder
        # e-mail came back. When the store returns "not acquired, no holder"
        # the lock backend is effectively unavailable (e.g. the
        # ``domain_edit_locks`` table is missing on an old deployment): degrade
        # to permissive rather than presenting a phantom "another user" lock
        # that also makes take-over impossible.
        if is_self:
            mode = MODE_EDIT
        elif holder_email:
            mode = MODE_VIEW
        else:
            mode = MODE_NONE
        return {
            "success": True,
            "mode": mode,
            "holder_email": holder_email,
            "holder_name": res.get("holder_name") or "",
            "acquired_at": res.get("acquired_at") or "",
            "is_self": is_self,
            "is_admin": is_admin,
            # Only a viewer of a lock held by someone else can take over, and
            # only an admin is allowed to.
            "can_take_over": bool(is_admin and mode == MODE_VIEW),
        }
