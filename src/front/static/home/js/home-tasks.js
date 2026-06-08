/**
 * Home → My Tasks
 *
 * Compact review worklist surfaced on the home page, just below the
 * Current Domain panel. Mirrors Registry → My Tasks but only reveals
 * itself when the current user actually has pending review tasks.
 *
 * Data source: GET /review/my-tasks (see ReviewService.my_tasks).
 */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', loadTasks);

    async function loadTasks() {
        const section = document.getElementById('homeTasksSection');
        const container = document.getElementById('homeTasksContainer');
        if (!section || !container) return;

        try {
            const resp = await fetch('/review/my-tasks', {
                credentials: 'same-origin',
            });
            const data = await resp.json();
            const tasks = (resp.ok && data.success && data.tasks) ? data.tasks : [];
            if (!tasks.length) {
                section.style.display = 'none';
                return;
            }
            render(container, tasks);
            section.style.display = '';
        } catch (err) {
            // Home page must stay usable even if the review API is down.
            console.error('home loadTasks error:', err);
            section.style.display = 'none';
        }
    }

    function render(container, tasks) {
        const rows = tasks.map((t) => {
            const actions = (t.actions || [])
                .map((a) => actionButton(t, a))
                .join(' ');
            return '<tr>' +
                '<td class="fw-medium">' + escapeHtml(t.domain) + '</td>' +
                '<td>v' + escapeHtml(t.version) + '</td>' +
                '<td>' + statusBadge(t.status) + '</td>' +
                '<td><span class="my-tasks-approvals">' +
                t.approvals + ' / ' + t.required + '</span></td>' +
                '<td class="text-end">' + commentsButton(t) + ' ' + actions +
                '</td></tr>';
        }).join('');

        container.innerHTML =
            '<div class="table-responsive">' +
            '<table class="table table-sm align-middle my-tasks-table mb-0">' +
            '<thead><tr>' +
            '<th>Domain</th><th>Version</th><th>Status</th>' +
            '<th>Approvals</th><th class="text-end">Your action</th>' +
            '</tr></thead><tbody>' + rows + '</tbody></table></div>';

        container.querySelectorAll('button[data-action]').forEach((btn) => {
            btn.addEventListener('click', onAction);
        });
        container.querySelectorAll('button[data-comments]').forEach((btn) => {
            btn.addEventListener('click', () => {
                ReviewModals.showComments(btn.dataset.domain, btn.dataset.version);
            });
        });
    }

    function commentsButton(task) {
        return '<button type="button" class="btn btn-sm btn-outline-secondary ms-1" ' +
            'data-comments="1" ' +
            'data-domain="' + escapeAttr(task.domain) + '" ' +
            'data-version="' + escapeAttr(task.version) + '" ' +
            'title="View all comments">' +
            '<i class="bi bi-chat-dots"></i></button>';
    }

    function actionButton(task, action) {
        const cls = action.id === 'publish'
            ? 'btn-success'
            : (action.id === 'review' ? 'btn-primary' : 'btn-outline-secondary');
        const icon = action.id === 'publish'
            ? 'broadcast'
            : (action.id === 'review' ? 'patch-check' : 'send');
        return '<button type="button" class="btn btn-sm ' + cls + ' ms-1" ' +
            'data-action="' + escapeAttr(action.id) + '" ' +
            'data-domain="' + escapeAttr(task.domain) + '" ' +
            'data-version="' + escapeAttr(task.version) + '">' +
            '<i class="bi bi-' + icon + ' me-1"></i>' +
            escapeHtml(action.label) + '</button>';
    }

    async function onAction(e) {
        const btn = e.currentTarget;
        const action = btn.dataset.action;
        const domain = btn.dataset.domain;
        const version = btn.dataset.version;

        if (action === 'review') {
            await loadDomainAndReview(domain, version);
            return;
        }
        if (action === 'submit') {
            await transition(domain, version, 'submit', {
                title: 'Submit for review',
                message: 'Submit <strong>' + escapeHtml(domain) + '</strong> v' +
                    escapeHtml(version) + ' for review? Editing locks until it is returned to Draft.',
                confirmText: 'Submit',
                icon: 'eye',
            });
            return;
        }
        if (action === 'publish') {
            await transition(domain, version, 'publish', {
                title: 'Publish version',
                message: 'Publish <strong>' + escapeHtml(domain) + '</strong> v' +
                    escapeHtml(version) + '? It becomes the live version on the API/MCP surface.',
                confirmText: 'Publish',
                icon: 'broadcast',
            });
        }
    }

    async function transition(domain, version, endpoint, dialog) {
        const r = await ReviewModals.promptComment({
            title: dialog.title,
            message: dialog.message,
            confirmText: dialog.confirmText,
            confirmClass: 'btn-primary',
            icon: dialog.icon,
        });
        if (!r.confirmed) return;

        try {
            const resp = await fetch(
                '/review/' + encodeURIComponent(domain) + '/' +
                encodeURIComponent(version) + '/' + endpoint,
                {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ comment: r.comment }),
                }
            );
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                showNotification(data.message || 'Action failed', 'error');
                return;
            }
            showNotification(
                domain + ' v' + version + ' is now ' + (data.status || '') + '.',
                'success'
            );
            loadTasks();
        } catch (err) {
            showNotification('Error: ' + err.message, 'error');
        }
    }

    async function loadDomainAndReview(domain, version) {
        try {
            showNotification('Opening ' + domain + ' v' + version + '…', 'info', 4000);
            const resp = await fetch('/domain/load-from-uc', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ domain: domain, version: version }),
            });
            const data = await resp.json();
            if (!data.success) {
                showNotification('Error: ' + (data.message || 'Failed to load domain'), 'error');
                return;
            }
            window.location.href = '/domain/?section=review';
        } catch (err) {
            showNotification('Error loading domain: ' + err.message, 'error');
        }
    }

    function statusBadge(status) {
        const map = {
            'DRAFT': 'bg-secondary',
            'IN-REVIEW': 'bg-warning text-dark',
            'PUBLISHED': 'bg-success',
        };
        const cls = map[status] || 'bg-secondary';
        const label = status === 'IN-REVIEW' ? 'In Review'
            : (status.charAt(0) + status.slice(1).toLowerCase());
        return '<span class="badge ' + cls + '">' + escapeHtml(label) + '</span>';
    }

    function escapeHtml(text) {
        if (text == null) return '';
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    function escapeAttr(text) {
        return escapeHtml(text).replace(/"/g, '&quot;');
    }
})();
