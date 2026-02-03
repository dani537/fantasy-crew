import requests
import pandas as pd
import datetime
import time
import random
from dataclasses import dataclass
from typing import List, Optional

# Módulos
from src.data_extraction.auth import random_headers

# Config
from src.config import GeneralSettings

LALIGA_INFO_URL = f"https://cf.biwenger.com/api/v2/competitions/la-liga/data?score={GeneralSettings.SCORE_TYPE}"
JORNADA_URL = 'https://cf.biwenger.com/api/v2/rounds/la-liga'

# SUMMARY:

# LaLigaGeneralData --> Extrae los datos generales de la liga (es decir, comunes de Biwenger en todas las ligas, no los de
#                       la liga en particular donde participa el usuario)
@dataclass
class ActiveEvent:
    id: int
    name: str
    status: str
    end: datetime.datetime
    type: str

@dataclass
class SeasonInfo:
    rounds: List[dict]
    active_events: List[ActiveEvent]

class LaLigaGeneralData:
    '''
    Extrae los datos generales de la liga (es decir, comunes de Biwenger en todas las ligas, no los de la liga en particular donde participa el usuario):
    - laliga_data: extrae los datos generales de la liga
    - players_info: crea un DataFrame a partir de los datos de jugadores (extraídos en laliga_data)
    - teams_info: crea un DataFrame a partir de los datos de equipos (extraídos en laliga_data)
    '''
    def __init__(self, session):
        self.session = session
        self._laliga_data()
        self._jornadas_data()
    
    def _laliga_data(self):
        headers = random_headers()
        headers['Referer'] = "https://biwenger.as.com/peloton/news"
        headers['Authorization'] = None  # Importante: no enviar el token al CDN
        
        response = self.session.get(LALIGA_INFO_URL, headers=headers)
        if response.status_code == 200:
            response_json = response.json()
            data = response_json.get('data', {})
            self.players = data.get('players', {})
            self.teams = data.get('teams', {})
            self.season_raw = data.get('season', {})
            self.active_events_raw = data.get('activeEvents', [])
            return True
        else:
            raise Exception(f"Error: {response.status_code} - {response.text}")
    
    def players_info(self) -> pd.DataFrame:
        """Crea un DataFrame a partir de los datos de jugadores"""
        if not self.players:
            raise ValueError("Primero debe obtener los datos de laliga_data")
        
        data_dict = {
            'id': [],
            'name': [],
            'slug': [],
            'teamID': [],
            'position': [],
            'altPositions': [],
            'price': [],
            'priceIncrement': [],
            'status': [],
            'statusInfo': [],
            'fitness': [],
            'points': [],
            'pointsHome': [],
            'pointsAway': [],
            'playedHome': [],
            'playedAway': []
        }
        
        for player_id, player in self.players.items():
            data_dict['id'].append(int(player_id))
            data_dict['name'].append(player.get('name', ''))
            data_dict['slug'].append(player.get('slug', ''))
            data_dict['teamID'].append(player.get('teamID'))
            data_dict['position'].append(player.get('position'))
            data_dict['altPositions'].append(player.get('altPositions'))
            data_dict['price'].append(player.get('price', 0))
            data_dict['priceIncrement'].append(player.get('priceIncrement', 0))
            data_dict['status'].append(player.get('status', 'unknown'))
            data_dict['statusInfo'].append(player.get('statusInfo'))
            data_dict['fitness'].append(player.get('fitness', []))
            data_dict['points'].append(player.get('points', 0))
            data_dict['pointsHome'].append(player.get('pointsHome'))
            data_dict['pointsAway'].append(player.get('pointsAway'))
            data_dict['playedHome'].append(player.get('playedHome'))
            data_dict['playedAway'].append(player.get('playedAway'))
        
        self.df_players = pd.DataFrame(data_dict)
        return self.df_players
    
    def teams_info(self) -> pd.DataFrame:
        """Crea un DataFrame a partir de los datos de equipos"""
        if not self.teams:
            raise ValueError("Primero debe obtener los datos de laliga_data")
        
        # Crear un mapa de ID a Nombre para resolver los nombres de los equipos en los partidos
        id_to_name = {int(tid): team['name'] for tid, team in self.teams.items()}
        
        data_dict = {
            'id': [],
            'name': [],
            'slug': [],
            'next_game_date': [],
            'next_game_home': [],
            'next_game_away': [],
            'next_game': [],
            'is_home': []
        }
        
        for team_id, team in self.teams.items():
            team_int_id = int(team_id)
            data_dict['id'].append(team_int_id)
            data_dict['name'].append(team['name'])
            data_dict['slug'].append(team['slug'])
            
            # Extraer info del próximo partido si existe
            next_games = team.get('nextGames', [])
            if next_games:
                next_game = next_games[0]
                data_dict['next_game_date'].append(pd.to_datetime(next_game.get('date'), unit='s'))
                home_id = next_game.get('home', {}).get('id')
                away_id = next_game.get('away', {}).get('id')
                
                home_name = id_to_name.get(home_id, f"ID:{home_id}")
                away_name = id_to_name.get(away_id, f"ID:{away_id}")
                
                data_dict['next_game_home'].append(home_name)
                data_dict['next_game_away'].append(away_name)
                data_dict['next_game'].append(f"{home_name} - {away_name}")
                data_dict['is_home'].append(home_id == team_int_id)
            else:
                data_dict['next_game_date'].append(None)
                data_dict['next_game_home'].append(None)
                data_dict['next_game_away'].append(None)
                data_dict['next_game'].append(None)
                data_dict['is_home'].append(None)
        
        self.df_teams = pd.DataFrame(data_dict)
        return self.df_teams

    def season_info(self) -> SeasonInfo:
        """Procesa y devuelve la información de la temporada y eventos activos"""
        if not hasattr(self, 'season_raw') or not self.season_raw:
            raise ValueError("Primero debe obtener los datos de laliga_data")
        
        rounds = self.season_raw.get('rounds', [])
        active_events_raw = getattr(self, 'active_events_raw', [])
        
        active_events = []
        for event in active_events_raw:
            active_events.append(ActiveEvent(
                id=event.get('id'),
                name=event.get('name'),
                status=event.get('status'),
                end=datetime.datetime.fromtimestamp(event.get('end')) if event.get('end') else None,
                type=event.get('type')
            ))
            
        return SeasonInfo(
            rounds=rounds,
            active_events=active_events
        )

    def _jornadas_data(self):
        """Extrae los datos de la próxima jornada"""
        headers = random_headers()
        headers['Referer'] = "https://biwenger.as.com/peloton/news"
        headers['Authorization'] = None
        
        response = self.session.get(JORNADA_URL, headers=headers)
        if response.status_code == 200:
            response_json = response.json()
            self.next_jornada = response_json.get('data', {}).get('next', {})
            return True
        else:
            raise Exception(f"Error: {response.status_code} - {response.text}")

    def next_jornada_info(self) -> pd.DataFrame:
        """Devuelve un DataFrame con los partidos de la próxima jornada"""
        if not hasattr(self, 'next_jornada') or not self.next_jornada:
            raise ValueError("No hay datos de la próxima jornada disponibles.")
        
        jornada_name = self.next_jornada.get('name', '')
        games = self.next_jornada.get('games', [])
        
        data_list = []
        for game in games:
            data_list.append({
                'jornada': jornada_name,
                'fecha': pd.to_datetime(game.get('date'), unit='s', utc=True).tz_convert('Europe/Madrid') if game.get('date') else None,
                'local': game.get('home', {}).get('name'),
                'visitante': game.get('away', {}).get('name'),
                'partido': f"{game.get('home', {}).get('name')} vs {game.get('away', {}).get('name')}",
                'estadio': game.get('location'),
                'status': game.get('status')
            })
        
        self.df_next_jornada = pd.DataFrame(data_list)
        return self.df_next_jornada

    def run(self):
        """Ejecuta y muestra todos los DataFrames generados por la clase"""
        self.players_info()
        print('🟢 Players extracted')
        self.teams_info()
        print('🟢 Teams extracted')
        self.season_info()
        print('🟢 Season & Active Events extracted')
        self.next_jornada_info()
        print('🟢 Next jornada extracted')

LEAGUE_URL = "https://biwenger.as.com/api/v2/league?include=all,-lastAccess&fields=*,standings,tournaments,group,settings(description)"
MARKET_URL = "https://biwenger.as.com/api/v2/market"

class UserLeagueData:
    '''
    Extrae los datos de la liga del usuario
    '''
    def __init__(self, session, token: str, league_id: int, user_id: int):
        self.token = token
        self.league_id = league_id
        self.user_id = user_id
        self._league_table_data(session)
        self._market_data(session)

    def _market_data(self, session):
        """Extrae los datos del mercado (ventas y ofertas)"""
        extra_headers = {
            'authorization': "Bearer " + self.token,
            'x-league': str(self.league_id),
            'x-user': str(self.user_id),
            'referer': "https://biwenger.as.com/market"
        }
        response = session.get(MARKET_URL, headers=extra_headers)
        if response.status_code == 200:
            data = response.json().get('data', {})
            self.market_sales = data.get('sales', [])
            self.market_offers = data.get('offers', [])
            return True
        else:
            raise Exception(f"Error al obtener datos del mercado: {response.status_code} - {response.text}")

    def market_sales_info(self) -> pd.DataFrame:
        """Crea un DataFrame a partir de los jugadores en venta en el mercado"""
        if not hasattr(self, 'market_sales'):
            raise ValueError("No hay datos de mercado disponibles.")
        
        data_list = []
        for sale in self.market_sales:
            player_info = sale.get('player', {})
            user_info = sale.get('user', {})
            
            data_list.append({
                'player_id': player_info.get('id'),
                'price': sale.get('price'),
                'date': pd.to_datetime(sale.get('date'), unit='s') if sale.get('date') else None,
                'until': pd.to_datetime(sale.get('until'), unit='s') if sale.get('until') else None,
                'user_id': user_info.get('id') if user_info else None,
                'user_name': user_info.get('name') if user_info else 'Mercado', # Si user es None, es venta de mercado/libre
                'clause': player_info.get('owner', {}).get('clause') if player_info.get('owner') else None
            })
            
        self.df_market_sales = pd.DataFrame(data_list)
        return self.df_market_sales

    def market_offers_info(self) -> pd.DataFrame:
        """Crea un DataFrame a partir de las ofertas recibidas"""
        if not hasattr(self, 'market_offers'):
            return pd.DataFrame()
            
        data_list = []
        for offer in self.market_offers:
             # Estructura basada en el JSON proporcionado
             # "id": 509423665, "amount": 1512400, "status": "waiting", "from": null (mercado), "requestedPlayers": [17148]
             offer_from = offer.get('from')
             
             data_list.append({
                 'offer_id': offer.get('id'),
                 'amount': offer.get('amount'),
                 'created': pd.to_datetime(offer.get('created'), unit='s') if offer.get('created') else None,
                 'until': pd.to_datetime(offer.get('until'), unit='s') if offer.get('until') else None,
                 'status': offer.get('status'),
                 'type': offer.get('type'),
                 'from_id': offer_from.get('id') if offer_from else None,
                 'from_name': offer_from.get('name') if offer_from else 'Mercado', # Si 'from' es null, es oferta del Mercado
                 'requested_player_id': offer.get('requestedPlayers')[0] if offer.get('requestedPlayers') else None
             })

        self.df_market_offers = pd.DataFrame(data_list)
        return self.df_market_offers


    def _league_table_data(self, session):
        extra_headers = {
            'authorization': "Bearer " + self.token,
            'x-league': str(self.league_id),
            'x-user': str(self.user_id),
            'referer': "https://biwenger.as.com/league"
        }
        response = session.get(LEAGUE_URL, headers=extra_headers)
        if response.status_code == 200:
            response_json = response.json()
            self.league_info = response_json.get('data', {}).get('standings', [])
            return True
        else:
            raise Exception(f"Error: {response.status_code} - {response.text}")

    def league_table(self) -> pd.DataFrame:
        if not self.league_info:
            raise ValueError("No hay datos de clasificación disponibles.")
        
        data_dict = {
            'id': [],
            'name': [],
            'points': [],
            'position': [],
            'teamSize': [],
            'teamValue': [],
            'teamValueInc': []
        }
        
        for user in self.league_info:
            data_dict['id'].append(user.get('id'))
            data_dict['name'].append(user.get('name'))
            data_dict['points'].append(user.get('points'))
            data_dict['position'].append(user.get('position'))
            data_dict['teamSize'].append(user.get('teamSize'))
            data_dict['teamValue'].append(user.get('teamValue'))
            data_dict['teamValueInc'].append(user.get('teamValueInc'))
            
        self.df_league_table = pd.DataFrame(data_dict)
        return self.df_league_table

    def all_teams_details(self, session) -> dict:
        """Extrae los datos de todos los equipos de la liga de forma individual"""
        if not self.league_info:
            self._league_table_data(session)
        
        all_details = {}
        for user in self.league_info:
            user_id = user.get('id')
            
            # Pausa aleatoria para simular comportamiento humano (entre 1 y 3 segundos)
            time.sleep(random.uniform(1, 3))
            
            # URL detallada con filtros específicos para obtener toda la información relevante
            url = f"https://biwenger.as.com/api/v2/user/{user_id}?fields=*,account(id),players(id,owner),lineups(round,points,count,position),league(id,name,competition,type,mode,marketMode,scoreID),market,seasons,offers,lastPositions"
            
            headers = {
                'authorization': "Bearer " + self.token,
                'x-league': str(self.league_id),
                'x-user': str(self.user_id),
                'referer': "https://biwenger.as.com/league"
            }
            response = session.get(url, headers=headers)
            if response.status_code == 200:
                all_details[user_id] = response.json().get('data', {})
                print(f"Datos obtenidos para el usuario {user_id}")
            else:
                print(f"Error al obtener datos del usuario {user_id}: {response.status_code}")
        
        return all_details

    def league_players_info(self, session) -> pd.DataFrame:
        """Extrae la información de todos los jugadores de todos los equipos de la liga"""
        all_teams_details = self.all_teams_details(session)
        
        players_list = []
        for team_id, team_data in all_teams_details.items():
            team_name = team_data.get('name')
            players = team_data.get('players', [])
            
            for player in players:
                player_id = player.get('id')
                owner_info = player.get('owner', {})
                
                player_entry = {
                    'team_id': team_id,
                    'team_name': team_name,
                    'player_id': player_id,
                    'purchase_date': datetime.datetime.fromtimestamp(owner_info.get('date')) if owner_info.get('date') else None,
                    'purchase_price': owner_info.get('price'),
                    'clause': owner_info.get('clause'),
                    'clause_locked_until': datetime.datetime.fromtimestamp(owner_info.get('clauseLockedUntil')) if owner_info.get('clauseLockedUntil') else None,
                    'invested': owner_info.get('invested')
                }
                players_list.append(player_entry)
        
        self.df_league_players = pd.DataFrame(players_list)
        return self.df_league_players

    def run(self, session):
        """
        Ejecuta y devuelve todos los DataFrames generados por la clase, incluyendo la extracción pesada de detalles de equipos.
        """
        print("🎬 Obteniendo datos completos de la liga del usuario...")
        
        # 1. Tabla de clasificación
        try:
            df_league = self.league_table()
            print(f"✅ League table extracted: {len(df_league)} usuarios")
        except:
             df_league = pd.DataFrame()
             print("⚠️ No hay datos de la clasificación.")

        # 2. Ventas en mercado
        try:
            df_sales = self.market_sales_info()
            print(f"✅ Market sales extracted: {len(df_sales)} ventas")
        except:
             df_sales = pd.DataFrame()
             print("⚠️ No hay datos de ventas en mercado.")
             
        # 3. Ofertas recibidas
        try:
            df_offers = self.market_offers_info()
            print(f"✅ Market offers extracted: {len(df_offers)} ofertas")
        except:
             df_offers = pd.DataFrame()
             print("⚠️ No hay datos de ofertas en mercado.")

        # 4. Jugadores de todos los equipos (Lento)
        try:
            print("⏳ Extrayendo detalles de todos los equipos (esto puede tardar)...")
            df_league_players = self.league_players_info(session)
            print(f"✅ League players extracted: {len(df_league_players)} jugadores en total")
        except Exception as e:
             df_league_players = pd.DataFrame()
             print(f"⚠️ Error al extraer jugadores de la liga: {e}")

        return {
            'league_table': df_league,
            'market_sales': df_sales,
            'market_offers': df_offers,
            'league_players': df_league_players
        }