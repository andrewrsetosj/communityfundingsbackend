"""
Send collaborator invitation emails (Resend HTTP API).

Requires RESEND_API_KEY and a verified sender (see https://resend.com/docs).
Optional: RESEND_FROM_EMAIL, FRONTEND_URL (default http://localhost:3000).

If RESEND_API_KEY is unset, invites are skipped (logged) so local dev without email still works.
"""

from __future__ import annotations

import html
import os
from urllib.parse import quote

import httpx


async def _inviter_display_name(creator_id: str) -> str:
    """Best-effort display name from public.creators."""
    from db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT name, last_name
            FROM public.creators
            WHERE creator_id = $1
            LIMIT 1
            """,
            creator_id,
        )
    if not row:
        return "The campaign organizer"
    first = (row["name"] or "").strip() if row["name"] is not None else ""
    last = (row["last_name"] or "").strip() if row["last_name"] is not None else ""
    full = f"{first} {last}".strip()
    return full if full else "The campaign organizer"


async def send_collaborator_invite_emails(
    emails: list[str],
    campaign_title: str,
    campaign_id: int,
    creator_id: str,
) -> None:
    if not emails:
        return

    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    if not api_key:
        print(
            "WARN: RESEND_API_KEY not set; skipping collaborator invite emails "
            f"({len(emails)} recipient(s)).",
        )
        return

    base = (os.getenv("FRONTEND_URL") or "http://localhost:3000").rstrip("/")
    from_email = (
        os.getenv("RESEND_FROM_EMAIL") or "Community Fundings <onboarding@resend.dev>"
    )
    inviter_name = await _inviter_display_name(creator_id)
    safe_inviter = html.escape(inviter_name)
    safe_title = html.escape(campaign_title or "a campaign")

    async with httpx.AsyncClient(timeout=20.0) as client:
        for to_addr in emails:
            to_addr = to_addr.strip().lower()
            if not to_addr:
                continue

            sign_up_url = (
                f"{base}/sign-up?email={quote(to_addr)}"
                f"&campaign_id={int(campaign_id)}&reason=collaborator"
            )
            sign_in_url = f"{base}/sign-in"
            href_signup = html.escape(sign_up_url, quote=True)
            href_signin = html.escape(sign_in_url, quote=True)

            # Inline styles for broad email client support
            body_html = f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"/><meta name="viewport" content="width=device-width"/></head>
<body style="margin:0;padding:0;background-color:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color:#f4f4f5;padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e4e4e7;box-shadow:0 1px 3px rgba(0,0,0,.06);">
          <tr>
            <td style="padding:28px 32px 8px 32px;">
              <p style="margin:0;font-size:13px;font-weight:600;letter-spacing:.06em;color:#7cb342;text-transform:uppercase;">Community Fundings</p>
              <h1 style="margin:12px 0 0 0;font-size:22px;line-height:1.3;color:#18181b;">You&apos;re invited to collaborate</h1>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 32px 24px 32px;">
              <p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;color:#3f3f46;">
                <strong style="color:#18181b;">{safe_inviter}</strong> invited you to collaborate on
                <strong style="color:#18181b;">{safe_title}</strong> on Community Fundings.
              </p>
              <p style="margin:0 0 24px 0;font-size:15px;line-height:1.6;color:#3f3f46;">
                Create an account with the same email address this invitation was sent to so we can connect you to the campaign.
              </p>
              <table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 0 20px 0;">
                <tr>
                  <td style="border-radius:9999px;background-color:#8bc34a;">
                    <a href="{href_signup}" style="display:inline-block;padding:12px 28px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:9999px;">Create your account</a>
                  </td>
                </tr>
              </table>
              <p style="margin:0 0 8px 0;font-size:14px;line-height:1.6;color:#52525b;">
                Already have an account?
                <a href="{href_signin}" style="color:#7cb342;font-weight:600;text-decoration:none;">Log in here</a>
              </p>
              <p style="margin:20px 0 0 0;padding-top:20px;border-top:1px solid #e4e4e7;font-size:12px;line-height:1.5;color:#a1a1aa;">
                If the button doesn&apos;t work, copy and paste this link into your browser:<br/>
                <span style="word-break:break-all;color:#71717a;">{html.escape(sign_up_url)}</span>
              </p>
            </td>
          </tr>
        </table>
        <p style="margin:20px 0 0 0;font-size:11px;color:#a1a1aa;text-align:center;">This message was sent by Community Fundings regarding a campaign collaboration.</p>
      </td>
    </tr>
  </table>
</body>
</html>"""

            subject = (
                f"{inviter_name} invited you to collaborate on "
                f"{campaign_title or 'Community Fundings'}"
            )

            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": from_email,
                    "to": [to_addr],
                    "subject": subject,
                    "html": body_html,
                },
            )
            if resp.status_code >= 400:
                print(
                    f"WARN: Resend failed for {to_addr}: {resp.status_code} {resp.text}",
                )
