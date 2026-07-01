// =====================================================
// SINGLE-EDITOR EDIT LOCK
//
// Only one browser may *edit* a given DRAFT (domain, version) at a time.
// The first opener acquires the lock (server-side, in ``load-from-uc``);
// later openers land read-only. This module, loaded on every page:
//
//   1. on load, asks the backend for the lock status of the session's
//      loaded version (``GET /domain/edit-lock``). The backend performs a
//      non-forcing acquire so the editor keeps the lock across reloads;
//   2. when this browser is the EDITOR (``mode == "edit"``) it runs a
//      ~30s heartbeat (``POST /domain/edit-lock/heartbeat``). If the lock
//      is taken over (``held == false``) the page flips to read-only;
//   3. when this browser is a VIEWER (``mode == "view"``) it stamps
//      ``body.read-only-locked`` — re-using every gate in
//      ``permissions.css`` — and shows a dismissible banner naming the
//      current editor. App-admins additionally get a "Take over editing"
//      button (``POST /domain/edit-lock/acquire`` with ``force``);
//   4. on tab close it best-effort releases the lock (the server TTL is
//      the real safety net, so a missed release self-heals in ~90s).
//
// ``window.editLockMode`` ('edit' | 'view' | 'none') is exposed so
// ``permissions.js`` → ``canEditOntology()`` respects the lock too.
// =====================================================

(function () {
    'use strict';

    // Default permissive until the async check resolves (mirrors
    // ``window.isActiveVersion`` in version-check.js). ``permissions.js``
    // only blocks on an explicit 'view'.
    window.editLockMode = window.editLockMode || 'edit';

    // Must be a third of the server TTL (_EDIT_LOCK_TTL_S = 90s) so two
    // missed beats still leave slack before the lock is declared stale.
    var HEARTBEAT_MS = 30000;
    var heartbeatTimer = null;
    var lockReleased = false;

    function notify(msg, kind) {
        if (typeof window.showNotification === 'function') {
            window.showNotification(msg, kind || 'info');
        }
    }

    function holderLabel(data) {
        return (data && (data.holder_name || data.holder_email)) || 'another user';
    }

    function escapeHtml(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // ----- read-only (viewer) presentation --------------------------

    function enterReadOnly() {
        window.editLockMode = 'view';
        document.body.classList.add('read-only-locked');
        if (window.OB && typeof window.OB.installReadOnlyContextMenuBlocker === 'function') {
            window.OB.installReadOnlyContextMenuBlocker();
        }
        if (window.OB && typeof window.OB.annotateRoleNavBadge === 'function') {
            window.OB.annotateRoleNavBadge(
                'This version is being edited by someone else (read-only).'
            );
        }
    }

    function renderBanner(data, opts) {
        opts = opts || {};
        if (document.getElementById('editLockBanner')) return;
        var banner = document.createElement('div');
        banner.id = 'editLockBanner';

        var msg = document.createElement('span');
        msg.className = 'edit-lock-msg';
        msg.innerHTML =
            '<i class="bi bi-lock-fill me-1"></i>' +
            (opts.takenOver
                ? 'Your editing session was taken over by <strong>' +
                  escapeHtml(holderLabel(data)) + '</strong> — this page is now read-only.'
                : 'This version is being edited by <strong>' +
                  escapeHtml(holderLabel(data)) + '</strong> — you have read-only access.');
        banner.appendChild(msg);

        var actions = document.createElement('span');
        actions.className = 'edit-lock-actions';

        if (data && data.can_take_over) {
            var takeover = document.createElement('button');
            takeover.type = 'button';
            takeover.className = 'btn btn-sm btn-warning';
            takeover.innerHTML = '<i class="bi bi-unlock me-1"></i>Take over editing';
            takeover.addEventListener('click', takeOver);
            actions.appendChild(takeover);
        }

        var dismiss = document.createElement('button');
        dismiss.type = 'button';
        dismiss.className = 'btn btn-sm btn-outline-secondary';
        dismiss.setAttribute('aria-label', 'Dismiss');
        dismiss.innerHTML = '<i class="bi bi-x-lg"></i>';
        dismiss.addEventListener('click', function () {
            banner.remove();
        });
        actions.appendChild(dismiss);

        banner.appendChild(actions);
        document.body.insertBefore(banner, document.body.firstChild);
    }

    // ----- editor (heartbeat) flow ----------------------------------

    function startHeartbeat() {
        if (heartbeatTimer) return;
        heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_MS);
    }

    function stopHeartbeat() {
        if (heartbeatTimer) {
            clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }
    }

    async function sendHeartbeat() {
        try {
            var res = await fetch('/domain/edit-lock/heartbeat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
            });
            var data = await res.json();
            if (data && data.held === false) {
                // Lost the lock — someone took over (or it expired and was
                // reclaimed). Flip to read-only and stop beating.
                stopHeartbeat();
                lockReleased = true; // nothing of ours to release anymore
                enterReadOnly();
                renderBanner(data, { takenOver: true });
                notify(
                    'Your editing session was taken over by ' +
                    holderLabel(data) + '. The page is now read-only.',
                    'warning'
                );
            }
        } catch (e) {
            // Transient network error — keep the lock optimistically; the
            // next beat (or the server TTL) reconciles state.
        }
    }

    // ----- admin take-over ------------------------------------------

    async function takeOver() {
        try {
            var res = await fetch('/domain/edit-lock/acquire', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ force: true }),
            });
            var data = await res.json();
            if (data && data.mode === 'edit') {
                notify('You are now the editor of this version.', 'success');
                window.location.reload();
            } else {
                notify('Could not take over the editing lock.', 'danger');
            }
        } catch (e) {
            notify('Could not take over the editing lock.', 'danger');
        }
    }

    // ----- tab-close release ----------------------------------------

    function releaseOnUnload() {
        if (lockReleased) return;
        lockReleased = true;
        try {
            // ``keepalive`` lets the request outlive the unload while still
            // carrying cookies + the CSRF header (window.fetch is patched
            // to attach it). sendBeacon can't set the CSRF header, so it
            // would be rejected by the CSRF middleware.
            fetch('/domain/edit-lock/release', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                keepalive: true,
            });
        } catch (e) {
            // Best-effort: the server TTL (~90s) frees the lock anyway.
        }
    }

    // ----- bootstrap -------------------------------------------------

    async function initEditLock() {
        var data;
        try {
            var res = await fetch('/domain/edit-lock', {
                headers: { 'Accept': 'application/json' },
                credentials: 'same-origin',
            });
            data = await res.json();
        } catch (e) {
            return; // no lock backend / not loaded — stay permissive
        }
        if (!data || !data.success) return;

        var mode = data.mode || 'none';
        window.editLockMode = mode;

        if (mode === 'edit') {
            startHeartbeat();
            window.addEventListener('pagehide', releaseOnUnload);
            window.addEventListener('beforeunload', releaseOnUnload);
        } else if (mode === 'view') {
            enterReadOnly();
            renderBanner(data, { takenOver: false });
        }
        // mode === 'none' → not a lockable DRAFT version; nothing to do.
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initEditLock);
    } else {
        initEditLock();
    }
})();
