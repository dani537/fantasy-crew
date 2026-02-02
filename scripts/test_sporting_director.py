import pandas as pd
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.agents.sporting_director import SportingDirector

def test_sporting_director():
    print("🧪 Testing Sporting Director Agent...")
    
    # Mock Data
    coach_report = """
    ## 📋 Squad Status
    Critical need for a Midfielder.
    
    ## 🚨 Urgent Needs
    WE NEED MIDFIELDER (MC)
    
    ## 💰 Market Strategy (For Sale)
    1. Gumbau - REAL - Not performing.
    """
    
    try:
        df_master = pd.read_csv('df_master_analysis.csv')
    except:
        print("❌ master data not found, cannot test properly.")
        return

    # Initialize
    director = SportingDirector()
    
    # Run
    # Note: We are running the REAL generate_content here as user requested no dry_run.
    # But for a quick test script, we might want to check if logic breaks.
    # The actual prompt generation is inside 'propose'.
    
    proposals = director.propose(coach_report, df_master)
    
    print("\n--- Result ---")
    print(proposals)

if __name__ == "__main__":
    test_sporting_director()
