"""
Vercel Python Serverless Function — Instagram public lookup via instagrapi.

Isolated service: NO Supabase, NO TikTok, NO trace calls.
Frontend calls: GET /api/insta?username=someone

Env vars (Vercel Dashboard only, never commit):
  INSTAGRAM_SESSIONID — sessionid from a dummy account (you provide it)
  ALLOWED_ORIGIN — frontend origin, default "*"
"""

import json
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    origin = os.environ.get("ALLOWED_ORIGIN", "*") or "*"
    handler.send_header("Access-Control-Allow-Origin", origin)
    handler.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    # Edge cache for 5 min so top-bar navigation feels instant.
    if status == 200:
        handler.send_header("Cache-Control", "s-maxage=300, stale-while-revalidate=600")
    handler.end_headers()
    handler.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        origin = os.environ.get("ALLOWED_ORIGIN", "*") or "*"
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            raw = (qs.get("username") or [""])[0].strip().lstrip("@")
            username = raw.split("/")[0].strip()

            if not username:
                _json_response(self, 400, {"error": "missing username, use ?username=someone"})
                return

            if not USERNAME_RE.match(username):
                _json_response(self, 400, {"error": "invalid username format"})
                return

            sessionid = os.environ.get("INSTAGRAM_SESSIONID", "").strip()
            if not sessionid:
                _json_response(self, 500, {"error": "server not configured (SESSIONID missing)"})
                return

            try:
                from instagrapi import Client
            except ImportError:
                _json_response(self, 500, {"error": "instagrapi not installed on server"})
                return

            cl = Client()
            # Do not log sessionid anywhere.
            try:
                cl.login_by_sessionid(sessionid)
            except Exception:
                _json_response(self, 401, {"error": "invalid or expired INSTAGRAM_SESSIONID"})
                return

            try:
                user = cl.user_info_by_username(username)
            except Exception as e:
                msg = str(e).lower()
                if "not found" in msg or "404" in msg or "user not found" in msg:
                    _json_response(self, 404, {"error": "user not found"})
                    return
                if "login" in msg or "401" in msg or "challenge" in msg:
                    _json_response(self, 401, {"error": "instagram asked for re-login, refresh SESSIONID"})
                    return
                if "429" in msg or "rate" in msg or "wait" in msg:
                    _json_response(self, 429, {"error": "instagram rate limit, try again in a minute"})
                    return
                _json_response(self, 502, {"error": "instagram fetch failed"})
                return

            payload = {
                "username": user.username,
                "full_name": user.full_name or "",
                "biography": user.biography or "",
                "follower_count": int(user.follower_count or 0),
                "following_count": int(user.following_count or 0),
                "media_count": int(user.media_count or 0),
                "profile_pic_url": str(user.profile_pic_url or ""),
                "profile_pic_url_hd": str(getattr(user, "profile_pic_url_hd", "") or user.profile_pic_url or ""),
                "is_private": bool(user.is_private),
                "is_verified": bool(user.is_verified),
                "is_business": bool(getattr(user, "is_business", False)),
                "external_url": str(getattr(user, "external_url", "") or ""),
                "category": str(getattr(user, "category", "") or ""),
            }
            _json_response(self, 200, payload)
        except BrokenPipeError:
            pass
        except Exception:
            try:
                _json_response(self, 500, {"error": "unexpected server error"})
            except Exception:
                pass
