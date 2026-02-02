import requests
import pandas as pd
import re
import time
import random
import os
import feedparser
from datetime import datetime
from dateutil import parser as date_parser
from bs4 import BeautifulSoup
from src.data_extraction.auth import get_random_user_agent

class ComuniateData:
    """
    Clase para interactuar con Comuniate.com y extraer datos externos como alineaciones probables.
    """
    AJAX_URL = "https://www.comuniate.com/ajax/pintar_alineacion.php"
    BASE_URL = "https://www.comuniate.com/"

    def __init__(self, session=None):
        """
        Inicializa la clase con una sesión de requests opcional.
        """
        self.session = session or requests.Session()
        self.id_jornada = None
        self.teams_map = {} # {id_equipo: nombre_equipo}

    def initialize_session(self):
        """
        Configura la sesión con un User-Agent aleatorio permanente y captura cookies iniciales.
        """
        try:
            headers = {
                'User-Agent': get_random_user_agent(),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'es-ES,es;q=0.8,en-US;q=0.5,en;q=0.3',
                'Connection': 'keep-alive',
            }
            self.session.headers.update(headers)
            response = self.session.get(self.BASE_URL)
            response.raise_for_status()
            print(f"🌐 Sesión inicializada. User-Agent persistente: {self.session.headers.get('User-Agent')}")
        except Exception as e:
            print(f"⚠️ Error al inicializar la sesión: {e}")
            raise

    def load_league_data(self):
        """
        Extrae metadatos (ID jornada y equipos) de la página principal.
        """
        try:
            # Reutilizamos la sesión ya inicializada
            response = self.session.get(self.BASE_URL)
            response.raise_for_status()
            html = response.text
            
            # 1. Extraer el número de jornada desde div.fuente_alternativa > span.success
            soup = BeautifulSoup(html, 'html.parser')
            fuente_alternativa = soup.find('div', class_='fuente_alternativa')
            if fuente_alternativa:
                span_jornada = fuente_alternativa.find('span', class_='success')
                if span_jornada:
                    self.id_jornada = int(span_jornada.get_text(strip=True))
                    print(f"📅 Jornada detectada: {self.id_jornada}")
                else:
                    raise Exception("❌ No se encontró el span.success con la jornada.")
            else:
                raise Exception("❌ No se encontró el elemento 'fuente_alternativa' para la jornada.")
                
            # 2. Extraer mapeo de equipos desde div#fila-escudos
            fila_escudos = soup.find('div', id='fila-escudos')
            if fila_escudos:
                enlaces = fila_escudos.find_all('a', class_='enlace-escudos')
                for a in enlaces:
                    id_equipo = a.get('data-id-equipo')
                    img = a.find('img')
                    if id_equipo and img:
                        nombre = img.get('alt', '').replace("Alineación y plantilla de ", "").strip()
                        self.teams_map[int(id_equipo)] = nombre
                print(f"✅ Mapeo de equipos cargado ({len(self.teams_map)} equipos).")
            else:
                print("⚠️ No se encontró el elemento #fila-escudos.")
        except Exception as e:
            print(f"⚠️ Error al cargar los datos de la liga: {e}")
            raise

    def get_probable_lineup(self, id_jornada: int = None, id_equipo: int = None, modo: str = "clasico") -> str:
        """
        Realiza una petición POST para obtener el HTML de la alineación probable de un equipo.
        
        Args:
            id_jornada (int): ID de la jornada (ej. 22). Si es None, usa el detectado en initialize_session.
            id_equipo (int): ID del equipo en Comuniate (ej. 89 para Alavés)
            modo (str): Modo de juego, por defecto "clasico"
            
        Returns:
            str: El contenido HTML de la alineación o None si hay error.
        """
        jornada = id_jornada or self.id_jornada
        if not jornada:
            raise ValueError("Debe proporcionar un id_jornada o llamar primero a initialize_session()")
        
        if not id_equipo:
            raise ValueError("Debe proporcionar un id_equipo")

        headers = {
            'accept': '*/*',
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://www.comuniate.com',
            'referer': f'https://www.comuniate.com/equipos/{id_equipo}/alineacion',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
        }

        payload = {
            'id_jornada': str(jornada),
            'id_equipo': str(id_equipo),
            'modo': modo
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.session.post(self.AJAX_URL, headers=headers, data=payload)
                response.raise_for_status()
                return response.text
            except requests.exceptions.RequestException as e:
                print(f"⚠️ Intento {attempt + 1}/{max_retries} fallido para equipo {id_equipo}: {e}")
                if attempt < max_retries - 1:
                    wait_time = random.uniform(2, 5) * (attempt + 1)
                    print(f"⏳ Reintentando en {wait_time:.2f}s...")
                    time.sleep(wait_time)
                else:
                    print(f"❌ Error persistente tras {max_retries} intentos.")
                    return None

    def parse_lineup_html(self, html_content: str) -> pd.DataFrame:
        """
        Parsea el HTML de la alineación y extrae la información de cada jugador.
        
        Returns:
            pd.DataFrame: DataFrame con las columnas [posicion, nombre, suplente, titularidad, apercibido, duda]
        """
        if not html_content:
            return None
        
        soup = BeautifulSoup(html_content, 'html.parser')
        players_data = []

        # Mapeo de IDs de contenedores a nombres de posición
        position_map = {
            'portero': 'Portero',
            'defensas': 'Defensa',
            'medios': 'Centrocampista',
            'delanteros': 'Delantero'
        }

        for container_id, pos_name in position_map.items():
            container = soup.find('div', id=container_id)
            if not container:
                continue
            
            # Buscamos cada bloque de jugador
            players = container.find_all('div', class_='jugador_campo')
            for p in players:
                # 1. Nombre y Suplente (Alternativo)
                nombre_div = p.find('div', class_='nombre_jugador')
                nombre = ""
                suplente = None
                
                if nombre_div:
                    # El nombre suele ser el primer texto o lo que no es div alternativo
                    alternativo_div = nombre_div.find('div', class_='alternativo')
                    if alternativo_div:
                        suplente = alternativo_div.get_text(strip=True)
                        # Extraemos el texto del padre quitando el del hijo para obtener solo el nombre del titular
                        nombre = nombre_div.get_text(strip=True).replace(suplente, "").strip()
                    else:
                        nombre = nombre_div.get_text(strip=True)

                # 2. Titularidad (%)
                titularidad = "100%" # Por defecto si no hay icono_porcentaje
                porcentaje_div = p.find('div', class_='icono_porcentaje')
                if porcentaje_div:
                    titularidad = porcentaje_div.get_text(strip=True)

                # 3. Apercibido
                apercibido = None
                apercibido_div = p.find('div', class_='apercibido')
                if apercibido_div:
                    apercibido = apercibido_div.get_text(strip=True)

                # 4. Duda
                duda = p.find('div', class_='duda') is not None
                
                players_data.append({
                    'posicion': pos_name,
                    'nombre': nombre,
                    'suplente': suplente,
                    'titularidad': titularidad,
                    'apercibido': apercibido,
                    'duda': duda
                })

        return pd.DataFrame(players_data)

    def extract_all_lineups(self, id_jornada: int = None, max_teams: int = None, output_file: str = None):
        """
        Extrae y parsea todas las alineaciones de la liga secuencialmente.
        
        Args:
            id_jornada (int): ID de la jornada. Si es None, usa el detectado.
            max_teams (int): Límite de equipos a procesar (para pruebas rápidas).
            output_file (str): Path al archivo CSV para guardado incremental.
            
        Returns:
            pd.DataFrame: DataFrame consolidado.
        """
        jornada = id_jornada or self.id_jornada
        if not self.teams_map:
            print("📢 Cargando datos de la liga antes de la extracción masiva...")
            self.load_league_data()
            
        all_players = []
        teams_to_process = list(self.teams_map.items())
        
        # Lógica de Resume: Verificar equipos ya procesados en el CSV
        processed_teams = set()
        if output_file and os.path.exists(output_file):
            try:
                existing_df = pd.read_csv(output_file)
                if 'id_equipo_comuniate' in existing_df.columns:
                    processed_teams = set(existing_df['id_equipo_comuniate'].unique())
                    print(f"🔄 Detectado archivo existente. {len(processed_teams)} equipos ya procesados.")
            except Exception as e:
                print(f"⚠️ Error al leer archivo existente: {e}")
        
        if max_teams:
            teams_to_process = teams_to_process[:max_teams]
            
        print(f"🚀 Iniciando extracción masiva para {len(teams_to_process)} equipos...")
        
        for i, (team_id, team_name) in enumerate(teams_to_process):
            if team_id in processed_teams:
                print(f"⏭️ Saltando {team_name} (ya procesado).")
                continue

            print(f"📦 [{i+1}/{len(teams_to_process)}] Extrayendo: {team_name}...")
            
            html = self.get_probable_lineup(id_jornada=jornada, id_equipo=team_id)
            if html:
                df = self.parse_lineup_html(html)
                if df is not None and not df.empty:
                    df['equipo'] = team_name
                    df['id_equipo_comuniate'] = team_id
                    all_players.append(df)
                    
                    # Guardado incremental
                    if output_file:
                        # Escribir header solo si el archivo no existe
                        header = not os.path.exists(output_file)
                        df.to_csv(output_file, mode='a', header=header, index=False)
                        print(f"💾 Guardado incremental en {output_file}")
                else:
                    print(f"⚠️ No se pudo parsear la alineación de {team_name}.")
            else:
                print(f"❌ Error al obtener HTML de {team_name}.")
            
            if i < len(teams_to_process) - 1:
                wait = random.uniform(10, 20)
                print(f"💤 Esperando {wait:.2f} segundos...")
                time.sleep(wait)
        
        # Si se usó archivo, recargarlo completo para devolver el total real
        if output_file and os.path.exists(output_file):
            return pd.read_csv(output_file)
            
        if all_players:
            master_df = pd.concat(all_players, ignore_index=True)
            print(f"✅ Extracción completada. Total jugadores: {len(master_df)}")
            return master_df
        
        print("⚠️ No se extrajo ningún dato nuevo.")
        return pd.DataFrame()

    def run(self, output_file: str = None) -> pd.DataFrame:
        """
        Método maestro que ejecuta todo el flujo:
        1. Inicializa sesión (headers persistentes).
        2. Carga datos de la liga (jornada + equipos).
        3. Realiza la extracción masiva.
        
        Args:
            output_file (str): Path opcional para guardado incremental.
            
        Returns:
            pd.DataFrame: DataFrame con TODOS los datos.
        """
        print("🎬 Iniciando ejecución completa del Agente Comuniate...")
        
        # 1. Preparar sesión
        self.initialize_session()
        
        # 2. Los datos de la liga se cargan automáticamente en extract_all_lineups si faltan,
        # pero forzamos la carga aquí para asegurar que todo está listo antes de empezar el loop.
        if not self.teams_map:
            self.load_league_data()
            
        # 3. Ejecutar extracción
        return self.extract_all_lineups(output_file=output_file)

class JornadaPerfectaData:
    """
    Clase para interactuar con JornadaPerfecta.com y extraer noticias vía RSS.
    """
    FEED_URL = "https://www.jornadaperfecta.com/feed/"

    def __init__(self, session=None):
        self.session = session or requests.Session()

    def fetch_news(self) -> pd.DataFrame:
        """
        Obtiene las últimas noticias del feed RSS de Jornada Perfecta.
        
        Returns:
            pd.DataFrame: DataFrame con las noticias [title, link, published, summary, tags]
        """
        try:
            # Aunque feedparser puede manejar URLs, usamos requests para mayor control (User-Agent, etc.)
            headers = {'User-Agent': get_random_user_agent()}
            response = self.session.get(self.FEED_URL, headers=headers)
            response.raise_for_status()
            
            feed = feedparser.parse(response.content)
            
            news_items = []
            for entry in feed.entries:
                tags = [tag.term for tag in entry.get('tags', [])]
                news_items.append({
                    'title': entry.get('title'),
                    'link': entry.get('link'),
                    'published': entry.get('published'),
                    'summary': entry.get('summary'),
                    'tags': tags
                })
            
            df = pd.DataFrame(news_items)
            print(f"✅ Se han obtenido {len(df)} noticias de Jornada Perfecta.")
            return df
        except Exception as e:
            print(f"⚠️ Error al obtener el feed de Jornada Perfecta: {e}")
            return pd.DataFrame()

    def get_clean_news(self, df: pd.DataFrame = None) -> pd.DataFrame:
        """
        Limpia y optimiza el DataFrame de noticias para consumo de LLMs (ahorro de tokens).
        """
        if df is None:
            df = self.fetch_news()
        
        if df.empty:
            return df

        clean_data = []
        for _, row in df.iterrows():
            # 1. Limpiar fecha (Día y Hora corta)
            try:
                dt = date_parser.parse(row['published'])
                clean_date = dt.strftime("%Y-%m-%d %H:%M")
            except:
                clean_date = row['published']

            # 2. Limpiar summary (quitar HTML y espacios extra)
            summary = row['summary']
            if summary:
                # Quitar etiquetas HTML
                summary = re.sub(r'<[^>]+>', '', summary)
                # Normalizar espacios y saltos de línea
                summary = ' '.join(summary.split())
                # Truncar si es excesivo (opcional, pero ayuda a tokens)
                if len(summary) > 300:
                    summary = summary[:297] + "..."

            # 3. Limpiar tags (unirlos en string)
            tags = row['tags']
            if isinstance(tags, list):
                tags_str = ', '.join(tags)
            else:
                tags_str = ""

            clean_data.append({
                'fecha': clean_date,
                'titulo': row['title'],
                'resumen': summary,
                'tags': tags_str
            })

        return pd.DataFrame(clean_data)

    def run(self) -> pd.DataFrame:
        """
        Método maestro que combina la obtención y limpieza de noticias.
        
        Returns:
            pd.DataFrame: DataFrame optimizado para LLMs.
        """
        print("🎬 Iniciando extracción y limpieza de noticias de Jornada Perfecta...")
        df_raw = self.fetch_news()
        return self.get_clean_news(df_raw)

class EuroClubIndexData:
    """
    Clase para interactuar con EuroClubIndex y extraer probabilidades de partidos.
    """
    API_URL = "https://www.euroclubindex.com/wp-json/happyhorizon/v1/get-module-match-odds/"
    REFERER_URL = "https://www.euroclubindex.com/match-odds/"

    def __init__(self, session=None):
        self.session = session or requests.Session()

    def get_match_odds(self, league_id: int = 67) -> pd.DataFrame:
        """
        Realiza la petición a la API y extrae las probabilidades de los partidos.
        
        Args:
            league_id (int): ID de la liga (67 para LaLiga).
            
        Returns:
            pd.DataFrame: DataFrame con las probabilidades [fecha, local, visitante, 1, X, 2]
        """
        try:
            # Configurar headers similares a los del navegador
            headers = {
                'User-Agent': get_random_user_agent(),
                'Accept': '*/*',
                'Referer': self.REFERER_URL,
                'X-Requested-With': 'XMLHttpRequest', # Común en WP AJAX/API
                'Origin': 'https://www.euroclubindex.com'
            }
            
            # Parametros de la query (payload en GET)
            params = {
                'selected_league': league_id
            }

            print(f"🌍 Consultando EuroClubIndex para liga {league_id}...")
            response = self.session.get(self.API_URL, headers=headers, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            # Verificar si hay datos de partidos
            if 'matchOdds' not in data:
                print("⚠️ La respuesta no contiene 'matchOdds'.")
                return pd.DataFrame()

            matches_data = []
            for match in data['matchOdds']:
                match_info = {
                    'fecha': match.get('d_Date'),
                    'local': match.get('c_HomeTeam'),
                    'visitante': match.get('c_Awayteam'),
                    '1': float(match.get('n_OddHomeWin', 0)),
                    'X': float(match.get('n_OddDraw', 0)),
                    '2': float(match.get('n_OddAwayWin', 0)),
                    'home_goals': match.get('n_HomeGoals'),
                    'away_goals': match.get('n_AwayGoals')
                }
                matches_data.append(match_info)
            
            df = pd.DataFrame(matches_data)
            print(f"✅ Se han obtenido {len(df)} partidos con probabilidades.")
            return df

        except Exception as e:
            print(f"❌ Error al obtener datos de EuroClubIndex: {e}")
            return pd.DataFrame()

    def run(self) -> pd.DataFrame:
        """
        Método maestro para ejecutar la extracción.
        """
        return self.get_match_odds()