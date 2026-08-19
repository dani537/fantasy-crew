# 📊 TEST 99: ESTUDIO ANALÍTICO, MODELO PREDICTIVO Y CALCULADORA DE PUJAS BIWENGER

---

## 🎯 Resumen Ejecutivo

Este módulo implementa un **sistema cuantitativo completo de análisis de subastas, modelo econométrico de regresión predictiva y calculadora interactiva de ofertas** entrenado sobre las 54 subastas reales registradas en la liga.

---

## 🧠 Arquitectura y Descubrimientos Clave

### 1. Mecánica Temporal Real de Biwenger (Día $D-1$ vs Día $D$)
* **Día $D-1$ (Día de Puja):** El jugador sale al mercado a las 7:01 AM al precio de salida $P_{D-1}$. Los mánagers analizan la ficha y emiten sus pujas observando el **Incremento Diario Visible en la Ficha** ($\Delta_{\text{vis}} = P_{D-1} - P_{D-2}$).
* **Día $D$ (Día de Resolución):** A las 7:00 AM amanece el nuevo precio $P_D$ y el servidor resuelve las subastas guardadas del Día $D-1$.
* **Extracción Oficial:** Todos los precios históricos reales de salida se obtienen al céntimo del array `"prices"` del endpoint oficial de la API de Biwenger (`/api/v2/players/la-liga/{slug}?fields=id,name,prices`).

### 2. Modelo de Regresión Predictiva OLS ($R^2 = 58.65\%$)
$$\text{Sobrepuja Recomendada (\%)} = \beta_0 + \beta_1 \cdot \ln(P_{D-1}) + \beta_2 \cdot (\text{Pujadores Esperados}) + \beta_3 \cdot (\text{Subida Diaria Visible \%}) + \text{Bonus Posición}$$

#### Coeficientes Estadísticos Entrenados:
* **Constante ($\beta_0$):** `+6.62%`
* **Factor Escala Logarítmica ($\ln(P_{D-1})$):** `-1.01%` *(Ajuste para evitar sobrepujar en cracks de alto valor)*
* **Competencia Esperada por Rival:** `+8.49%` por cada pujador rival
* **Subida Diaria Visible (%):** `+3.53%` de sobrepuja adicional por cada +1% de subida en ficha
* **Bonus por Posición:**
  * **Portero (GK):** `+7.64%` *(Escasez de titulares)*
  * **Delantero (FW):** `+3.64%` *(Premio al gol)*
  * **Defensa (DF):** `+0.84%`
  * **Centrocampista (MF):** `0.00%` *(Posición base)*

### 3. Varianza Explicada ($R^2$ por Variable)
1. 🥇 **Subida Diaria Visible en Ficha (%):** **`50.75%`** de la varianza por sí sola *(Efecto FOMO del mercado)*.
2. 🥈 **Nº de Pujadores Esperados:** **`29.39%`** de la varianza.
3. 🥉 **Posición Táctica (GK/FW/DF/MF):** **`3.37%`**.
4. 🏅 **Escala Logarítmica del Precio:** **`2.40%`**.

---

## 🛠️ Herramientas y Scripts Disponibles

| Archivo | Descripción / Uso |
| :--- | :--- |
| **[`run.py`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/99_pujas/run.py)** | Runner principal del Test 99 (recalcula modelos, simulación, Excel e informes). |
| **[`calculadora_pujas.py`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/99_pujas/calculadora_pujas.py)** | **Calculadora CLI interactiva** para calcular la oferta óptima antes de pujar. |
| **[`modelo_predictivo_pujas.py`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/99_pujas/modelo_predictivo_pujas.py)** | Motor estadístico de Regresión Múltiple OLS. |
| **[`simulacion_pujas.py`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/99_pujas/simulacion_pujas.py)** | Motor de simulación retroactiva sobre las 54 subastas reales de la liga. |

---

## 📄 Archivos e Informes Generados

* 📊 **[`estudio_pujas.xlsx`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/99_pujas/estudio_pujas.xlsx):** Excel con 5 pestañas con formato profesional (`Detalle_Subastas`, `Resumen_Por_Rivales`, `Competencia_vs_Sobrepuja`, `Modelo_Predictivo`, `Simulacion_Historica`).
* 📋 **[`resumen_estudio_pujas.md`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/99_pujas/resumen_estudio_pujas.md):** Informe ejecutivo de las 54 subastas.
* 📈 **[`estudio_modelo_predictivo.md`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/99_pujas/estudio_modelo_predictivo.md):** Informe econométrico completo.
* 🎲 **[`estudio_simulacion_pujas.md`](file:///home/daniel/Code/006.%20Biwenger%20Agent/test/99_pujas/estudio_simulacion_pujas.md):** Informe de victorias y eficiencia de la simulación.

---

## 💻 Guía de Uso de la Calculadora CLI

### Modo Interactivo:
```bash
.venv/bin/python test/99_pujas/calculadora_pujas.py
```

### Modo Directo con Parámetros:
```bash
.venv/bin/python test/99_pujas/calculadora_pujas.py --precio 3.37M --posicion DF --pujadores 2 --subida 20k
```
