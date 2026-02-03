import pandas as pd
from dotenv import load_dotenv
import os
import io
from contextlib import redirect_stdout

# Imports for data extraction
from src.data_extraction.auth import BiwengerAuth
from src.data_extraction.biwenger_data import LaLigaGeneralData, UserLeagueData
from src.data_extraction.external_data import ComuniateData, JornadaPerfectaData, EuroClubIndexData

# Config
from src.config import Credentials

def print_step(step_number, message, status="running"):
    """
    Helper to print structured step messages.
    Status can be 'running', 'done', 'error'.
    """
    if status == "running":
        print(f"\n🚀 STEP {step_number}: {message}...")
    elif status == "done":
        print(f"✅ STEP {step_number}: Done.")
    elif status == "error":
        print(f"❌ STEP {step_number}: Error!")

def extract_and_save_data():
    """
    Simulates the data extraction process from main.ipynb.
    Authenticates with Biwenger and fetches all required data, saving it to CSVs.
    """
    load_dotenv()

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
            # Convert dataclass to dict
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
        print_step(2, "Extracting LaLiga General Data (Players, Teams, Next Match, Season)")
        with redirect_stdout(f):
            laliga_data = LaLigaGeneralData(auth.session)
            laliga_data.run()
            season_info = laliga_data.season_info()

        # Fantasy League Data
        print_step(3, "Extracting User League Data (Table, Market, My Players)")
        with redirect_stdout(f):
            user_league_data = UserLeagueData(session=auth.session, token=auth.token, league_id=auth.player_info.league_id, user_id=auth.player_info.team_id)
            user_league_data.run(auth.session)

        # External Data Breakdown
        print_step(4, "Extracting External Data: Comuniate (Lineups & Status)")
        with redirect_stdout(f):
            comuniate = ComuniateData(session=auth.session)
            df_comuniate = comuniate.run()

        print_step(5, "Extracting External Data: Jornada Perfecta (News)")
        with redirect_stdout(f):
            jp = JornadaPerfectaData(session=auth.session)
            df_news = jp.run()

        print_step(6, "Extracting External Data: EuroClubIndex (Odds)")
        with redirect_stdout(f):
            eci = EuroClubIndexData(session=auth.session)
            df_odds = eci.run()

        # Saving to CSVs
        print_step(7, "Saving extracted data to CSVs")
        os.makedirs('./data', exist_ok=True)
        
        laliga_data.df_players.to_csv('./data/players.csv', index=False)
        laliga_data.df_teams.to_csv('./data/teams.csv', index=False)
        laliga_data.df_next_jornada.to_csv('./data/next_jornada.csv', index=False)
        
        # Save Season info
        pd.DataFrame(season_info.rounds).to_csv('./data/rounds.csv', index=False)
        
        # Convert List[ActiveEvent] dataclasses to DataFrame
        active_events_list = []
        for event in season_info.active_events:
            active_events_list.append({
                'id': event.id,
                'name': event.name,
                'status': event.status,
                'end': event.end,
                'type': event.type
            })
        pd.DataFrame(active_events_list).to_csv('./data/active_events.csv', index=False)

        user_league_data.df_league_players.to_csv('./data/league_players.csv', index=False)
        user_league_data.df_league_table.to_csv('./data/league_teams.csv', index=False)
        user_league_data.df_market_offers.to_csv('./data/market_offers.csv', index=False)
        user_league_data.df_market_sales.to_csv('./data/market_sales.csv', index=False)

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
                     pass

                imported_data[name] = pd.read_csv(path)
            except pd.errors.EmptyDataError:
                print(f"⚠️ Warning: {name} ({path}) is empty. Creating empty DataFrame.")
                imported_data[name] = pd.DataFrame() 
            except Exception as e:
                print(f"⚠️ Warning: Could not read {path}. Error: {e}")
                imported_data[name] = pd.DataFrame()
        else:
            print(f"⚠️ Warning: File {path} not found.")
            imported_data[name] = pd.DataFrame()
            
    return imported_data

def tables_columns(data):
    print_step(9, "Renaming columns and normalizing data")
    rename_maps = {
        'players': {
            'id': 'PLAYER_ID', 'name': 'PLAYER_NAME', 'slug': 'PLAYER_SLUG',
            'teamID': 'PLAYER_TEAM_ID', 'position': 'PLAYER_POSITION',
            'altPositions': 'PLAYER_ALT_POSITIONS', 'price': 'PLAYER_PRICE',
            'priceIncrement': 'PLAYER_PRICE_INCREMENT', 'status': 'PLAYER_STATUS',
            'statusInfo': 'PLAYER_STATUS_INFO', 'fitness': 'PLAYER_FITNESS',
            'points': 'PLAYER_POINTS', 'pointsHome': 'PLAYER_POINTS_HOME',
            'pointsAway': 'PLAYER_POINTS_AWAY', 'playedHome': 'PLAYER_PLAYED_HOME',
            'playedAway': 'PLAYER_PLAYED_AWAY'
        },
        'teams': {
            'id': 'TEAM_ID', 'name': 'TEAM_NAME', 'slug': 'TEAM_SLUG',
            'next_game_date': 'TEAM_NEXT_GAME_DATE', 'next_game_home': 'TEAM_NEXT_GAME_HOME',
            'next_game_away': 'TEAM_NEXT_GAME_AWAY', 'next_game': 'TEAM_NEXT_GAME',
            'is_home': 'TEAM_IS_HOME'
        },
        'next_match': {
            'jornada': 'NEXT_MATCH_JORNADA', 'fecha': 'NEXT_MATCH_FECHA',
            'local': 'NEXT_MATCH_LOCAL', 'visitante': 'NEXT_MATCH_VISITANTE',
            'partido': 'NEXT_MATCH_PARTIDO', 'estadio': 'NEXT_MATCH_ESTADIO',
            'status': 'NEXT_MATCH_STATUS'
        },
        'league_players': {
            'team_id': 'BIWPLAYER_TEAM_ID', 'team_name': 'BIWPLAYER_TEAM_NAME',
            'player_id': 'BIWPLAYER_ID', 'purchase_date': 'BIWPLAYER_PURCHASE_DATE',
            'purchase_price': 'BIWPLAYER_PURCHASE_PRICE', 'clause': 'BIWPLAYER_CLAUSE',
            'clause_locked_until': 'BIWPLAYER_CLAUSE_LOCKED_UNTIL', 'invested': 'BIWPLAYER_INVESTED'
        },
        'league_teams': {
            'id': 'BIWTEAM_ID', 'name': 'BIWTEAM_NAME', 'points': 'BIWTEAM_POINTS',
            'position': 'BIWTEAM_POSITION', 'teamSize': 'BIWTEAM_TEAM_SIZE',
            'teamValue': 'BIWTEAM_TEAM_VALUE', 'teamValueInc': 'BIWTEAM_TEAM_VALUE_INC'
        },
        'market_offers': {
            'offer_id': 'MARKET_OFFER_ID', 'amount': 'MARKET_OFFER_AMOUNT',
            'created': 'MARKET_OFFER_CREATED', 'until': 'MARKET_OFFER_UNTIL',
            'status': 'MARKET_OFFER_STATUS', 'type': 'MARKET_OFFER_TYPE',
            'from_id': 'MARKET_OFFER_FROM_ID', 'from_name': 'MARKET_OFFER_FROM_NAME',
            'requested_player_id': 'MARKET_OFFER_REQUESTED_PLAYER_ID'
        },
        'market_sales': {
            'player_id': 'MARKET_SALE_PLAYER_ID', 'price': 'MARKET_SALE_PRICE',
            'date': 'MARKET_SALE_DATE', 'until': 'MARKET_SALE_UNTIL',
            'user_id': 'MARKET_SALE_USER_ID', 'user_name': 'MARKET_SALE_USER_NAME',
            'clause': 'MARKET_SALE_CLAUSE'
        },
        'comuniate': {
            'posicion': 'COMUNIATE_POSITION', 'nombre': 'COMUNIATE_NAME',
            'suplente': 'COMUNIATE_SUPPLENT', 'titularidad': 'COMUNIATE_STARTER',
            'apercibido': 'COMUNIATE_CAUTIONED', 'duda': 'COMUNIATE_DOUBT',
            'equipo': 'COMUNIATE_TEAM', 'id_equipo_comuniate': 'COMUNIATE_TEAM_ID'
        },
        'odds': {
            'fecha': 'ODDS_FECHA', 'local': 'ODDS_LOCAL', 'visitante': 'ODDS_VISITANTE',
            '1': 'ODDS_1', 'X': 'ODDS_X', '2': 'ODDS_2',
            'home_goals': 'ODDS_HOME_GOALS', 'away_goals': 'ODDS_AWAY_GOALS'
        },
        'rounds': {
            'id': 'ROUND_ID', 'name': 'ROUND_NAME', 'short': 'ROUND_SHORT',
            'status': 'ROUND_STATUS', 'type': 'ROUND_TYPE'
        },
        'active_events': {
            'id': 'EVENT_ID', 'name': 'EVENT_NAME', 'status': 'EVENT_STATUS',
            'end': 'EVENT_END', 'type': 'EVENT_TYPE'
        }
    }

    for key, mapping in rename_maps.items():
        if key in data and not data[key].empty:
            data[key] = data[key].rename(columns=mapping)

    # Specific calculations for players
    if 'players' in data and not data['players'].empty:
        dfp = data['players']
        total_played = dfp['PLAYER_PLAYED_HOME'] + dfp['PLAYER_PLAYED_AWAY']
        # Avoid division by zero
        total_played = total_played.replace(0, 1) 
        
        data['players']['AVG_POINTS'] = (dfp['PLAYER_POINTS_HOME'] + dfp['PLAYER_POINTS_AWAY']) / total_played
        
        played_home = dfp['PLAYER_PLAYED_HOME'].replace(0, 1)
        played_away = dfp['PLAYER_PLAYED_AWAY'].replace(0, 1)

        data['players']['AVG_POINTS_HOME'] = dfp['PLAYER_POINTS_HOME'] / played_home
        data['players']['AVG_POINTS_AWAY'] = dfp['PLAYER_POINTS_AWAY'] / played_away

    return data

def get_data(extract: bool = True):
    """
    Orchestrates the data pipeline.
    
    Args:
        extract (bool): If True, runs the data extraction process. 
                        If False, skips extraction and loads data from existing CSVs.
    
    Returns:
        dict: The processed data dictionary with normalized column names.
    """
    if extract:
        extract_and_save_data()
    
    data = import_data()
    data = tables_columns(data)
    
    return data
