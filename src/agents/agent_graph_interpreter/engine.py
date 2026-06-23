"""
Graph Interpreter Agent — engine.

Receives pre-computed graph centrality metrics and calls the LLM in a
tool-enabled loop. The agent may invoke ``get_entity_details`` to look up
specific entities before writing its structured insights.

Output contract: ``AgentResult.sections`` is a list of dicts matching:
    { "title": str, "body": str }            (Key Findings)
    { "title": str, "items": list[dict] }    (Notable Entities)
    { "title": str, "items": list[str] }     (Recommendations)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from back.core.logging import get_logger
from back.core.helpers import URIHelpers
from agents.agent_graph_interpreter.tools import TOOL_DEFINITIONS, TOOL_HANDLERS
from agents.tools.context import ToolContext
from agents.engine_base import (
    AgentStep,
    accumulate_usage,
    call_serving_endpoint,
    dispatch_tool,
)
from agents.tracing import trace_agent

logger = get_logger(__name__)

MAX_ITERATIONS = 8
LLM_TIMEOUT = 120
_TRACE_NAME = "graph_interpreter"


@dataclass
class AgentResult:
    """Outcome of a graph-interpreter run."""

    success: bool
    sections: List[Dict[str, Any]] = field(default_factory=list)
    steps: List[AgentStep] = field(default_factory=list)
    iterations: int = 0
    error: str = ""
    usage: Dict[str, int] = field(default_factory=dict)


_SYSTEM_PROMPT = """\
You are a knowledge-graph analytics expert.
You receive centrality metrics (PageRank, betweenness, degree, closeness, clustering)
for a set of entities in a business knowledge graph.

Your task is to produce concise, actionable insights in the following JSON structure
(and ONLY that structure — no markdown fences, no prose outside the JSON):

{
  "sections": [
    { "title": "Key Findings", "body": "<2-4 sentences summarising the graph structure and standout patterns>" },
    { "title": "Notable Entities", "items": [ { "label": "<entity name>", "reason": "<why it stands out>" } ] },
    { "title": "Recommendations", "items": [ "<action 1>", "<action 2>", "<action 3>" ] }
  ]
}

RULES
- Only mention entities whose labels appear in the provided metrics payload.
- Use the ``get_entity_details`` tool to look up the top-1 or top-2 PageRank nodes
  BEFORE writing your final answer. This grounds your observations in real data.
- If ``zero_metrics`` is non-empty, explain what that means in Key Findings
  (e.g. clustering = 0 is typical for bipartite graphs).
- If ``flat_entity_types`` is present, mention those types in Key Findings and
  recommend in Recommendations that they be excluded from graph sync or aggregated
  (e.g. store counts instead of individual rows).
- If ``top_pagerank`` is empty, state that no ranked entities are available.
- Be specific: mention entity names, scores, and structural observations.
- Keep "Notable Entities" to at most 5 items.
- Keep "Recommendations" to 2-4 actionable bullet points.
"""


def _build_user_message(payload: Dict[str, Any]) -> str:
    stats = payload.get("stats", {})
    nodes = payload.get("nodes", {})
    node_labels = payload.get("node_labels", {})
    class_filter = payload.get("class_filter") or []
    entity_type = (
        URIHelpers.extract_local_name(class_filter[0]) if class_filter else None
    )

    top_nodes = sorted(
        nodes.items(), key=lambda x: x[1].get("pagerank", 0), reverse=True
    )[:10]
    top_rows = []
    for uri, m in top_nodes:
        label = node_labels.get(uri, URIHelpers.extract_local_name(uri))
        top_rows.append({
            "uri": uri,
            "label": label,
            "pagerank": round(m.get("pagerank", 0), 6),
            "degree": round(m.get("degree", 0), 6),
            "betweenness": round(m.get("betweenness", 0), 6),
            "closeness": round(m.get("closeness", 0), 6),
            "clustering": round(m.get("clustering", 0), 6),
        })

    zero_metrics = [
        key
        for key in ("pagerank", "degree", "betweenness", "closeness", "clustering")
        if nodes and all(n.get(key, 0) == 0 for _, n in nodes.items())
    ]

    # Flat / time-series types flagged by the backend heuristic
    flat_types: list[str] = []
    for profile in (payload.get("entity_type_profiles") or {}).values():
        if profile.get("is_flat"):
            flat_types.append(
                URIHelpers.extract_local_name(profile.get("uri", ""))
                or profile.get("uri", "")
            )

    summary = {
        "entity_type": entity_type,
        "class_filter_applied": bool(class_filter),
        "stats": {
            "node_count": stats.get("node_count"),
            "graph_node_count": stats.get("graph_node_count"),
            "edge_count": stats.get("edge_count"),
            "connected_components": stats.get("connected_components"),
            "avg_degree": stats.get("avg_degree"),
            "density": stats.get("density"),
        },
        "top_pagerank": top_rows,
        "zero_metrics": zero_metrics,
    }
    if flat_types:
        summary["flat_entity_types"] = flat_types

    return (
        "Analyze the following graph centrality metrics and return insights.\n\n"
        + json.dumps(summary, indent=2)
    )


def _parse_sections(content: str) -> List[Dict[str, Any]]:
    """Extract sections list from the LLM text content."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    try:
        return json.loads(text).get("sections", [])
    except json.JSONDecodeError as exc:
        logger.warning("_parse_sections: JSON decode failed (%s); returning raw text", exc)
        return [{"title": "Interpretation", "body": text}]


@trace_agent(name=_TRACE_NAME)
def run_agent(
    host: str,
    token: str,
    endpoint_name: str,
    metrics_payload: Dict[str, Any],
    base_url: str,
    domain_name: str,
    session_cookies: Dict[str, str],
    session_headers: Optional[Dict[str, str]] = None,
    on_step: Optional[Callable[[str], None]] = None,
) -> AgentResult:
    """Run one interpretation turn for a metrics payload.

    Args:
        host, token, endpoint_name: Databricks serving-endpoint credentials.
        metrics_payload: The full JSON returned by ``/dtwin/metrics/compute``
            plus an optional ``class_filter`` list.
        base_url: Loopback OntoBricks URL, e.g. ``http://localhost:8000``.
        domain_name: Active domain name (forwarded to ToolContext).
        session_cookies: User session cookies forwarded to loopback routes.
        session_headers: Optional Databricks-Apps identity headers.
        on_step: Optional progress callback.
    """
    logger.info(
        "===== GRAPH INTERPRETER START ===== endpoint=%s domain=%s",
        endpoint_name,
        domain_name,
    )

    ctx = ToolContext(
        host=host.rstrip("/") if host else "",
        token=token or "",
        dtwin_base_url=base_url,
        dtwin_session_cookies=session_cookies or {},
        dtwin_session_headers=session_headers or {},
        dtwin_domain_name=domain_name or "",
    )

    result = AgentResult(success=False)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_message(metrics_payload)},
    ]

    for iteration in range(MAX_ITERATIONS):
        result.iterations = iteration + 1
        is_last = iteration == MAX_ITERATIONS - 1
        send_tools = TOOL_DEFINITIONS if not is_last else None

        if on_step:
            on_step(f"Iteration {iteration + 1}…")

        try:
            llm_response = call_serving_endpoint(
                host,
                token,
                endpoint_name,
                messages,
                tools=send_tools,
                max_tokens=1024,
                temperature=0.1,
                timeout=LLM_TIMEOUT,
                trace_name=_TRACE_NAME,
            )
        except Exception as exc:
            error_msg = f"LLM request failed: {exc}"
            logger.error("graph_interpreter: %s at iteration %d", error_msg, iteration + 1)
            result.error = error_msg
            return result

        accumulate_usage(result.usage, llm_response.get("usage", {}))

        choices = llm_response.get("choices", [])
        if not choices:
            result.error = "No choices in LLM response"
            return result

        message = choices[0].get("message", {})
        content = message.get("content", "") or ""
        tool_calls = message.get("tool_calls")

        if tool_calls:
            messages.append(message)

            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                tool_id = tc.get("id", "")
                raw_args = func.get("arguments", "{}")

                try:
                    arguments = (
                        json.loads(raw_args)
                        if isinstance(raw_args, str)
                        else raw_args
                    )
                except json.JSONDecodeError:
                    arguments = {}

                logger.info(
                    "graph_interpreter: iteration %d — tool_call '%s'",
                    iteration + 1,
                    tool_name,
                )

                call_step = AgentStep(
                    step_type="tool_call",
                    content=json.dumps(arguments, default=str),
                    tool_name=tool_name,
                )
                result.steps.append(call_step)

                t0 = time.time()
                tool_result = dispatch_tool(
                    TOOL_HANDLERS, ctx, tool_name, arguments,
                    trace_name=_TRACE_NAME,
                )
                tool_elapsed = int((time.time() - t0) * 1000)

                result.steps.append(AgentStep(
                    step_type="tool_result",
                    content=tool_result[:500],
                    tool_name=tool_name,
                    duration_ms=tool_elapsed,
                ))

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": tool_result,
                })
        else:
            result.success = True
            result.sections = _parse_sections(content)
            result.steps.append(AgentStep(step_type="output", content=content[:500]))
            logger.info(
                "===== GRAPH INTERPRETER DONE ===== iterations=%d sections=%d",
                result.iterations,
                len(result.sections),
            )
            return result

    result.error = "Max iterations reached without a final answer"
    return result
