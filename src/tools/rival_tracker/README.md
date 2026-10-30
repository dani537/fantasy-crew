# 🕵️ Rival Financial & League Tracker Tool

Esta herramienta se encarga del seguimiento financiero, estimación de saldos y radar de rivales de tu liga privada de Biwenger.

---

## 🎯 Diferencia entre los dos Trackers del Proyecto

Para evitar duplicidades y confusión, el proyecto cuenta con dos herramientas especializadas con objetivos claramente diferenciados:

| Tracker | Ubicación | Objetivo | Hoja Google Sheets | Salida Local |
| :--- | :--- | :--- | :--- | :--- |
| **Rival Tracker** *(Este módulo)* | `src/tools/rival_tracker/` | Rastrea el muro de la liga, fichajes entre managers, calcula **saldo disponible estimado** y **patrimonio** de cada rival. | `fantasy_tracker` (`1V3l...`) | `data/rival_financials.csv` |
| **Daily Market Tracker** | `src/tools/data_extraction/daily_market_tracker.py` | Captura diaria del **censo de 580 jugadores de LaLiga** con sus 40 columnas (curvas de precio, % compras/ventas, sentimiento). | `daily_data` (`1Fsu...`) | `data/history/` |

---

## 🚀 Uso Directo

```bash
python -m src.tools.rival_tracker.tracker
```

O sincronizando un número específico de días hacia atrás (ej: 14 días):

```bash
python -m src.tools.rival_tracker.tracker 14
```
