# Arquitectura de Datos Históricos y Base para Backtesting (`2026-08-01` → Actualidad)

Este documento detalla toda la arquitectura de extracción, reconstrucción histórica retroactiva, almacenamiento **Dual-Sink (Google Sheets + SQLite/CSV Data Warehouse)** y automatización diaria en GitHub Actions implementada para alimentar el futuro **Motor de Backtesting Anti-Overfitting y Gestor Inteligente de Equipo**.

---

## 1. Visión General de la Arquitectura (Dual-Sink)

El sistema mantiene sincronizados dos almacenes complementarios cada día:

1. **Google Sheets (`fantasy_tracker` — ID `1V3lDapPrpGgLGVl-rvNi3Ishy70dAo22UEn24toa4kk`)**:
   - Actúa como **almacén maestro persistente en la nube** (sobrevive a los contenedores efímeros de GitHub Actions) y cuadro de mando visual auditable.
2. **Data Warehouse Analítico Local ([`data/warehouse/biwenger_history.db`](../src/tools/data_extraction/warehouse.py) + CSVs en `data/warehouse/*.csv`)**:
   - Base de datos relacional **SQLite** indexada por claves primarias compuestas + espejos en CSV de lectura ultrarrápida (~5 ms por consulta) diseñada para simulaciones masivas de *backtesting* y validación *Walk-Forward*.
   - Si el contenedor local está vacío (por ejemplo, al arrancar un *runner* efímero de GitHub Actions), `BiwengerWarehouse.read_table()` recupera automáticamente las tablas previas desde Google Sheets.

---

## 2. Catálogo Completo de Tablas y Esquemas de Datos

### A. Tablas Históricas de Reconstrucción para Backtesting (Gestionadas por [`historical_backfill.py`](../src/tools/data_extraction/historical_backfill.py) y [`daily_market_tracker.py`](../src/tools/data_extraction/daily_market_tracker.py))

| Pestaña Google Sheets | Tabla SQLite (`biwenger_history.db`) | Clave Primaria (`PK`) | Granularidad y Cobertura (`Desde 2026-08-01`) |
| :--- | :--- | :--- | :--- |
| **`Historico_Continuo`** | `daily_player_market` | `(fecha, player_id)` | **Jugador × Día (`86+ días`, `551–580` jugadores/día, 43 columnas)**: Precio diario exacto desde el `2026-08-01`, `subida_24h`, variaciones a `7d/14d/30d`, mínimo/máximo anual, sentimiento global (`% compras`, `% ventas`, `% uso`, `presion_neta`), rankings, puntos acumulados, goles, asistencias, minutos, media AS Picas, media SofaScore, probabilidad titular Comuniate, **`propietario_liga`**, **`clausula_actual`** y **`en_venta_mercado`** cada día. |
| **`Historico_Equipos_Diario`** | `daily_manager_snapshots` | `(fecha, user_id)` | **Mánager × Día (`86+ días × 15 mánagers`)**: Reconstrucción exacta día a día desde el `2026-08-01` de `num_jugadores`, **`saldo_disponible_exacto`** (**0 € de desviación**), `valor_plantilla`, `subida_plantilla_24h`, `patrimonio_total`, `beneficio_neto`, `max_puja_disponible` (`Saldo + 25% Valor`), gastos/ingresos/primas/blindajes acumulados y lista exacta de `plantilla_ids` poseídos esa noche. |
| **`Rendimiento_Jornadas`** | `player_round_reports` | `(match_id, player_id)` | **Jugador × Partido / Jornada**: Actuación individual por partido oficial de LaLiga extraída de `/api/v2/rounds/la-liga/{round_id}?score=5`: condición (`LOCAL`/`VISITANTE`), resultado, titular/suplente, minuto exacto de entrada/salida, minutos jugados, goles, asistencias, amarillas/rojas, portería a cero, **Picas AS**, **Nota SofaScore**, **Puntos Biwenger**, **Puntos Picas**, **Puntos SofaScore**, precio y propietario en la fecha del partido, MVP del partido/jornada y presencia en Once Ideal. |
| **`Partidos_Calendario`** | `laliga_matches` | `(round_id, match_id)` | **Partido Oficial LaLiga (380 partidos, J1–J38 + aplazados)**: Fecha/hora, estadio, goles local/visitante, estado (`finished`/`pending`), ratings de dificultad Biwenger, probabilidades 1-X-2 y goles esperados (`xG`) de **EuroClubIndex**, y estadísticas avanzadas de equipo en partidos jugados (`posesion`, `tiros`, `tiros_puerta`, `corners`, `entradas`, `precision_pase`). |
| **`Equipos_LaLiga`** | `laliga_team_standings` | `(team_id)` | **Equipo LaLiga (20 clubes)**: Clasificación real (`PJ, PG, PE, PP, GF, GC, DG, Puntos`), porterías a cero, medias de posesión y tiros por partido, puntos fantasy totales (local vs visitante), valor de mercado total de la plantilla y próximo rival con probabilidad de victoria. |
| **`Pujas_Historico`** | `league_bids` | `(id_puja)` | **Puja Individual (`837+ pujas desde 2026-08-01`)**: Todas las pujas **ganadoras y perdedoras** del muro de la liga enriquecidas con `precio_mercado_dia` exacto en la fecha de la puja, `sobrepuja_eur`, `sobrepuja_pct`, `num_pujadores_subasta` y `diferencia_vs_segunda_puja`. |
| **`Alineaciones_Jornadas`** | `league_round_lineups` | `(round_id, user_id)` | **Mánager × Jornada (`15 mánagers × jornada`)**: Los 11 titulares alineados, formación usada, jugadores que dejó en el banquillo (reconstruidos de su plantilla al inicio de la jornada), `puntos_banquillo`, **`puntos_optimos_posibles`** (calculando la mejor formación legal entre sus jugadores disponibles), `puntos_perdidos_entrenador`, `eficiencia_entrenador_pct`, desglose completo de primas y clasificación general acumulada. |
| **`Mercado_Diario_Historico`** | `daily_market_listings` | `(id_registro)` | **Jugador en Mercado / Oferta × Día**: Registro histórico de todas las subastas resueltas desde el `2026-08-01` más la captura diaria de los jugadores puestos en venta cada día (por el Mercado libre o por rivales) y las ofertas del ordenador (`Mercado`) recibidas por tu plantilla. |
| **`Comuniate_Historico`** | `comuniate_lineups` | `(id_jornada, equipo, nombre)` | **Jugador × Jornada (Comuniate J1–Actual)**: Alineaciones probables e históricas de los 20 equipos de LaLiga por jornada (`titularidad %`, `es_titular`, `apercibido`, `duda`). |

---

### B. Pestañas Operativas de la Liga (Gestionadas por [`runner.py`](../src/tools/data_extraction/runner.py) y [`tracker.py`](../src/tools/rival_tracker/tracker.py))

- **`Movimientos`**: Libro mayor deduplicado de todas las compras, ventas, cláusulas y blindajes desde el `2026-08-01` (`965+` registros).
- **`Primas_Jornadas`**: Primas oficiales por jornada deduplicadas por `(round_base, user_id)` para evitar dobles conteos en jornadas aplazadas (`Jornada 1 (ajornada)`, `Jornada 6 (ajornada)`).
- **`Config_Inicial`**: Presupuesto inicial (`Saldo Inicial` y `Valor Plantilla Inicial` del sorteo del `2026-08-01`, respaldado también en [`data/history_exact_balances.csv`](../data/history_exact_balances.csv)).
- **`Saldos_Estimados`**: Tabla financiera en vivo de los 15 mánagers conciliada al céntimo con el saldo real del API.
- **`Mi_Plantilla`**, **`Estado_Usuario`**, **`Mercado_Fichajes`** y **`Predicciones_IA`**: Pestañas operativas del día actual.

---

## 3. Reglas Matemáticas Clave para el Futuro Motor de Backtesting

1. **Reconstrucción Exacta del Sorteo Inicial (`2026-08-01 13:58:25`, `leagueReset`)**:
   - Partiendo de las plantillas actuales (`league_players.csv`) y deshaciendo en orden cronológico inverso todas las transferencias reales (`type != 'clause_increment'`) desde hoy hasta el `2026-08-01`, se obtienen exactamente los **15 jugadores iniciales** asignados a cada uno de los 15 mánagers.
   - El presupuesto inicial de cada mánager fue exactamente `40.000.000 € = Saldo_Inicial_01_08 + Valor_Plantilla_01_08`.

2. **Fórmula de Conciliación de Saldos (`0 €` de Desviación)**:
   $$\text{Saldo}(u, t) = \text{Saldo\_Inicial}(u) - \sum_{\tau \le t} \text{Compras}(u, \tau) + \sum_{\tau \le t} \text{Ventas}(u, \tau) - \sum_{\tau \le t} \text{Blindajes}(u, \tau) + \sum_{J \le t} \text{Prima\_Oficial\_Deduplicada}(u, J)$$
   - Cuando una jornada tiene recalculo por partido aplazado (p. ej. `Jornada 1 (ajornada)` o `Jornada 6 (ajornada)`), el evento `roundFinished` publica la prima total recalculada de esa jornada base (`Jornada 1`, `Jornada 6`), por lo que **reemplaza** (o suma únicamente el diferencial $\Delta$) respecto al pago inicial de la jornada base.

3. **Fórmula Oficial de Cláusulas de Rescisión de la Liga (`AZ Finance`)**:
   - Implementada en `compute_official_clause()` en [`src/tools/data_extraction/warehouse.py`](../src/tools/data_extraction/warehouse.py#L46-L70):
     - Base $\le 2\text{M €} \rightarrow 200\%$
     - $2\text{M €} < \text{Base} \le 5\text{M €} \rightarrow 175\%$
     - $5\text{M €} < \text{Base} \le 10\text{M €} \rightarrow 150\%$
     - $\text{Base} > 10\text{M €} \rightarrow 125\%$
     donde $\text{Base} = \max(\text{Precio\_Compra}, \text{Valor\_Mercado\_Día})$ más los blindajes manuales (`clauseIncrement`).

---

## 4. Ejecución Diaria y Comandos Manuales

### Automatización en GitHub Actions ([`.github/workflows/daily_market_tracker.yml`](../.github/workflows/daily_market_tracker.yml))
Se ejecuta automáticamente cada mañana en 4 pasos secuenciales diseñados para consumir **< 185 peticiones/hora** (respetando siempre `x-rate-limit-limit: 200` de Biwenger con reserva de seguridad `x-rate-limit-remaining >= 15`):

```bash
# 1. Extrae estado actual de liga, fuentes externas, hojas operativas y guarda api_session_cache.json
.venv/bin/python -u -m src.tools.data_extraction.runner

# 2. Actualiza todas las tablas históricas en SQLite Warehouse y Google Sheets (reutilizando caché de sesión)
.venv/bin/python -u -m src.tools.data_extraction.historical_backfill --comuniate-history

# 3. Añade el snapshot diario de los 551 jugadores a Historico_Continuo (controlando x-rate-limit-remaining)
.venv/bin/python -u src/tools/data_extraction/daily_market_tracker.py

# 4. Entrena el modelo predictivo y actualiza Predicciones_IA
.venv/bin/python -u test/05_market_prediction/run.py --sync-sheets
```

### Reconstrucción Completa Manual (`--backfill`)
Si en cualquier momento se desea regenerar desde cero toda la historia desde el `2026-08-01`:
```bash
.venv/bin/python -u -m src.tools.data_extraction.historical_backfill --backfill --comuniate-history
```
