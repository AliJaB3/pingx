
import json, secrets
from uuid import uuid4
from datetime import datetime, timedelta, timezone
import httpx
from config import THREEXUI_BASE_URL, THREEXUI_USERNAME, THREEXUI_PASSWORD

TZ = timezone.utc


class ThreeXUIError(RuntimeError):
    pass


class ThreeXUISession:
    def __init__(self, base_url: str, username: str, password: str):
        self.base = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.client: httpx.AsyncClient | None = None
        self._logged_in = False

    def _create_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base, timeout=20.0, follow_redirects=True)

    async def close(self):
        if self.client:
            await self.client.aclose()
            self.client = None
            self._logged_in = False

    async def _login(self):
        if not self.client:
            self.client = self._create_client()
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
            if ping.status_code == 401:
                self._logged_in = False
                await self._login()
        except Exception:
            self._logged_in = False
            await self._login()

    async def request(self, method: str, path: str, json_data=None, params=None, data=None, headers=None):
        await self._ensure()
        r = await self.client.request(method, path, json=json_data, params=params, data=data, headers=headers)
        if r.status_code == 401:
            # Session expired; re-login once
            self._logged_in = False
            await self._login()
            r = await self.client.request(method, path, json=json_data, params=params, data=data, headers=headers)
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
        # direct get endpoint if available
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

    async def add_client(self, inbound_id: int, email: str, expire_days: int, data_gb: int, remark: str):
        total = (int(data_gb) * 1024**3) if int(data_gb) > 0 else 0
        expiry = int((datetime.now(TZ) + timedelta(days=int(expire_days))).timestamp() * 1000)
        new_id = str(uuid4())
        sub_id = secrets.token_hex(6)
        payload = {
            "id": new_id,
            "email": email,
            "enable": True,
            "expiryTime": expiry,
            "total": total,
            "limitIp": 0,
            "subId": sub_id,
            "remark": remark,
        }

        attempts = [
            (
                "POST",
                "/panel/api/inbounds/addClient",
                {"id": int(inbound_id), "settings": json.dumps({"clients": [payload]}, ensure_ascii=False)},
                None,
                None,
            ),
            (
                "POST",
                "/panel/api/inbounds/addClient",
                {"id": int(inbound_id), "client": json.dumps(payload, ensure_ascii=False)},
                None,
                None,
            ),
        ]
        last_err = None
        for method, path, json_body, data_body, headers in attempts:
            try:
                await self.request(method, path, json_data=json_body, data=data_body, headers=headers)
                v = await self._verify_client_added(inbound_id, email=email, client_id=new_id)
                if v:
                    payload.update({k: v for k, v in v.items()})
                    return {"client": payload}
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
                if need:
                    await self.update_client(inbound_id, v["id"], v)
                v = await self._verify_client_added(inbound_id, email=email, client_id=v["id"])
                if v:
                    return {"client": v}
        except Exception as e:
            last_err = e

        # Fallback: update inbound with appended client (panel/api/inbounds/update/:id)
        try:
            ib = await self.get_inbound(inbound_id)
            if ib:
                s = ib.get("settings")
                s = json.loads(s) if isinstance(s, str) else (s or {})
                clients = s.get("clients") or []
                clients.append(payload)
                s["clients"] = clients
                await self.request(
                    "POST",
                    f"/panel/api/inbounds/update/{int(inbound_id)}",
                    json_data={"id": int(inbound_id), "settings": json.dumps(s, ensure_ascii=False)},
                )
                v = await self._verify_client_added(inbound_id, email=email, client_id=new_id)
                if v:
                    return {"client": v}
        except Exception as e:
            last_err = e

        raise ThreeXUIError(f"addClient failed on all endpoints: {last_err}")

    async def update_client(self, inbound_id: int, client_id: str, client_payload: dict):
        paths = [
            f"/panel/api/inbounds/updateClient/{client_id}",
            f"/panel/api/inbounds/{int(inbound_id)}/updateClient/{client_id}",
        ]
        body = {"id": int(inbound_id), "client": json.dumps(client_payload, ensure_ascii=False)}
        last = None
        for p in paths:
            try:
                last = await self.request("POST", p, json_data=body)
                return last
            except Exception as e:
                last = e
        raise ThreeXUIError(f"updateClient failed: {last}")

    async def rotate_subid(self, inbound_id: int, client_id: str) -> str:
        inbound = await self.get_inbound(inbound_id)
        if not inbound:
            raise ThreeXUIError("Inbound not found")
        s = inbound.get("settings")
        s = json.loads(s) if isinstance(s, str) else (s or {})
        hit = next((c for c in (s.get("clients") or []) if str(c.get("id")).replace("-", "") == str(client_id).replace("-", "")), None)
        if not hit:
            raise ThreeXUIError("Client not found")
        new_sub = secrets.token_hex(8)
        payload = dict(hit)
        payload["subId"] = new_sub
        await self.update_client(inbound_id, hit["id"], payload)
        return new_sub

    async def get_client_stats(self, inbound_id: int, client_id: str, email: str | None = None):
        try:
            inbound = await self.get_inbound(inbound_id)
            if inbound:
                s = inbound.get("settings")
                s = json.loads(s) if isinstance(s, str) else (s or {})
                for c in s.get("clients") or []:
                    if str(c.get("id")) == str(client_id) or (email and c.get("email") == email):
                        return {
                            "up": int(c.get("up", 0)),
                            "down": int(c.get("down", 0)),
                            "total": int(c.get("total", 0)),
                            "expiryTime": int(c.get("expiryTime", 0) or 0),
                        }
        except Exception:
            pass
        for p in (
            f"/panel/api/inbounds/getClientTraffics/{email or client_id}",
            f"/panel/api/inbounds/getClientTrafficsById/{client_id}",
        ):
            try:
                d = await self.request("GET", p, params={"inboundId": inbound_id})
                if isinstance(d, dict) and "obj" in d:
                    obj = d["obj"]
                    if isinstance(obj, dict):
                        return {
                            "up": int(obj.get("up", 0)),
                            "down": int(obj.get("down", 0)),
                            "total": int(obj.get("total", 0)),
                            "expiryTime": int(obj.get("expiryTime", 0) or 0),
                        }
                    if isinstance(obj, list):
                        for it in obj:
                            if str(it.get("id")) == str(client_id) or (email and it.get("email") == email):
                                return {
                                    "up": int(it.get("up", 0)),
                                    "down": int(it.get("down", 0)),
                                    "total": int(it.get("total", 0)),
                                    "expiryTime": int(it.get("expiryTime", 0) or 0),
                                }
            except Exception:
                continue
        return None


three_session = None
if THREEXUI_BASE_URL and THREEXUI_USERNAME and THREEXUI_PASSWORD:
    three_session = ThreeXUISession(THREEXUI_BASE_URL, THREEXUI_USERNAME, THREEXUI_PASSWORD)
