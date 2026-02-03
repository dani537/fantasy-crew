# PROCESO DE ANÁLISIS Y ROLES

Este documento detalla el flujo de información y las responsabilidades de cada agente dentro del sistema Biwenger Agent.

## 1. Data Analyst (El Arquitecto de Datos)
Es el núcleo técnico del sistema. Su responsabilidad es descargar, limpiar y transformar toda la información necesaria para que los demás agentes puedan tomar decisiones.

### Tablas y Datos Extraídos (CSVs en `./data/`)
El Analista genera los siguientes archivos para persistir la información:

| Archivo | Descripción |
| :--- | :--- |
| `players.csv` | Estadísticas base de todos los jugadores de LaLiga (puntos, precios, etc.). |
| `teams.csv` | Información de los 20 equipos de LaLiga y sus próximos enfrentamientos. |
| `next_jornada.csv` | Fechas, horarios y estadios de los partidos de la próxima jornada. |
| `league_players.csv` | Jugadores pertenecientes a otros managers de nuestra liga (incluye sus cláusulas). |
| `league_teams.csv` | Clasificación actual de nuestra liga, valor de equipo y puntos. |
| `market_offers.csv` | Ofertas recibidas por nuestros jugadores (del mercado o de otros managers). |
| `market_sales.csv` | Jugadores que están actualmente disponibles para comprar en el mercado. |
| `comuniate.csv` | Probabilidades de titularidad y estados de salud (lesionados, dudas). |
| `news.csv` | Últimas noticias y crónicas de relevancia para la jornada. |
| `odds.csv` | Probabilidades de victoria (cuotas) para cada partido. |
| `user_info.csv` | Datos críticos del usuario: Presupuesto, IDs de liga/equipo y balance. |
| `rounds.csv` | Definición de todas las jornadas de la temporada. |
| `active_events.csv` | Estado de las jornadas en juego (cuándo empiezan y cuándo terminan). |

### Transformaciones y Lógica
El Analista no solo descarga datos, sino que crea el `df_master` mediante consolidación y **Feature Engineering**:
- **Matching Difuso**: Une nombres de diferentes fuentes (Biwenger vs Comuniate).
- **Métricas Financieras**: Calcula el `VALUE_SCORE` (puntos por millón), `TREND_SCORE` (tendencia de precio) y la rentabilidad de las cláusulas.

---

## 2. Roles del Equipo

### Coach (El Míster)
- **Misión**: Maximizar los puntos de la jornada inmediata.
- **Datos**: Utiliza el `df_master` filtrado por nuestro equipo, el calendario de `next_jornada.csv` y las probabilidades de `comuniate.csv`.
- **Estrategia**: Decide el 11 ideal, la formación táctica y optimiza posiciones para aprovechar los bonus por gol según el puesto.

### Sporting Director (El Broker)
- **Misión**: Planificación estratégica del equipo y salud financiera.
- **Datos**: Utiliza el `df_master` completo (mercado), `user_info.csv` y `active_events.csv`.
- **Estrategia**: Se encarga de que el balance sea siempre positivo para evitar sanciones, propone fichajes basados en las métricas de valor del Analista y gestiona el plan de ventas.

### President (El Presi)
- **Misión**: Autoridad final y visión global.
- **Rol**: Evalúa el "Plan de Transferencias" del Director Deportivo y la alineación del Coach, dando el visto bueno final para la ejecución de las operaciones.
