"""LangGraph definition for the canonical workflow.

START -> validate_and_snapshot -> autopsy -> characterization
  INSPECT -> complete_inspect -> END
  PREVIEW -> roadmap -> complete_preview -> END
  REVIVE  -> roadmap -> request_approval -> await_decision (interrupt)
               approve -> reanimation_unavailable (Reanimation arrives in task 7) -> END
               edit    -> revise -> request_approval
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


def _after_roadmap(state: WorkflowState) -> str:
    if state.get("halt"):
        return END
    return "complete_preview" if state["mode"] == "PREVIEW" else "request_approval"


def _after_decision(state: WorkflowState) -> str:
    if state.get("halt"):
        return END
    decision = state.get("decision", {}).get("decision")
    return {"approve": "reanimation_unavailable", "edit": "revise"}.get(decision, END)


def _next(target: str):  # noqa: ANN202
    return lambda state: END if state.get("halt") else target


def build_workflow(ctx: WorkflowContext, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    n = Nodes(ctx)
    g = StateGraph(WorkflowState)
    guarded = n.guarded
    g.add_node(
        "validate_and_snapshot", guarded(n.validate_and_snapshot), retry_policy=TRANSIENT_RETRY
    )
    g.add_node("autopsy", guarded(n.autopsy), retry_policy=TRANSIENT_RETRY)
    g.add_node("characterization", guarded(n.characterization), retry_policy=TRANSIENT_RETRY)
    g.add_node("complete_inspect", guarded(n.complete_inspect))
    g.add_node("roadmap", guarded(n.roadmap))
    g.add_node("complete_preview", guarded(n.complete_preview))
    g.add_node("request_approval", guarded(n.request_approval))
    g.add_node("await_decision", guarded(n.await_decision))
    g.add_node("revise", guarded(n.revise))
    g.add_node("reanimation_unavailable", guarded(n.reanimation_unavailable))

    g.add_edge(START, "validate_and_snapshot")
    g.add_conditional_edges("validate_and_snapshot", _next("autopsy"))
    g.add_conditional_edges("autopsy", _next("characterization"))
    g.add_conditional_edges("characterization", _after_characterization)
    g.add_edge("complete_inspect", END)
    g.add_conditional_edges("roadmap", _after_roadmap)
    g.add_edge("complete_preview", END)
    g.add_conditional_edges("request_approval", _next("await_decision"))
    g.add_conditional_edges("await_decision", _after_decision)
    g.add_conditional_edges("revise", _next("request_approval"))
    g.add_edge("reanimation_unavailable", END)
    return g.compile(checkpointer=checkpointer)
