# 🧠 MODELO PREDICTIVO DE SOBREPUJAS Y OFERTAS ÓPTIMAS
**Fecha de Construcción:** 2026-08-19 02:41:27  
**Dataset de Entrenamiento:** `135` subastas reales de la liga  
**Precisión del Modelo ($R^2$):** `68.9%`  
**Error Medio Absoluto (MAE):** `±15.08%`  

---

## 🔍 1. Validación de Hipótesis y Crítica del Modelo
### 📊 H1: Relación entre Valor del Jugador y Sobrepuja (CONFIRMADA 100%)
* **Hipótesis del usuario:** A menor valor del jugador, mayor es la sobrepuja relativa en % (pujas de pánico/baratas). A mayor valor, el % de sobrepuja cae pero sube el importe en euros absolutos.
* **Evidencia empírica en los datos:**
  - Correlación **Precio vs Sobrepuja %:** `-0.25` (Negativa clara).
  - Correlación **Precio vs Sobrepuja €:** `+0.31` (Positiva clara).
  - *Jugadores <1.5M€:* Sobrepuja media del **`32.2%`** (+351k €).
  - *Jugadores >4.5M€:* Sobrepuja media del **`16.1%`** (+1.11M € absolutos).

### ⚽ H2: Impacto de la Posición (CONFIRMADA)
* **Porteros (GK):** Sufren el mayor sobreprecio absoluto y en % (**`+27.4%`** / +1.20M € de media) por escasez de porteros titulares en la liga.
* **Delanteros (FW):** 2ª mayor sobrepuja (**`+26.6%`**) por la alta cotización del gol.
* **Mediocentros (MF):** Los más estables (**`+16.4%`**) por la alta abundancia de opciones.

### ⚔️ H3: Impacto de la Competencia Esperada (CONFIRMADA)
* Correlación **Nº Pujadores vs Sobrepuja %:** `+0.52` (La variable más determinante del mercado).

---

## 📐 2. Ecuación Matemática del Modelo OLS
$$\text{Sobrepuja\_Pct (\%)} = 64.46 - 5.50 \cdot \ln(\text{Precio\_Salida}) + 16.27 \cdot (\text{Nº\_Pujadores}) + \text{Bonus\_Posición}$$

**Valores de `Bonus_Posición`:**
* **Portero (GK):** `+-7.25%`
* **Delantero (FW):** `+1.73%`
* **Defensa (DF):** `-1.46%`
* **Centrocampista (MF):** `+0.00%` (Categoría base de referencia)

---

## 📊 3. Matriz Predictiva de Ofertas Óptimas (Lookup Table)
Usa esta matriz para consultar de un vistazo el % de sobrepuja recomendado según el valor de salida, posición y grado de competencia esperado:
| Rango Precio    | Precio Base (€)   | Posición   | 1 Puja (%)   | 2 Pujas (%)   | 3 Pujas (%)   |
|:----------------|:------------------|:-----------|:-------------|:--------------|:--------------|
| Muy Bajo (<1M€) | 0.50M €           | GK         | 1.3%         | 17.6%         | 33.8%         |
| Muy Bajo (<1M€) | 0.50M €           | DF         | 7.1%         | 23.4%         | 39.6%         |
| Muy Bajo (<1M€) | 0.50M €           | MF         | 8.6%         | 24.8%         | 41.1%         |
| Muy Bajo (<1M€) | 0.50M €           | FW         | 10.3%        | 26.6%         | 42.8%         |
| Bajo (1.5M€)    | 1.50M €           | GK         | 0.0%         | 11.5%         | 27.8%         |
| Bajo (1.5M€)    | 1.50M €           | DF         | 1.1%         | 17.3%         | 33.6%         |
| Bajo (1.5M€)    | 1.50M €           | MF         | 2.5%         | 18.8%         | 35.1%         |
| Bajo (1.5M€)    | 1.50M €           | FW         | 4.3%         | 20.5%         | 36.8%         |
| Medio (3.0M€)   | 3.00M €           | GK         | 0.0%         | 7.7%          | 24.0%         |
| Medio (3.0M€)   | 3.00M €           | DF         | 0.0%         | 13.5%         | 29.8%         |
| Medio (3.0M€)   | 3.00M €           | MF         | 0.0%         | 15.0%         | 31.2%         |
| Medio (3.0M€)   | 3.00M €           | FW         | 0.4%         | 16.7%         | 33.0%         |
| Alto (5.0M€)    | 5.00M €           | GK         | 0.0%         | 4.9%          | 21.2%         |
| Alto (5.0M€)    | 5.00M €           | DF         | 0.0%         | 10.7%         | 27.0%         |
| Alto (5.0M€)    | 5.00M €           | MF         | 0.0%         | 12.2%         | 28.4%         |
| Alto (5.0M€)    | 5.00M €           | FW         | 0.0%         | 13.9%         | 30.2%         |
| Crack (>8.0M€)  | 8.50M €           | GK         | 0.0%         | 2.0%          | 18.3%         |
| Crack (>8.0M€)  | 8.50M €           | DF         | 0.0%         | 7.8%          | 24.1%         |
| Crack (>8.0M€)  | 8.50M €           | MF         | 0.0%         | 9.2%          | 25.5%         |
| Crack (>8.0M€)  | 8.50M €           | FW         | 0.0%         | 11.0%         | 27.3%         |

---

## 💡 4. Ejemplos Prácticos de Aplicación
* 🔹 **Caso: Delantero revelación barato disputado por 3 rivales** (FW, 1.00M €, 3 pujadores esperados):
  - **Sobrepuja Recomendada:** `+39.03%` (`+0.39M €`)
  - **Oferta Total Recomendada:** **`1.39M €`** (1,390,250 €)
* 🔹 **Caso: Portero titular de gama media disputado por 2 rivales** (GK, 2.50M €, 2 pujadores esperados):
  - **Sobrepuja Recomendada:** `+8.72%` (`+0.22M €`)
  - **Oferta Total Recomendada:** **`2.72M €`** (2,718,122 €)
* 🔹 **Caso: Defensa top disputado por 2 rivales** (DF, 4.50M €, 2 pujadores esperados):
  - **Sobrepuja Recomendada:** `+11.29%` (`+0.51M €`)
  - **Oferta Total Recomendada:** **`5.01M €`** (5,008,051 €)
* 🔹 **Caso: Centrocampista crack disputado por 2 rivales** (MF, 10.00M €, 2 pujadores esperados):
  - **Sobrepuja Recomendada:** `+8.35%` (`+0.84M €`)
  - **Oferta Total Recomendada:** **`10.84M €`** (10,835,335 €)