# ⚽ Fantasy Champion — Tu Director Deportivo Automático para Biwenger

> **Analiza tu liga cada mañana, elige tu mejor once, compra gangas antes de que suban, negocia con rivales sin bloquear tu saldo, vigila clausulazos y te envía un boletín animado por correo y Telegram.**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](docs/QUICKSTART.md)
[![IA Gemini 4 Hydrogen](https://img.shields.io/badge/Supervisor%20IA-gemini--4--hydrogen-4285F4?logo=googlegemini&logoColor=white)](docs/ESTRATEGIAS.md)
[![Estrategia Activa](https://img.shields.io/badge/Estrategia-LIQUIDITY__VULTURE__PRO-10B981)](docs/ESTRATEGIAS.md)
[![Automatización](https://img.shields.io/badge/GitHub%20Actions-100%25%20Gratis-2088FF?logo=githubactions&logoColor=white)](.github/workflows)

---

## 👋 ¿Qué hace por tu equipo cada día?

No necesitas pasarte el día mirando el mercado ni hacer cuentas antes de cada jornada. **Fantasy Champion** conecta con tu liga de Biwenger, **autodescubre sus reglas reales** (sistema de puntos, crédito permitido, límite diario de ventas y cierre de cláusulas) y trabaja por ti en 4 frentes:

### 1. 📋 Pone siempre tu mejor Once (`MILP` + Guardia Final)
- Calcula la **alineación matemáticamente óptima** entre las formaciones legales de Biwenger maximizando los puntos esperados (`xP`) reales.
- **45 minutos antes del primer partido**, vuelve a revisar partes médicos y titularidades de última hora para sacar del once a cualquier lesionado o sancionado inesperado.

### 2. 💸 Compra gangas, vende en pico y limpia pujas atascadas
- **Escaparate diario sin riesgo:** coloca en el mercado a tus transferibles no esenciales para recibir ofertas de Biwenger y de rivales.
- **Trading inteligente (`LIQUIDITY_VULTURE_PRO`):** puja por jugadores con subida esperada en 24–72h (`EFR`), rota rápido lo que se estanca más de 3 días y aplica el dinero donde más puntos da.
- **Negociación automática con rivales:** evalúa ofertas por tus jugadores frente a su valor real (`NPV_HOLD` vs `NPV_SELL`), rechaza contraofertas caras y **cancela pujas salientes olvidadas (`≥48h`)** para liberar margen real de puja (`available_max_bid`).

### 3. 🎯 Aprovecha clausulazos rentables y evita ceros por deuda
- Detecta cláusulas con descuento real respecto a puntos esperados y mejora neta de tu XI, respetando los topes automáticos y las ventanas de bloqueo pre-jornada.
- **Salvavidas antes del arranque:** `2.5 horas` antes del primer partido comprueba si estás en negativo; si hace falta, liquida solo lo mínimo imprescindible para empezar en **`>= 0 €`** y no puntuar cero.

### 4. 📰 Te envía un Email animado + Boletín de la Liga + Telegram
- **Newsletter HTML con animaciones GIF:** incluye un resumen **"Lectura Fácil"** en lenguaje claro, qué operaciones hizo o propone hoy, tu crédito liberado, tu XI óptimo sobre el campo y el **📰 Boletín de la Liga** (pulso de la clasificación, últimos clausulazos, fichajes/blindajes de rivales, parte médico de LaLiga y mayores subidas/bajadas 24h).
- **Control total desde Telegram:** consúltale a cualquier hora o mándale órdenes (`/estado`, `/once`, `/tesis`, `/aprobar`, `/intocable Pedri`).

---

## 🎛️ Elige cuánto control quieres (`FULL_AUTO` · `SEMI_AUTO` · `DRY_RUN`)

Puedes cambiar el comportamiento con una sola variable (`AUTOPILOT_MODE`):

| Modo | ¿Qué ejecuta el bot solo? | ¿Cuándo te pide permiso? | Ideal para... |
| :--- | :--- | :--- | :--- |
| 🤖 **`FULL_AUTO`** *(Por defecto)* | Todo lo aprobado por guardrails: escaparate, pujas, ventas óptimas, limpieza de contraofertas/pujas estancadas, rescate pre-jornada, alineación y clausulazos dentro del tope seguro (`45%` patrimonio neto / máx. `25M €`). | Nunca bloquea operaciones (siempre te avisa por Email y Telegram con todo explicado). | Dejarlo correr solo todo el año. |
| 🛡️ **`SEMI_AUTO`** | Escaparate diario, pujas óptimas, ventas menores, limpieza de pujas caras/estancadas, alineación y salvamento de deuda pre-jornada. | Pone en cola (`/aprobar ID`) **todos los clausulazos**, **blindajes**, **contraofertas aceptables** y **ventas importantes (`>= 4M €` o titulares/pilares)**. | Supervisar movimientos grandes con un toque. |
| 🧪 **`DRY_RUN`** | Nada en Biwenger (`0` cambios reales). | Simula el plan completo y te envía igualmente el correo y el resumen para auditar qué haría. | Probar estrategias o revisar antes de activar. |

---

## ⏱️ Automatización 100% en la nube (GitHub Actions)

El proyecto incluye **4 workflows listos** en [`.github/workflows/`](.github/workflows):

| Workflow | Cuándo corre | Qué hace exactamente |
| :--- | :--- | :--- |
| ⚙️ [`daily_market_tracker.yml`](.github/workflows/daily_market_tracker.yml) | Cada mañana (programado o manual) | Descarga movimientos del muro, actualiza el Data Warehouse SQLite + Sheets, recalcula predicciones ML de mercado y ejecuta el piloto completo (`--phase MORNING_FULL --notify`). |
| ⚡ [`coach_lineup_autopilot.yml`](.github/workflows/coach_lineup_autopilot.yml) | Ejecución rápida bajo demanda (`<30s`) | Re-evalúa en segundos saldo en vivo, pujas, negociaciones, once y envía el informe actualizado sin esperar a la extracción completa. |
| 🎯 [`tactical_pulse.yml`](.github/workflows/tactical_pulse.yml) | Ligado al calendario real de LaLiga + botón manual | Dispara pulsos quirúrgicos en 3 momentos clave (**`PRE_CLAUSE_LOCK`** `-48h`, **`PRE_KICKOFF_SOLVENCY`** `-2.5h`, **`FINAL_LINEUP_GUARD`** `-45m`) y permite aprobar/rechazar `ID`s manualmente desde GitHub. |
| 💬 [`telegram_responder.yml`](.github/workflows/telegram_responder.yml) | Al instante ante mensajes de Telegram | Responde comandos (`/estado`, `/once`, `/tesis`, `/pendientes`, `/aprobar`, `/intocable`) sin necesitar un servidor encendido 24/7. |

---

## 🚀 Puesta en Marcha en 3 Minutos

### En local (para probar o usar el asistente guiado)

```bash
git clone <URL_DE_TU_REPO> fantasy-champion
cd fantasy-champion
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Configura tu Biwenger, reglas de liga, Gmail y Telegram en ~90s
python -m src.setup_wizard

# 2. Haz tu primera simulación segura sin tocar tu equipo real
python -m src.tools.strategy_engine.autopilot --mode DRY_RUN
```

👉 **¿Quieres subirlo gratis a GitHub Actions o ver todos los comandos?** Sigue la guía paso a paso en [**`docs/QUICKSTART.md`**](docs/QUICKSTART.md).

---

## 📚 Documentación Detallada (Explora cada pieza a fondo)

Para mantener este README fácil de leer, toda la parte técnica, matemática y estratégica vive en guías dedicadas:

| Documento | Qué encontrarás dentro |
| :--- | :--- |
| 🧠 [**Estrategias, IA y Torneo (`docs/ESTRATEGIAS.md`)**](docs/ESTRATEGIAS.md) | Cómo funciona **`LIQUIDITY_VULTURE_PRO`**, tabla de los **11 arquetipos**, resumen del **torneo de 798 simulaciones**, posturas por clasificación, cadencia del supervisor **`gemini-4-hydrogen`**, negociación de contraofertas y topes de seguridad. |
| 📊 [**Comparativa Visual HTML (`strategy_comparison.html`)**](data/backtests/strategy_comparison.html) | Gráficos interactivos comparando las estrategias evaluadas *(ábrelo en tu navegador)*. |
| 📑 [**Informe del Torneo (`massive_tournament_report.md`)**](data/backtests/massive_tournament_report.md) | Resultados completos de las 3 etapas (`M1`, `M2`, `M3`) sobre `105` días causales (`J1..J12`) con `0` insolvencias en el podio. |
| ⚡ [**Guía Rápida e Instalación (`docs/QUICKSTART.md`)**](docs/QUICKSTART.md) | Configuración de `.env` / secretos de GitHub Actions, Webhook Telegram en Cloudflare Workers Free y comandos diarios. |
| 🏗️ [**Arquitectura de Datos y Backtesting (`docs/ARQUITECTURA_DATOS_Y_BACKTESTING.md`)**](docs/ARQUITECTURA_DATOS_Y_BACKTESTING.md) | Cómo se sincroniza Dual-Sink (SQLite + Google Sheets) reconstruyendo saldos históricos con `0 €` de desviación. |
| 📋 [**Manual de Google Sheets (`docs/GOOGLE_SHEETS_TRACKER.md`)**](docs/GOOGLE_SHEETS_TRACKER.md) | Explicación de pestañas (`Movimientos`, `Saldos_Estimados`, `Decisiones_Pendientes`, etc.). |
| 📈 [**Estudio y Roadmap del Modelo de Mercado**](docs/ESTUDIO_PREDICCION_VALOR_MERCADO.md) | Cómo se predicen subidas y bajadas a 24–48h ([ver también Roadmap](docs/ROADMAP_MEJORAS_PREDICCION_MERCADO.md)). |

---

## 🗂️ Mapa Rápido del Proyecto

```text
├── .github/workflows/          # 4 pipelines autónomos en GitHub Actions
├── docs/                       # Guías paso a paso, estrategias, datos y modelo ML
├── data/backtests/             # Informes y comparativas HTML del torneo de 798 sims
├── src/
│   ├── config.py               # Configuración central y topes de seguridad (.env)
│   ├── setup_wizard.py         # Asistente interactivo de instalación
│   └── tools/
│       ├── data_extraction/    # API Biwenger, histórico 0€ desvío y FastDeltaRefresh
│       ├── market_prediction/  # Features econométricas y predicción de subidas 24-72h
│       ├── strategy_engine/    # Optimizador MILP, 11 arquetipos, supervisor LLM y autopilot
│       ├── coach_analytic/     # Alineación óptima y guardrails deterministas
│       ├── biwenger_actions/   # Cliente de pujas, ventas, cláusulas y contraofertas
│       ├── notifications/      # Newsletter HTML animada + Boletín + Telegram Bot
│       ├── scheduler/          # Pulso dinámico ligado al calendario oficial de LaLiga
│       └── backtester/         # Simulador causal estricto anti-lookahead
└── test/                       # Suites de regresión y validación arquitectónica
```
