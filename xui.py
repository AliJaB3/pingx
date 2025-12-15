import json
import secrets
from uuid import uuid4
from datetime import datetime, timedelta, timezone
import logging
import httpx
from config import THREEXUI_BASE_URL, THREEXUI_USERNAME, THREEXUI_PASSWORD

TZ = timezone.utc
logger = logging.getLogger("pingx.xui")


class ThreeXUIError(RuntimeError):
    pass


class ThreeXUISession:
    def __init__(self, base_url: str, username: str, password: str):
        base = (base_url or "").strip().rstrip("/")
        low = base.lower()
        for suffix in ("/panel", "/xui", "/dashboard"):
            if low.endswith(suffix):
                base = base[: -len(suffix)]
                low = base.lower()
                break
        self.base = base or base_url.rstrip("/")
        self.username = username
        self.password = password
        self.client: httpx.AsyncClient | None = None
        self._logged_in = False

    def _sanitize(self, obj):
        if obj is None:
            return None
        if isinstance(obj, dict):
            res = {}
            for k, v in obj.items():
                if str(k).lower() in ("password", "pass", "authorization"):
                    res[k] = "***"
                else:
                    res[k] = self._sanitize(v)
            return res
        if isinstance(obj, (list, tuple)):
            return [self._sanitize(x) for x in obj]
        return obj

    def _create_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base, timeout=20.0, follow_redirects=True)

    async def _reset_client(self):
        if self.client:
            try:
                await self.client.aclose()
            except Exception:
                pass
        self.client = self._create_client()
        self._logged_in = False

    async def close(self):
        if self.client:
            await self.client.aclose()
            self.client = None
            self._logged_in = False

    async def _login(self):
        await self._reset_client()
        payload = {"username": self.username, "password": self.password}
        attempts = [
            ("POST", "/login", None, payload, None),
            ("POST", "/panel/login", None, payload, None),
            ("POST", "/panel/api/login", payload, None, None),
        ]
        last_err = None
        for method, path, json_body, data_body, headers in attempts:
            try:
                r = await self.client.request(method, path, json=json_body, data=data_body, headers=headers)
                if r.status_code in (200, 204, 302, 303):
                    self._logged_in = True
                    return
                last_err = f"{path} -> {r.status_code} {r.text[:200]}"
            except Exception as e:  # noqa: PERF203 - report last error
                last_err = e
        raise ThreeXUIError(f"Login to 3x-ui failed: {last_err}")

    async def _ensure(self):
        if not self.client:
            self.client = self._create_client()
        if not self._logged_in:
            await self._login()
            return
        try:
            ping = await self.client.get("/panel/api/inbounds/list")
            if ping.status_code == 401 or self._looks_like_html(ping):
                await self._reset_client()
                await self._login()
        except Exception:
            await self._reset_client()
            await self._login()

    @staticmethod
    def _looks_like_html(resp: httpx.Response) -> bool:
        """Detect responses that are likely HTML login pages instead of JSON API payloads."""
        ctype = (resp.headers.get("content-type") or "").lower()
        if "text/html" in ctype or "text/xml" in ctype:
            return True
        text = resp.text[:200].lstrip().lower() if resp.text else ""
        if text.startswith("<!doctype html") or text.startswith("<html"):
            return True
        return False

    async def request(self, method: str, path: str, json_data=None, params=None, data=None, headers=None):
        await self._ensure()
        safe_json = self._sanitize(json_data)
        safe_data = self._sanitize(data)
        safe_headers = self._sanitize(headers)
        logger.info("3xui request %s %s json=%s params=%s data=%s headers=%s", method, path, safe_json, params, safe_data, safe_headers)

        async def _do_request():
            return await self.client.request(method, path, json=json_data, params=params, data=data, headers=headers)

        r = await _do_request()
        if r.status_code == 401 or self._looks_like_html(r):
            # Session likely expired (login page HTML/redirect). Refresh login and retry once.
            logger.warning("3xui session looks expired (status=%s, html=%s), re-authenticating", r.status_code, self._looks_like_html(r))
            await self._reset_client()
            await self._login()
            r = await _do_request()
        logger.info("3xui response %s %s status=%s body=%s", method, path, r.status_code, (r.text[:500] if r.text else ""))
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"raw": r.text}

    async def list_inbounds(self):
        for path in ("/panel/api/inbounds/list", "/panel/inbounds", "/xui/inbound/list"):
            try:
                d = await self.request("GET", path)
                obj = d.get("obj") if isinstance(d, dict) else None
                if obj is None:
                    obj = d.get("data") if isinstance(d, dict) else None
                if obj is None and isinstance(d, dict):
                    obj = d.get("inbounds")
                if isinstance(obj, list):
                    return obj
            except Exception:
                continue
        return []

    async def get_inbound(self, inbound_id: int):
        for p in (f"/panel/api/inbounds/get/{inbound_id}",):
            try:
                d = await self.request("GET", p)
                if isinstance(d, dict) and d.get("obj"):
                    return d["obj"]
            except Exception:
                continue
        for it in await self.list_inbounds():
            if str(it.get("id")) == str(inbound_id):
                return it
        return None

    async def _verify_client_added(self, inbound_id: int, email: str, client_id: str | None = None):
        ib = await self.get_inbound(inbound_id)
        if not ib:
            return None
        s = ib.get("settings")
        try:
            s = json.loads(s) if isinstance(s, str) else (s or {})
        except Exception:
            s = {}
        for c in s.get("clients") or []:
            if (client_id and str(c.get("id")).replace("-", "") == str(client_id).replace("-", "")) or (
                email and c.get("email") == email
            ):
                return c
        return None

    async def _find_client_by_email(self, inbound_id: int, email: str):
        c = await self._verify_client_added(inbound_id, email=email, client_id=None)
        if c:
            return c
        stat = await self.get_client_stats(inbound_id, client_id=email, email=email)
        return stat

    @staticmethod
    def _format_stat(obj):
        if not obj:
            return None
        res = dict(obj)
        for key in ("up", "down", "total", "expiryTime"):
            try:
                res[key] = int(obj.get(key) or 0)
            except Exception:
                res[key] = 0
        return res

    async def add_client(
        self, inbound_id: int, email: str, expire_days: int, data_gb: int, remark: str, limit_ip: int | None = None
    ):
        total = (int(data_gb) * 1024**3) if int(data_gb) > 0 else 0
        expiry = int((datetime.now(TZ) + timedelta(days=int(expire_days))).timestamp() * 1000)
        new_id = str(uuid4())
        sub_id = secrets.token_hex(6)
        limit = max(0, int(limit_ip or 0))
        payload = {
            "id": new_id,
            "email": email,
            "enable": True,
            "expiryTime": expiry,
            "total": total,
            "limitIp": limit,
            "subId": sub_id,
            "remark": remark,
        }

        attempts = [
            (
                "POST",
                "/panel/api/inbounds/addClient",
                {"id": int(inbound_id), "client": json.dumps(payload, ensure_ascii=False)},
                None,
                None,
            ),
            (
                "POST",
                "/panel/api/inbounds/addClient",
                None,
                {"id": int(inbound_id), "client": json.dumps(payload, ensure_ascii=False)},
                {"Content-Type": "application/x-www-form-urlencoded"},
            ),
            (
                "POST",
                "/panel/api/inbounds/addClient",
                {"id": int(inbound_id), "settings": json.dumps({"clients": [payload]}, ensure_ascii=False)},
                None,
                None,
            ),
            (
                "POST",
                "/api/inbounds/addClient",
                {"id": int(inbound_id), "client": json.dumps(payload, ensure_ascii=False)},
                None,
                None,
            ),
            (
                "POST",
                "/xui/inbound/addClient",
                {"id": int(inbound_id), "client": json.dumps(payload, ensure_ascii=False)},
                None,
                None,
            ),
        ]
        last_err = None
        last_resp = None
        for method, path, json_body, data_body, headers in attempts:
            try:
                resp = await self.request(method, path, json_data=json_body, data=data_body, headers=headers)
                last_resp = resp
                v = await self._verify_client_added(inbound_id, email=email, client_id=new_id)
                if v:
                    payload.update({k: v for k, v in v.items()})
                    return {"client": payload, "resp": resp}
                if isinstance(resp, dict) and resp.get("success") is True:
                    return {"client": payload, "resp": resp, "warn": "not verified, success flag only"}
            except Exception as e:
                last_err = e
                continue
        try:
            v = await self._verify_client_added(inbound_id, email=email)
            if v:
                need = False
                if not v.get("subId"):
                    v["subId"] = sub_id
                    need = True
                if int(v.get("expiryTime") or 0) == 0:
                    v["expiryTime"] = expiry
                    need = True
                if total > 0 and int(v.get("total") or 0) == 0:
                    v["total"] = total
                    need = True
                if int(v.get("limitIp") or 0) != limit:
                    v["limitIp"] = limit
                    need = True
                if need:
                    await self.update_client(inbound_id, v["id"], v)
                v2 = await self._verify_client_added(inbound_id, email=email, client_id=v["id"])
                if v2:
                    return {"client": v2}
        except Exception as e:
            last_err = e

        try:
            ib = await self.get_inbound(inbound_id)
            if ib:
                s = ib.get("settings")
                s = json.loads(s) if isinstance(s, str) else (s or {})
                clients = list(s.get("clients") or [])
                clients.append(payload)
                s["clients"] = clients
                full_payload = self._build_full_inbound_update(ib, inbound_id, s)
                resp = await self.request("POST", f"/panel/api/inbounds/update/{int(inbound_id)}", json_data=full_payload)
                last_resp = resp
                v = await self._verify_client_added(inbound_id, email=email, client_id=new_id)
                if v:
                    return {"client": v, "resp": resp}
                if isinstance(resp, dict) and resp.get("success") is True:
                    return {"client": payload, "resp": resp, "warn": "not verified after update"}
        except Exception as e:
            last_err = e

        try:
            if isinstance(last_resp, dict) and "UNIQUE constraint failed: client_traffics.email" in str(last_resp.get("msg", "")):
                existing = await self._find_client_by_email(inbound_id, email=email)
                if existing:
                    need = False
                    if not existing.get("subId"):
                        existing["subId"] = sub_id
                        need = True
                    if int(existing.get("expiryTime") or 0) == 0:
                        existing["expiryTime"] = expiry
                        need = True
                    if total > 0 and int(existing.get("total") or 0) == 0:
                        existing["total"] = total
                        need = True
                    if not existing.get("email"):
                        existing["email"] = email
                    if int(existing.get("limitIp") or 0) != limit:
                        existing["limitIp"] = limit
                        need = True
                    if need:
                        await self.update_client(inbound_id, existing["id"], existing)
                        existing = await self._verify_client_added(inbound_id, email=email, client_id=existing["id"])
                    if existing:
                        return {"client": existing, "resp": last_resp, "warn": "email existed, reused client"}
        except Exception as e:
            last_err = e

        raise ThreeXUIError(f"addClient failed on all endpoints: last_err={last_err}, last_resp={last_resp}")

    @staticmethod
    def _serialize_settings_value(val):
        if isinstance(val, (dict, list)):
            return json.dumps(val, ensure_ascii=False)
        return val

    def _build_full_inbound_update(self, inbound: dict, inbound_id: int, new_settings: dict) -> dict:
        """
        Build a full inbound payload for /update calls.
        Sending only `id` + `settings` causes the panel to reset other fields after a while,
        so we always send back the critical fields we read from the panel.
        """
        fields = (
            "id",
            "up",
            "down",
            "total",
            "remark",
            "enable",
            "expiryTime",
            "listen",
            "port",
            "protocol",
            "settings",
            "streamSettings",
            "sniffing",
            "allocate",
            "tag",
        )
        payload = {}
        inbound = inbound or {}
        inbound["id"] = inbound.get("id") or inbound_id
        for f in fields:
            if f == "settings":
                payload[f] = json.dumps(new_settings, ensure_ascii=False)
                continue
            if f in inbound:
                payload[f] = self._serialize_settings_value(inbound.get(f))
        payload["id"] = int(payload.get("id", inbound_id))
        return payload

    async def update_client(self, inbound_id: int, client_id: str, client_payload: dict):
        payload_str = json.dumps(client_payload, ensure_ascii=False)
        attempts = [
            # JSON body with client as string
            ("POST", f"/panel/api/inbounds/updateClient/{client_id}", {"id": int(inbound_id), "client": payload_str}, None, None),
            # Form body with client string
            ("POST", f"/panel/api/inbounds/updateClient/{client_id}", None, {"id": int(inbound_id), "client": payload_str}, {"Content-Type": "application/x-www-form-urlencoded"}),
            # JSON body with client as raw dict
            ("POST", f"/panel/api/inbounds/updateClient/{client_id}", {"id": int(inbound_id), "client": client_payload}, None, None),
            # Alternate path with json body
            ("POST", f"/panel/api/inbounds/{int(inbound_id)}/updateClient/{client_id}", {"id": int(inbound_id), "client": payload_str}, None, None),
            # API prefixed path
            ("POST", f"/api/inbounds/updateClient/{client_id}", {"id": int(inbound_id), "client": payload_str}, None, None),
            # xui prefixed path
            ("POST", f"/xui/inbound/updateClient/{client_id}", {"id": int(inbound_id), "client": payload_str}, None, None),
            # settings-based update (single client) via panel api
            (
                "POST",
                f"/panel/api/inbounds/updateClient/{client_id}",
                {"id": int(inbound_id), "settings": json.dumps({"clients": [client_payload]}, ensure_ascii=False)},
                None,
                None,
            ),
        ]
        last = None
        for method, path, json_body, data_body, headers in attempts:
            try:
                resp = await self.request(method, path, json_data=json_body, data=data_body, headers=headers)
                last = resp
                if isinstance(resp, dict) and resp.get("success") is False:
                    continue
                return resp
            except Exception as e:
                last = e
        # Fallback: read full inbound settings, replace target client, push back
        try:
            inbound = await self.get_inbound(inbound_id)
            if inbound:
                s = inbound.get("settings")
                s = json.loads(s) if isinstance(s, str) else (s or {})
                clients = list(s.get("clients") or [])
                replaced = False
                cid_norm = str(client_id or "").replace("-", "")
                target_email = client_payload.get("email")
                target_sub = str(client_payload.get("subId") or "").replace("-", "")
                for idx, c in enumerate(clients):
                    cur_id = str(c.get("id") or "").replace("-", "")
                    cur_sub = str(c.get("subId") or "").replace("-", "")
                    if cid_norm and cur_id == cid_norm:
                        clients[idx] = client_payload
                        replaced = True
                        break
                    if target_email and c.get("email") == target_email:
                        clients[idx] = client_payload
                        replaced = True
                        break
                    if target_sub and cur_sub == target_sub:
                        clients[idx] = client_payload
                        replaced = True
                        break
                if replaced:
                    s["clients"] = clients
                    payload = self._build_full_inbound_update(inbound, inbound_id, s)
                    return await self.request("POST", f"/panel/api/inbounds/update/{int(inbound_id)}", json_data=payload)
        except Exception as e:
            last = e
        raise ThreeXUIError(f"updateClient failed: {last}")

    async def rotate_subid(self, inbound_id: int, client_id: str, email: str | None = None) -> str:
        inbound = await self.get_inbound(inbound_id)
        if not inbound:
            raise ThreeXUIError("Inbound not found")
        s = inbound.get("settings")
        s = json.loads(s) if isinstance(s, str) else (s or {})
        hit = next((c for c in (s.get("clients") or []) if str(c.get("id")).replace("-", "") == str(client_id).replace("-", "")), None)
        if not hit and email:
            hit = await self._find_client_by_email(inbound_id, email)
        if not hit:
            raise ThreeXUIError("Client not found")
        real_id = hit.get("id") or client_id
        new_sub = secrets.token_hex(8)
        payload = dict(hit)
        payload["subId"] = new_sub
        await self.update_client(inbound_id, real_id, payload)
        return new_sub

    async def get_client_stats(self, inbound_id: int, client_id: str, email: str | None = None):
        try:
            inbound = await self.get_inbound(inbound_id)
            if inbound:
                s = inbound.get("settings")
                s = json.loads(s) if isinstance(s, str) else (s or {})
                for c in s.get("clients") or []:
                    if str(c.get("id")) == str(client_id) or (email and c.get("email") == email):
                        return self._format_stat(c)
        except Exception:
            pass
        paths = (
            f"/panel/api/inbounds/getClientTraffics/{email or client_id}",
            f"/panel/api/inbounds/getClientTrafficsById/{client_id}",
            f"/api/inbounds/getClientTraffics/{email or client_id}",
            f"/api/inbounds/getClientTrafficsById/{client_id}",
            f"/xui/inbound/getClientTraffics/{email or client_id}",
            f"/xui/inbound/getClientTrafficsById/{client_id}",
        )
        for p in paths:
            try:
                d = await self.request("GET", p, params={"inboundId": inbound_id})
                if isinstance(d, dict) and "obj" in d:
                    obj = d["obj"]
                    if isinstance(obj, dict):
                        return self._format_stat(obj)
                    if isinstance(obj, list):
                        for it in obj:
                            if str(it.get("id")) == str(client_id) or (email and it.get("email") == email):
                                return self._format_stat(it)
            except Exception:
                continue
        return None


three_session = None
if THREEXUI_BASE_URL and THREEXUI_USERNAME and THREEXUI_PASSWORD:
    three_session = ThreeXUISession(THREEXUI_BASE_URL, THREEXUI_USERNAME, THREEXUI_PASSWORD)
