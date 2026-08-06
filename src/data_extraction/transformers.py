import os
import pandas as pd
import ast
import numpy as np
from thefuzz import process

def print_step(step_number, message, status="running"):
    if status == "running":
        print(f"\n🚀 STEP {step_number}: {message}...")
    elif status == "done":
        print(f"✅ STEP {step_number}: Done.")
    elif status == "error":
        print(f"❌ STEP {step_number}: Error!")

def rename_and_normalize_columns(data):
    """
    Renames columns from all sources to a standardized format.
    """
    print_step("T1", "Renaming columns and normalizing data")
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

def process_comuniate(data):
    """
    Realiza el matching difuso (fuzzy matching) entre los datos de Comuniate y Biwenger.
    """
    if 'comuniate' not in data or 'players' not in data or 'teams' not in data:
        print("Skipping comuniate processing due to missing data keys.")
        return data
        
    if data['comuniate'].empty:
        print("Skipping comuniate processing: 'comuniate' DataFrame is empty.")
        return data

    print_step("T2", "Processing Comuniate data (Fuzzy Matching)")
    df_comuniate = data['comuniate']
    # Filter out coaches (position 5)
    df_players = data['players'][data['players']['PLAYER_POSITION'] != 5]
    df_teams = data['teams']

    # Join players and teams to have a list of players per team name
    df_aux = df_players.merge(df_teams, left_on="PLAYER_TEAM_ID", right_on="TEAM_ID", how="left")

    # 1. Team Mapping
    equipos_origen = df_comuniate['COMUNIATE_TEAM'].unique()
    equipos_destino = df_aux['TEAM_NAME'].unique()

    diccionario_equipos = {}
    for equipo in equipos_origen:
        if isinstance(equipo, str):
            mejor_match, score = process.extractOne(equipo, equipos_destino)
            diccionario_equipos[equipo] = mejor_match
        else:
            diccionario_equipos[equipo] = None

    df_comuniate['BIW_TEAM_NAME'] = df_comuniate['COMUNIATE_TEAM'].map(diccionario_equipos)

    # 2. Player Mapping
    diccionario_jugadores = {}
    for index, row in df_comuniate.iterrows():
        player_name = row['COMUNIATE_NAME']
        team_name = row['BIW_TEAM_NAME']
        
        # Ensure we have a valid team match
        if pd.isna(team_name):
            diccionario_jugadores[player_name] = None
            continue

        players_team_list = df_aux[df_aux['TEAM_NAME'] == team_name]['PLAYER_NAME'].tolist()

        if players_team_list and isinstance(player_name, str):
            best_match, score = process.extractOne(player_name, players_team_list)
            diccionario_jugadores[player_name] = best_match
        else:
            diccionario_jugadores[player_name] = None

    df_comuniate['BIW_PLAYER_NAME'] = df_comuniate['COMUNIATE_NAME'].map(diccionario_jugadores)

    data['comuniate'] = df_comuniate
    return data

def process_odds(data):
    """
    Asocia las cuotas (Odds) del próximo partido a cada equipo.
    """
    if 'odds' not in data or 'teams' not in data:
        print("Skipping odds processing due to missing data keys.")
        return data
        
    if data['odds'].empty:
        print("Skipping odds processing: 'odds' DataFrame is empty.")
        return data

    print_step("T3", "Processing Odds data (Match Matching)")
    df_odds = data['odds']
    df_teams = data['teams']

    # Filter unplayed matches (where goals are NaN or empty)
    if 'ODDS_HOME_GOALS' in df_odds.columns:
        future_odds = df_odds[df_odds['ODDS_HOME_GOALS'].isna() | (df_odds['ODDS_HOME_GOALS'] == '')].copy()
    else:
        future_odds = df_odds.copy()

    # Prepare mapping columns
    df_teams['ODDS_1'] = np.nan
    df_teams['ODDS_X'] = np.nan
    df_teams['ODDS_2'] = np.nan

    # Helper to find odds info
    odds_team_names = pd.concat([future_odds['ODDS_LOCAL'], future_odds['ODDS_VISITANTE']]).unique()
    biwenger_teams = df_teams['TEAM_NAME'].unique()
    
    odds_to_biwenger_map = {}
    for ot in odds_team_names:
        if isinstance(ot, str):
            match, score = process.extractOne(ot, biwenger_teams)
            if score > 80: # Threshold to ensure decent match
                odds_to_biwenger_map[ot] = match
    
    for idx, row in future_odds.iterrows():
        local_odds_name = row.get('ODDS_LOCAL')
        visit_odds_name = row.get('ODDS_VISITANTE')
        
        local_biw = odds_to_biwenger_map.get(local_odds_name)
        visit_biw = odds_to_biwenger_map.get(visit_odds_name)
        
        o1 = row.get('ODDS_1')
        ox = row.get('ODDS_X')
        o2 = row.get('ODDS_2')
        
        if local_biw:
            mask = df_teams['TEAM_NAME'] == local_biw
            df_teams.loc[mask, 'ODDS_1'] = o1
            df_teams.loc[mask, 'ODDS_X'] = ox
            df_teams.loc[mask, 'ODDS_2'] = o2
            
        if visit_biw:
            mask = df_teams['TEAM_NAME'] == visit_biw
            df_teams.loc[mask, 'ODDS_1'] = o1
            df_teams.loc[mask, 'ODDS_X'] = ox
            df_teams.loc[mask, 'ODDS_2'] = o2

    data['teams'] = df_teams

    # ALSO ENRICH df_next_match IF AVAILABLE
    if 'next_match' in data and not data['next_match'].empty:
        df_next_match = data['next_match']
        df_next_match['ODDS_1'] = np.nan
        df_next_match['ODDS_X'] = np.nan
        df_next_match['ODDS_2'] = np.nan

        for idx, row in future_odds.iterrows():
            local_odds_name = row.get('ODDS_LOCAL')
            local_biw = odds_to_biwenger_map.get(local_odds_name)
            
            o1 = row.get('ODDS_1')
            ox = row.get('ODDS_X')
            o2 = row.get('ODDS_2')

            if local_biw:
                mask = df_next_match['NEXT_MATCH_LOCAL'] == local_biw
                df_next_match.loc[mask, 'ODDS_1'] = o1
                df_next_match.loc[mask, 'ODDS_X'] = ox
                df_next_match.loc[mask, 'ODDS_2'] = o2
        
        cols_to_round = ['ODDS_1', 'ODDS_X', 'ODDS_2']
        df_next_match[cols_to_round] = df_next_match[cols_to_round].astype(float).round(2)

        data['next_match'] = df_next_match
        print("   ℹ️ Saving enriched next_match to ./data/next_jornada.csv")
        df_next_match.to_csv('./data/next_jornada.csv', index=False)

    return data

def consolidate_player_data(data):
    """
    Consolida todas las fuentes de datos en un único DataFrame maestro de jugadores.
    """
    print_step("T4", "Consolidating player data")
    required_keys = ['players', 'teams', 'league_players', 'market_offers', 'market_sales', 'comuniate']
    if not all(key in data for key in required_keys):
        print(f"Missing one of {required_keys}, skipping consolidation.")
        return None

    # 1. Base: Tabla de jugadores de LaLiga
    df_players_total = data['players']

    # Normalize merge keys to a consistent numeric dtype (CSVs with NaNs or
    # mixed types would otherwise crash the merges with int64/object mismatches)
    def _coerce_key(df, col):
        if df is not None and not df.empty and col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')
        return df

    df_players_total = _coerce_key(df_players_total, 'PLAYER_ID')
    df_players_total = _coerce_key(df_players_total, 'PLAYER_TEAM_ID')
    data['teams'] = _coerce_key(data['teams'], 'TEAM_ID')
    data['league_players'] = _coerce_key(data['league_players'], 'BIWPLAYER_ID')
    data['market_offers'] = _coerce_key(data['market_offers'], 'MARKET_OFFER_REQUESTED_PLAYER_ID')
    data['market_sales'] = _coerce_key(data['market_sales'], 'MARKET_SALE_PLAYER_ID')
    
    # 2. Unión con Equipos
    df_players_total = df_players_total.merge(data['teams'], left_on="PLAYER_TEAM_ID", right_on="TEAM_ID", how="left")
    
    # 3. Datos de la Liga (Propiedad)
    if not data['league_players'].empty:
        df_players_total = df_players_total.merge(data['league_players'], left_on="PLAYER_ID", right_on="BIWPLAYER_ID", how="left")
        
    # 4. Ofertas de Mercado
    if not data['market_offers'].empty:
        df_players_total = df_players_total.merge(data['market_offers'], left_on="PLAYER_ID", right_on="MARKET_OFFER_REQUESTED_PLAYER_ID", how="left")
        
    # 5. Ventas de Mercado
    if not data['market_sales'].empty:
         df_players_total = df_players_total.merge(data['market_sales'], left_on="PLAYER_ID", right_on="MARKET_SALE_PLAYER_ID", how="left")
         
    # 6. Datos de Comuniate
    if not data['comuniate'].empty:
         df_players_total = df_players_total.merge(data['comuniate'], left_on="PLAYER_NAME", right_on="BIW_PLAYER_NAME", how="left")

    # 7. Selección de Columnas
    selected_columns = [
        'PLAYER_ID', 'PLAYER_NAME', 'PLAYER_POSITION', 'PLAYER_ALT_POSITIONS', 'PLAYER_PRICE',
        'PLAYER_PRICE_INCREMENT', 'PLAYER_STATUS', 'PLAYER_STATUS_INFO',
        'PLAYER_FITNESS', 'PLAYER_POINTS', 'AVG_POINTS', 'AVG_POINTS_HOME',
        'AVG_POINTS_AWAY', 'TEAM_ID', 'TEAM_NAME', 'TEAM_IS_HOME',
        'ODDS_1', 'ODDS_X', 'ODDS_2',
        'BIWPLAYER_TEAM_NAME', 'BIWPLAYER_TEAM_ID', 'BIWPLAYER_PURCHASE_DATE', 'BIWPLAYER_PURCHASE_PRICE',
        'BIWPLAYER_CLAUSE', 'BIWPLAYER_CLAUSE_LOCKED_UNTIL', 'BIWPLAYER_INVESTED',
        'MARKET_OFFER_AMOUNT', 'MARKET_OFFER_UNTIL', 'MARKET_OFFER_FROM_NAME',
        'MARKET_OFFER_ID', 'MARKET_OFFER_FROM_ID',
        'MARKET_OFFER_REQUESTED_PLAYER_ID', 'MARKET_SALE_PRICE', 'MARKET_SALE_UNTIL',
        'MARKET_SALE_USER_ID', 'MARKET_SALE_USER_NAME', 'MARKET_SALE_CLAUSE',
        'COMUNIATE_STARTER', 'COMUNIATE_SUPPLENT', 'COMUNIATE_DOUBT', 'COMUNIATE_CAUTIONED'
    ]
    
    existing_columns = [col for col in selected_columns if col in df_players_total.columns]
    res = df_players_total[existing_columns].copy()
    if 'PLAYER_ID' in res.columns:
        res = res.drop_duplicates(subset=['PLAYER_ID'], keep='first')
    return res

def feature_engineering(df):
    """
    Cleans data and creates new features for the Master Analysis.
    """
    print_step("T5", "Running Feature Engineering")
    df = df.copy()

    # 1. Map Positions
    pos_map = {1: 'GK', 2: 'DF', 3: 'MF', 4: 'FW'}
    if 'PLAYER_POSITION' in df.columns:
        df['PLAYER_POSITION'] = df['PLAYER_POSITION'].map(pos_map).fillna(df['PLAYER_POSITION'])

    # 2. Map Alt Positions
    def map_alt_positions(val):
        if pd.isna(val) or val == '' or val == '[]':
            return ''
        try:
            if isinstance(val, str):
                if val.startswith('['):
                    val_list = ast.literal_eval(val)
                else:
                    val_list = [int(x.strip()) for x in val.split(',') if x.strip().isdigit()]
            elif isinstance(val, (list, tuple)):
                val_list = val
            else:
                return ''
            
            mapped = [pos_map.get(int(x), str(x)) for x in val_list]
            return ", ".join(mapped)
        except Exception:
            return str(val)

    if 'PLAYER_ALT_POSITIONS' in df.columns:
        df['PLAYER_ALT_POSITIONS'] = df['PLAYER_ALT_POSITIONS'].apply(map_alt_positions)

    # 3. Clean COMUNIATE_STARTER
    def clean_percentage(val):
        if pd.isna(val):
            return 0.0
        if isinstance(val, (int, float)):
            return float(val) / 100 if val > 1 else float(val)
        if isinstance(val, str):
            val = val.replace('%', '').strip()
            try:
                return float(val) / 100
            except ValueError:
                return 0.0
        return 0.0

    if 'COMUNIATE_STARTER' in df.columns:
        df['COMUNIATE_STARTER'] = df['COMUNIATE_STARTER'].apply(clean_percentage)
    else:
        df['COMUNIATE_STARTER'] = 0.0

    # 4. Round decimal points
    float_cols = df.select_dtypes(include=['float64', 'float32']).columns
    df[float_cols] = df[float_cols].round(2)

    # 5. Financial & Scoring Metrics
    if 'PLAYER_POINTS' in df.columns:
        df['PERCENTILE'] = df['PLAYER_POINTS'].rank(pct=True)
        if 'PLAYER_POSITION' in df.columns:
            df['POSITION_PERCENTILE'] = df.groupby('PLAYER_POSITION')['PLAYER_POINTS'].rank(pct=True)
        else:
            df['POSITION_PERCENTILE'] = 0.0
    else:
        df['PERCENTILE'] = 0.0
        df['POSITION_PERCENTILE'] = 0.0

    # 6. Availability Metrics
    is_market = (df.get('MARKET_SALE_PRICE', 0) > 0)
    is_clause = (df.get('BIWPLAYER_CLAUSE', 0) > 0) & (df.get('BIWPLAYER_CLAUSE_LOCKED_UNTIL').isna() | (df.get('BIWPLAYER_CLAUSE_LOCKED_UNTIL') == ''))
    
    df['IS_AVAILABLE'] = is_market | is_clause
    
    df['AVAILABILITY_TYPE'] = "None"
    df.loc[is_market, 'AVAILABILITY_TYPE'] = "Purchase"
    df.loc[is_clause, 'AVAILABILITY_TYPE'] = "Clause"
    df.loc[is_market & is_clause, 'AVAILABILITY_TYPE'] = "Purchase, Clause"

    df['REAL_SALE_PRICE'] = 0.0
    df.loc[is_clause, 'REAL_SALE_PRICE'] = df.loc[is_clause, 'BIWPLAYER_CLAUSE']
    df.loc[is_market, 'REAL_SALE_PRICE'] = df.loc[is_market, 'MARKET_SALE_PRICE']

    # 7. Momentum Metrics
    def calculate_momentum(val):
        if pd.isna(val) or val == '' or val == '[]':
            return 0.0
        try:
            if isinstance(val, str):
                val_list = ast.literal_eval(val)
            elif isinstance(val, (list, tuple)):
                val_list = val
            else:
                return 0.0
            
            processed_points = []
            for x in val_list:
                if x in ['injured', 'sanctioned', 'doubt']:
                    continue

                if x is None or x == 'discarded':
                    processed_points.append(0.0)
                elif isinstance(x, (int, float)):
                    processed_points.append(float(x))
            
            if not processed_points:
                return 0.0
            
            return sum(processed_points) / len(processed_points)
        except Exception:
            return 0.0

    if 'PLAYER_FITNESS' in df.columns:
        df['AVG_POINTS_MOMENTUM'] = df['PLAYER_FITNESS'].apply(calculate_momentum)
        if 'AVG_POINTS' in df.columns:
            df['MOMENTUM_TREND'] = df['AVG_POINTS_MOMENTUM'] - df['AVG_POINTS']
        else:
            df['MOMENTUM_TREND'] = 0.0
    else:
        df['AVG_POINTS_MOMENTUM'] = 0.0
        df['MOMENTUM_TREND'] = 0.0

    # 8. Moneyball Metrics
    df['COST_PER_POINT'] = 0.0
    mask_cpp = (df['IS_AVAILABLE']) & (df.get('AVG_POINTS', 0) > 0)
    df.loc[mask_cpp, 'COST_PER_POINT'] = (df.loc[mask_cpp, 'REAL_SALE_PRICE'] / 1_000_000) / df.loc[mask_cpp, 'AVG_POINTS']
    
    df['COST_PER_MOMENTUM_POINT'] = 0.0
    mask_cpmp = (df['IS_AVAILABLE']) & (df['AVG_POINTS_MOMENTUM'] > 0)
    df.loc[mask_cpmp, 'COST_PER_MOMENTUM_POINT'] = (df.loc[mask_cpmp, 'REAL_SALE_PRICE'] / 1_000_000) / df.loc[mask_cpmp, 'AVG_POINTS_MOMENTUM']

    # 9. Advanced Expected Points (xP)
    if 'COMUNIATE_SUPPLENT' in df.columns:
        df['COMUNIATE_SUPPLENT'] = df['COMUNIATE_SUPPLENT'].apply(clean_percentage)
    else:
        df['COMUNIATE_SUPPLENT'] = 0.0

    def calculate_advanced_xp(row):
        starter_prob = float(row.get('COMUNIATE_STARTER') or 0.0)
        sub_prob = float(row.get('COMUNIATE_SUPPLENT') or 0.0)
        
        # 1. Base rating using home/away split and momentum
        is_home = bool(row.get('TEAM_IS_HOME'))
        avg_split = float(row.get('AVG_POINTS_HOME') if is_home else row.get('AVG_POINTS_AWAY') or 0.0)
        avg_base = avg_split if avg_split > 0 else float(row.get('AVG_POINTS') or 0.0)
        momentum = float(row.get('AVG_POINTS_MOMENTUM') or 0.0)
        
        base_rating = (momentum * 0.7 + avg_base * 0.3) if (momentum > 0 and avg_base > 0) else max(momentum, avg_base)
        
        # 2. Match difficulty factor based on betting odds
        match_factor = 1.0
        odds_win = float(row.get('ODDS_1') if is_home else row.get('ODDS_2') or 0.0)
        if odds_win > 0:
            match_factor = float(np.clip(2.5 / odds_win, 0.80, 1.20))
            
        expected_minutes_weight = starter_prob + (sub_prob * 0.75)
        xp = base_rating * expected_minutes_weight * match_factor
        
        # Status penalties
        status = str(row.get('PLAYER_STATUS') or 'ok').lower()
        if status == 'doubt':
            xp *= 0.65
        elif status in ('injured', 'sanctioned', 'suspended'):
            xp = 0.0
            
        return round(xp, 2)

    df['EXPECTED_POINTS'] = df.apply(calculate_advanced_xp, axis=1)
    
    df['COST_PER_XP'] = 0.0
    mask_cpxp = (df['IS_AVAILABLE']) & (df['EXPECTED_POINTS'] > 0)
    df.loc[mask_cpxp, 'COST_PER_XP'] = (df.loc[mask_cpxp, 'REAL_SALE_PRICE'] / 1_000_000) / df.loc[mask_cpxp, 'EXPECTED_POINTS']

    # 10. IS_MY_PLAYER Flag
    df['IS_MY_PLAYER'] = False
    if os.path.exists('./data/user_info.csv') and 'BIWPLAYER_TEAM_NAME' in df.columns:
        try:
            user_info = pd.read_csv('./data/user_info.csv')
            if 'team_name' in user_info.columns and not user_info.empty:
                my_team_name = user_info['team_name'].iloc[0]
                df['IS_MY_PLAYER'] = (df['BIWPLAYER_TEAM_NAME'] == my_team_name)
        except Exception:
            pass

    return df
