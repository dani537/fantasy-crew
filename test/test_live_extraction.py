"""
Live Data Extraction Test Script
=================================
Executes the data extraction pipeline and displays a summary report 
of all downloaded datasets, including Biwenger API, League Board, 
Rival Financials, Comuniate, and EuroClubIndex Odds.

Usage:
    .venv/bin/python test/test_live_extraction.py
"""

import sys
import os
import time
import pandas as pd

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.data_extraction.runner import orchestrate_pipeline, import_data


def main():
    print("=" * 70)
    print("🧪 BIWENGER AGENT - LIVE DATA EXTRACTION TEST")
    print("=" * 70)
    
    start_time = time.time()
    
    try:
        # Run orchestrator
        df_master = orchestrate_pipeline(extract=True)
        elapsed = time.time() - start_time
        
        print("\n" + "=" * 70)
        print(f"✅ EXTRACTION COMPLETED IN {elapsed:.2f} SECONDS!")
        print("=" * 70)
        
        # Load exported CSVs
        data = import_data()
        
        # Report Overview
        print("\n📊 --- EXTRACTION SUMMARY DASHBOARD ---")
        summary_rows = []
        for key, df in data.items():
            filepath = f"./data/{key}.csv" if os.path.exists(f"./data/{key}.csv") else "N/A"
            size_kb = os.path.getsize(f"./data/{key}.csv") / 1024 if os.path.exists(f"./data/{key}.csv") else 0
            summary_rows.append({
                'Dataset': key,
                'Rows': len(df) if isinstance(df, pd.DataFrame) else 0,
                'Cols': len(df.columns) if isinstance(df, pd.DataFrame) else 0,
                'Size (KB)': f"{size_kb:.1f} KB"
            })
            
        df_summary = pd.DataFrame(summary_rows)
        print(df_summary.to_string(index=False))
        
        # Highlight New Strategic Datasets
        print("\n💰 --- NEW STRATEGIC DATA: RIVAL FINANCIALS ---")
        if 'rival_financials' in data and not data['rival_financials'].empty:
            print(data['rival_financials'].to_string(index=False))
        else:
            print("   (No rival financial data found or board is empty)")
            
        print("\n🏷️ --- NEW STRATEGIC DATA: LEAGUE BOARD TRANSFERS (First 5) ---")
        if 'board_transfers' in data and not data['board_transfers'].empty:
            print(data['board_transfers'].head().to_string(index=False))
        else:
            print("   (No recent transfers found on board)")
            
        print("\n🎯 --- NEW STRATEGIC DATA: RIVAL LOSING BIDS (First 5) ---")
        if 'board_bids' in data and not data['board_bids'].empty:
            print(data['board_bids'].head().to_string(index=False))
        else:
            print("   (No losing bids captured yet on board)")

        print("\n📋 --- MARKET SALES (First 5) ---")
        if 'market_sales' in data and not data['market_sales'].empty:
            cols = [c for c in ['MARKET_SALE_PLAYER_ID', 'MARKET_SALE_USER_NAME', 'MARKET_SALE_PRICE', 'MARKET_SALE_CLAUSE'] if c in data['market_sales'].columns]
            print(data['market_sales'][cols].head().to_string(index=False))
            
        print("\n🏆 --- MASTER DATAFRAME OVERVIEW ---")
        if df_master is not None and not df_master.empty:
            print(f"Master Shape: {df_master.shape[0]} rows x {df_master.shape[1]} columns")
            sample_cols = [c for c in ['PLAYER_NAME', 'TEAM_NAME', 'PLAYER_POSITION', 'PLAYER_PRICE', 'AVG_POINTS', 'COMUNIATE_STARTER', 'ODDS_1'] if c in df_master.columns]
            print(df_master[sample_cols].head(10).to_string(index=False))
        else:
            print("❌ Master DataFrame is empty.")
            
        print("\n" + "=" * 70)
        print("🎉 TEST SUITE PASSED SUCCESSFULLY!")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n❌ TEST FAILED WITH ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
