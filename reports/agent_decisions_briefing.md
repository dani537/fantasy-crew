# 📋 BRIEFING Y PROMPT DE AUDITORÍA: EVALUACIÓN DE DECISIONES DE BIWENGER AGENT

> **Instrucciones para el Agente Auditor Externo**:
> Actúa como un Director Deportivo y Consultor Táctico Senior de Fantasy Football (LaLiga / Biwenger). Analiza los datos del equipo, las oportunidades del mercado y las decisiones tomadas por nuestro agente de IA (`Biwenger Agent`) en sus dos fases de simulación (`PRE_AUCTION` y `POST_AUCTION`). Valida si las operaciones ejecutadas son óptimas o si existen alternativas financieras/tácticas mejores.

---

## 📌 1. CONTEXTO DE LA LIGA Y REGLAS DE JUEGO

* **Plataforma**: Biwenger (LaLiga 2026).
* **Fase de Temporada**: Pretemporada (mercado de fichajes abierto, plantilla en construcción).
* **Sistema de Puntuación**: Media Picas AS + SofaScore (`SCORE_TYPE = 5`).
* **Filosofía del Agente**: Moneyball (Maximizar $\text{xP}$ por euro invertido, eliminar sesgos emocionales y asegurar liquidez).
* **Penalización Táctica**: **-4 puntos por cada posición vacía** en el 11 titular al inicio de la jornada.
* **Regla de Bloqueo de Ventas**: Con menos de 12 jugadores aptos en plantilla, **no se permite vender a ningún jugador apto** para evitar dejar el equipo sin efectivos.

---

## 💰 2. ESTADO FINANCIERO Y PLANTILLA ACTUAL DE MI EQUIPO (`Dani SR`)

### Balance Financiero:
* **Saldo Disponible**: `19.440.300 €`
* **Saldo Comprometido en Pujas Pendientes**: `3.139.500 €`
* **Presupuesto Libre Real (Efectivo para nuevas pujas)**: `16.300.800 €`
* **Valor de Mercado de Plantilla**: `27.640.000 €`
* **Saldo Futuro Estimado**: `+19.785.050 €`

### Diagnóstico Táctico de Plantilla (9 Jugadores - Sin Portero):
* **Esquema Táctico Actual**: `4-4-2` (parcial, con **2 huecos vacíos** en el once y **-4 ptos** de penalización activa por falta de efectivos).
* **Portería (POR/GK)**: **0 Porteros** (Situación Crítica / Prioridad Máxima).

| Jugador | Posición | Valor Mercado | Estado / Notas |
| :--- | :---: | :---: | :--- |
| **Nobel Mendy** | DEF / MED | 1.370.000 € | Titular probable / Apto |
| **Morten Hjulmand** | MED | 8.830.000 € | Titular clave / Apto |
| **Guido Rodríguez** | MED | 4.670.000 € | Titular clave / Apto |
| **Suso** | MED | 1.570.000 € | Rotación / Apto |
| **Marc Aguado** | MED | 1.850.000 € | Rotación / Apto |
| **Raúl Moro** | DEL / MED | 4.150.000 € | Puesto a la venta por 4.15M€ |
| **Berenguer** | DEL / MED | 4.150.000 € | Titular / Apto |
| **Bekhoucha** | DEF | 150.000 € | Puesto a la venta por 150k€ |
| **Álvaro Cortés** | POR / DEF | 150.000 € | Puesto a la venta por 150k€ |

---

## 🛒 3. CATÁLOGO COMPLETO DEL MERCADO DISPONIBLE

### A. Mercado de la Banca / Mercado Libre (Free Agents - "Mercado")
*Jugadores libres para subasta directa antes del reset (Pre-7:00 AM).*

| Jugador | Posición | Precio Mercado | Tendencia 24h | Perfil / Comentarios |
| :--- | :---: | :---: | :---: | :--- |
| **Isco** | MED | 11.260.000 € | ⬆️ +32.375 €/día | Estrella / Crack indiscutible |
| **Camavinga** | MED | 4.590.000 € | ⬆️ +80.375 €/día | Titular/Rotación top |
| **Aramburu** | DEF | 3.190.000 € | ⬆️ +80.375 €/día | Defensa titular al alza |
| **Bigas** | DEF | 2.410.000 € | ⬆️ +70.375 €/día | Defensa titular experimentado |
| **Gorrotxategi** | MED | 1.380.000 € | ⬆️ +70.375 €/día | Promesa / Especulación |
| **Kiko Femenía** | DEF | 910.000 € | ⬆️ +20.375 €/día | Lateral económico |
| **Héctor Fort** | DEF | 670.000 € | ⬆️ +50.375 €/día | Parche joven |
| **Yanis Begraoui / Musuayi** | DEL | 450.000 € | ⬇️ -10.000 €/día | Delantero barato |
| **André Almeida** | MED | 330.000 € | ➡️ 0 €/día | Parche de medio campo |
| **Louliashvili** | POR | 320.000 € | ⬆️ +12.240 €/día | Portero suplente económico |
| **Tete Morente** | MED | 310.000 € | ➡️ 0 €/día | Rotación |
| **Bakis** | DEL | 200.000 € | ⬆️ +12.240 €/día | Delantero económico |
| **Javi Enríquez / Zakharyan / Marcao / Pelayo** | Varios | 150.000 € | Varios | Precios mínimos (parches de 150k) |

### B. Mercado de Rivales (Jugadores propiedad de otros Managers)
*Disponibles para ofertas directas o clausulazos negociados (Post-7:00 AM).*

| Jugador | Posición | Vendedor | Precio Pedido / Cláusula |
| :--- | :---: | :---: | :---: |
| **Bellingham** | MED | La Vinotinto | 17.620.000 € |
| **Mikautadze** | DEL | Joan GM | 5.210.000 € |
| **Ilaix Moriba** | MED | La Vinotinto | 3.230.000 € |
| **Javi Guerra** | MED | Piedras FC | 4.320.000 € |
| **Odysseas** | POR | Piedras FC | 3.780.000 € |
| **Mayoral** | DEL | RusoPoderoso | 1.910.000 € |
| **Pau Navarro** | DEF | Palmeroks | 2.210.000 € |
| **Moi Gómez** | MED | RusoPoderoso | 430.000 € |
| **Ximo Navarro** | DEF | RusoPoderoso | 1.000.000 € |
| **Danjuma** | DEL | JubiladosFC | 1.640.000 € |
| **Torrentes / Óscar Valentín** | MED | Tesoreros del gol | ~1.46M€ |

---

## ⚡ 4. DECISIONES Y OPERACIONES EJECUTADAS POR EL AGENTE

### 🅰️ Simulación Fase 1: PRE_AUCTION (Pre-7:00 AM - Subastas Libres con la Banca)

* **Diagnóstico del Míster**: Plantilla de solo 9 jugadores y **0 porteros**. URGENTE fichar un POR y un DEF. Ventas prohibidas al no llegar a 12 jugadores aptos.
* **Operaciones Decididas por el Director Deportivo**:
  1. **Clausulazo / Puja Fuerte por Alfonso Herrero (7.420.000 €)**:
     - *Motivo*: Cobertura de la posición crítica de Portero (POR) con un titular garantizado.
  2. **Puja por Aramburu (3.400.000 €)**:
     - *Motivo*: Cobertura de la posición de Defensa (DEF) con una sobrepuja ligera sobre su valor (3.19M€) para asegurar ganar la subasta.
  3. **Puja por Bigas (2.800.000 €)**:
     - *Motivo*: Cobertura de un segundo Defensa (DEF) titular en alza para completar la zaga.
  4. **Cancelación de Puja previa por Aramburu**:
     - *Motivo*: Se cancela la puja anterior que se quedaba corta para re-elevar el importe a 3.4M€ y asegurar el activo.
  5. **Retirada del Mercado de Raúl Moro**:
     - *Motivo*: Estaba puesto a la venta, pero al estar prohibidas las ventas por plantilla corta ($<12$), se cancela su venta para no perder efectivos.

---

### 🅱️ Simulación Fase 2: POST_AUCTION (Post-7:00 AM - Ofertas y Cláusulas Directas)

* **Diagnóstico del Míster**: Mismas necesidades críticas (fichar POR y DEF para evitar penalización de -4 pts por hueco).
* **Operaciones Decididas por el Director Deportivo**:
  1. **Clausulazo por Alfonso Herrero (7.420.000 €)**:
     - *Motivo*: Cierre definitivo de la portería mediante pago directo de cláusula.
  2. **Clausulazo por Abqar (3.600.000 €)**:
     - *Motivo*: Compra garantizada de un defensa titular (DEF) mediante pago de cláusula de rescisión.
  3. **Cancelación de Puja por Aramburu**:
     - *Motivo*: Al asegurar el fichaje de Abqar mediante cláusula, se libera el presupuesto comprometiendo a Aramburu para evitar sobre-costes o duplicidad no deseada.

---

## ❓ 5. PREGUNTAS CLAVE PARA EL AGENTE AUDITOR EXTERNO

Por favor, revisa el plan ejecutado por nuestro agente y responde a las siguientes cuestiones:

1. **Evaluación de la Portería**: ¿Es acertado invertir **7.42M€** en el clausulazo de Alfonso Herrero teniendo en cuenta que el presupuesto disponible era de 16.3M€ y que no había ningún otro portero titular en la banca libre?
2. **Priorización Táctica vs Estrellas**: ¿Apruebas priorizar el fichaje de defensores titulares (Aramburu / Bigas / Abqar) para evitar la penalización de -4 puntos por hueco en lugar de pujar todo el presupuesto por una estrella como **Isco (11.26M€)** o **Bellingham (17.62M€)**?
3. **Gestión de Ventas**: ¿Consideras correcta la decisión del agente de bloquear las ventas de jugadores como Raúl Moro o Suso debido a que la plantilla solo cuenta con 9 futbolistas aptos?
4. **Optimización Financiera**: De las opciones de rivales en el mercado (Danjuma a 1.64M€, Mayoral a 1.91M€, Odysseas a 3.78M€), ¿propondrías alguna operación alternativa más rentable?
