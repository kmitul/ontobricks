// =====================================================
// SINGLE-EDITOR EDIT LOCK
//
// Only one browser may *edit* a given DRAFT (domain, version) at a time.
// The first opener acquires the lock (server-side, in ``load-from-uc``);
// later openers land read-only. There is NO timeout / heartbeat: the lock
// is held until the editor explicitly *closes* the domain (the "Close"
// button → ``POST /domain/close``), an app-admin takes it over, or the
// version leaves DRAFT.
//
// This module, loaded on every page:
//
//   1. on load, asks the backend for the lock status of the session's
//      loaded version (``GET /domain/edit-lock``). The backend performs a
//      non-forcing acquire so the editor keeps the lock across reloads /
//      in-app navigation (the lock is keyed by e-mail, so a plain page
//      change never hands it to someone else);
//   2. when this browser is a VIEWER (``mode == "view"``) it stamps
//      ``body.read-only-locked`` — re-using every gate in
//      ``permissions.css`` — and shows a dismissible banner naming the
//      current editor. App-admins additionally get a "Take over editing"
//      button (``POST /domain/edit-lock/acquire`` with ``force``).
//
// ``window.editLockMode`` ('edit' | 'view' | 'none') is exposed so
// ``permissions.js`` → ``canEditOntology()`` respects the lock too.
//
// Note: the lock is deliberately NOT released on tab close / navigation.
// Because this is a multi-page app, every navigation is a full unload;
// releasing there would briefly free the lock and let another user steal
// it mid-session. The lock is only freed by an explicit Close.
// =====================================================

(function () {
    'use strict';

    // Default permissive until the async check resolves (mirrors
    // ``window.isActiveVersion`` in version-check.js). ``permissions.js``
    // only blocks on an explicit 'view'.
    window.editLockMode = window.editLockMode || 'edit';

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

    function enterReadOnly(data) {
        window.editLockMode = 'view';
        document.body.classList.add('read-only-locked');
        if (window.OB && typeof window.OB.installReadOnlyContextMenuBlocker === 'function') {
            window.OB.installReadOnlyContextMenuBlocker();
        }
        if (window.OB && typeof window.OB.annotateRoleNavBadge === 'function') {
            window.OB.annotateRoleNavBadge(
                'This version is being edited by ' + holderLabel(data) + ' (read-only).'
            );
        }
    }

    function renderBanner(data) {
        if (document.getElementById('editLockBanner')) return;
        var banner = document.createElement('div');
        banner.id = 'editLockBanner';

        var msg = document.createElement('span');
        msg.className = 'edit-lock-msg';
        msg.innerHTML =
            '<i class="bi bi-lock-fill me-1"></i>' +
            'This version is being edited by <strong>' +
            escapeHtml(holderLabel(data)) +
            '</strong> — you have read-only access until they close the domain.';
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

        if (mode === 'view') {
            enterReadOnly(data);
            renderBanner(data);
        }
        // mode === 'edit' → this browser holds the lock; nothing to do.
        // mode === 'none' → not a lockable DRAFT version; nothing to do.
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initEditLock);
    } else {
        initEditLock();
    }
})();
