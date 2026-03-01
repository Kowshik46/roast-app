"""
Log resume uploads to Supabase (optional). Set SUPABASE_URL and SUPABASE_ANON_KEY in .env.
Table: upload_logs (see supabase_schema.sql).
"""
import os

def get_client_ip(request):
    """Client IP, respecting X-Forwarded-For / X-Real-IP when behind a proxy."""
    return (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP")
        or request.remote_addr
        or ""
    )

def log_upload_to_supabase(
    ip_address,
    filename,
    *,
    extracted_name=None,
    extracted_email=None,
    extracted_phone=None,
    score=None,
    repetitive_score=None,
    leadership_score=None,
    strategy_score=None,
    ai_exposure_score=None,
    user_agent=None,
):
    """Insert one row into Supabase upload_logs. No-op if Supabase not configured."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        return
    try:
        from supabase import create_client
        client = create_client(url, key)
        row = {
            "ip_address": ip_address or None,
            "filename": filename,
            "extracted_name": extracted_name or None,
            "extracted_email": extracted_email or None,
            "extracted_phone": extracted_phone or None,
            "score": score,
            "repetitive_score": repetitive_score,
            "leadership_score": leadership_score,
            "strategy_score": strategy_score,
            "ai_exposure_score": ai_exposure_score,
            "user_agent": (user_agent or None)[:500] if user_agent else None,
        }
        client.table("upload_logs").insert(row).execute()
    except Exception as e:
        print(f"Supabase log upload failed: {e}")
