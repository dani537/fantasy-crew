# 🧪 Test 05: Modelo Predictivo de Mercado y Sentimiento Biwenger

Este módulo implementa el entorno de pruebas para entrenar, calibrar y validar predicciones sobre el valor de mercado de los jugadores de Biwenger a 24-48 horas vista.

---

## 🎯 Objetivo

Anticipar las subidas y bajadas de precio de los 580 jugadores de LaLiga mediante el análisis de:
* **Sentimiento Comunitario:** `% Compras 24h`, `% Ventas 24h`, `% Uso en Ligas`, `Presión Neta`.
* **Curvas Financieras:** Variaciones a 24h, 7d, 14d, 30d, 1y Min/Max.
* **Métricas Deportivas:** Medias de puntos, SofaScore, picas, minutos y racha de fitness.
* **Contexto Táctico:** Titularidad y dudas en Comuniate.

---

## 🚀 Cómo Ejecutar el Test

Desde la raíz del proyecto:

```bash
python test/05_market_prediction/run.py
```

---

## 📊 Salidas del Modelo

1. **Terminal:**
   * **Top 10 Joyas Especulativas:** Jugadores baratos con máxima presión neta compradora ideales para tradear.
   * **Top 10 Alarmas de Desplome:** Jugadores con ventas masivas para ejecutar Stop-Loss inmediato.
2. **Dataset de Predicciones Completo:**
   * Genera el archivo `data/predictions/predicciones_mercado_hoy.csv` con las predicciones en euros (€) y porcentaje (%) de los 580 jugadores.
