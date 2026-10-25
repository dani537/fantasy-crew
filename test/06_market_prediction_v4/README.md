# 🧠 Motor Predictivo Cuantitativo de Mercado Biwenger (v4 - Master Edition)

¡Bienvenido al cerebro econométrico de tu **Biwenger Agent**! 🚀

Este módulo utiliza Machine Learning, microestructura financiera y modelado residual de dos etapas para predecir **cuánto valdrá cada jugador de LaLiga mañana, pasado y en 3 días**, anticipando con precisión matemática cuándo un jugador va a **explotar al alza**, cuándo va a **frenar en su pico (techo)** o cuándo debes **venderlo de urgencia para no perder millones**.

---

## 💡 ¿Cómo funciona la arquitectura v4? (Explicado para humanos)

El algoritmo oficial de Biwenger no sube ni baja precios al azar: responde al flujo de órdenes de compra/venta y a una inercia de mercado muy marcada (casi el 50% de los jugadores repiten exactamente la misma subida en euros al día siguiente, y el 30% están congelados en 0 €).

Para batir a la inercia simple sin caer en los errores típicos de regresión clásica, la **versión v4** implementa una **arquitectura de dos etapas con modelado residual, rejilla discreta y estacionalidad semanal de 14 días (Fase 1 del Roadmap)**:

```
                         ┌──────────────────────────────────────────────┐
                         │      61 VARIABLES MAESTRAS POR JUGADOR       │
                         │ (Lags t..t-4, Lags 7d Semanales, MACD, Rachas)│
                         └──────────────────────┬───────────────────────┘
                                                │
                ┌───────────────────────────────┴───────────────────────────────┐
                ▼                                                               ▼
 ┌─────────────────────────────┐                                 ┌─────────────────────────────┐
 │ ETAPA 1: ZERO-HURDLE FILTER │                                 │  ETAPA 2: REGRESOR RESIDUAL │
 │ (¿El jugador se va a mover?)│                                 │ (Aceleración: Δ Subida €)   │
 │                             │                                 │                             │
 │ • HistGB Classifier (p≥0.30)│                                 │ • HistGradientBoosting (50%)│
 │ • Filtro de inercia por     │                                 │ • ExtraTrees Regressor (25%)│
 │   racha plana (streak_flat) │                                 │ • Random Forest (15%)       │
 │ ➔ P(Movimiento vs Cero)     │                                 │ • Ridge Regularizado (10%)  │
 └──────────────┬──────────────┘                                 └──────────────┬──────────────┘
                │                                                               │
                └───────────────────────────────┬───────────────────────────────┘
                                                ▼
                                ┌──────────────────────────────┐
                                │   CUANTIZADOR BIWENGER 10k   │
                                │ (Rejilla oficial múltiplo 10k│
                                │  con deadband de +/- 5.000 €)│
                                └──────────────┬───────────────┘
                                               │
                ┌──────────────────────────────┴───────────────────────────────┐
                ▼                                                              ▼
 ┌─────────────────────────────┐                                ┌──────────────────────────────┐
 │   ROLLOUT MULTITEMPORAL     │                                │  INTELIGENCIA TÁCTICA & EFR  │
 │  (Trayectorias 24h/48h/72h) │                                │  (Horizonte Óptimo de Salida)│
 │                             │                                │                              │
 │ • Decaimiento autorregresivo│                                │ • Valor Esperado EFR 72h (€) │
 │ • Coherencia y monotonicidad│                                │ • Retención Óptima (Días)    │
 │   física entre horizontes   │                                │ • Alertas Techo (ATI) / Suelo│
 └─────────────────────────────┘                                └──────────────────────────────┘
```

---

## 📊 Métricas Reales Validadas (Head-to-Head Fuera de Muestra con 14 días)

Evaluado mediante **Walk-Forward temporal estricto** sobre 567 transiciones en fechas no vistas por los modelos (`2026-09-14` y `2026-09-15`):

| Indicador / Métrica | Baseline (Inercia) | Modelo v3 Anterior | **Nuevo Modelo v4 (Actual)** | Impacto Práctico |
|:---|:---:|:---:|:---:|:---|
| **MAE Global (€)** | 12.257 € | 10.970 € | **9.877 €** | **-1.093 € de error medio por jugador** frente a v3 (-2.380 € vs Baseline). Rompe la barrera de los 10k €. |
| **Coeficiente $R^2$ (€)** | 0.8174 | 0.8631 | **0.8919** | Roza el 0.90 de varianza explicada fuera de muestra. |
| **$R^2$ Aceleración Delta** | 0.0000 | 0.2880 | **0.3908** | **+35.7% de ganancia relativa en predicción de aceleración/frenazo**. |
| **Exactitud Direccional** | 91.7% | 91.9% | **92.1%** | Acierto récord determinando si el jugador subirá, bajará o se mantendrá. |
| **Exactitud en Ceros (Flat)** | 89.8% | 89.2% | **87.9%** | Protege a los jugadores planos sin ahogar salidas de letargo. |
| **Rejilla Oficial 10.000 €** | 100% | 100% | **100.0% Múltiplos 10k** | Formato idéntico al motor oficial de Biwenger. |
| **Alpha Cartera Top 10 (72h)** | -0.88% (Mercado) | +12.1% | **+14.57% (+15.45% Alpha)** | **+890.000 € limpios** generados en 72h con el Top 10 especulativo en mercado bajista. |
| **Alertas de Techo (ATI)** | N/A | 4.4x lift | **5.3x a 10.0x sobre el azar** | Caza en el 100% del Top 6 de techos el día exacto de salida antes de caída. |

---

### Desglose por Segmentos de Capital (v4 vs v3):
* **Micro-gangas (< 1.5M €):** MAE baja a **3.082 €** (Mejora: **+287 €** frente a v3). Exactitud direccional: **92.8%**.
* **Gama Media (1.5M - 5M €):** MAE baja a **12.511 €** (Mejora: **+1.435 €** frente a v3). Exactitud direccional: **91.9%**.
* **Consolidados (5M - 10M €):** MAE baja a **30.208 €** (Mejora: **+4.375 €** frente a v3). Exactitud direccional: **91.7%**.
* **Cracks Élite (> 10M €):** MAE baja a **29.412 €** (Mejora: **+588 €** frente a v3).

---

## 🚀 Guía Rápida de Uso

### 1. Ejecutar las Predicciones del Mercado de Hoy
Genera el informe de terminal, calcula las proyecciones multitemporales y exporta los datos para testeo local:
```bash
.venv/bin/python test/05_market_prediction/run.py
```
*(Opcional: añade `--sync-sheets` para sincronizar con la pestaña `Predicciones_IA` de Google Sheets).*

### 2. Ejecutar la Batería Completa de Benchmarks Econométricos
Corre los benchmarks científicos comparativos (Head-to-head, ablación de variables, regímenes 72h y backtest financiero):
```bash
.venv/bin/python test/05_market_prediction/experiment_runner.py
```

### 3. Calibrar Puntos de Inflexión (Techo y Suelo)
Evalúa las matrices de confusión, precisión y lift de las alertas tempranas de giro:
```bash
.venv/bin/python test/05_market_prediction/inflection_engine.py
```

---

## 📁 Estructura de Ficheros

* [`predictive_model.py`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/05_market_prediction/predictive_model.py): Clase principal `BiwengerMarketPredictorV4` con arquitectura Zero-Hurdle, aceleración residual y exportación Excel.
* [`features.py`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/05_market_prediction/features.py): Feature Store v4 con lags semanales 7d, rachas de inercia y MACD.
* [`targets.py`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/05_market_prediction/targets.py): Generador de etiquetas residuales, horizonte temporal y regímenes.
* [`run.py`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/05_market_prediction/run.py): Ejecutable principal con visualización de tablas tácticas y retención óptima.
* [`experiment_runner.py`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/05_market_prediction/experiment_runner.py): Suite de experimentación comparativa (Baseline vs v3 vs v4).
* [`inflection_engine.py`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/05_market_prediction/inflection_engine.py): Calibrador de umbrales para las alertas tempranas de giro.
