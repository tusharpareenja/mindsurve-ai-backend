"""Best-effort collaborator invitation emails (SMTP optional)."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def send_project_invite_email(
    *,
    to_email: str,
    inviter_name: str,
    project_name: str,
    is_new_user: bool,
) -> bool:
    settings = get_settings()
    smtp_host = (settings.SMTP_SERVER or "").strip()
    smtp_user = (settings.SMTP_USERNAME or "").strip()
    smtp_password = (settings.SMTP_PASSWORD or "").strip()
    from_email = (settings.FROM_EMAIL or smtp_user or "").strip()
    if not smtp_host or not from_email:
        logger.info(
            "Invite email skipped (SMTP not configured) → %s for project %s",
            to_email,
            project_name,
        )
        return False

    frontend = settings.cors_origins[0] if settings.cors_origins else "http://localhost:3000"
    action_url = f"{frontend.rstrip('/')}/{'register' if is_new_user else 'login'}"
    action = "Create account & join" if is_new_user else "Open MindSurve"
    extra = (
        "<p>Create a MindSurve account with this email to access the shared project.</p>"
        if is_new_user
        else "<p>Sign in with this email to see the project in your sidebar.</p>"
    )
    html = f"""
    <html><body style="font-family:Arial,sans-serif;line-height:1.5;color:#222">
      <div style="max-width:560px;margin:0 auto;padding:24px">
        <h2 style="color:#2563eb">You're invited to collaborate</h2>
        <p>Hi,</p>
        <p><strong>{inviter_name}</strong> invited you to work on
        <strong>{project_name}</strong> in MindSurve.</p>
        <p>You'll be able to edit chats and studies in this project.</p>
        {extra}
        <p style="margin:28px 0;text-align:center">
          <a href="{action_url}" style="background:#2563eb;color:#fff;padding:12px 24px;
             border-radius:8px;text-decoration:none;display:inline-block">{action}</a>
        </p>
      </div>
    </body></html>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"You're invited to {project_name} on MindSurve"
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html"))

    try:
        port = int(settings.SMTP_PORT or 587)
        with smtplib.SMTP(smtp_host, port, timeout=20) as server:
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(from_email, [to_email], msg.as_string())
        return True
    except Exception:
        logger.exception("Failed to send collaborator invite email to %s", to_email)
        return False
