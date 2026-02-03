"""
FANTASY CREW - Multi-Agent Orchestration
=========================================

This script runs the complete Fantasy Crew workflow:
1. Data Analyst: Extracts and consolidates all data into df_master
2. Coach: Analyzes squad and recommends lineup
3. Sporting Director: Proposes market operations
4. President: Makes final decisions

All reports are saved as markdown files in ./reports/
"""

from src.agents.data_analyst import DataAnalyst
from src.agents.coach import Coach
from src.agents.sporting_director import SportingDirector
from src.agents.president import President
from src.data_extraction.pipeline import print_step
from datetime import datetime
import os


def run_fantasy_crew(extract: bool = False):
    """
    Runs the full Fantasy Crew pipeline.
    
    Args:
        extract: If True, downloads fresh data from APIs. 
                 If False, uses existing CSVs.
    """
    print("=" * 60)
    print("🚀 FANTASY CREW - Multi-Agent System")
    print(f"📅 Run Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # Create reports directory
    os.makedirs('./reports', exist_ok=True)
    
    # Initialize Agents
    analyst = DataAnalyst()
    coach = Coach()
    director = SportingDirector()
    president = President()
    
    try:
        # ======================================================================
        # STEP 1: DATA ANALYST (The Oracle)
        # ======================================================================
        df_master = analyst.run(extract=extract)
        
        if df_master is None or df_master.empty:
            print("❌ Could not generate analysis data. Aborting workflow.")
            return
        
        print(f"✅ df_master generated with {len(df_master)} rows.")
        
        # Save master data
        print_step(14, "Saving Master Analysis Data")
        df_master.to_csv('./data/_master.csv', index=False)
        
        # ======================================================================
        # STEP 2: COACH (The Mister)
        # ======================================================================
        coach_report = coach.analyze(df_master)
        
        # Save Coach Report
        with open('./reports/01_coach_report.md', 'w') as f:
            f.write(f"# 📋 Coach Report (The Mister)\n")
            f.write(f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n")
            f.write(coach_report)
        print("📄 Saved: ./reports/01_coach_report.md")
        
        # ======================================================================
        # STEP 3: SPORTING DIRECTOR (The Broker)
        # ======================================================================
        transfer_proposals = director.propose(coach_report, df_master)
        
        # Save Sporting Director Report
        with open('./reports/02_sporting_director_proposals.md', 'w') as f:
            f.write(f"# 💼 Sporting Director Proposals (The Broker)\n")
            f.write(f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n")
            f.write(transfer_proposals)
        print("📄 Saved: ./reports/02_sporting_director_proposals.md")
        
        # ======================================================================
        # STEP 4: PRESIDENT (The Strategist)
        # ======================================================================
        final_decision = president.decide(coach_report, transfer_proposals, df_master)
        
        # Save President Decision
        with open('./reports/03_president_decision.md', 'w') as f:
            f.write(f"# 🏛️ President's Executive Order (The Strategist)\n")
            f.write(f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n")
            f.write(final_decision)
        print("📄 Saved: ./reports/03_president_decision.md")
        
        # ======================================================================
        # STEP 5: CONSOLIDATED FINAL REPORT
        # ======================================================================
        print_step(23, "Generating Final Consolidated Report")
        
        final_report = f"""# 📁 Fantasy Crew: Final Executive Report
_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_

---

## 📋 Coach's Report (The Mister)
{coach_report}

---

## 💼 Sporting Director's Proposals (The Broker)
{transfer_proposals}

---

## 🏛️ President's Decisions (The Strategist)
{final_decision}
"""
        with open('./reports/00_final_report.md', 'w') as f:
            f.write(final_report)
        
        print("=" * 60)
        print("✅ FANTASY CREW RUN COMPLETE!")
        print("=" * 60)
        print("📂 Reports saved in ./reports/:")
        print("   • 00_final_report.md (consolidated)")
        print("   • 01_coach_report.md")
        print("   • 02_sporting_director_proposals.md")
        print("   • 03_president_decision.md")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ Fatal Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Set extract=True to download fresh data from Biwenger API
    # Set extract=False to use existing CSV files in ./data/
    run_fantasy_crew(extract=True)
