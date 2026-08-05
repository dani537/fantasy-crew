import pandas as pd
from dotenv import load_dotenv
import os
import io
from contextlib import redirect_stdout

# Imports for data extraction
from src.data_extraction.auth import BiwengerAuth
from src.data_extraction.biwenger_data import BiwengerGeneralData, UserLeagueData
from src.data_extraction.external_data import ComuniateData, JornadaPerfectaData, EuroClubIndexData

# Imports for data transformation
from src.data_extraction.transformers import (
    print_step,
    rename_and_normalize_columns,
    process_comuniate,
    process_odds,
    consolidate_player_data,
    feature_engineering
)

# Config
from src.config import Credentials

def extract_and_save_data():
    """
    Simulates the data extraction process.
    Authenticates with Biwenger and fetches all required data, saving it to CSVs.
    """
    load_dotenv()

    missing = Credentials.validate()
    if missing:
        raise RuntimeError(f"Missing required credentials in .env: {', '.join(missing)}")

    # Suppress output from imported modules to reduce noise
    f = io.StringIO()
    
    try:
        # Authentication
        print_step(1, "Authenticating with Biwenger")
        with redirect_stdout(f):
            auth = BiwengerAuth(email=Credentials.BIWENGER_USERNAME, password=Credentials.BIWENGER_PASSWORD)
            auth.run()
        
        # Save User Info/Metadata
        print_step(1.5, "Saving User Info metadata")
        if auth.player_info:
            user_info_dict = {
                'user_id': [auth.player_info.user_id],
                'user_name': [auth.player_info.user_name],
                'league_id': [auth.player_info.league_id],
                'league_name': [auth.player_info.league_name],
                'team_id': [auth.player_info.team_id],
                'team_name': [auth.player_info.team_name],
                'balance': [auth.player_info.balance]
            }
            df_user_info = pd.DataFrame(user_info_dict)
            os.makedirs('./data', exist_ok=True)
            df_user_info.to_csv('./data/user_info.csv', index=False)

        # LaLiga General Data
        print_step(2, f"Extracting General Data for Competition: {auth.player_info.competition_slug}")
        with redirect_stdout(f):
            general_data = BiwengerGeneralData(auth.session, competition_slug=auth.player_info.competition_slug)
            general_data.run()
            season_info = general_data.season_info()

        # Fantasy League Data
        print_step(3, "Extracting User League Data (Table, Market, My Players)")
        with redirect_stdout(f):
            user_league_data = UserLeagueData(session=auth.session, token=auth.token, league_id=auth.player_info.league_id, user_id=auth.player_info.team_id)
            user_league_data.run(auth.session)

        # External Data Breakdown
        print_step(4, "Extracting External Data: Comuniate (Lineups & Status)")
        with redirect_stdout(f):
            comuniate = ComuniateData()
            df_comuniate = comuniate.run()

        print_step(5, "Extracting External Data: Jornada Perfecta (News)")
        with redirect_stdout(f):
            jp = JornadaPerfectaData()
            df_news = jp.run()

        print_step(6, "Extracting External Data: EuroClubIndex (Odds)")
        with redirect_stdout(f):
            eci = EuroClubIndexData()
            df_odds = eci.run()

        # Saving to CSVs
        print_step(7, "Saving extracted data to CSVs")
        os.makedirs('./data', exist_ok=True)
        
        general_data.df_players.to_csv('./data/players.csv', index=False)
        general_data.df_teams.to_csv('./data/teams.csv', index=False)
        general_data.df_next_jornada.to_csv('./data/next_jornada.csv', index=False)
        
        pd.DataFrame(season_info.rounds).to_csv('./data/rounds.csv', index=False)
        
        active_events_list = []
        for event in season_info.active_events:
            active_events_list.append({
                'id': event.id,
                'name': event.name,
                'status': event.status,
                'end': event.end,
                'type': event.type
            })
        # Always write with proper columns, even when there are no active events
        pd.DataFrame(active_events_list, columns=['id', 'name', 'status', 'end', 'type']).to_csv('./data/active_events.csv', index=False)

        user_league_data.df_league_players.to_csv('./data/league_players.csv', index=False)
        user_league_data.df_league_table.to_csv('./data/league_teams.csv', index=False)
        user_league_data.df_market_offers.to_csv('./data/market_offers.csv', index=False)
        user_league_data.df_market_sales.to_csv('./data/market_sales.csv', index=False)

        if hasattr(user_league_data, 'df_board_transfers'):
            user_league_data.df_board_transfers.to_csv('./data/board_transfers.csv', index=False)
        if hasattr(user_league_data, 'df_board_bids'):
            user_league_data.df_board_bids.to_csv('./data/board_bids.csv', index=False)
        if hasattr(user_league_data, 'df_rival_financials'):
            user_league_data.df_rival_financials.to_csv('./data/rival_financials.csv', index=False)

        df_comuniate.to_csv('./data/comuniate.csv', index=False)
        df_news.to_csv('./data/news.csv', index=False)
        df_odds.to_csv('./data/odds.csv', index=False)
        
        print("✅ Data extraction pipeline completed successfully.")
        
    except Exception as e:
        print(f"❌ Error during extraction: {e}")
        print("--- Detailed Logs ---")
        print(f.getvalue())
        raise e

def import_data():
    """
    Reads the CSV files generated by extraction.
    """
    print_step(8, "Importing data from CSVs")
    files = {
        'players': './data/players.csv',
        'teams': './data/teams.csv',
        'next_match': './data/next_jornada.csv',
        'league_players': './data/league_players.csv',
        'league_teams': './data/league_teams.csv',
        'market_offers': './data/market_offers.csv',
        'market_sales': './data/market_sales.csv',
        'board_transfers': './data/board_transfers.csv',
        'board_bids': './data/board_bids.csv',
        'rival_financials': './data/rival_financials.csv',
        'comuniate': './data/comuniate.csv',
        'news': './data/news.csv',
        'odds': './data/odds.csv',
        'user_info': './data/user_info.csv',
        'rounds': './data/rounds.csv',
        'active_events': './data/active_events.csv'
    }
    
    imported_data = {}
    for name, path in files.items():
        if os.path.exists(path):
            try:
                if os.path.getsize(path) < 5: 
                     imported_data[name] = pd.DataFrame()
                else:
                    imported_data[name] = pd.read_csv(path)
            except pd.errors.EmptyDataError:
                imported_data[name] = pd.DataFrame() 
            except Exception as e:
                imported_data[name] = pd.DataFrame()
        else:
            imported_data[name] = pd.DataFrame()
            
    return imported_data

def orchestrate_pipeline(extract: bool = True) -> pd.DataFrame:
    """
    Orchestrates the data extraction and transformation pipeline.
    
    Args:
        extract (bool): If True, runs the data extraction process. 
                        If False, skips extraction and loads data from existing CSVs.
    
    Returns:
        pd.DataFrame: The final master dataframe ready for analysis.
    """
    if extract:
        extract_and_save_data()
    
    # 1. Load Data
    data = import_data()
    
    # 2. Rename and Normalize Data
    data = rename_and_normalize_columns(data)
    
    # 3. Process Auxiliary Data
    data = process_comuniate(data)
    data = process_odds(data)
    
    # 4. Consolidate Player Data
    df_players_total = consolidate_player_data(data)
    
    # 5. Feature Engineering
    if df_players_total is not None and not df_players_total.empty:
        df_master = feature_engineering(df_players_total)
        print_step("T6", "Saving Master DataFrames")
        df_master.to_csv('./data/_master.csv', index=False)
        df_master.to_excel('./data/_master.xlsx', index=False)
        return df_master
    
    return df_players_total
