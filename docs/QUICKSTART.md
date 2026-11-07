# ⚡ Guía Rápida ("Insultantemente Fácil") — `Fantasy Champion v3`

Bienvenido a **Fantasy Champion v3**: tu Director Deportivo Cuantitativo para Biwenger. No necesitas saber matemáticas ni configurar las reglas de tu liga a mano — el bot se conecta a tu liga y **autodescubre todas sus normas oficiales** (sistema de puntos, crédito permitido, límite de jugadores en venta, horas de bloqueo de cláusulas y escalones de cláusulas).

---

## 🚀 Opción A: Puesta en Marcha en 3 Minutos (En tu ordenador / servidor)

### 1. Instala las dependencias
```bash
git clone <URL_DE_TU_REPO> fantasy-champion
cd fantasy-champion
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Lanza el Asistente Automático (90 segundos)
```bash
python -m src.setup_wizard
```
El asistente:
1. Inicia sesión en tu Biwenger y te muestra tus ligas para que elijas cuál gestionar.
2. Descarga automáticamente todas las reglas oficiales de esa liga.
3. Configura tu correo (para el informe HTML cada mañana) y tu Bot de Telegram (autodetectando tu `CHAT_ID` con solo enviarle `"Hola"`).

Verifica que todo está en verde en cualquier momento con:
```bash
python -m src.setup_wizard --check
```

### 3. Haz tu primera simulación segura (`DRY_RUN`, no toca nada en Biwenger)
```bash
# Descarga el histórico inicial de tu liga (solo la primera vez):
python -m src.tools.data_extraction.runner
python -m src.tools.data_extraction.historical_backfill

# Simula las decisiones de hoy y recibe el informe en pantalla:
python -m src.tools.strategy_engine.autopilot --mode DRY_RUN
```

### 4. Habla con tu Director Deportivo por Telegram
```bash
python -m src.tools.notifications.telegram_bot --listen
```
Escríbele en Telegram como a un amigo (`"¿Cómo vamos de caja para el parón?"`, `"No vendas a Pedri"`) o usa los atajos:
- `/estado` — Saldo real, cuándo cierran las cláusulas (ej. 48h antes) y ofertas activas en `<12s`.
- `/once` — Tu mejor alineación matemática para esta jornada.
- `/tesis` — Qué rol tiene cada jugador de tu plantilla y si compensa aguantarlo o venderlo (`NPV_HOLD` vs `NPV_SELL`).
- `/pendientes` y `/aprobar CL-1234` — Aprueba con un toque un clausulazo o venta importante propuesta por el bot.
- `/intocable Nombre` / `/transferible Nombre` — Marca o desmarca jugadores protegidos en su memoria persistente.

---

## ☁️ Opción B: Automatización 100% Gratis en GitHub Actions (Sin tener el PC encendido)

1. Haz un **Fork** privado de este repositorio en tu cuenta de GitHub.
2. Ve a **Settings → Secrets and variables → Actions → New repository secret** y añade:
   - `BIWENGER_USERNAME` y `BIWENGER_PASSWORD` (o `BIWENGER_TOKEN`)
   - `GEMINI_API_KEY` (gratis en [Google AI Studio](https://aistudio.google.com/apikey))
   - `GMAIL_ADDRESS` y `GMAIL_PASSWORD` (Contraseña de aplicación de Gmail)
   - `TELEGRAM_BOT_TOKEN` y `TELEGRAM_ALLOWED_CHAT_ID`
   - *(Opcional)* `GOOGLE_SHEET_ID` y `GOOGLE_SERVICE_ACCOUNT_JSON` si quieres persistencia en Google Sheets además del Dashboard Streamlit.
3. ¡Ya está! Los workflows incluidos se encargan solos de todo:
   - **`daily_market_tracker.yml` (Cada mañana):** Extrae los ~550 jugadores de LaLiga, entrena las predicciones y **al terminar** ejecuta `AutopilotRunner` enviándote tu Email HTML + mensaje de Telegram con las pujas hechas, el escaparate Top-5 y el once actualizado.
   - **`tactical_pulse.yml` (Pulso dinámico de jornada):** Vigila gratis el calendario real de LaLiga (ya empiece la jornada en viernes, martes intersemanal o sábado) y dispara revisiones rápidas (`<12s`) en **3 momentos clave**:
     1. **2,5h antes del bloqueo de cláusulas** (ej. miércoles tarde si hay bloqueo de 48h).
     2. **2,5h antes del primer partido** (garantía absoluta de `saldo >= 0 €` para no hacer un cero).
     3. **30 min antes del primer partido** (guardia anti-lesiones de última hora en el once titular).
   - **`telegram_responder.yml` + `deploy/cloudflare_telegram_webhook.js` (Opcional):** Si quieres respuesta instantánea en Telegram sin servidor propio, despliega las 35 líneas de `cloudflare_telegram_webhook.js` en Cloudflare Workers Free.

---

## 🛡️ ¿Qué hace el bot solo y qué depende de ti en `SEMI_AUTO`?

| Operación | ¿Quién actúa en `SEMI_AUTO`? | ¿Te avisa siempre? |
| :--- | :---: | :---: |
| **Alineación Óptima MILP (y cambio anti-lesión 30m antes)** | 🤖 **Autónomo** | ✅ Email + Telegram |
| **Escaparate Preventivo Top-5 (+5% VM, coste 0 €)** | 🤖 **Autónomo** | ✅ Email + Telegram |
| **Pujas Óptimas en el Mercado Diario** | 🤖 **Autónomo** *(configurable)* | ✅ Email + Telegram |
| **Ventas menores de especulación / parche (< 4 M€)** | 🤖 **Autónomo** *(configurable)* | ✅ Email + Telegram |
| **Salvamento de deuda antes de empezar la jornada** | 🤖 **Autónomo** (evita puntuar 0) | ✅ Email + Telegram |
| **Clausulazos a rivales y Blindajes de cláusula** | 👤 **SOLO TÚ** (`/aprobar CL-...`) | 🔔 Alerta Prioritaria |
| **Ventas importantes (Estrellas, Titulares o ≥ 4 M€)** | 👤 **SOLO TÚ** (`/aprobar VT-...`) | 🔔 Alerta Prioritaria |
