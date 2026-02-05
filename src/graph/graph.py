"""
LangGraph Graph Builder for Fantasy Crew Multi-Agent System
============================================================

Builds the StateGraph that orchestrates the agent workflow.

WORKFLOW:
    ┌──────────────┐
    │  DataAnalyst │
    │   (START)    │
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │    Coach     │
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │   Sporting   │◄──────┐
    │   Director   │       │
    └──────┬───────┘       │
           │               │
           ▼               │
    ┌──────────────┐       │
    │  President   │───────┘
    │  (Decision)  │ (rejected → loop back)
    └──────┬───────┘
           │ (approved)
           ▼
    ┌──────────────┐
    │   Generate   │
    │   Reports    │
    └──────┬───────┘
           │
           ▼
         [END]
"""

from langgraph.graph import StateGraph, START, END
from src.graph.state import AgentState
from src.graph.nodes import (
    data_analyst_node,
    coach_node,
    sporting_director_node,
    president_node,
    generate_report_node,
    email_report_node
)


def should_continue(state: AgentState) -> str:
    """
    Conditional edge: Determines next step after President decision.
    
    Returns:
        - "generate_reports" if approved or max iterations reached
        - "sporting_director" if rejected (loop back for revision)
    """
    decision_status = state.get("decision_status", "pending")
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", 2)
    
    # If approved or we've hit max iterations, proceed to reports
    if decision_status == "approved" or iteration_count >= max_iterations:
        return "generate_reports"
    
    # If rejected, loop back to Sporting Director
    if decision_status == "rejected":
        return "sporting_director"
    
    # Default: proceed to reports
    return "generate_reports"


def build_fantasy_crew_graph():
    """
    Builds and compiles the Fantasy Crew LangGraph.
    
    Returns:
        Compiled StateGraph ready for execution.
    """
    # Create the graph with our state schema
    graph = StateGraph(AgentState)
    
    # Add nodes
    graph.add_node("data_analyst", data_analyst_node)
    graph.add_node("coach", coach_node)
    graph.add_node("sporting_director", sporting_director_node)
    graph.add_node("president", president_node)
    graph.add_node("generate_reports", generate_report_node)
    graph.add_node("send_email", email_report_node)
    
    # Define edges (workflow)
    graph.add_edge(START, "data_analyst")
    graph.add_edge("data_analyst", "coach")
    graph.add_edge("coach", "sporting_director")
    graph.add_edge("sporting_director", "president")
    
    # Conditional edge from President
    graph.add_conditional_edges(
        "president",
        should_continue,
        {
            "generate_reports": "generate_reports",
            "sporting_director": "sporting_director"
        }
    )
    
    # Reports -> Email -> END
    graph.add_edge("generate_reports", "send_email")
    graph.add_edge("send_email", END)
    
    # Compile and return
    return graph.compile()


# Export the compiled graph
fantasy_crew_graph = build_fantasy_crew_graph()
