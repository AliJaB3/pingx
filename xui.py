import json, secrets
from uuid import uuid4
from datetime import datetime, timedelta, timezone
import httpx
from config import THREEXUI_BASE_URL, THREEXUI_USERNAME, THREEXUI_PASSWORD

TZ = timezone.utc

class ThreeXUISession:
    def __init__(self, base_url, username, password):
        self.base=base_url.rstrip("/")
        self.username=username
        self.password=password
        self.client=None

    async def _ensure(self):
        if self.client: return
        self.client=httpx.AsyncClient(timeout=25.0, follow_redirects=True)
        payload={"username":self.username,"password":self.password}
        try:
            r=await self.client.post(f"{self.base}/panel/api/login", json=payload)
            if r.status_code==200: return
        except: pass
        try:
            r=await self.client.post(f"{self.base}/login", data=payload)
            if r.status_code in (200,302): return
        except: pass
        raise RuntimeError("Login to 3x-ui failed")

    async def request(self, method, path, json_data=None, params=None, data=None, headers=None):
        await self._ensure()
        r=await self.client.request(method, f"{self.base}{path}", json=json_data, params=params, data=data, headers=headers)
        r.raise_for_status()
        try: return r.json()
        except: return {"raw": r.text}

    async def list_inbounds(self):
        for path in ("/panel/api/inbounds/list", "/panel/inbounds", "/xui/inbound/list"):
            try:
                d=await self.request("GET", path)
                obj=d.get("obj") or d.get("data") or d.get("inbounds") or d
                if isinstance(obj, list): return obj
            except: continue
        return []

    async def get_inbound(self, inbound_id:int):
        for it in await self.list_inbounds():
            if str(it.get("id"))==str(inbound_id): return it
        return None

    async def _verify_client_added(self, inbound_id:int, email:str, client_id:str|None=None):
        ib=await self.get_inbound(inbound_id)
        if not ib: return None
        s=ib.get("settings")
        try: s=json.loads(s) if isinstance(s,str) else (s or {})
        except: s={}
        for c in (s.get("clients") or []):
            if (client_id and str(c.get("id")).replace("-","")==str(client_id).replace("-","")) or (email and c.get("email")==email):
                return c
        return None

    async def add_client(self, inbound_id:int, email:str, expire_days:int, data_gb:int, remark:str):
        total=(int(data_gb)*1024**3) if int(data_gb)>0 else 0
        expiry=int((datetime.now(TZ)+timedelta(days=int(expire_days))).timestamp()*1000)
        new_id=str(uuid4())
        sub_id=secrets.token_hex(6)
        payload={"id": new_id, "email": email, "enable": True, "expiryTime": expiry, "total": total, "limitIp": 0, "subId": sub_id, "remark": remark}

        attempts = [
            ("POST","/panel/api/inbounds/addClient", {"id": int(inbound_id), "client": json.dumps(payload, ensure_ascii=False)}, None, None),
            ("POST","/panel/inbounds/addClient", {"id": int(inbound_id), "client": json.dumps(payload, ensure_ascii=False)}, None, None),
            ("POST","/xui/inbound/addClient", None, {"id": str(inbound_id), "settings": json.dumps({"clients":[payload]}, ensure_ascii=False)}, {"Content-Type":"application/x-www-form-urlencoded"}),
            ("POST","/panel/inbound/addClient", None, {"id": str(inbound_id), "settings": json.dumps({"clients":[payload]}, ensure_ascii=False)}, {"Content-Type":"application/x-www-form-urlencoded"}),
        ]
        last_err=None
        for method, path, json_body, data_body, headers in attempts:
            try:
                await self.request(method, path, json_data=json_body, data=data_body, headers=headers)
                v=await self._verify_client_added(inbound_id, email=email, client_id=new_id)
                if v:
                    payload.update({k:v for k,v in v.items()})
                    return {"client": payload}
            except Exception as e:
                last_err=e
                continue
        try:
            v=await self._verify_client_added(inbound_id, email=email)
            if v:
                need=False
                if not v.get("subId"): v["subId"]=sub_id; need=True
                if int(v.get("expiryTime") or 0)==0: v["expiryTime"]=expiry; need=True
                if total>0 and int(v.get("total") or 0)==0: v["total"]=total; need=True
                if need:
                    await self.update_client(inbound_id, v["id"], v)
                v=await self._verify_client_added(inbound_id, email=email, client_id=v["id'])
                if v: return {"client": v}
        except Exception as e:
            last_err=e
        raise RuntimeError(f"addClient failed on all endpoints: {last_err}")

    async def update_client(self, inbound_id:int, client_id:str, client_payload:dict):
        paths=[f"/panel/api/inbounds/updateClient/{client_id}", f"/panel/inbounds/updateClient/{client_id}"]
        body={"id":int(inbound_id), "client": json.dumps(client_payload, ensure_ascii=False)}
        last=None
        for p in paths:
            try:
                last=await self.request("POST", p, json_data=body)
                return last
            except Exception as e:
                last=e
        raise RuntimeError(f"updateClient failed: {last}")

    async def rotate_subid(self, inbound_id:int, client_id:str)->str:
        inbound=await self.get_inbound(inbound_id)
        if not inbound: raise RuntimeError("Inbound not found")
        s=inbound.get("settings"); s=json.loads(s) if isinstance(s,str) else (s or {})
        hit=next((c for c in (s.get("clients") or []) if str(c.get("id")).replace("-","")==str(client_id).replace("-","")), None)
        if not hit: raise RuntimeError("Client not found")
        new_sub=secrets.token_hex(8); payload=dict(hit); payload["subId"]=new_sub
        await self.update_client(inbound_id, hit["id"], payload)
        return new_sub

    async def get_client_stats(self, inbound_id:int, client_id:str, email:str|None=None):
        try:
            inbound=await self.get_inbound(inbound_id)
            if inbound:
                s=inbound.get("settings"); s=json.loads(s) if isinstance(s,str) else (s or {})
                for c in (s.get("clients") or []):
                    if str(c.get("id"))==str(client_id) or (email and c.get("email")==email):
                        return {"up":int(c.get("up",0)),"down":int(c.get("down",0)),
                                "total":int(c.get("total",0)),"expiryTime":int(c.get("expiryTime",0) or 0)}
        except: pass
        for p in (f"/panel/api/inbounds/getClientTraffics/{email or client_id}",
                  "/panel/api/inbounds/listClientTraffics"):
            try:
                d=await self.request("GET", p, params={"inboundId": inbound_id})
                if isinstance(d,dict) and "obj" in d:
                    obj=d["obj"]
                    if isinstance(obj,dict):
                        return {"up":int(obj.get("up",0)),"down":int(obj.get("down",0)),
                                "total":int(obj.get("total",0)),"expiryTime":int(obj.get("expiryTime",0) or 0)}
                    if isinstance(obj,list):
                        for it in obj:
                            if str(it.get("id"))==str(client_id) or (email and it.get("email")==email):
                                return {"up":int(it.get("up",0)),"down":int(it.get("down",0)),
                                        "total":int(it.get("total",0)),"expiryTime":int(it.get("expiryTime",0) or 0)}
            except: continue
        return None

three_session=None
if THREEXUI_BASE_URL and THREEXUI_USERNAME and THREEXUI_PASSWORD:
    three_session=ThreeXUISession(THREEXUI_BASE_URL, THREEXUI_USERNAME, THREEXUI_PASSWORD)
