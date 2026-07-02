// =====================================================
// SINGLE-EDITOR EDIT LOCK
//
// Only one browser may *edit* a given DRAFT (domain, version) at a time.
// The first opener acquires the lock (server-side, in ``load-from-uc``);
// later openers land read-only. The lock is held until the editor explicitly
// *closes* the domain (the "Close" button → ``POST /domain/close``), an
// app-admin takes it over, the version leaves DRAFT, or — when a lease TTL is
// configured (``lease_ttl_s > 0``) — its lease lapses.
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
//      button (``POST /domain/edit-lock/acquire`` with ``force``);
//   3. when this browser is the EDITOR (``mode == "edit"``) and a lease TTL
//      is set, a keep-alive timer pings ``POST /domain/edit-lock/renew``
//      every ~TTL/3 (and on tab re-focus) to hold the lease. If a renew
//      reports the lease was lost (reclaimed after going stale), it flips to
//      read-only and shows a "session expired" banner. Hovering the navbar
//      domain badge (#currentDomainName) shows a live countdown of the
//      remaining lease (the time before it would expire if the tab went idle).
//
// ``window.editLockMode`` ('edit' | 'view' | 'none') is exposed so
// ``permissions.js`` → ``canEditOntology()`` respects the lock too.
//
// Note: the lock is deliberately NOT released on tab close / navigation.
// Because this is a multi-page app, every navigation is a full unload;
// releasing there would briefly free the lock and let another user steal
// it mid-session. The lock is only freed by an explicit Close or by the
// lease lapsing after a full TTL with no renew (a crashed / abandoned tab).
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

    function renderExpiredBanner() {
        var existing = document.getElementById('editLockBanner');
        if (existing) existing.remove();
        var banner = document.createElement('div');
        banner.id = 'editLockBanner';

        var msg = document.createElement('span');
        msg.className = 'edit-lock-msg';
        msg.innerHTML =
            '<i class="bi bi-hourglass-bottom me-1"></i>' +
            'Your editing session expired after a period of inactivity — you ' +
            'now have <strong>read-only</strong> access and saving is disabled. ' +
            'Reload to reconnect (you regain editing if no one else has taken over).';
        banner.appendChild(msg);

        var actions = document.createElement('span');
        actions.className = 'edit-lock-actions';

        var reload = document.createElement('button');
        reload.type = 'button';
        reload.className = 'btn btn-sm btn-warning';
        reload.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i>Reload';
        reload.addEventListener('click', function () {
            window.location.reload();
        });
        actions.appendChild(reload);

        banner.appendChild(actions);
        document.body.insertBefore(banner, document.body.firstChild);
    }

    // ----- lease keep-alive (renew) ---------------------------------

    var renewTimer = null;
    // Lease TTL (seconds) and the local time (ms) of the last confirmed renew,
    // used to compute the remaining lease shown in the domain-badge tooltip.
    var leaseTtlS = 0;
    var lastRenewAt = 0;

    function stopRenew() {
        if (renewTimer) {
            clearInterval(renewTimer);
            renewTimer = null;
        }
        document.removeEventListener('visibilitychange', onVisible);
    }

    function onLeaseLost() {
        stopRenew();
        teardownLeaseTooltip();
        window.editLockMode = 'view';
        enterReadOnly({});
        renderExpiredBanner();
    }

    async function doRenew() {
        try {
            var res = await fetch('/domain/edit-lock/renew', {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                credentials: 'same-origin',
            });
            var data = await res.json();
            // Only a definitive "not renewed" (success + renewed === false)
            // means the lease was lost. A transient backend blip
            // (success === false) is ignored — the next tick retries.
            if (data && data.success === true) {
                if (data.renewed === false) {
                    onLeaseLost();
                } else {
                    lastRenewAt = Date.now(); // lease clock reset
                }
            }
        } catch (e) {
            // Network hiccup — keep the timer; the next tick retries.
        }
    }

    function onVisible() {
        if (!document.hidden) doRenew();
    }

    function startRenew(ttlSeconds) {
        var ttl = parseInt(ttlSeconds, 10);
        if (!ttl || ttl <= 0) return; // lease disabled
        leaseTtlS = ttl;
        lastRenewAt = Date.now();
        // Renew at ~a third of the TTL (min 30s) so a couple of missed pings
        // still land inside the lease window.
        var everyMs = Math.max(30, Math.floor(ttl / 3)) * 1000;
        renewTimer = setInterval(doRenew, everyMs);
        document.addEventListener('visibilitychange', onVisible);
        setupLeaseTooltip();
    }

    // ----- lease countdown tooltip on the navbar domain badge -------
    //
    // While this browser is the editor, hovering the navbar domain badge
    // (#currentDomainName) shows how long the edit lease lasts if the session
    // goes idle. It refreshes live (every second) while the tooltip is open.

    var domainTip = null;
    var tipTimer = null;

    function remainingSeconds() {
        if (!leaseTtlS) return 0;
        var elapsed = (Date.now() - lastRenewAt) / 1000;
        return Math.max(0, Math.round(leaseTtlS - elapsed));
    }

    function fmtMMSS(sec) {
        var m = Math.floor(sec / 60);
        var s = sec % 60;
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function leaseTooltipText() {
        return 'Editing lock — auto-renews while open. Expires in ' +
            fmtMMSS(remainingSeconds()) + ' if this tab goes idle.';
    }

    function setupLeaseTooltip() {
        if (domainTip) return;
        var badge = document.getElementById('currentDomainName');
        if (!badge || !window.bootstrap || !window.bootstrap.Tooltip) return;
        domainTip = new window.bootstrap.Tooltip(badge, {
            title: leaseTooltipText, // re-evaluated by Bootstrap on each show
            trigger: 'hover focus',
            placement: 'bottom',
            customClass: 'ob-lease-tooltip',
        });
        badge.addEventListener('shown.bs.tooltip', onTipShown);
        badge.addEventListener('hide.bs.tooltip', onTipHide);
    }

    function onTipShown() {
        if (tipTimer) clearInterval(tipTimer);
        tipTimer = setInterval(function () {
            if (domainTip && typeof domainTip.setContent === 'function') {
                domainTip.setContent({ '.tooltip-inner': leaseTooltipText() });
            }
        }, 1000);
    }

    function onTipHide() {
        if (tipTimer) {
            clearInterval(tipTimer);
            tipTimer = null;
        }
    }

    function teardownLeaseTooltip() {
        onTipHide();
        var badge = document.getElementById('currentDomainName');
        if (badge) {
            badge.removeEventListener('shown.bs.tooltip', onTipShown);
            badge.removeEventListener('hide.bs.tooltip', onTipHide);
        }
        if (domainTip) {
            domainTip.dispose();
            domainTip = null;
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
        } else if (mode === 'edit') {
            // This browser holds the lock — keep its lease alive.
            startRenew(data.lease_ttl_s);
        }
        // mode === 'none' → not a lockable DRAFT version; nothing to do.
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initEditLock);
    } else {
        initEditLock();
    }
})();
