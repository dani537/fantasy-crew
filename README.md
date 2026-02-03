# 🚀 Fantasy Crew (Multi-Agent System)

> [!NOTE]
> This document is also available in [Catalan](docs/README.ca.md) and [Spanish](docs/README.es.md).

**Goal:** Build a team of autonomous AI agents to manage a Biwenger squad, optimizing both sporting and financial performance using state-of-the-art LLMs and advanced data analysis.

This system outperforms a human player by eliminating emotional bias, operating 24/7, and processing massive volumes of real-time data to maximize Squad Value and total points.

---

## 👥 The Coaching Staff (The Agents)

The system operates using a **sequential multi-agent architecture**, where each role utilizes Language Models (LLMs) and data processing to add value at a specific stage of the pipeline.

### 1. 🔮 The Analyst (Data Analyst)
**"The Source of Truth"**
*   **Role:** Data engineering and consolidation agent. Prepares the ground for language models through deterministic cleaning.
*   **Processing (Feature Engineering):**
    *   **Multi-Source Fuzzy Matching:** Cross-references team and player names across Biwenger, Comuniate, and bookmakers (Odds), resolving discrepancies (e.g., "RCD Espanyol" vs "Espanyol").
    *   **Tactical Normalization:** Maps numerical positions to readable labels (`GK`, `DF`, `MF`, `FW`) and processes alternative positions.
    *   **Probability Cleaning:** Converts noise in press data (e.g., "80%") into clean numerical values for analysis.
    *   **Token Optimization:** Rounds metrics to 2 decimal places to maximize efficiency within LLM context windows.
*   **Output:** Generates `df_master_analysis.csv` (full squad data) and enriches `data/next_match.csv` with victory probabilities (Odds).

### 2. 📋 The Coach (The Mister)
**"The Sports Strategist"**
*   **Role:** Makes tactical decisions based on performance and availability.
*   **Logic (DeepSeek):**
    *   **Temporal Context:** Considers the current date/time and the proximity of the next game week.
    *   **Lineup Management:** Prioritizes offensive formations (3-4-3) but remains flexible to avoid the **-4 point penalty** for empty slots.
    *   **Club Awareness:** Recognizes teammates (via `TEAM_NAME`) to secure the goal if both the starter and substitute goalkeepers from the same club are available.
    *   **Momentum Analysis:** Evaluates recent form (`PLAYER_FITNESS`) and relative performance (Home/Away) against opponent difficulty (Odds).
*   **Market Strategy:** Defines which players are necessary sales (**REAL**) and which are listed to receive preventive offers (**RESERVE**).

### 3. 💼 The Sporting Director (The Broker)
**"The Financial Controller"**
*   **Role:** Executes market strategy under strict budgetary discipline.
*   **Logic (DeepSeek):**
    *   **The Positive Balance Dogma:** Priority #1 is ensuring the team does not start the game week with a negative balance (which would result in zero points).
    *   **Budget Management:** Loads real balance from `user_info.csv` and estimates revenue from proposed sales to calculate purchasing power.
    *   **Needs-Based Scouting:** Cross-references the Coach's cries for help (e.g., "WE NEED MF") with the best opportunities on the market.
*   **Output:** Signing proposals that balance sporting impact and profitability (`ROI`).

### 4. 🧠 The President (The Strategist)
**"The Executive Authority"**
*   **Role:** Final validator with a vision for risk and long-term strategy.
*   **Logic (DeepSeek):**
    *   **Budgetary Filter:** Applies maximum financial severity; rejects ostentatious signings that compromise the club's stability.
    *   **Conditional Approval:** May authorize a signing subject to the prior sale of a non-performing asset.
*   **Output:** Issues the **Final Executive Report** with the definitive actions to be taken.

---

## 🔄 Workflow

The system runs these agents in a chain (`main.py`):

1.  **Extract & Transform:** `DataAnalyst` downloads data and creates the `df_master_analysis`.
2.  **Squad Analysis:** `Coach` reads your team data and detects issues.
3.  **Market Scouting:** `SportingDirector` reads the Coach's report and searches for market solutions.
4.  **Executive Decision:** `President` reviews solutions and gives the green light.
5.  **Reporting:** The final `final_recommendations.md` file is generated, documenting the entire process.

---

## 📊 Data Sources

The system is powered by a robust data architecture extracted automatically through various processes (`src/`):

### 1. Biwenger API (Official Data)
Direct connection to the Biwenger API to obtain the real state of the league.
*   **LaLiga General Data (`LaLigaGeneralData`):**
    *   Complete **Player** database (Points, Price, Status, Fitness, Home/Away stats).
    *   **Team** information (Calendar, Upcoming opponents).
    *   **Next Game Week** data (Schedules, Matches).
*   **User League Data (`UserLeagueData`):**
    *   **Rivals:** We scan all rival squads to know their lineups, purchase prices, and most importantly, their **Release Clauses**.
    *   **Market:** Monitoring free agents for sale and offers received for our players.
    *   **Standings:** Current state of the points table and squad value.

### 2. Comuniate (Advanced Web Scraping)
Extraction of tactical intelligence from *Comuniate.com* using `BeautifulSoup`.
*   **Probable Lineups:** Predicted starting elevens for the next game week.
*   **Start Probability:** Estimated percentage of a player starting the match.
*   **Status Alerts:** Detection of players **One Card Away** from suspension or **Doubtful** due to injury.
*   **Tactical Positions:** Precise classification of the player's role on the pitch.

### 3. Jornada Perfecta (RSS & News Analysis)
Real-time news ingestion system from *JornadaPerfecta.com*.
*   **News Processing:** Reading and cleaning of sports articles.
*   **LLM Summarization:** Transforming news into optimized formats for "The Oracle" (AI) to understand context (injuries, rotations, press conferences).

### 4. Betting Houses (Odds)
Market statistical data to support decision-making.
*   **Match Prediction:** Mathematical probabilities (1X2) extracted and mapped for each match.
*   **Player Difficulty:** Allows evaluating if a player faces an "easy" match (clear favorite) or a "wall" (the opponent is favorite), optimizing lineup recommendations.
*   **Synchronization:** Automatic mapping via the Analyst to cross betting data with the Biwenger squad.

---

## 🛠️ Technology Stack

*   **Language:** Python 3.12+
*   **Agent Management:** LangGraph / CrewAI (Role orchestration).
*   **Data Processing:**
    *   `Pandas` for DataFrame manipulation and data cleaning.
    *   `BeautifulSoup4` for Web Scraping (Comuniate).
    *   `Feedparser` for RSS reading.
*   **AI Models (LLMs):**
    *   **DeepSeek-V3:** Intermediate logic and structured data processing (High Performance/Low Cost).
    *   **DeepSeek-R1:** Complex reasoning engine for the "President".
    *   **Gemini 1.5 Flash:** Long-context analysis (wide window) for processing massive news feeds.

---

## 🎯 Competitive Advantage

1.  **No Emotional Bias:** The system does not "fall in love" with players. It sells when statistics indicate a decline and buys when it detects an opportunity.
2.  **Financial Engineering:** Precise calculation of future value, clauses, and profit margins.
3.  **Reaction Speed:** Ability to buy or sell seconds after a relevant news item occurs (training injury, confirmed lineup).
4.  **Global Vision:** Cross-referencing market data, news, and advanced statistics that would take a human hours to compile manually.
