# 🧪 Test 05: Modelo Predictivo de Mercado y Sentimiento Biwenger

Este módulo implementa el motor de Machine Learning y econometría estadística entrenado sobre el histórico continuo de LaLiga (censo de 580 jugadores) para predecir variaciones de precio, probabilidades de subida/bajada y puntos de inflexión con 24h, 48h y 72h de antelación.

---

## 🎯 Objetivo y Métricas Validadas

Anticipar las subidas y bajadas de precio mediante el cruce de:
* **Sentimiento Comunitario:** `% Compras 24h`, `% Ventas 24h`, `% Uso en Ligas`, `Presión Neta` y `Ratio Demanda`.
* **Inercia y Elasticidad:** Factor de penetración comunitaria $(1 - \text{Uso}/100)$ y resistencia de capital por tier de valor.
* **Momentum y Aceleración:** Derivadas de cambio diario (aceleración de subida y velocidad de liquidación).
* **Métricas Deportivas y Tácticas:** Puntos, medias, SofaScore, picas, minutos y titularidad.

### 📊 Rendimiento del Modelo (Backtest 5-Fold Cross Validation sobre 1.159 transiciones)
| Métrica | Baseline (Persistencia) | Ridge Regularizado | Random Forest | **Ensemble Blend (Producción)** |
| :--- | :---: | :---: | :---: | :---: |
| **Error Medio Absoluto (MAE)** | 10.224 € | 10.314 € | 9.855 € | **9.539 €** |
| **Coeficiente $R^2$ (Varianza explicada)** | 87.34% | 88.67% | 88.81% | **89.49%** |
| **Exactitud Direccional (%)** | 90.2% | 85.7% | 90.3% | **90.2%** |
| **Precision Subidas ($>0$)** | 91.0% | 88.4% | 94.2% | **95.1%** |
| **Precision Caídas ($<0$)** | 89.5% | 87.1% | 90.4% | **90.8%** |

---

## 🚀 Cómo Ejecutar el Test

Desde la raíz del proyecto:

```bash
.venv/bin/python test/05_market_prediction/run.py
```

---

## 📊 Salidas del Modelo

1. **Terminal:**
   * **Resultados de Validación Cruzada:** Comparativa de MAE, $R^2$ y exactitud direccional.
   * **Top 10 Joyas Especulativas (< 5M €):** Máximo potencial de revalorización porcentual a 24h y 48h.
   * **Top 10 Alarmas de Desplome:** Jugadores en liquidación masiva para ejecutar Stop-Loss inmediato.
   * **Top 5 Cracks de Élite (> 8M €):** Grandes patrimonios en tendencia alcista continuada.

2. **Dataset CSV Enriquecido:**
   * `data/predictions/predicciones_mercado_hoy.csv`: Contiene las 580 predicciones con proyección 24h, 48h y 72h, probabilidades calibradas y señal de acción operativa.

3. **Libro Excel Multitab:**
   * `data/predictions/modelo_predictivo_mercado.xlsx`:
     - `Predicciones_Hoy`: Matriz completa de los 580 jugadores.
     - `Top_Joyas_Compra`: Ficha de compra especulativa.
     - `Top_Alarmas_Venta`: Alertas urgentes de venta.
     - `Cracks_Elite`: Seguimiento de superestrellas.
     - `Metricas_Validacion`: Ficha de auditoría econométrica.

4. **Nube Google Sheets (Sincronización Automática):**
   * Pestaña `Predicciones_IA` en la hoja maestra compartida de Google Drive.
