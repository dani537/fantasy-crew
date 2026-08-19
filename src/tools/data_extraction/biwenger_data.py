import requests
import pandas as pd
import datetime
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pydantic import BaseModel
from typing import List, Optional

# Módulos
from src.tools.data_extraction.auth import random_headers

# Config
from src.config import GeneralSettings

# We'll build URLs dynamically in the class

# SUMMARY:

# LaLigaGeneralData --> Extrae los datos generales de la liga (es decir, comunes de Biwenger en todas las ligas, no los de
#                       la liga en particular donde participa el usuario)

class ActiveEvent(BaseModel):
    id: int
    name: str
    status: str
    end: Optional[datetime.datetime]
    type: str

class SeasonInfo(BaseModel):
    rounds: List[dict]
    active_events: List[ActiveEvent]

class BiwengerGeneralData:
    '''
    Extrae los datos generales de la competición según el slug (ej. la-liga, euroliga):
    - laliga_data: extrae los datos generales de la liga (jugadores, equipos)
    - players_info: crea un DataFrame a partir de los datos de jugadores (extraídos en laliga_data)
    - teams_info: crea un DataFrame a partir de los datos de equipos (extraídos en laliga_data)
    '''
    def __init__(self, session=None, competition_slug: str = "la-liga"):
        self.session = session if session is not None else requests.Session()
        self.competition_slug = competition_slug
        self.info_url = f"https://cf.biwenger.com/api/v2/competitions/{self.competition_slug}/data?score={GeneralSettings.SCORE_TYPE}"
        self.jornada_url = f"https://cf.biwenger.com/api/v2/rounds/{self.competition_slug}"
        
        self._laliga_data()
        self._jornadas_data()
    
    def _laliga_data(self):
        headers = random_headers()
        headers['Referer'] = "https://biwenger.as.com/peloton/news"
        headers['Authorization'] = None  # Importante: no enviar el token al CDN
        
        response = self.session.get(self.info_url, headers=headers)
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
        """Extrae los datos de la próxima jornada (o la activa si está pendiente)"""
        headers = random_headers()
        headers['Referer'] = "https://biwenger.as.com/peloton/news"
        headers['Authorization'] = None
        
        response = self.session.get(self.jornada_url, headers=headers)
        if response.status_code == 200:
            response_json = response.json()
            data_root = response_json.get('data', {})
            # Si el objeto raíz contiene partidos y no está completado, es la jornada inmediata a jugar
            if data_root.get('games') and data_root.get('status') != 'completed':
                self.next_jornada = data_root
            else:
                self.next_jornada = data_root.get('next', {})
            return True
        else:
            raise Exception(f"Error: {response.status_code} - {response.text}")

    def next_jornada_info(self) -> pd.DataFrame:
        """Devuelve un DataFrame con los partidos de la próxima jornada y sus métricas de dificultad"""
        if not hasattr(self, 'next_jornada') or not self.next_jornada:
            raise ValueError("No hay datos de la próxima jornada disponibles.")
        
        jornada_name = self.next_jornada.get('name', '')
        games = self.next_jornada.get('games', [])
        
        data_list = []
        for game in games:
            home_diff = game.get('home', {}).get('difficulty', {}).get('rating')
            away_diff = game.get('away', {}).get('difficulty', {}).get('rating')
            data_list.append({
                'jornada': jornada_name,
                'fecha': pd.to_datetime(game.get('date'), unit='s', utc=True).tz_convert('Europe/Madrid') if game.get('date') else None,
                'local': game.get('home', {}).get('name'),
                'visitante': game.get('away', {}).get('name'),
                'partido': f"{game.get('home', {}).get('name')} vs {game.get('away', {}).get('name')}",
                'home_difficulty': home_diff,
                'away_difficulty': away_diff,
                'estadio': game.get('location'),
                'status': game.get('status')
            })
        
        self.df_next_jornada = pd.DataFrame(data_list)
        return self.df_next_jornada

    def active_events_info(self) -> pd.DataFrame:
        """
        Extrae y enriquece la información de las jornadas en curso y próximas:
        ID, nombre, estado, fecha_inicio, fecha_fin, primer_partido, ultimo_partido, progreso.
        """
        if not hasattr(self, 'next_jornada') or not self.next_jornada:
            self._jornadas_data()

        rounds_list = []
        if getattr(self, 'next_jornada', None):
            rounds_list.append(self.next_jornada)
            if self.next_jornada.get('next'):
                rounds_list.append(self.next_jornada['next'])

        rows = []
        for rd in rounds_list:
            r_id = rd.get('id')
            r_name = rd.get('name')
            r_status = rd.get('status')
            if not r_status or r_status == 'None':
                r_status = 'pending'
            status_label = 'En juego' if r_status == 'active' else ('Pendiente' if r_status == 'pending' else str(r_status).capitalize())
            
            games = rd.get('games', [])
            if games:
                dates = [g.get('date') for g in games if g.get('date')]
                min_date = min(dates) if dates else None
                max_date = max(dates) if dates else None
                
                start_dt = pd.to_datetime(min_date, unit='s', utc=True).tz_convert('Europe/Madrid') if min_date else None
                end_dt = pd.to_datetime(max_date, unit='s', utc=True).tz_convert('Europe/Madrid') if max_date else None
                
                first_game = next((g for g in games if g.get('date') == min_date), {})
                last_game = next((g for g in games if g.get('date') == max_date), {})
                
                first_str = f"{first_game.get('home',{}).get('name')} vs {first_game.get('away',{}).get('name')} ({start_dt.strftime('%d/%m %H:%M')})" if start_dt else '—'
                last_str = f"{last_game.get('home',{}).get('name')} vs {last_game.get('away',{}).get('name')} ({end_dt.strftime('%d/%m %H:%M')})" if end_dt else '—'
                
                finished_cnt = sum(1 for g in games if g.get('status') == 'finished')
                total_cnt = len(games)
                
                rows.append({
                    'ID_JORNADA': r_id,
                    'JORNADA': r_name,
                    'ESTADO': status_label,
                    'FECHA_INICIO': start_dt.strftime('%Y-%m-%d %H:%M') if start_dt else None,
                    'FECHA_FIN': end_dt.strftime('%Y-%m-%d %H:%M') if end_dt else None,
                    'PRIMER_PARTIDO': first_str,
                    'ULTIMO_PARTIDO': last_str,
                    'PROGRESO': f"{finished_cnt}/{total_cnt} partidos jugados",
                    'TOTAL_PARTIDOS': total_cnt
                })

        self.df_active_events = pd.DataFrame(rows)
        return self.df_active_events

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
        self.active_events_info()
        print('🟢 Active events enriched schedule extracted')

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

             # requestedPlayers items can be plain ints or dicts {'id': ..., ...}
             requested_id = None
             req_players = offer.get('requestedPlayers') or []
             if req_players:
                 first = req_players[0]
                 requested_id = first.get('id') if isinstance(first, dict) else first

             data_list.append({
                 'offer_id': offer.get('id'),
                 'amount': offer.get('amount'),
                 'created': pd.to_datetime(offer.get('created'), unit='s') if offer.get('created') else None,
                 'until': pd.to_datetime(offer.get('until'), unit='s') if offer.get('until') else None,
                 'status': offer.get('status'),
                 'type': offer.get('type'),
                 'from_id': offer_from.get('id') if offer_from else None,
                 'from_name': offer_from.get('name') if offer_from else 'Mercado', # Si 'from' es null, es oferta del Mercado
                 'requested_player_id': requested_id
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

    def league_round_standings(self, session) -> list:
        """
        Extrae las alineaciones y puntuaciones en vivo de la jornada actual para cada usuario.
        """
        extra_headers = {
            'authorization': "Bearer " + self.token,
            'x-league': str(self.league_id),
            'x-user': str(self.user_id),
            'referer': "https://biwenger.as.com/league"
        }
        url = f"https://biwenger.as.com/api/v2/rounds/league?league={self.league_id}"
        try:
            response = session.get(url, headers=extra_headers, timeout=10)
            if response.status_code == 200:
                data = response.json().get('data', {})
                return data.get('league', {}).get('standings', [])
        except Exception as e:
            print(f"⚠️ Error obteniendo live round standings: {e}")
        return []

    def all_teams_details(self, session) -> dict:
        """Extrae los datos de todos los equipos de la liga de forma concurrente para mayor velocidad"""
        if not self.league_info:
            self._league_table_data(session)
        
        all_details = {}
        
        def fetch_user_detail(user):
            user_id = user.get('id')
            url = f"https://biwenger.as.com/api/v2/user/{user_id}?fields=*,account(id),players(id,owner),lineups(round,points,count,position),league(id,name,competition,type,mode,marketMode,scoreID),market,seasons,offers,lastPositions"
            headers = {
                'authorization': "Bearer " + self.token,
                'x-league': str(self.league_id),
                'x-user': str(self.user_id),
                'referer': "https://biwenger.as.com/league"
            }
            try:
                response = session.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    return user_id, response.json().get('data', {})
                else:
                    print(f"⚠️ Error al obtener datos del usuario {user_id}: {response.status_code}")
            except Exception as e:
                print(f"⚠️ Excepción al obtener datos del usuario {user_id}: {e}")
            return user_id, None

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_user = {executor.submit(fetch_user_detail, user): user for user in self.league_info}
            for future in as_completed(future_to_user):
                user_id, data = future.result()
                if data:
                    all_details[user_id] = data
                    print(f"✅ Datos obtenidos para el usuario {user_id}")
        
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

    def league_board_info(self, session, limit: int = 100, fetch_all: bool = True, days_back: Optional[int] = None) -> dict:
        """
        Extrae el histórico del muro de la liga (/api/v2/league/{league_id}/board)
        recorriendo todas las páginas (paginación por offset) y lo procesa en:
          - df_board_transfers: Compras, ventas, traspasos entre usuarios y clausulazos.
          - df_board_bids: Registro de pujas (ganadoras y perdedoras de rivales).
          - df_rival_financials: Resumen estimado de gasto, ingresos y balance de cada manager.
        Si se especifica days_back, solo se extraen eventos de los últimos N días.
        """
        extra_headers = {
            'authorization': "Bearer " + self.token,
            'x-league': str(self.league_id),
            'x-user': str(self.user_id),
            'referer': "https://biwenger.as.com/board"
        }
        
        all_items = []
        offset = 0
        batch_limit = 100
        cutoff_ts = None
        if days_back is not None and days_back > 0:
            cutoff_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_back)).timestamp()
        
        try:
            while True:
                url = f"https://biwenger.as.com/api/v2/league/{self.league_id}/board?limit={batch_limit}&offset={offset}"
                response = session.get(url, headers=extra_headers)
                if response.status_code != 200:
                    print(f"⚠️ Error obteniendo el muro de la liga (offset {offset}): {response.status_code}")
                    break
                
                raw_items = response.json().get('data', [])
                if not raw_items:
                    break
                
                stop_early = False
                for item in raw_items:
                    item_ts = item.get('date', 0)
                    if cutoff_ts and item_ts < cutoff_ts:
                        stop_early = True
                        break
                    all_items.append(item)
                
                if stop_early or not fetch_all or len(raw_items) < batch_limit:
                    break
                
                offset += batch_limit


            transfers_list = []
            bids_list = []
            financials_map = {}

            for item in all_items:
                item_type = item.get('type')
                item_date = pd.to_datetime(item.get('date'), unit='s') if item.get('date') else None
                content = item.get('content')
                if content is None:
                    continue

                entries = content if isinstance(content, list) else [content]

                if item_type == 'market':
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        buyer = entry.get('to')
                        buyer_id = buyer.get('id') if isinstance(buyer, dict) else None
                        buyer_name = buyer.get('name') if isinstance(buyer, dict) else None
                        player_id = entry.get('player')
                        amount = entry.get('amount', 0)
                        entry_date = pd.to_datetime(entry.get('date'), unit='s') if entry.get('date') else item_date

                        if buyer_id:
                            transfers_list.append({
                                'date': entry_date,
                                'type': 'market_buy',
                                'player_id': player_id,
                                'buyer_id': buyer_id,
                                'buyer_name': buyer_name,
                                'seller_id': None,
                                'seller_name': 'Mercado',
                                'amount': amount,
                                'clause': False
                            })

                            if buyer_id not in financials_map:
                                financials_map[buyer_id] = {'user_name': buyer_name, 'total_spent': 0, 'total_income': 0, 'bids_placed': 0, 'purchases': 0, 'sales': 0, 'clauses_paid': 0, 'clauses_received': 0}
                            financials_map[buyer_id]['total_spent'] += amount
                            financials_map[buyer_id]['purchases'] += 1

                        bids = entry.get('bids', [])
                        if isinstance(bids, list):
                            for bid in bids:
                                if not isinstance(bid, dict):
                                    continue
                                user = bid.get('user', {})
                                u_id = user.get('id') if isinstance(user, dict) else None
                                u_name = user.get('name') if isinstance(user, dict) else 'Desconocido'
                                bid_amount = bid.get('amount', 0)
                                bid_date = pd.to_datetime(bid.get('date'), unit='s') if bid.get('date') else entry_date
                                won = (u_id == buyer_id) if buyer_id else False

                                bids_list.append({
                                    'date': bid_date,
                                    'player_id': player_id,
                                    'bidder_id': u_id,
                                    'bidder_name': u_name,
                                    'bid_amount': bid_amount,
                                    'winning_amount': amount,
                                    'won': won
                                })

                                if u_id:
                                    if u_id not in financials_map:
                                        financials_map[u_id] = {'user_name': u_name, 'total_spent': 0, 'total_income': 0, 'bids_placed': 0, 'purchases': 0, 'sales': 0, 'clauses_paid': 0, 'clauses_received': 0}
                                    financials_map[u_id]['bids_placed'] += 1

                elif item_type == 'transfer':
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        seller = entry.get('from')
                        seller_id = seller.get('id') if isinstance(seller, dict) else None
                        seller_name = seller.get('name') if isinstance(seller, dict) else 'Mercado'

                        buyer = entry.get('to')
                        buyer_id = buyer.get('id') if isinstance(buyer, dict) else None
                        buyer_name = buyer.get('name') if isinstance(buyer, dict) else 'Mercado'

                        player_id = entry.get('player')
                        amount = entry.get('amount', 0)
                        is_clause = entry.get('type') == 'clause'
                        
                        if is_clause:
                            t_type = 'clause_steal'
                        elif seller_id and buyer_id:
                            t_type = 'user_transfer'
                        elif seller_id and not buyer_id:
                            t_type = 'user_sale'
                        else:
                            t_type = 'market_buy'

                        transfers_list.append({
                            'date': item_date,
                            'type': t_type,
                            'player_id': player_id,
                            'buyer_id': buyer_id,
                            'buyer_name': buyer_name,
                            'seller_id': seller_id,
                            'seller_name': seller_name,
                            'amount': amount,
                            'clause': is_clause
                        })

                        if seller_id:
                            if seller_id not in financials_map:
                                financials_map[seller_id] = {'user_name': seller_name, 'total_spent': 0, 'total_income': 0, 'bids_placed': 0, 'purchases': 0, 'sales': 0, 'clauses_paid': 0, 'clauses_received': 0}
                            financials_map[seller_id]['total_income'] += amount
                            financials_map[seller_id]['sales'] += 1
                            if is_clause:
                                financials_map[seller_id]['clauses_received'] += 1

                        if buyer_id and buyer_id != seller_id:
                            if buyer_id not in financials_map:
                                financials_map[buyer_id] = {'user_name': buyer_name, 'total_spent': 0, 'total_income': 0, 'bids_placed': 0, 'purchases': 0, 'sales': 0, 'clauses_paid': 0, 'clauses_received': 0}
                            financials_map[buyer_id]['total_spent'] += amount
                            financials_map[buyer_id]['purchases'] += 1
                            if is_clause:
                                financials_map[buyer_id]['clauses_paid'] += 1

            df_transfers = pd.DataFrame(transfers_list)
            df_bids = pd.DataFrame(bids_list)
            fin_rows = [{'user_id': k, **v, 'net_balance_change': v['total_income'] - v['total_spent']} for k, v in financials_map.items()]
            df_financials = pd.DataFrame(fin_rows)

            self.df_board_transfers = df_transfers
            self.df_board_bids = df_bids
            self.df_rival_financials = df_financials

            return {'transfers': df_transfers, 'bids': df_bids, 'financials': df_financials}
        except Exception as e:
            print(f"⚠️ Excepción al extraer el muro de la liga: {e}")
            self.df_board_transfers = pd.DataFrame()
            self.df_board_bids = pd.DataFrame()
            self.df_rival_financials = pd.DataFrame()
            return {'transfers': pd.DataFrame(), 'bids': pd.DataFrame(), 'financials': pd.DataFrame()}

    def run(self, session):
        """
        Ejecuta y devuelve todos los DataFrames generados por la clase, incluyendo el muro de noticias de la liga.
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

        # 4. Muro de la liga (Transacciones y Pujas)
        try:
            print("⏳ Extraídos datos del muro de la liga (transacciones y pujas de rivales)...")
            board_res = self.league_board_info(session)
            print(f"✅ Board extracted: {len(board_res['transfers'])} transacciones, {len(board_res['bids'])} pujas perdedoras de rivales")
        except Exception as e:
            print(f"⚠️ Error al extraer el muro de la liga: {e}")

        # 5. Jugadores de todos los equipos (Lento)
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
            'board_transfers': getattr(self, 'df_board_transfers', pd.DataFrame()),
            'board_bids': getattr(self, 'df_board_bids', pd.DataFrame()),
            'rival_financials': getattr(self, 'df_rival_financials', pd.DataFrame()),
            'league_players': df_league_players
        }