# 🚀 Fantasy Crew (Multi-Agent System)

> [!NOTE]
> Aquest document també està disponible en [Anglès](README.md) i [Castellà](README.es.md).

**Objectiu:** Crear un equip d'agents d'IA autònoms que gestionin una plantilla de Biwenger, optimitzant el rendiment esportiu i financer mitjançant l'ús de LLMs d'última generació i anàlisi de dades avançat.

Aquest sistema supera un jugador humà en eliminar el biaix emocional, operar 24/7 i processar grans volums de dades en temps real per maximitzar el Valor de Mercat (VM) i la puntuació de la plantilla.

---

## 👥 L'Staff Tècnic (Els Agents)

El sistema opera mitjançant una **arquitectura seqüencial de multi-agents**, on cada rol utilitza models de llenguatge (LLMs) i processament de dades per aportar valor en una etapa específica del pipeline.

### 1. 🔮 L'Analista (Data Analyst)
**"La Font de Veritat"**
*   **Rol:** Agent d'enginyeria i consolidació de dades. Prepara el terreny per als models de llenguatge mitjançant neteja determinista.
*   **Processament (Feature Engineering):**
    *   **Fuzzy Matching Multi-Font:** Creua noms d'equips i jugadors entre Biwenger, Comuniate i cases d'aposta (Odds), resolent discrepàncies (ex. "RCD Espanyol" vs "Espanyol").
    *   **Normalització Tàctica:** Mapeja posicions numèriques a etiquetes llegibles (`GK`, `DF`, `MF`, `FW`) i processa posicions alternatives.
    *   **Neteja de Probabilitats:** Converteix sorolls en les dades de premsa (ex. "80%") en valors numèrics nets per a l'anàlisi.
    *   **Optimització de Tokens:** Arrodoneix mètriques a 2 decimals per maximitzar l'eficiència en la finestra de context dels LLMs.
*   **Sortida:** Genera `df_master_analysis.csv` (plantilla completa) i enriqueix `data/next_match.csv` amb probabilitats de victòria (Odds).

### 2. 📋 L'Entrenador (The Mister)
**"L'Estratega Esportiu"**
*   **Rol:** Pren decisions tàctiques basades en el rendiment i la disponibilitat.
*   **Lògica (DeepSeek):**
    *   **Context Temporal:** Considera la data/hora actual i la proximitat de l'inici de la jornada.
    *   **Gestió d'Alineacions:** Prioritza formacions ofensives (3-4-3) però és flexible per evitar la penalització de **-4 punts** per forats buits.
    *   **Consciència de Club:** Reconeix companys d'equip (via `TEAM_NAME`) per assegurar la porteria si compta amb el porter titular i el suplent del mateix club.
    *   **Anàlisi de Momentum:** Avalua la ratxa (`PLAYER_FITNESS`) i el rendiment relatiu (Casa/Fora) davant la dificultat del rival (Odds).
*   **Estratègia de Mercat:** Defineix quins jugadors són vendes necessàries (**REAL**) i quins s'incriuen per rebre ofertes preventives (**RESERVE**).

### 3. 💼 El Director Esportiu (The Broker)
**"El Controlador Financer"**
*   **Rol:** Executa l'estratègia de mercat sota una disciplina pressupostària estricta.
*   **Lògica (DeepSeek):**
    *   **El Dogma del Balanç Positiu:** La seva prioritat #1 és assegurar que l'equip no comenci la jornada amb saldo negatiu (fet que anul·laria els punts).
    *   **Gestió de Pressupost:** Carrega el saldo real des de `user_info.csv` i estima els ingressos per vendes proposades per calcular el poder de compra.
    *   **Scouting Basat en Necessitats:** Creua els crits d'auxili de l'Entrenador (ex. "NECESSITEM MC") amb les millors oportunitats del mercat.
*   **Sortida:** Projectes de fitxatge que equilibren impacte esportiu i rendibilitat (`ROI`).

### 4. 🧠 El President (The Strategist)
**"L'Autoritat Executiva"**
*   **Rol:** Validador final amb visió de risc i llarg termini.
*   **Lògica (DeepSeek):**
    *   **Filtre Pressupostari:** Aplica la màxima severitat financera; rebutja fitxatges ostentosos que comprometin l'estabilitat del club.
    *   **Aprovació Condicional:** Pot autoritzar un fitxatge supeditat a la venda prèvia d'un llast de l'equip.
*   **Sortida:** Emet l'**Informe Executiu Final** amb les accions definitives a prendre.

---

## 🔄 Flux de Treball (Workflow)

El sistema executa aquests agents en cadena (`main.py`):

1.  **Extract & Transform:** `DataAnalyst` descarrega dades i crea el `df_master_analysis`.
2.  **Squad Analysis:** `Coach` llegeix les dades del teu equip i detecta problemes.
3.  **Market Scouting:** `SportingDirector` llegeix l'informe del Coach i busca solucions al mercat.
4.  **Executive Decision:** `President` revisa les solucions i dona llum verda.
5.  **Reporting:** Es genera l'arxiu final `final_recommendations.md` amb tot el procés.

---

## 📊 Fonts de Dades (Data Sources)

El sistema s'alimenta d'una arquitectura de dades robusta extreta automàticament mitjançant diversos processos (`src/`):

### 1. Biwenger API (Dades Oficials)
Connexió directa amb l'API de Biwenger per obtenir l'estat real de la lliga.
*   **Dades Generals de LaLiga (`LaLigaGeneralData`):**
    *   Base de dades completa de **Jugadors** (Punts, Preu, Estat físic, Fitness, Estadístiques local/visitant).
    *   Informació d'**Equips** (Calendari, Pròxims rivals).
    *   Dades de la **Pròxima Jornada** (Horaris, Partits).
*   **Dades de la Lliga d'Usuari (`UserLeagueData`):**
    *   **Rivals:** Escanejem les plantilles de tots els rivals per conèixer les seves alineacions, preus de compra i, el més important, les seves **Clàusules de Rescissió**.
    *   **Mercat:** Monitorització de jugadors lliures en venda i ofertes rebudes pels nostres jugadors.
    *   **Classificació:** Estat actual de la taula de punts i valor d'equip.

### 2. Comuniate (Web Scraping Avançat)
Extracció d'intel·ligència tàctica des de *Comuniate.com* mitjançant `BeautifulSoup`.
*   **Alineacions Probables:** Predicció dels onzes titulars per a la següent jornada.
*   **Probabilitat de Titularitat:** Percentatge estimat que un jugador iniciï el partit.
*   **Alertes d'Estat:** Detecció de jugadors **Apercebuts** (risc de sanció) o **Dubte** per molèsties.
*   **Posicions Tàctiques:** Classificació precisa del rol del jugador al camp.

### 3. Jornada Perfecta (RSS & News Analysis)
Sistema d'ingesta de notícies en temps real des de *JornadaPerfecta.com*.
*   **Processament de Notícies:** Lectura i neteja d'articles esportius.
*   **Resum per a LLMs:** Transformació de notícies en formats optimitzats perquè "The Oracle" (IA) pugui llegir-les i entendre el context (lesions, rotacions, rodes de premsa).

### 4. Casas d'Aposta (Odds)
Dades estadístiques de mercat per recolzar la presa de decisions.
*   **Predicció de Partits:** Probabilitats matemàtiques (1X2) extretes i mapejades per a cada matx.
*   **Dificultat del Jugador:** Permet avaluar si un jugador s'enfronta a un partit "fàcil" (favorit clar) o un "mura" (el rival és favorit), optimitzant la recomanació d'alineació.
*   **Sincronització:** Mapeig automàtic mitjançant l'Analista per creuar dades d'apostes amb la plantilla de Biwenger.

---

## 🛠️ Stack Tecnològic

*   **Llenguatge:** Python 3.12+
*   **Gestió d'Agents:** LangGraph / CrewAI (Orquestració de rols).
*   **Processament de Dades:**
    *   `Pandas` per a manipulació de DataFrames i neteja de dades.
    *   `BeautifulSoup4` per a Web Scraping (Comuniate).
    *   `Feedparser` per a lectura de RSS.
*   **Models d'IA (LLMs):**
    *   **DeepSeek-V3:** Lògica intermèdia i processament de dades estructurades (High Performance/Low Cost).
    *   **DeepSeek-R1:** Motor de raonament complex per al "President".
    *   **Gemini 1.5 Flash:** Anàlisi de context llarg (finestra àmplia) per processar notícies massives.

---

## 🎯 Avantatge Competitiu

1.  **Sense Biaix Emocional:** El sistema no s'"enamora" de jugadors. Ven quan l'estadística indica declivi i fitxa quan detecta oportunitat.
2.  **Enginyeria Financera:** Càlcul precís del valor futur, clàusules i marges de benefici.
3.  **Velocitat de Reacció:** Capacitat de fitxar o vendre segons després que passi una notícia rellevant (lesió en entrenament, alineació confirmada).
4.  **Visió Global:** Creuat de dades de mercat, notícies i estadística avançada que un humà trigaria hores a recopilar manualment.
