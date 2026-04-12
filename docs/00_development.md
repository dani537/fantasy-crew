# 🚀 Biwenger Agent Development Roadmap

Este documento detalla la estructura y el flujo de trabajo del sistema agéntico para la gestión de la liga Biwenger.

---

## 📊 STEP 1: DATA ANALYST

La misión del **Data Analyst** es la ejecución de procesos técnicos que suministran la base de datos necesaria para el sistema multiagente.

### 🔍 Tipos de Datos Extraídos
El sistema distingue claramente entre información global del juego e información específica de la liga del usuario:

*   **Datos Generales (Globales de Biwenger y Externos):**
    *   **Jugadores:** Fichas técnicas, valor de mercado, subidas/bajadas de precio, y puntos acumulados.
    *   **Equipos y Partidos:** Calendario (próxima jornada), localía y métricas externas de predicción (probabilidades de resultado de EuroClubIndex).
    *   **Contexto Externo:** Alineaciones probables, titularidades, dudas o lesiones (Comuniate) y noticias de última hora (Jornada Perfecta).
*   **Datos Específicos de tu Liga (Contexto Local y Económico):**
    *   **Estado Financiero:** Saldo actual del usuario, presupuesto disponible para fichajes y valor total de la plantilla.
    *   **Mercado Activo:** Jugadores en venta actualmente y el historial de transacciones (ofertas y fichajes recientes).
    *   **Ofertas Pendientes:** Ofertas reales de traspaso recibidas de la máquina o de otros usuarios que el usuario aún no ha aceptado ni rechazado.
    *   **Plantillas (Rosters):** Quién posee a cada jugador, el precio por el que lo fichó, su cláusula de rescisión y cuándo finaliza su bloqueo.
    *   **Rivalidad:** Clasificación actual, puntos y valor de la plantilla de todos tus rivales directos.

### ⚙️ Transformación y Métricas Calculadas
Los datos brutos son transformados y cruzados para generar insights precisos para la IA:
1.  **Normalización:** Se estandarizan las columnas de múltiples fuentes de origen para que todo el sistema "hable el mismo idioma".
2.  **Cálculo de Nuevas Métricas:** Para facilitar la toma de decisiones del Coach, se autocalculan promedios avanzados:
    *   **Promedio Global (`AVG_POINTS_TOTAL`):** Relación de puntos totales divididos por partidos disputados.
    *   **Promedio Situacional (`AVG_POINTS_HOME` / `AWAY`):** Desglose del rendimiento dependiendo de si el jugador juega en casa o fuera. Esto es clave para alinear jugadores según dónde disputen la próxima jornada.
3.  **Persistencia:** Todos estos datos interconectados se salvan en archivos (generalmente CSVs) sirviendo como la "Memoria Base" sobre la que actuarán el resto de agentes.

> [!IMPORTANT]
> El Data Analyst no toma decisiones, simplemente "prepara la mesa" proveyendo todo el contexto estadístico y de mercado posible.


---

## 📋 STEP 2: COACH

El Coach analiza los datos procesados para optimizar el rendimiento de la plantilla actual.

### 🎯 Objetivos Estratégicos
1.  **Maximizar Puntuación:** Optimizar el once inicial basándose en estadísticas, tendencias, proximidad de partidos, probabilidad de titularidad y noticias de última hora.
2.  **Análisis de Roster:** Identificar debilidades críticas para reforzar y fortalezas para potenciar.

### 🔄 Flujo de Trabajo y Contratos de Datos

Para asegurar una integración robusta entre agentes, la comunicación se realiza mediante esquemas JSON rígidos.

#### 1. Análisis Individual del Equipo (Coach ➔ Roster)
El Coach etiqueta individualmente a cada jugador del equipo propio para evitar "redacciones" y forzar una clasificación técnica.

```json
{
  "analisis_jugadores": [
    {
      "id_jugador": 12345,
      "nombre": "Jude Bellingham",
      "posicion": "MED",
      "estado_fisico": "disponible", 
      "titularidad_proyectada": "seguro", 
      "analisis_tactico": "Promedia 8.5 puntos en casa. El Madrid juega contra el colista.",
      "etiqueta_mercado": "intocable", 
      "puntuacion_esperada_jornada": 8.5
    }
  ]
}
```
*   **Enums Estado Físico:** `disponible`, `lesionado`, `duda`, `sancionado`.
*   **Enums Etiqueta Mercado:** `intocable`, `mantener`, `rotacion`, `vendible`, `venta_urgente`.

#### 2. Propuesta para Dirección Deportiva (El "Briefing")
Documento de traspaso (*handoff*) que traduce las etiquetas tácticas en directrices financieras e instrucciones de mercado directas.

```json
{
  "resumen_plantilla": {
    "huecos_titulares_libres": 2,
    "valoracion_general": "Defensa sólida pero ataque dependiente de un solo jugador."
  },
  "lista_ventas": [
    {
      "id_jugador": 67890,
      "nombre": "Jugador Parche",
      "motivo": "Pierde valor rápido. Liberar ficha.",
      "prioridad_venta": "ALTA" 
    }
  ],
  "necesidades_fichaje": [
    {
      "id_necesidad": "req_1",
      "posicion_requerida": "DEL",
      "perfil_tactico": "Titular indiscutible. Necesitamos gol inmediato.",
      "presupuesto_recomendado_porcentaje": 60, 
      "prioridad": "ALTA"
    }
  ]
}
```
> [!TIP]
> Esta estructura permite que el Director Deportivo actúe basándose en prioridades y porcentajes de presupuesto, automatizando la lógica de pujas.


---

## 💼 STEP 3: SPORTING DIRECTOR (Director Deportivo)

El Director Deportivo es el ejecutor del mercado. Su trabajo es coger el "Briefing" táctico/financiero del Coach y cruzarlo con el estado del Mercado Activo y el Banco de la liga para tomar decisiones exactas de compra/venta monetarias.

### 🎯 Objetivos Estratégicos
1.  **Resolver Ofertas Recibidas:** Decidir si aceptar, rechazar o mantener ofertas concretas ("Computer" u otros usuarios) que tengamos sobre la mesa.
2.  **Ejecutar Ventas (Nuevas):** Colocar en el mercado actual a los jugadores descartados por el Coach.
3.  **Cazar Oportunidades (Fichajes):** Revisar el mercado de hoy y cruzar los perfiles disponibles con las necesidades (`necesidades_fichaje`) del Coach, asumiendo que **a veces el mercado no ofrecerá soluciones** (Mercado Vacío).
4.  **Gestión Inteligente y Segura de Pujas (Regla del Saldo):** Calcular la puja exacta sin romper en ningún caso la viabilidad económica. Si sobrepujar pone la cuenta bancaria en negativo a escasas horas de que empiece la jornada, la puja debe descartarse para evitar puntuar cero.

### 🔄 Flujo de Trabajo y Contratos de Datos

El output del Director Deportivo es el paso final que da pie o a un debate (con el Coach/Presidente) o a la ejecución pura e integración con la API de Biwenger. Debe ser transaccional.

#### JSON Paso 3: Propuesta de Operaciones (Salida del Sporting Director)
Un listado de órdenes ejecutables, asociadas a IDs reales, con un importe monetario claro.

```json
{
  "analisis_financiero_previo": {
    "presupuesto_disponible": 15000000,
    "valor_mercado_objetivo_ventas": 3200000,
    "saldo_proyectado_post_operaciones": 2500000 
  },
  "resolucion_ofertas_pendientes": [
    {
      "id_oferta": 998877,
      "id_jugador": 67890,
      "accion": "aceptar",
      "justificacion": "La oferta de la máquina supera su valor de mercado un 5% y el jugador está bajando."
    }
  ],
  "operaciones_venta": [
    {
      "id_jugador": 67891,
      "nombre": "Jugador Descarte",
      "estrategia_venta": "inmediata",
      "precio_minimo_esperado": 1500000,
      "justificacion": "El Coach pide su venta. Lo ponemos a la venta hoy para escuchar ofertas mañana."
    }
  ],
  "operaciones_compra": [
    {
      "id_jugador_mercado": 55443,
      "nombre": "Delantero Top",
      "id_necesidad_coach": "req_1",
      "importe_oferta": 9500000,
      "tipo_puja": "sobrepuja_agresiva",
      "justificacion": "Cubre necesidad ALTA. Usamos el tope presupuestado porque sube rápido de precio. El saldo post-operaciones se mantiene positivo."
    }
  ],
  "necesidades_no_cubiertas": [
    {
      "id_necesidad_coach": "req_2",
      "motivo": "No han salido al mercado defensas titulares que se ajusten al presupuesto."
    }
  ]
}
```

*   **Enums Resolución Ofertas:** `aceptar`, `rechazar`, `mantener`.
*   **Enums Estrategia Venta (Ventas Nuevas):** `inmediata` (aceptar primera oferta razonable mañana), `especulativa` (esperar oferta por encima del valor).
*   **Enums Tipo Puja:** `valor_mercado` (puja exacta por VM), `sobrepuja_ligera` (+5%), `sobrepuja_agresiva` (+15-20%), `clausulazo` (pago de cláusula a rival).

> [!WARNING]
> **La Regla de Oro en Biwenger es el saldo en positivo**. El campo `saldo_proyectado_post_operaciones` actúa como control de fallos. Si las operaciones de compra sumadas superan al saldo más las ventas aceptadas, el agente debe recular automáticamente si el inicio de jornada es inminente (< 48h).