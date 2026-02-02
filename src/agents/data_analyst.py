import pandas as pd
import ast
import numpy as np
from thefuzz import process
from src.data_extraction.pipeline import get_data, print_step

class DataAnalyst:
    """
    Agent responsible for analyzing data.
    It orchestrates the data extraction pipeline and performs specific business logic transformations
    to prepare the data for consumption.
    """

    def __init__(self):
        pass

    def run(self, extract: bool = True):
        """
        Executes the analyst's main workflow:
        1. Get data (extract or load from CSV).
        2. Process/Transform specific datasets (Comuniate fuzzy matching).
        3. Consolidate player data.
        4. Feature Engineering (Cleaning & Metrics).
        5. Return master dataframe.
        """
        # 1. Get Data
        data = get_data(extract=extract)

        # 2. Process Auxiliary Data
        data = self._process_comuniate(data)
        data = self._process_odds(data)

        # 3. Consolidate Player Data
        df_players_total = self._consolidate_player_data(data)

        if df_players_total is not None and not df_players_total.empty:
            # 4. Feature Engineering
            df_master = self._feature_engineering(df_players_total)
            return df_master
        
        return df_players_total

    def _feature_engineering(self, df):
        """
        Cleans data and creates new features for the Master Analysis.
        """
        print_step(13, "Running Feature Engineering")
        
        # Avoid SettingWithCopyWarning
        df = df.copy()

        # ----------------------------------------------------------------------
        # 1. Map Positions (1->GK, 2->DF, 3->MF, 4->FW)
        # ----------------------------------------------------------------------
        pos_map = {1: 'GK', 2: 'DF', 3: 'MF', 4: 'FW'}
        if 'PLAYER_POSITION' in df.columns:
            df['PLAYER_POSITION'] = df['PLAYER_POSITION'].map(pos_map).fillna(df['PLAYER_POSITION'])

        # ----------------------------------------------------------------------
        # 2. Map Alt Positions (e.g. [2, 4] -> "DF, FW")
        # ----------------------------------------------------------------------
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

        # ----------------------------------------------------------------------
        # 3. Clean COMUNIATE_STARTER (e.g. "80%" -> 0.8)
        # ----------------------------------------------------------------------
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

        # ----------------------------------------------------------------------
        # 4. Round decimal points for efficiency (Tokens)
        # ----------------------------------------------------------------------
        float_cols = df.select_dtypes(include=['float64', 'float32']).columns
        df[float_cols] = df[float_cols].round(2)

        return df

    def _process_comuniate(self, data):
        """
        Realiza el matching difuso (fuzzy matching) entre los datos de Comuniate y Biwenger.
        """
        if 'comuniate' not in data or 'players' not in data or 'teams' not in data:
            print("Skipping comuniate processing due to missing data keys.")
            return data
            
        if data['comuniate'].empty:
            print("Skipping comuniate processing: 'comuniate' DataFrame is empty.")
            return data

        print_step(10, "Processing Comuniate data (Fuzzy Matching)")
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

    def _process_odds(self, data):
        """
        Asocia las cuotas (Odds) del próximo partido a cada equipo.
        """
        if 'odds' not in data or 'teams' not in data:
            print("Skipping odds processing due to missing data keys.")
            return data
            
        if data['odds'].empty:
            print("Skipping odds processing: 'odds' DataFrame is empty.")
            return data

        print_step(10.5, "Processing Odds data (Match Matching)")
        df_odds = data['odds']
        df_teams = data['teams']

        # Filter unplayed matches (where goals are NaN or empty)
        # Assuming 'ODDS_HOME_GOALS' is NaN for future matches
        if 'ODDS_HOME_GOALS' in df_odds.columns:
            future_odds = df_odds[df_odds['ODDS_HOME_GOALS'].isna() | (df_odds['ODDS_HOME_GOALS'] == '')].copy()
        else:
            future_odds = df_odds.copy()

        # Prepare mapping columns
        df_teams['ODDS_1'] = np.nan
        df_teams['ODDS_X'] = np.nan
        df_teams['ODDS_2'] = np.nan

        # Helper to find odds info
        # We match based on team names. Since 'odds' source names might differ, we use fuzzy matching.
        # Ideally, we map 'ODDS_LOCAL' and 'ODDS_VISITANTE' to our 'TEAM_NAME'.
        
        # 1. Create a map of Odds Team Name -> Biwenger Team Name
        odds_team_names = pd.concat([future_odds['ODDS_LOCAL'], future_odds['ODDS_VISITANTE']]).unique()
        biwenger_teams = df_teams['TEAM_NAME'].unique()
        
        odds_to_biwenger_map = {}
        for ot in odds_team_names:
            if isinstance(ot, str):
                match, score = process.extractOne(ot, biwenger_teams)
                if score > 80: # Threshold to ensure decent match
                    odds_to_biwenger_map[ot] = match
        
        # 2. Iterate through matches in future_odds and assign to teams
        for idx, row in future_odds.iterrows():
            local_odds_name = row.get('ODDS_LOCAL')
            visit_odds_name = row.get('ODDS_VISITANTE')
            
            local_biw = odds_to_biwenger_map.get(local_odds_name)
            visit_biw = odds_to_biwenger_map.get(visit_odds_name)
            
            # Values
            o1 = row.get('ODDS_1')
            ox = row.get('ODDS_X')
            o2 = row.get('ODDS_2')
            
            if local_biw:
                # Assign to local team row
                mask = df_teams['TEAM_NAME'] == local_biw
                df_teams.loc[mask, 'ODDS_1'] = o1
                df_teams.loc[mask, 'ODDS_X'] = ox
                df_teams.loc[mask, 'ODDS_2'] = o2
                
            if visit_biw:
                # Assign to visitor team row
                mask = df_teams['TEAM_NAME'] == visit_biw
                df_teams.loc[mask, 'ODDS_1'] = o1
                df_teams.loc[mask, 'ODDS_X'] = ox
                df_teams.loc[mask, 'ODDS_2'] = o2

        data['teams'] = df_teams

        # ---------------------------------------------------------
        # ALSO ENRICH df_next_match IF AVAILABLE
        # ---------------------------------------------------------
        if 'next_match' in data and not data['next_match'].empty:
            df_next_match = data['next_match']
            df_next_match['ODDS_1'] = np.nan
            df_next_match['ODDS_X'] = np.nan
            df_next_match['ODDS_2'] = np.nan

            for idx, row in future_odds.iterrows():
                local_odds_name = row.get('ODDS_LOCAL')
                local_biw = odds_to_biwenger_map.get(local_odds_name)
                
                # Values
                o1 = row.get('ODDS_1')
                ox = row.get('ODDS_X')
                o2 = row.get('ODDS_2')

                if local_biw:
                    # Match on NEXT_MATCH_LOCAL
                    mask = df_next_match['NEXT_MATCH_LOCAL'] == local_biw
                    df_next_match.loc[mask, 'ODDS_1'] = o1
                    df_next_match.loc[mask, 'ODDS_X'] = ox
                    df_next_match.loc[mask, 'ODDS_2'] = o2
            
            # Round odds columns
            cols_to_round = ['ODDS_1', 'ODDS_X', 'ODDS_2']
            df_next_match[cols_to_round] = df_next_match[cols_to_round].astype(float).round(2)

            data['next_match'] = df_next_match
            # Save the enriched dataframe to disk
            print("   ℹ️ Saving enriched next_match to ./data/next_match.csv")
            df_next_match.to_csv('./data/next_match.csv', index=False)

        return data

    def _consolidate_player_data(self, data):
        """
        Consolida todas las fuentes de datos en un único DataFrame maestro de jugadores.
        
        Esta función realiza una serie de 'left joins' empezando por la tabla base de jugadores
        de LaLiga para ir enriqueciéndola con información de equipos, propiedad en la liga,
        mercado y predicciones tácticas.
        """
        print_step(11, "Consolidating player data")
        required_keys = ['players', 'teams', 'league_players', 'market_offers', 'market_sales', 'comuniate']
        if not all(key in data for key in required_keys):
            print(f"Missing one of {required_keys}, skipping consolidation.")
            return None

        # 1. Base: Tabla de jugadores de LaLiga (todos los jugadores disponibles)
        df_players_total = data['players']
        
        # 2. Unión con Equipos: Añade información del equipo (Nombre, si juega en casa, etc.)
        df_players_total = df_players_total.merge(data['teams'], left_on="PLAYER_TEAM_ID", right_on="TEAM_ID", how="left")
        
        # 3. Datos de la Liga (Propiedad): Añade quién tiene al jugador, por cuánto lo compró y su cláusula.
        if not data['league_players'].empty:
            df_players_total = df_players_total.merge(data['league_players'], left_on="PLAYER_ID", right_on="BIWPLAYER_ID", how="left")
            
        # 4. Ofertas de Mercado: Añade si hay ofertas activas por el jugador (y su importe).
        if not data['market_offers'].empty:
            df_players_total = df_players_total.merge(data['market_offers'], left_on="PLAYER_ID", right_on="MARKET_OFFER_REQUESTED_PLAYER_ID", how="left")
            
        # 5. Ventas de Mercado: Añade si el jugador está en el mercado (precio de venta, usuario que lo vende).
        if not data['market_sales'].empty:
             df_players_total = df_players_total.merge(data['market_sales'], left_on="PLAYER_ID", right_on="MARKET_SALE_PLAYER_ID", how="left")
             
        # 6. Datos de Comuniate: Añade la probabilidad de titularidad y alertas tácticas (dudas, apercibidos).
        if not data['comuniate'].empty:
             df_players_total = df_players_total.merge(data['comuniate'], left_on="PLAYER_NAME", right_on="BIW_PLAYER_NAME", how="left")

        # 7. Selección de Columnas: Limpia el DataFrame para mantener solo los campos relevantes para el análisis.
        selected_columns = [
            'PLAYER_NAME', 'PLAYER_POSITION', 'PLAYER_ALT_POSITIONS', 'PLAYER_PRICE',
            'PLAYER_PRICE_INCREMENT', 'PLAYER_STATUS', 'PLAYER_STATUS_INFO',
            'PLAYER_FITNESS', 'PLAYER_POINTS', 'AVG_POINTS', 'AVG_POINTS_HOME',
            'AVG_POINTS_AWAY', 'TEAM_ID', 'TEAM_NAME', 'TEAM_IS_HOME',
            'ODDS_1', 'ODDS_X', 'ODDS_2',
            'BIWPLAYER_TEAM_NAME', 'BIWPLAYER_PURCHASE_DATE', 'BIWPLAYER_PURCHASE_PRICE',
            'BIWPLAYER_CLAUSE', 'BIWPLAYER_CLAUSE_LOCKED_UNTIL', 'BIWPLAYER_INVESTED',
            'MARKET_OFFER_AMOUNT', 'MARKET_OFFER_UNTIL', 'MARKET_OFFER_FROM_NAME',
            'MARKET_OFFER_REQUESTED_PLAYER_ID', 'MARKET_SALE_PRICE', 'MARKET_SALE_UNTIL',
            'MARKET_SALE_USER_NAME', 'MARKET_SALE_CLAUSE',
            'COMUNIATE_STARTER', 'COMUNIATE_SUPPLENT', 'COMUNIATE_DOUBT', 'COMUNIATE_CAUTIONED'
        ]
        
        # Filtrar solo las columnas que realmente existen (evita errores si alguna tabla falló)
        existing_columns = [col for col in selected_columns if col in df_players_total.columns]
        
        return df_players_total[existing_columns]
