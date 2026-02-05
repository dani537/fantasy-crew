"""
LangGraph State Schema for Fantasy Crew Multi-Agent System
==========================================================

Defines the shared state that flows through all agent nodes.
"""

from typing import TypedDict, Optional
import pandas as pd


class AgentState(TypedDict):
    """
    Shared state for the Fantasy Crew agent workflow.
    
    This state is passed between all nodes in the graph and accumulates
    the results from each agent's analysis.
    """
    # Data
    df_master: Optional[pd.DataFrame]
    
    # Agent outputs
    coach_report: str
    sd_proposals: str
    president_decision: str
    
    # Control flow
    iteration_count: int
    max_iterations: int
    decision_status: str  # "approved", "rejected", "pending"
    
    # Metadata
    error: Optional[str]
