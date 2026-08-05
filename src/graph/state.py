"""
LangGraph State Schema for Fantasy Crew Multi-Agent System
==========================================================

Defines the shared state that flows through all agent nodes.
"""

from typing import TypedDict, Optional
import pandas as pd


class AgentState(TypedDict):
    """
    Shared state for the simplified Fantasy Crew agent workflow.
    """
    # Data
    df_master: Optional[pd.DataFrame]
    
    # Agent outputs
    coach_report: dict
    sd_decisions: dict
    approved_actions: Optional[dict]
    execution_results: Optional[list]
    final_report: Optional[str]
    email_sent: Optional[bool]
    
    # Metadata
    error: Optional[str]

