"""
LangGraph Graph Builder for Fantasy Crew Multi-Agent System (Simplified Architecture)
========================================================================================

Builds the clean, linear StateGraph that orchestrates the simplified agent workflow.

WORKFLOW:
    ┌──────────────────┐
    │  DataExtraction  │
    │  (Deterministic) │
    └────────┬─────────┘
             │
             ▼
    ┌──────────────────┐
    │      Coach       │
    │(Tactical Analyst)│
    └────────┬─────────┘
             │
             ▼
    ┌──────────────────┐
    │ SportingDirector │
    │(Executive Broker)│
    └────────┬─────────┘
             │
             ▼
    ┌──────────────────┐
    │  ExecuteActions  │
    │  (Biwenger API)  │
    └────────┬─────────┘
             │
             ▼
    ┌──────────────────┐
    │ GenerateReports  │
    └────────┬─────────┘
             │
             ▼
    ┌──────────────────┐
    │    SendEmail     │
    └────────┬─────────┘
             │
             ▼
           [END]
"""

from langgraph.graph import StateGraph, START, END
from src.graph.state import AgentState
from src.graph.nodes import (
    data_extraction_node,
    coach_node,
    sporting_director_node,
    execute_actions_node,
    generate_report_node,
    email_report_node
)


def build_fantasy_crew_graph():
    """
    Builds and compiles the simplified Fantasy Crew LangGraph.
    
    Returns:
        Compiled StateGraph ready for execution.
    """
    graph = StateGraph(AgentState)
    
    # Add nodes
    graph.add_node("data_extraction", data_extraction_node)
    graph.add_node("coach", coach_node)
    graph.add_node("sporting_director", sporting_director_node)
    graph.add_node("execute_actions", execute_actions_node)
    graph.add_node("generate_reports", generate_report_node)
    graph.add_node("send_email", email_report_node)
    
    # Define linear edges (no loops, direct execution)
    graph.add_edge(START, "data_extraction")
    graph.add_edge("data_extraction", "coach")
    graph.add_edge("coach", "sporting_director")
    graph.add_edge("sporting_director", "execute_actions")
    graph.add_edge("execute_actions", "generate_reports")
    graph.add_edge("generate_reports", "send_email")
    graph.add_edge("send_email", END)
    
    return graph.compile()


# Export compiled graph
fantasy_crew_graph = build_fantasy_crew_graph()
