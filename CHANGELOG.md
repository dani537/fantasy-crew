# 📋 CHANGELOG — Biwenger Agent

Registro de cambios de la sesión de refactorización y optimización (agosto 2026).

---

## 1. 🔒 Seguridad

| Cambio | Archivo(s) | Detalle |
|---|---|---|
| **Fuga de credenciales corregida** | `src/data_extraction/external_data.py`, `runner.py` | Los scrapers de Comuniate, JornadaPerfecta y EuroClubIndex reutilizaban la sesión autenticada de Biwenger → el **Bearer token se enviaba a dominios de terceros**. Ahora cada scraper crea su propia sesión limpia. |
| **Validación de credenciales al arrancar** | `src/config.py`, `runner.py` | Error claro si falta `BIWENGER_USERNAME/PASSWORD` o `DEEPSEEK_API_KEY`, en vez de fallar a mitad de pipeline. |
| **Modo DRY_RUN** | `src/config.py`, `graph/nodes.py`, `main.py` | `DRY_RUN=true` en `.env` simula todo el flujo sin enviar ninguna escritura a la API de Biwenger. Esencial para iterar sin riesgo. |
| **Defaults seguros** | `src/config.py` | `SCORE_TYPE` por defecto `5` (antes la URL quedaba con `score=None` si faltaba); `LANGUAGE` por defecto `es`. |

## 2. 🐛 Bugs corregidos

| Bug | Impacto real observado | Fix |
|---|---|---|
| Coach y SD leían `data/next_match.csv` (fichero **residual de abril**) en vez de `next_jornada.csv` que genera el pipeline | El Coach justificaba ventas con partidos inventados ("Raúl Moro a domicilio contra un Athletic fuerte" — en pretemporada) | Ambos agentes leen ahora `next_jornada.csv`; fichero residual eliminado |
| Las ofertas a jugadores **vendidos por rivales** se enviaban con `to=null` | La puja de 9,48M€ por Mikautadze **falló** (la API exige `to=<seller_user_id>`) | Se conserva `MARKET_SALE_USER_ID` en el master y la oferta se dirige automáticamente al vendedor |
| `requestedPlayers` de la API puede ser `[{"id": ...}]` en vez de `[12345]` | Columna objeto que rompía merges y dejaba ofertas huérfanas | Parser robusto que extrae el `id` en ambos formatos |
| Crash de merge `int64` vs `object` (dependiente de datos) | Pipeline completo caía con ciertos CSVs | Normalización de claves a `Int64` antes de todos los merges en `transformers.py` |
| `json_helper` eliminaba `//.*` como "comentarios" | Destruía URLs `https://...` dentro de los valores del JSON | Solo se eliminan líneas que son comentario completo + fallback de llaves externas |
| `active_events.csv` vacío sin cabeceras | Warning ruidoso "No columns to parse" | Siempre se escribe con columnas definidas |
| El Coach devolvía un string en caso de error (el resto esperaba dict) | Inconsistencia de tipos en reportes | Siempre devuelve dict |

## 3. 🧹 Limpieza de código

- Eliminados módulos muertos: `src/agents/president.py` (importaba un módulo inexistente: habría crasheado si se usara), `src/prompts/president_prompts.py`, `src/llm_endpoints/gemini.py`, `get_coach_critique_prompt` y roles de sistema sin uso.
- Duplicado `SPORTING_DIRECTOR_SYSTEM_ROLE` unificado en `system_roles.py`.

## 4. 🛡️ Nueva capa de estrategia determinista (`src/strategy/`) — la mejora principal

Filosofía: **los LLMs proponen, Python valida y corrige**. Motivada por decisiones reales catastróficas detectadas en la ejecución anterior.

### `lineup.py`
- Validación estricta de la alineación del LLM: 11 jugadores exactos, 1 portero, formación conocida, conteo DF/MF/FW coherente, IDs reales de la plantilla.
- Fallback determinista: si la alineación es ilegal, calcula el mejor XI posible (maximiza xP; en pretemporada usa titularidad + precio como proxy).
- Ordenación GK→DF→MF→FW como exige la API.
- Si no hay XI legal (sin portero), no se toca la alineación y se prioriza fichar GK.

### `guardrails.py`
- **Ventas bloqueadas si**: dejan <11 jugadores sanos · es el único portero · titular probable (≥70%) sano · precio <70% de lo pagado (protección de cláusulas).
- **Pujas bloqueadas si**: lesionado · puja < precio mínimo de subasta · >50% del presupuesto · total > presupuesto disponible real.
- **Presupuesto real** = saldo − pujas ya comprometidas (replica la "puja máxima" que impone Biwenger — descubierto en ejecución real: `"Offer above maximum bid"`).
- **Anti-duplicados**: no se puja por jugadores con puja ya pendiente.
- **Auditoría de plantilla** (`compute_squad_needs`): huecos por línea que se inyectan como contexto determinista a ambos agentes.

### Validación de la estrategia con datos reales
Los guardrails, aplicados a la ejecución anterior, **habrían bloqueado las 5 ventas** (plantilla de 10 < 11 mínimo) incluida la de Berenguer (comprado por cláusula de 5M€, listado a 2,06M€). En la nueva ejecución real el agente **retiró a Berenguer del mercado** y **canceló una puja viva de 1,59M€ por Militão (lesionado)**.

## 5. 🎯 Estrategia de pujas optimizada

- Vista de mercado del SD enriquecida: `EXPECTED_POINTS`, `COST_PER_XP`, `MOMENTUM_TREND`, `TEAM_IS_HOME`, `ODDS_1/ODDS_2`, `MARKET_SALE_USER_NAME`.
- Prompt con **jerarquía de ponderación explícita**: necesidad > titularidad > forma/cuotas > tendencia de precio > valor.
- **Cobertura de subastas (hedging)**: para gaps críticos (sin portero) se puja por 2 objetivos alternativos; perder la única puja de GK es peor que reselller el excedente.
- **Limpieza de pujas pendientes**: auto-cancelación determinista de pujas sobre lesionados/sancionados + `operaciones_cancelar_pujas` del SD (`MarketActions.cancel_offer` nuevo).
- Ya **no se fuerzan compras** (antes el prompt exigía 1-3 pujas siempre): 0 pujas es una decisión válida.

## 6. 🗞️ Email estilo periódico + idioma configurable

- Nueva plantilla "Biwenger Chronicle" (`email_templates.py`): compatible con Gmail (tablas + CSS inline), cabecera de diario, titular, entradilla, franja de cifras clave, secciones de crónica y caja de acciones.
- Idioma del email vía `LANGUAGE=es|en|ca|fr|de|it|pt` en `.env`. Prompts internos de razonamiento 100% en inglés; solo el periódico se traduce.
- Preview local en `./reports/email_preview.html` en cada ejecución.

## 7. 🌅 Modo briefing matutino (`--mode briefing`)

Diseñado para el reset de mercado de las 7:00:
- `main.py --mode action`: flujo completo con acciones (a cualquier hora).
- `main.py --mode briefing` (~7:10): **solo lectura** — extracción + email matutino (qué pasó esta noche, estado de plantilla, mercado del día, avisos) + limpieza determinista de pujas redundantes. **1 sola llamada LLM** para minimizar tokens.

## 8. ⏰ Modo subasta (`--mode auction`) + GitHub Actions

- **Auto-detección de la hora de resolución**: lee los timestamps `until` de las ventas del mercado (UTC) y los convierte a hora local — nada hardcodeado; si Biwenger cambia la hora del reset, el agente se adapta.
- **Comportamiento adaptativo según el margen**: con >4 min antes del cierre ejecuta el análisis completo y las pujas; si arranca justo (6:59) no improvisa — espera los minutos restantes; si ya pasó, evalúa directamente.
- **Evaluación post-subasta**: re-extrae datos y compara snapshots pre/post para informar de subastas ganadas (jugador ya en plantilla) y perdidas.
- **Limpieza de pujas hedge**: si ganamos un jugador, nuestras otras pujas pendientes de esa misma posición se cancelan automáticamente (determinista, sin LLM). La misma limpieza se integra en el modo briefing.
- **Workflow CI preparado** (`.github/workflows/biwenger_agent.yml`): cron en UTC (6:45 CEST auction / 7:10 CEST briefing), ejecución manual con selector de modo y `dry_run`. Se crea `requirements-ci.txt` mínimo porque el `requirements.txt` original incluye paquetes de sistema de escritorio (dbus-python, pycups, wxPython...) que rompen `pip install` en un runner CI.

---

*Archivos nuevos: `src/strategy/__init__.py`, `src/strategy/lineup.py`, `src/strategy/guardrails.py`, `src/briefing.py`, `src/auction.py`, `requirements-ci.txt`, `CHANGELOG.md`.*
