"""
Email Utility for Fantasy Crew
==============================

Sends reports via Gmail using SMTP with App Password.

Environment Variables Required:
- GMAIL_ADRESS: Your Gmail address
- GMAIL_PASSWORD: App Password (not your regular password)
"""

import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def send_report_email(report_content: str, subject: str = None) -> bool:
    """
    Sends the Fantasy Crew report via Gmail.
    
    Args:
        report_content: The markdown content of the report.
        subject: Optional custom subject. Defaults to "Fantasy Crew Report - {date}".
        
    Returns:
        True if email sent successfully, False otherwise.
    """
    # Load credentials from environment
    gmail_address = os.getenv("GMAIL_ADRESS")
    gmail_password = os.getenv("GMAIL_PASSWORD")
    
    if not gmail_address or not gmail_password:
        print("❌ Email Error: GMAIL_ADRESS or GMAIL_PASSWORD not found in .env")
        return False
    
    # Default subject with timestamp
    if subject is None:
        subject = f"🏆 Fantasy Crew Report - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    try:
        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = gmail_address
        msg["To"] = gmail_address  # Send to self
        
        # Plain text version
        text_part = MIMEText(report_content, "plain", "utf-8")
        msg.attach(text_part)
        
        # Connect to Gmail SMTP server
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, gmail_password)
            server.sendmail(gmail_address, gmail_address, msg.as_string())
        
        print(f"📧 Email sent successfully to {gmail_address}")
        return True
        
    except smtplib.SMTPAuthenticationError:
        print("❌ Email Error: Authentication failed. Check your App Password.")
        return False
    except smtplib.SMTPException as e:
        print(f"❌ Email Error: SMTP error - {e}")
        return False
    except Exception as e:
        print(f"❌ Email Error: {e}")
        return False


def send_report_from_file(filepath: str = "./reports/00_final_report.md") -> bool:
    """
    Reads a report file and sends it via email.
    
    Args:
        filepath: Path to the report markdown file.
        
    Returns:
        True if successful, False otherwise.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return send_report_email(content)
    except FileNotFoundError:
        print(f"❌ Email Error: Report file not found: {filepath}")
        return False
    except Exception as e:
        print(f"❌ Email Error: Could not read report file - {e}")
        return False
