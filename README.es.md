# 🚀 Fantasy Crew (Multi-Agent System)

**Objetivo:** Crear un equipo de agentes de IA autónomos que gestionen una plantilla de Biwenger, optimizando el rendimiento deportivo y financiero mediante el uso de LLMs de última generación y análisis de datos avanzado.

Este sistema supera a un jugador humano al eliminar el sesgo emocional, operar 24/7 y procesar grandes volúmenes de datos en tiempo real para maximizar el Valor de Mercado (VM) y la puntuación de la plantilla.

---

## 👥 El Staff Técnico (Los Agentes)

El sistema opera mediante una **arquitectura secuencial de multi-agentes**, donde cada rol utiliza modelos de lenguaje (LLMs) y procesamiento de datos para aportar valor en una etapa específica del pipeline.

### 1. 🔮 El Analista (Data Analyst)
**"La Fuente de Verdad"**
*   **Rol:** Agente de ingeniería y consolidación de datos. Prepara el terreno para los modelos de lenguaje mediante limpieza determinista.
*   **Procesamiento (Feature Engineering):**
    *   **Fuzzy Matching Multi-Fuente:** Cruza nombres de equipos y jugadores entre Biwenger, Comuniate y casas de apuestas (Odds), resolviendo discrepancias (ej. "RCD Espanyol" vs "Espanyol").
    *   **Normalización Táctica:** Mapea posiciones numéricas a etiquetas legibles (`GK`, `DF`, `MF`, `FW`) y procesa posiciones alternativas.
    *   **Limpieza de Probabilidades:** Convierte ruidos en los datos de prensa (ej. "80%") en valores numéricos limpios para el análisis.
    *   **Optimización de Tokens:** Redondea métricas a 2 decimales para maximizar la eficiencia en la ventana de contexto de los LLMs.
*   **Salida:** Genera `df_master_analysis.csv` (plantilla completa) y enriquece `data/next_match.csv` con probabilidades de victoria (Odds).

### 2. 📋 El Entrenador (The Mister)
**"El Estratega Deportivo"**
*   **Rol:** Toma decisiones tácticas basadas en el rendimiento y la disponibilidad.
*   **Lógica (DeepSeek):**
    *   **Contexto Temporal:** Considera la fecha/hora actual y la proximidad del inicio de la jornada.
    *   **Gestión de Alineaciones:** Prioriza formaciones ofensivas (3-4-3) pero es flexible para evitar la penalización de **-4 puntos** por huecos vacíos.
    *   **Conciencia de Club:** Reconoce compañeros de equipo (vía `TEAM_NAME`) para asegurar la portería si cuenta con el portero titular y el suplente del mismo club.
    *   **Análisis de Momentum:** Evalúa la racha (`PLAYER_FITNESS`) y el rendimiento relativo (Casa/Fuera) frente a la dificultad del rival (Odds).
*   **Estrategia de Mercado:** Define qué jugadores son ventas necesarias (**REAL**) y cuáles se listan para recibir ofertas preventivas (**RESERVE**).

### 3. 💼 El Director Deportivo (The Broker)
**"El Controlador Financiero"**
*   **Rol:** Ejecuta la estrategia de mercado bajo una disciplina presupuestaria estricta.
*   **Lógica (DeepSeek):**
    *   **El Dogma del Balance Positivo:** Su prioridad #1 es asegurar que el equipo no empiece la jornada con saldo negativo (lo que anularía los puntos).
    *   **Gestión de Presupuesto:** Carga el saldo real desde `user_info.csv` y estima los ingresos por ventas propuestas para calcular el poder de compra.
    *   **Scouting Basado en Necesidades:** Cruza los gritos de auxilio del Coach (ej. "NECESITAMOS MC") con las mejores oportunidades del mercado.
*   **Salida:** Proyectos de fichaje que equilibran impacto deportivo y rentabilidad (`ROI`).

### 4. 🧠 El Presidente (The Strategist)
**"La Autoridad Ejecutiva"**
*   **Rol:** Validador final con visión de riesgo y largo plazo.
*   **Lógica (DeepSeek):**
    *   **Filtro Presupuestario:** Aplica la máxima severidad financiera; rechaza fichajes ostentosos que comprometan la estabilidad del club.
    *   **Aprobación Condicional:** Puede autorizar un fichaje supeditado a la venta previa de un lastre del equipo.
*   **Salida:** Emite el **Informe Ejecutivo Final** con las acciones definitivas a tomar.

---

## 🔄 Flujo de Trabajo (Workflow)

El sistema ejecuta estos agentes en cadena (`main.py`):

1.  **Extract & Transform:** `DataAnalyst` descarga datos y crea el `df_master_analysis`.
2.  **Squad Analysis:** `Coach` lee los datos de tu equipo y detecta problemas.
3.  **Market Scouting:** `SportingDirector` lee el informe del Coach y busca soluciones en el mercado.
4.  **Executive Decision:** `President` revisa las soluciones y da luz verde.
5.  **Reporting:** Se genera el archivo final `final_recommendations.md` con todo el proceso.

---

## 📊 Fuentes de Datos (Data Sources)

El sistema se alimenta de una arquitectura de datos robusta extraída automáticamente mediante diversos procesos (`src/`):

### 1. Biwenger API (Datos Oficiales)
Conexión directa con la API de Biwenger para obtener el estado real de la liga.
*   **Datos Generales de LaLiga (`LaLigaGeneralData`):**
    *   Base de datos completa de **Jugadores** (Puntos, Precio, Estado físico, Fitness, Estadísticas local/visitante).
    *   Información de **Equipos** (Calendario, Próximos rivales).
    *   Datos de la **Próxima Jornada** (Horarios, Partidos).
*   **Datos de la Liga de Usuario (`UserLeagueData`):**
    *   **Rivales:** Escaneamos las plantillas de todos los rivales para conocer sus alineaciones, precios de compra y, lo más importante, sus **Cláusulas de Rescisión**.
    *   **Mercado:** Monitorización de jugadores libres en venta y ofertas recibidas por nuestros jugadores.
    *   **Clasificación:** Estado actual de la tabla de puntos y valor de equipo.

### 2. Comuniate (Web Scraping Avanzado)
Extracción de inteligencia táctica desde *Comuniate.com* mediante `BeautifulSoup`.
*   **Alineaciones Probables:** Predicción de los onces titulares para la siguiente jornada.
*   **Probabilidad de Titularidad:** Porcentaje estimado de que un jugador inicie el partido.
*   **Alertas de Estado:** Detección de jugadores **Apercibidos** (riesgo de sanción) o **Duda** por molestias.
*   **Posiciones Tácticas:** Clasificación precisa del rol del jugador en el campo.

### 3. Jornada Perfecta (RSS & News Analysis)
Sistema de ingesta de noticias en tiempo real desde *JornadaPerfecta.com*.
*   **Procesamiento de Noticias:** Lectura y limpieza de artículos deportivos.
*   **Resumen para LLMs:** Transformación de noticias en formatos optimizados para que "The Oracle" (IA) pueda leerlas y entender el contexto (lesiones, rotaciones, ruedas de prensa).

### 4. Casas de Apuestas (Odds)
Datos estadísticos de mercado para apoyar la toma de decisiones.
*   **Predicción de Partidos:** Probabilidades matemáticas (1X2) extraídas y mapeadas para cada encuentro.
*   **Dificultad del Jugador:** Permite evaluar si un jugador se enfrenta a un partido "fácil" (favorito claro) o un "muro" (el rival es favorito), optimizando la recomendación de alineación.
*   **Sincronización:** Mapeo automático mediante el Analista para cruzar datos de apuestas con la plantilla de Biwenger.

---

## 🛠️ Stack Tecnológico

*   **Lenguaje:** Python 3.12+
*   **Gestión de Agentes:** LangGraph / CrewAI (Orquestación de roles).
*   **Procesamiento de Datos:**
    *   `Pandas` para manipulación de DataFrames y limpieza de datos.
    *   `BeautifulSoup4` para Web Scraping (Comuniate).
    *   `Feedparser` para lectura de RSS.
*   **Modelos de IA (LLMs):**
    *   **DeepSeek-V3:** Lógica intermedia y procesamiento de datos estructurados (High Performance/Low Cost).
    *   **DeepSeek-R1:** Motor de razonamiento complejo para el "Presidente".
    *   **Gemini 1.5 Flash:** Análisis de contexto largo (ventana amplia) para procesar noticias masivas.

---

## 🎯 Ventaja Competitiva

1.  **Sin Sesgo Emocional:** El sistema no se "enamora" de jugadores. Vende cuando la estadística indica declive y ficha cuando detecta oportunidad.
2.  **Ingeniería Financiera:** Cálculo preciso del valor futuro, cláusulas y márgenes de beneficio.
3.  **Velocidad de Reacción:** Capacidad de fichar o vender segundos después de que ocurra una noticia relevante (lesión en entrenamiento, alineación confirmada).
4.  **Visión Global:** Cruzado de datos de mercado, noticias y estadística avanzada que un humano tardaría horas en recopilar manualmente.
