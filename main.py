from src.agents.data_analyst import DataAnalyst
from src.agents.coach import Coach
from src.agents.sporting_director import SportingDirector
from src.agents.president import President
from src.data_extraction.pipeline import print_step
import os

if __name__ == "__main__":
    
    # --------------------------------------------------------------------------
    # FANTASY CREW ORCHESTRATION
    # --------------------------------------------------------------------------
    
    # 1. Initialize Agents
    analyst = DataAnalyst()
    coach = Coach()
    director = SportingDirector()
    president = President()
    
    try:
        # STEP 1: DATA ANALYST (The Oracle)
        # extract=True (default) -> Extracts fresh data
        # extract=False -> Uses existing CSVs
        df_master = analyst.run(extract=False) # Default to False for Agent Logic testing
        
        if df_master is not None and not df_master.empty:
            print_step(14, "Saving Master Analysis Data")
            df_master.to_csv('df_master_analysis.csv', index=False)
            df_master.to_excel('df_master_analysis.xlsx', index=False)
            
            # STEP 2: COACH (The Mister)
            coach_report = coach.analyze(df_master)
            
            # STEP 3: SPORTING DIRECTOR (The Broker)
            transfer_proposals = director.propose(coach_report, df_master)
            
            # STEP 4: PRESIDENT (The Strategist)
            final_decision = president.decide(transfer_proposals)
            
            # STEP 5: GENERATE FINAL REPORT
            print_step(23, "Generating Final Recommendations Report")
            
            final_report = f"""# 📁 Fantasy Crew: Final Executive Report

## 📋 Coach's Report (The Mister)
{coach_report}

---

## 💼 Sporting Director's Proposals (The Broker)
{transfer_proposals}

---

## 🏛️ President's Decisions (The Strategist)
{final_decision}
"""
            with open("final_recommendations.md", "w") as f:
                f.write(final_report)
                
            print("✅ Report generated: 'final_recommendations.md'")
            
        else:
            print("❌ Could not generate analysis data. Aborting workflow.")
            
    except Exception as e:
        print(f"❌ Fatal Error: {e}")
