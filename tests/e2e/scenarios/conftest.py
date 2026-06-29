"""Auto-tag every test under ``tests/e2e/scenarios/`` with ``scenario``.

The scenario suites are long, full-journey walks (and, for the ``*_live``
ones, billable: warehouse + LLM + a durable registry write). They are
**excluded from routine and agent test runs** and only execute on explicit
request — see ``.cursor/08-testing-and-deployment.mdc``.

Run them with::

    uv run pytest -m scenario              # all scenarios
    uv run pytest tests/e2e/scenarios/ -m scenario

The live scenario additionally needs ``ONTOBRICKS_SCENARIO_LIVE=1``.
"""

from __future__ import annotations

import pathlib

import pytest

_SCENARIOS_DIR = pathlib.Path(__file__).parent.resolve()


def pytest_collection_modifyitems(items):
    """Mark tests collected from this directory as ``scenario``.

    A sub-directory ``conftest`` hook still receives the *whole* session's
    item list, so we must filter to items whose file lives under this
    directory before tagging them.
    """
    for item in items:
        item_path = pathlib.Path(str(getattr(item, "path", item.fspath))).resolve()
        if _SCENARIOS_DIR in item_path.parents or item_path == _SCENARIOS_DIR:
            item.add_marker(pytest.mark.scenario)
