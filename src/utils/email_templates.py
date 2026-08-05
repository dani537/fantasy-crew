"""
Email Templates
===============
Newspaper-style Jinja2 HTML template for the Biwenger Agent reports.

IMPORTANT: email clients (especially Gmail) strip <style> blocks and modern CSS,
so everything here uses tables and inline styles only.
"""

BASE_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="{{ lang }}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#ece7df;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#ece7df;">
        <tr>
            <td align="center" style="padding:20px 10px;">
                <table role="presentation" width="620" cellpadding="0" cellspacing="0" style="background-color:#fdfbf7;border:1px solid #d8d2c4;">

                    <!-- MASTHEAD -->
                    <tr>
                        <td align="center" style="padding:24px 20px 8px 20px;border-bottom:3px double #1a1a1a;">
                            <div style="font-family:Georgia,'Times New Roman',serif;font-size:34px;font-weight:bold;color:#1a1a1a;letter-spacing:2px;">
                                ⚽ {{ newspaper_name }}
                            </div>
                            <div style="font-family:Georgia,serif;font-size:12px;color:#6b655a;font-style:italic;padding-top:6px;">
                                {{ edition_line }}
                            </div>
                        </td>
                    </tr>

                    <!-- HEADLINE -->
                    <tr>
                        <td align="center" style="padding:22px 28px 6px 28px;">
                            <div style="font-family:Georgia,'Times New Roman',serif;font-size:26px;font-weight:bold;color:#1a1a1a;line-height:1.25;">
                                {{ headline }}
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td align="center" style="padding:0 34px 18px 34px;border-bottom:1px solid #c9c2b2;">
                            <div style="font-family:Georgia,serif;font-size:15px;color:#4a463e;font-style:italic;line-height:1.5;">
                                {{ lede }}
                            </div>
                        </td>
                    </tr>

                    <!-- KEY FIGURES STRIP -->
                    {% if stats_html %}
                    <tr>
                        <td style="padding:14px 28px;border-bottom:1px solid #c9c2b2;background-color:#f5f1e8;">
                            <div style="font-family:Georgia,serif;font-size:13px;color:#1a1a1a;">
                                {{ stats_html }}
                            </div>
                        </td>
                    </tr>
                    {% endif %}

                    <!-- SECTIONS -->
                    {% for section in sections %}
                    <tr>
                        <td style="padding:18px 28px 4px 28px;">
                            <div style="font-family:Georgia,serif;font-size:17px;font-weight:bold;color:#7a1f1f;border-bottom:2px solid #7a1f1f;padding-bottom:4px;text-transform:uppercase;letter-spacing:1px;">
                                {{ section.title }}
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:10px 28px 6px 28px;">
                            <div style="font-family:Georgia,serif;font-size:14px;color:#2b2b2b;line-height:1.65;">
                                {{ section.body_html }}
                            </div>
                        </td>
                    </tr>
                    {% endfor %}

                    <!-- ACTIONS BOX -->
                    {% if actions_html %}
                    <tr>
                        <td style="padding:14px 28px 20px 28px;">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f5f1e8;border:1px dashed #7a1f1f;">
                                <tr>
                                    <td style="padding:14px 16px;font-family:Georgia,serif;font-size:13px;color:#2b2b2b;line-height:1.6;">
                                        {{ actions_html }}
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    {% endif %}

                    <!-- FOOTER -->
                    <tr>
                        <td align="center" style="padding:14px 20px;background-color:#1a1a1a;">
                            <div style="font-family:Georgia,serif;font-size:11px;color:#c9c2b2;">
                                {{ footer_line }}
                            </div>
                        </td>
                    </tr>

                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""
