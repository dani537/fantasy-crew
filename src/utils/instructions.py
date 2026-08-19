"""
Google Sheets User Instructions Loader
======================================
Loads human manager instructions and suggestions from the 'Instrucciones' sheet in Google Sheets,
caches them locally, and filters them based on the target recipient ('coach' vs 'agent').
"""

import os
import gspread
import pandas as pd
from typing import List, Optional

from src.config import GeneralSettings


LOCAL_INSTRUCTIONS_PATH = "./data/instructions.csv"


def load_instructions_from_sheets() -> pd.DataFrame:
    """
    Attempts to fetch instructions from Google Sheets 'Instrucciones' worksheet.
    Caches the results locally to ./data/instructions.csv for offline resilience.
    """
    sheet_id = GeneralSettings.GOOGLE_SHEET_ID
    creds_path = GeneralSettings.GOOGLE_SERVICE_ACCOUNT_FILE

    if sheet_id and creds_path and os.path.exists(creds_path):
        try:
            gc = gspread.service_account(filename=creds_path)
            sh = gc.open_by_key(sheet_id)
            
            # Find worksheet with 'instruc' in name
            target_ws = None
            for ws in sh.worksheets():
                if "instruc" in ws.title.lower():
                    target_ws = ws
                    break

            if target_ws is not None:
                records = target_ws.get_all_records()
                if records:
                    df = pd.DataFrame(records)
                    os.makedirs(os.path.dirname(LOCAL_INSTRUCTIONS_PATH), exist_ok=True)
                    df.to_csv(LOCAL_INSTRUCTIONS_PATH, index=False)
                    return df
        except Exception as e:
            print(f"⚠️ Aviso al leer 'Instrucciones' de Google Sheets: {e}")

    # Fallback to local cached file if available
    if os.path.exists(LOCAL_INSTRUCTIONS_PATH):
        try:
            return pd.read_csv(LOCAL_INSTRUCTIONS_PATH)
        except Exception:
            pass

    return pd.DataFrame()


def get_instructions_for_recipient(target_role: str = "coach") -> List[str]:
    """
    Retrieves and filters instructions based on the recipient role.
    
    Roles:
      - 'coach': receives messages addressed to 'all' or 'coach'
      - 'agent': receives messages addressed to 'all', 'agent', or 'coach' (the agent sees everything)
    """
    df = load_instructions_from_sheets()
    if df.empty:
        return []

    # Identify columns
    receiver_col = None
    instruction_col = None
    for c in df.columns:
        c_low = str(c).strip().lower()
        if c_low in ("receiver", "destinatario", "destinatarios", "to"):
            receiver_col = c
        elif c_low in ("instructions", "instrucciones", "instruccion", "mensaje", "texto", "sugerencia"):
            instruction_col = c

    if not instruction_col:
        # Fallback to last column
        instruction_col = df.columns[-1]

    filtered_texts = []
    target_clean = target_role.strip().lower()

    for _, row in df.iterrows():
        text = str(row.get(instruction_col, "")).strip()
        if not text or text.lower() in ("nan", "none", ""):
            continue

        raw_rec = str(row.get(receiver_col, "all")).strip().lower() if receiver_col else "all"

        if target_clean == "coach":
            # Coach only sees 'all' or 'coach' (never 'agent'-only private directives)
            if raw_rec in ("all", "coach", "todos", "mister", "el mister"):
                filtered_texts.append(text)
        elif target_clean == "agent":
            # Agent sees everything ('all', 'agent', 'coach')
            if raw_rec in ("all", "agent", "coach", "todos", "agente", "mister", "el mister", "director deportivo"):
                filtered_texts.append(text)
        else:
            filtered_texts.append(text)

    return filtered_texts


def format_instructions_for_prompt(target_role: str = "coach") -> str:
    """
    Formats the filtered instructions as a clean Markdown bullet list.
    """
    instructions = get_instructions_for_recipient(target_role)
    if not instructions:
        return ""
    
    lines = [f"- {inst}" for inst in instructions]
    return "\n".join(lines)
