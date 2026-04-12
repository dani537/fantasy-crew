"""
Email Templates
===============
Contains Jinja2 HTML templates for the Fantasy Crew reports.
"""

BASE_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            color: #333;
            background-color: #f4f7f6;
            margin: 0;
            padding: 0;
        }
        .container {
            max-width: 600px;
            margin: 20px auto;
            background: #ffffff;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 4px 10px rgba(0,0,0,0.1);
        }
        .header {
            background-color: #1a2a6c;
            color: #ffffff;
            padding: 30px 20px;
            text-align: center;
        }
        .header h1 {
            margin: 0;
            font-size: 24px;
            text-transform: uppercase;
            letter-spacing: 2px;
        }
        .content {
            padding: 30px 20px;
        }
        .headline {
            font-size: 22px;
            font-weight: bold;
            color: #1a2a6c;
            margin-bottom: 20px;
            border-bottom: 2px solid #f2a900;
            padding-bottom: 10px;
        }
        .section {
            margin-bottom: 25px;
        }
        .section-title {
            font-size: 18px;
            font-weight: bold;
            color: #b21f1f;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
        }
        .section-content {
            background: #f9f9f9;
            padding: 15px;
            border-left: 4px solid #1a2a6c;
            border-radius: 0 4px 4px 0;
        }
        .actions-box {
            background-color: #eef2f3;
            border: 1px dashed #1a2a6c;
            padding: 15px;
            border-radius: 6px;
        }
        .footer {
            background-color: #1a2a6c;
            color: #ffffff;
            text-align: center;
            padding: 20px;
            font-size: 12px;
        }
        .badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 12px;
            font-size: 10px;
            font-weight: bold;
            text-transform: uppercase;
            margin-right: 5px;
        }
        .badge-buy { background-color: #28a745; color: white; }
        .badge-sell { background-color: #dc3545; color: white; }
        .badge-lineup { background-color: #007bff; color: white; }
        
        ul { padding-left: 20px; margin: 0; }
        li { margin-bottom: 8px; }
        b, strong { color: #1a2a6c; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🏆 FANTASY CREW</h1>
            <p>Reporte de Análisis Multiagente</p>
        </div>
        
        <div class="content">
            <div class="headline">
                {{ headline }}
            </div>
            
            <p>{{ introduction }}</p>
            
            <div class="section">
                <div class="section-title">📊 El Debate (Míster vs Broker)</div>
                <div class="section-content">
                    {{ debate_summary }}
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">🏛️ El Veredicto del Presidente</div>
                <div class="section-content">
                    {{ president_verdict }}
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">⚡ Acciones Ejecutadas</div>
                <div class="actions-box">
                    {{ actions_html }}
                </div>
            </div>
        </div>
        
        <div class="footer">
            <p>Generado automáticamente por tu equipo de agentes de Fantasy Crew</p>
            <p>&copy; 2024 Biwenger Agent Manager</p>
        </div>
    </div>
</body>
</html>
"""
