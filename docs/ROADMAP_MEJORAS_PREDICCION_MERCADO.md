# 🗺️ Roadmap de Mejoras Futuras: Motor Cuantitativo y Predictivo Biwenger

Este documento registra la hoja de ruta técnica y las líneas de investigación prioritarias para evolucionar el **Motor Predictivo de Mercado (v2)** a medida que el sistema acumule mayor profundidad histórica y nuevos flujos de datos en tiempo real.

---

## 📌 Resumen de Fases y Requisitos de Activación

| Fase / Línea de Mejora | Requisito de Activación | Impacto Clave | Prioridad |
|:---|:---|:---|:---:|
| **Fase 1: Estacionalidad y Efecto Calendario Semanal** | Histórico continuo $\ge$ 25-30 días | Captura de caídas de viernes y euforias de lunes | 🟢 Alta |
| **Fase 2: Sniper Post-Partido en Tiempo Real** | Conector de actas/notas post-partido (22:00) | Comprar perlas 6 horas antes de la subida oficial | 🟢 Alta |
| **Fase 3: Personalización a Tu Liga & Optimizador de Deuda** | Integración con `market_sales.csv` y plantilla | Maximizar compras locales y saldar deuda sin pérdidas | 🟡 Media-Alta |
| **Fase 4: Modelos Asimétricos (CatBoost / LightGBM)** | Histórico continuo $\ge$ 45-60 días | Tolerancia cero a falsos optimismos en desplomes | 🟡 Media |
| **Fase 5: NLP de Partes Médicos y Declaraciones** | Flujo continuo de `data/news.csv` | Anticipar bajas y suplencias exógenas | 🔵 Media-Baja |

---

## 🔬 Detalle Técnico por Línea de Mejora

### 1. Fase 1: Estacionalidad de Mercado y Efecto Calendario Semanal
* **Condición de activación:** $\ge$ 3-4 semanas de datos continuos (actualmente disponemos de 8 días).
* **Fundamento Econométrico:** El mercado de Biwenger no opera en un régimen estacionario. La liquidez y la psicología del jugador de fantasy siguen un patrón semanal rígido:
  * **Jueves tarde / Viernes:** *Efecto Deuda*. Los managers venden masivamente a Computer para salir de números rojos antes de que empiece la jornada (a las 21:00). La presión neta global cae un 15-25%.
  * **Sábado / Domingo:** *Dispersión por Minutos*. El mercado se polariza violentamente según qué jugadores disputan minutos reales y cuáles quedan en el banquillo.
  * **Lunes / Martes:** *Euforia Especulativa*. Entran primas de jornada; los usuarios invierten masivamente en las revelaciones del fin de semana (máxima aceleración alcista).
  * **Miércoles:** *Toma de Beneficios*. Estabilización del ciclo.
* **Nuevas Características a Implementar:**
  * `dia_semana_encoded`: Variable cíclica senoidal/cosenoidal ($\sin(2\pi d/7)$, $\cos(2\pi d/7)$).
  * `horas_inicio_jornada`: Distancia temporal en horas al pitido inicial de la jornada.
  * `factor_liquidez_dia`: Ponderación del volumen esperado de compras comunitarias según el día de la semana.

---

### 2. Fase 2: Sniper Post-Partido en Tiempo Real (Pre-Mercado)
* **Condición de activación:** Conector inmediato con el cierre de partidos (22:00 - 23:00).
* **Fundamento:** El algoritmo oficial de Biwenger recalcula los precios de mercado cada madrugada a las **04:00 UTC**.
  * Cuando un jugador de 200.000 € marca un doblete o suma 14 puntos en SofaScore un sábado a las 19:30 (ej. *Ochieng*, *Fer Niño*), el modelo actual lo detecta el domingo por la mañana una vez el mercado ya subió +90.000 €.
  * **La Oportunidad:** Al conectar un hook al finalizar el partido (mediante el scraper de actas o Comuniate), el agente puede generar la recomendación de puja **a las 22:30 del mismo sábado**, permitiendo ficharlo esa noche antes de la primera subida y capturando el 100% de la ola de revalorización (5-7 días consecutivos de subidas de +100k a +150k).
* **Nuevas Características a Implementar:**
  * `shock_rendimiento_partido`: Desviación de puntos respecto a su media histórica:
    $$\text{Shock} = (\text{Puntos}_{\text{último\_partido}} - \text{Media}) \times \left(1 - \frac{\text{Uso}}{100}\right)$$
  * `alerta_sniper_inmediata`: Flag de compra nocturna prioritaria antes del cierre del mercado diario.

---

### 3. Fase 3: Conexión con tu Liga Real y Solver Knapsack de Deuda
* **Condición de activación:** Cruce diario entre predicciones y los archivos locales de tu liga.
* **Fundamento:** El valor predictivo del modelo debe traducirse en decisiones operativas directas sobre tu plantilla y tus rivales.
* **Componentes a Desarrollar:**
  1. **Filtrador de Mercado Local:**
     * Cruzar las predicciones con [`data/raw/market_sales.csv`](file:///home/daniel/Code/006.%20Biwenger%20Agent/data/raw/market_sales.csv) para presentar únicamente los jugadores disponibles para fichar hoy en tu liga privada, ordenados por **EFR (Valor Esperado)** y **ROI a 72h**.
  2. **Escáner de Cláusulas Robables (Buscador de Gangas Rivales):**
     * Contrastar [`data/raw/league_players.csv`](file:///home/daniel/Code/006.%20Biwenger%20Agent/data/raw/league_players.csv) (cláusulas de rivales) con la proyección de subida a 5 días:
     * Si un rival tiene a un jugador con cláusula de 2.000.000 € y el modelo proyecta que alcanzará 3.200.000 € en 5 días, activar la señal: `💎 CLÁUSULA ROBABLE (+1.2M € de plusvalía)`.
  3. **Optimizador Knapsack de Deuda (Mochila Financiera):**
     * Si el usuario se encuentra con saldo negativo el viernes (ej. -3.400.000 €), resolver el problema de optimización:
       $$\min \sum_{j \in \text{Ventas}} \text{EFR}_{72h}(j) \quad \text{sujeto a} \quad \sum_{j \in \text{Ventas}} \text{Precio}(j) \ge |\text{Deuda}|$$
     * Garantiza saldar la deuda vendiendo la combinación de jugadores que sacrifique la menor ganancia de patrimonio futuro.

---

### 4. Fase 4: Funciones de Pérdida Asimétricas (Stop-Loss Blindado)
* **Condición de activación:** 40-50 días de datos para entrenar modelos especializados en `CatBoost` o `LightGBM`.
* **Fundamento:** En inversión fantasy, equivocarse por exceso no cuesta lo mismo que equivocarse por defecto:
  * Si el modelo proyecta que un jugador sube +50k y en realidad queda plano $\rightarrow$ **Costo de oportunidad mínimo**.
  * Si el modelo proyecta que un jugador se mantiene o sube poco y en realidad se desploma -250k $\rightarrow$ **Destrucción directa de patrimonio**.
* **Modelado Matemático:**
  * Implementar una función de pérdida asimétrica personalizada:
    $$\mathcal{L}_{\text{asym}}(y, \hat{y}) = \begin{cases} (1 + \alpha) \cdot (y - \hat{y})^2 & \text{si } y < 0 \text{ y } \hat{y} \ge y \quad (\text{Penalizar subestimar caídas}) \\ (y - \hat{y})^2 & \text{en otro caso} \end{cases}$$
  * Con $\alpha = 2.0$ a $3.0$, el modelo adquiere una aversión extrema al riesgo de caídas, maximizando la precisión de las alertas rojas de *Venta Urgente (Stop-Loss)*.

---

### 5. Fase 5: Procesamiento de Lenguaje Natural (NLP) para Partes Médicos
* **Condición de activación:** Flujo continuo de titulares en [`data/news.csv`](file:///home/daniel/Code/006.%20Biwenger%20Agent/data/news.csv).
* **Fundamento:** Antes de que la comunidad venda masivamente, la noticia de una lesión o rotura fibrilar aparece en la prensa deportiva (Marca, AS, Comuniate).
* **Solución:**
  * Clasificador zero-shot / expresiones regulares de alta precisión para detectar eventos críticos:
    * `[LESIÓN / BAJA / ROTURA]` $\rightarrow$ $P(\text{Desplome}) = 100\%$, forzar salida inmediata.
    * `[ALTA MÉDICA / ENTRENA CON GRUPO]` $\rightarrow$ $P(\text{Rebote Suelo}) \uparrow$, oportunidad de compra en mínimos.
    * `[DESCANSO / ROTACIÓN CONFIRMADA]` $\rightarrow$ Alerta táctica para alinear o vender temporalmente.

---

*Documento creado y versionado en el repositorio para su ejecución programada conforme crezca el histórico de datos.* 📈⚽
