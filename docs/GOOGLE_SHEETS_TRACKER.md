# 📊 Manual de Integración: Biwenger Google Sheets Tracker

Este documento detalla el funcionamiento del sistema de seguimiento de mercado, compras, ventas y estimación de saldos en tiempo real de todos los rivales de tu liga de **Biwenger**, integrado con **Google Sheets**.

🔗 **Libro de Google Sheets en vivo:** [fantasy_tracker](https://docs.google.com/spreadsheets/d/1V3lDapPrpGgLGVl-rvNi3Ishy70dAo22UEn24toa4kk/edit)

---

## 🎯 1. Arquitectura y Funcionamiento

El sistema opera bajo el principio de **libro mayor inmutable con sincronización incremental**:

1. **Histórico Completo Inicial**:
   - Se han cargado en Google Sheets los **281 movimientos reales** producidos desde el Día 1 (01/08) hasta hoy.
2. **Sincronizaciones Diarias Rápidas**:
   - En las ejecuciones rutinarias, el script **solo descarga los últimos 5 días** del muro de Biwenger.
   - Compara con el histórico de Google Sheets y añade **únicamente las operaciones nuevas** mediante una clave compuesta única (`id_unico`).
3. **Cálculo Financiero Preciso**:
   - A partir del saldo base inicial registrado al inicio de la liga (`23.84M € - 24.06M €` según plantilla asignada para totalizar 40M € de patrimonio base) y del acumulado de compras (-) y ventas (+), calcula el **saldo líquido disponible estimado** y el **patrimonio total** de cada rival al céntimo.

---

## 📋 2. Estructura de las Hojas en Google Sheets

### 1. `Movimientos` (Histórico de Operaciones)
| Columna | Descripción |
| :--- | :--- |
| `id_unico` | Identificador único compuesto para deduplicar automáticamente |
| `fecha` | Fecha y hora exacta de la transacción |
| `tipo` | `Compra Mercado`, `Venta Mercado`, `Traspaso entre Managers`, `Clausulazo` |
| `jugador` | Nombre real del futbolista |
| `player_id` | ID numérico del jugador en Biwenger |
| `comprador` / `buyer_id` | Nombre y ID del manager comprador (o Mercado) |
| `vendedor` / `seller_id` | Nombre y ID del manager vendedor (o Mercado) |
| `precio` | Importe en € |
| `es_clausula` | `SÍ` / `NO` |

### 2. `Config_Inicial` (Bases de Partida y Primas)
| Columna | Descripción |
| :--- | :--- |
| `user_id` | ID del manager en Biwenger |
| `manager` | Nombre del equipo / rival |
| `presupuesto_inicial` | Saldo inicial en caja al inicio de la liga (01/08) |
| `valor_equipo_inicial` | Valor de mercado de la plantilla inicial sorteada |
| `primas_manuales` | Primas adicionales de liga, por jornada o penalizaciones (editable) |
| `notas` | Descripción / observaciones |

> 💡 **Personalización:** Puedes cambiar `presupuesto_inicial` o sumar primas en `primas_manuales` en cualquier momento en esta hoja; el script los tomará en cuenta en la siguiente sincronización.

### 3. `Saldos_Estimados` (Panel de Control Financiero)
| Columna | Descripción |
| :--- | :--- |
| `Pos` | Posición en el ranking por patrimonio total |
| `Manager` | Nombre del rival |
| `Saldo Disponible Est.` | Dinero en caja disponible: $\text{Saldo Inicial} + \text{Ventas} - \text{Compras} + \text{Primas}$ |
| `Valor Plantilla` | Valor de mercado en vivo del equipo del rival |
| `Patrimonio Total Est.` | $\text{Saldo Disponible Est.} + \text{Valor Plantilla}$ |
| `Beneficio Neto (€)` | Ganancia neta patrimonial respecto a los 40M € iniciales |
| `Presupuesto Inicial` | Caja de partida |
| `Total Gastado` / `Total Ingresado` | Suma de todas las compras y ventas realizadas |
| `Primas Manuales` | Primas añadidas |
| `Fichajes` / `Ventas` | Número total de operaciones |
| `Última Actualización` | Fecha y hora del cálculo |

---

## 🚀 3. Cómo Ejecutar el Tracker

### 1. Sincronización Rutinaria (Recomendada - Últimos 5 días):
```bash
python main.py --mode tracker
```

### 2. Sincronización con ventana personalizada de días (ej. 10 días):
```bash
python main.py --mode tracker --days 10
```

### 3. Sincronización Completa Forzada (Descargar todo el muro):
```bash
python main.py --mode tracker --full
```

---

## ☁️ 4. Configuración de Credenciales de Google Cloud

1. **APIs habilitadas en GCP**: `Google Sheets API` y `Google Drive API`.
2. **Cuenta de Servicio**: `credentials_google.json` en la raíz del proyecto (incluida en `.gitignore`).
3. **Variables en `.env`**:
   ```env
   GOOGLE_SHEET_ID=1V3lDapPrpGgLGVl-rvNi3Ishy70dAo22UEn24toa4kk
   GOOGLE_SERVICE_ACCOUNT_FILE=./credentials_google.json
   ```
