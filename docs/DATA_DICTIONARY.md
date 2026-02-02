# Data Extraction Dictionary

This document provides a comprehensive overview of the data extractions performed by the Biwenger Agent. It details the source of the data, the extraction logic, and the resulting fields saved in various CSV files.

---

## 1. Players Data (`players.csv`)

**Source:** `LaLigaGeneralData.players_info` via Biwenger API (`/competitions/la-liga/data`).

| Field | Description |
| :--- | :--- |
| `PLAYER_ID` | Unique identifier for the player. |
| `PLAYER_NAME` | Full name of the player. |
| `PLAYER_SLUG` | URL-friendly name of the player. |
| `PLAYER_TEAM_ID` | ID of the team the player belongs to. |
| `PLAYER_POSITION` | Primary playing position (1: GK, 2: DF, 3: MF, 4: FW). |
| `PLAYER_ALT_POSITIONS` | Alternative positions the player can play. |
| `PLAYER_PRICE` | Current market value in Biwenger. |
| `PLAYER_PRICE_INCREMENT` | Daily change in market value. |
| `PLAYER_STATUS` | Availability status (e.g., "ok", "injured", "doubt"). |
| `PLAYER_STATUS_INFO` | Additional details regarding player status. |
| `PLAYER_FITNESS` | Recent form indicators. |
| `PLAYER_POINTS` | Total points accumulated in the season. |
| `PLAYER_POINTS_HOME` | Total points earned in home games. |
| `PLAYER_POINTS_AWAY` | Total points earned in away games. |
| `PLAYER_PLAYED_HOME` | Number of home games played. |
| `PLAYER_PLAYED_AWAY` | Number of away games played. |
| `AVG_POINTS` | Calculated average points per game. |
| `AVG_POINTS_HOME` | Average points in home games. |
| `AVG_POINTS_AWAY` | Average points in away games. |

---

## 2. Teams Data (`teams.csv`)

**Source:** `LaLigaGeneralData.teams_info` via Biwenger API (`/competitions/la-liga/data`).

| Field | Description |
| :--- | :--- |
| `TEAM_ID` | Unique identifier for the team. |
| `TEAM_NAME` | Name of the football club. |
| `TEAM_SLUG` | URL-friendly name of the team. |
| `TEAM_NEXT_GAME_DATE` | Date and time of the next scheduled match. |
| `TEAM_NEXT_GAME_HOME` | Name of the home team for the next match. |
| `TEAM_NEXT_GAME_AWAY` | Name of the away team for the next match. |
| `TEAM_NEXT_GAME` | Friendly match string (e.g., "Real Madrid - Barcelona"). |
| `TEAM_IS_HOME` | Boolean indicating if the team plays at home in the next match. |

---

## 3. Next Match Schedule (`next_jornada.csv`)

**Source:** `LaLigaGeneralData.next_jornada_info` via Biwenger API (`/rounds/la-liga`).

| Field | Description |
| :--- | :--- |
| `NEXT_MATCH_JORNADA` | Name or number of the upcoming matchday. |
| `NEXT_MATCH_FECHA` | Kick-off timestamp (localized). |
| `NEXT_MATCH_LOCAL` | Name of the home team. |
| `NEXT_MATCH_VISITANTE` | Name of the away team. |
| `NEXT_MATCH_PARTIDO` | Match description (e.g., "Team A vs Team B"). |
| `NEXT_MATCH_ESTADIO` | Venue of the match. |
| `NEXT_MATCH_STATUS` | Current status of the match (e.g., "not_started"). |

---

## 4. User League Players (`league_players.csv`)

**Source:** `UserLeagueData.league_players_info` (Iterative team detail extraction).

| Field | Description |
| :--- | :--- |
| `BIWPLAYER_TEAM_ID` | Identifier of the user's team in the league. |
| `BIWPLAYER_TEAM_NAME` | Name of the user's team. |
| `BIWPLAYER_ID` | Global Biwenger player ID. |
| `BIWPLAYER_PURCHASE_DATE` | Date when the player was acquired. |
| `BIWPLAYER_PURCHASE_PRICE` | Amount paid for the player. |
| `BIWPLAYER_CLAUSE` | Current release clause value. |
| `BIWPLAYER_CLAUSE_LOCKED_UNTIL` | Timestamp until the clause remains non-payable. |
| `BIWPLAYER_INVESTED` | Total amount invested in the player. |

---

## 5. League Standings (`league_teams.csv`)

**Source:** `UserLeagueData.league_table` via Biwenger API (`/league?include=standings`).

| Field | Description |
| :--- | :--- |
| `BIWTEAM_ID` | Unique identifier for the user team. |
| `BIWTEAM_NAME` | Name of the user team. |
| `BIWTEAM_POINTS` | Current points in the league standings. |
| `BIWTEAM_POSITION` | Current rank in the league. |
| `BIWTEAM_TEAM_SIZE` | Number of players in the roster. |
| `BIWTEAM_TEAM_VALUE` | Total market value of the squad. |
| `BIWTEAM_TEAM_VALUE_INC` | Recent change in squad value. |

---

## 6. Market Offers (`market_offers.csv`)

**Source:** `UserLeagueData.market_offers_info` via Biwenger API (`/market`).

| Field | Description |
| :--- | :--- |
| `MARKET_OFFER_ID` | Unique identifier for the offer. |
| `MARKET_OFFER_AMOUNT` | Amount offered for a player. |
| `MARKET_OFFER_CREATED` | Creation timestamp of the offer. |
| `MARKET_OFFER_UNTIL` | Expiry timestamp of the offer. |
| `MARKET_OFFER_STATUS` | Current state (e.g., "waiting", "accepted"). |
| `MARKET_OFFER_TYPE` | Type of offer (e.g., transfer, loan). |
| `MARKET_OFFER_FROM_ID` | ID of the user/entity making the offer. |
| `MARKET_OFFER_FROM_NAME` | Name of the offeror (e.g., "Mercado"). |
| `MARKET_OFFER_REQUESTED_PLAYER_ID` | ID of the player involved in the offer. |

---

## 7. Market Sales (`market_sales.csv`)

**Source:** `UserLeagueData.market_sales_info` via Biwenger API (`/market`).

| Field | Description |
| :--- | :--- |
| `MARKET_SALE_PLAYER_ID` | ID of the player put up for sale. |
| `MARKET_SALE_PRICE` | Sale price or initial bidding amount. |
| `MARKET_SALE_DATE` | Listing date. |
| `MARKET_SALE_UNTIL` | Listing expiration date. |
| `MARKET_SALE_USER_ID` | Seller ID. |
| `MARKET_SALE_USER_NAME` | Seller name (e.g., "Mercado"). |
| `MARKET_SALE_CLAUSE` | Player's release clause at the time of sale. |

---

## 8. Comuniate Probable Lineups (`comuniate.csv`)

**Source:** `ComuniateData.run` (Web Scraping from `comuniate.com`).

| Field | Description |
| :--- | :--- |
| `COMUNIATE_POSITION` | Player position as per Comuniate. |
| `COMUNIATE_NAME` | Player name as parsed from the lineup. |
| `COMUNIATE_SUPPLENT` | Name of the potential substitute if available. |
| `COMUNIATE_STARTER` | Percentage indicating likelihood of starting. |
| `COMUNIATE_CAUTIONED` | Indicator if the player is one yellow card away from suspension. |
| `COMUNIATE_DOUBT` | Boolean indicating physical or technical doubt. |
| `COMUNIATE_TEAM` | Team name associated with the lineup. |
| `COMUNIATE_TEAM_ID` | Internal Comuniate team identifier. |

---

## 9. News Feed (`news.csv`)

**Source:** `JornadaPerfectaData.run` (RSS Feed from `jornadaperfecta.com`).

| Field | Description |
| :--- | :--- |
| `fecha` | Publication date of the news item. |
| `titulo` | Headline of the news article. |
| `resumen` | Short excerpt or summary of the content. |
| `tags` | Relevant keywords (labels) for the article. |

---

## 10. Match Odds (`odds.csv`)

**Source:** `EuroClubIndexData.run` (API from `euroclubindex.com`).

| Field | Description |
| :--- | :--- |
| `ODDS_FECHA` | Match date. |
| `ODDS_LOCAL` | Home team name. |
| `ODDS_VISITANTE` | Away team name. |
| `ODDS_1` | Winning probability for the home team (decimal). |
| `ODDS_X` | Draw probability (decimal). |
| `ODDS_2` | Winning probability for the away team (decimal). |
| `ODDS_HOME_GOALS` | Predicted goals for the home team. |
| `ODDS_AWAY_GOALS` | Predicted goals for the away team. |

---

## Technical Transformation Notes

The `tables_columns` function in `main.py` is responsible for renaming raw API fields to the standardized format documented above. This ensures consistency across the application’s analytical modules.
