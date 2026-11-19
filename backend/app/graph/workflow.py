"""LangGraph definition for the canonical workflow.

START -> validate_and_snapshot -> autopsy -> characterization
  INSPECT -> complete_inspect -> END
  otherwise roadmap -> generate_patches
    PREVIEW -> complete_preview -> END
    REVIVE, nothing patchable -> complete_no_patches -> END
    REVIVE -> request_approval -> await_decision (interrupt)
        approve -> reanimation -> consecration -> END
        edit    -> revise -> generate_patches -> request_approval
        reject  -> END
Every node is guarded: a cancelled run skips remaining work and ends.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy

from app.graph.nodes import Nodes
from app.graph.state import WorkflowContext, WorkflowState

# Bounded retries, only for transient I/O. Deterministic failures (WorkflowError) are not retried.
TRANSIENT_RETRY = RetryPolicy(
    max_attempts=2, initial_interval=0.5, retry_on=(OSError, TimeoutError)
)


def _after_characterization(state: WorkflowState) -> str:
    if state.get("halt"):
        return END
    return "complete_inspect" if state["mode"] == "INSPECT" else "roadmap"


def _after_patches(state: WorkflowState) -> str:
    if state.get("halt"):
        return END
    if state["mode"] == "PREVIEW":
        return "complete_preview"
    return "request_approval" if state["patchset"]["patches"] else "complete_no_patches"


def _after_decision(state: WorkflowState) -> str:
    if state.get("halt"):
        return END
    decision = state.get("decision", {}).get("decision")
    return {"approve": "reanimation", "edit": "revise"}.get(decision, END)


def _next(target: str):  # noqa: ANN202
    return lambda state: END if state.get("halt") else target


def build_workflow(ctx: WorkflowContext, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    n = Nodes(ctx)
    g = StateGraph(WorkflowState)
    guarded = n.guarded
    for name in ("validate_and_snapshot", "autopsy", "characterization"):
        g.add_node(name, guarded(getattr(n, name)), retry_policy=TRANSIENT_RETRY)
    for name in (
        "complete_inspect",
        "roadmap",
        "generate_patches",
        "complete_preview",
        "complete_no_patches",
        "request_approval",
        "await_decision",
        "revise",
        "reanimation",
        "consecration",
    ):
        g.add_node(name, guarded(getattr(n, name)))

    g.add_edge(START, "validate_and_snapshot")
    g.add_conditional_edges("validate_and_snapshot", _next("autopsy"))
    g.add_conditional_edges("autopsy", _next("characterization"))
    g.add_conditional_edges("characterization", _after_characterization)
    g.add_edge("complete_inspect", END)
    g.add_conditional_edges("roadmap", _next("generate_patches"))
    g.add_conditional_edges("generate_patches", _after_patches)
    g.add_edge("complete_preview", END)
    g.add_edge("complete_no_patches", END)
    g.add_conditional_edges("request_approval", _next("await_decision"))
    g.add_conditional_edges("await_decision", _after_decision)
    g.add_conditional_edges("revise", _next("generate_patches"))
    g.add_conditional_edges("reanimation", _next("consecration"))
    g.add_edge("consecration", END)
    return g.compile(checkpointer=checkpointer)
