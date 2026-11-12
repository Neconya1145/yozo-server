import os
import sys
import time
import json
import random
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from flask import Flask, request, render_template_string, redirect, session, Response
import requests
import hashlib
import zlib
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# ============================================================
# 你的核心配置（必须修改授权码）
# ============================================================
SUPABASE_URL = "https://onlzfynbsozcubcmyctf.supabase.co"
SUPABASE_KEY = "sb_publishable_q1pJRI6-SuSgOLE4vjI0gQ_HPy7-rKg"

QQ_EMAIL = "2133177217@qq.com"
QQ_AUTH_CODE = "glyjvegrwfddfggb" # 【换成你新生成的！】

def get_sb_headers():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}

verify_codes = {}
app = Flask(__name__)
app.secret_key = "Yozo_Cloud_2024"

# ============================================================
# 完整发票业务逻辑 (原版代码，一字未改)
# ============================================================
CLIENT_ID = "A63E01C2905"
KEYCHIP_ID = "A63E-01C29050000"
REGION_ID = 8
PLACE_ID = 0
TITLE_SERVER_URL = "https://maimai-gm.wahlap.com:42081/"
DEFAULT_TITLE_SERVER_URL = "https://maimai-gm.wahlap.com:42081/"
API_VERSION = "1.55"
SERVLET_PATH = "Maimai2Servlet/"
LOGIN_DATETIME_UTC_OFFSET = 28800
AES_KEY = "FKM2JX:VjZNK6hc:A0<JU:i5oR7LA]9W"
AES_IV = "F>;24DjU9W6ZsRH["
ENDPOINT_SALT = "MaimaiChn"
OBFUSCATE_PARAM = "8bF76dE9"
POWER_ON_URL = "https://at.sys-allnet.cn/net/initialize"
POWER_ON_KEY = bytes([47, 63, 106, 111, 43, 34, 76, 38, 92, 67, 114, 57, 40, 61, 107, 71])
POWER_ON_TITLE_VER = API_VERSION
CHIME_CHIP_ID = "A63E-01C29050000"
CHIME_URL = "http://ai.sys-allnet.cn/wc_aime/api/get_data"
CHIME_HOST = "ai.sys-allnet.cn"
CHIME_SALT = "XcW5FW4cPArBXEk4vzKz3CIrMuA5EVVW"
QR_EXPIRE_MINUTES = 10
TICKET_COOLDOWN_MINUTES = 15
SIMULATED_PLAY_SECONDS = 30

TICKET_CHOICES = {2: (2, 1, "二倍"), 3: (3, 2, "三倍"), 5: (5, 4, "五倍")}
class TicketError(Exception): pass

@dataclass
class QrInfo:
    raw: str
    token: str
    generated_at: datetime
    expire_at: datetime

@dataclass
class MachineInfo:
    client_id: str
    keychip_id: str
    region_id: int
    place_id: int
    region_name: str
    place_name: str
    title_server_url: str

def as_int(value: object, default: int = 0) -> int:
    if value is None: return default
    try: return int(value)
    except: return default

def normalize_base_url(raw: str) -> str:
    value = raw.strip()
    lower = value.lower()
    path = "maimai2servlet"
    if lower.endswith("/" + path + "/"): value = value[: -(len(path) + 2)]
    elif lower.endswith("/" + path): value = value[: -(len(path) + 1)]
    if value and not value.endswith("/"): value += "/"
    return value

def parse_qr(raw: str) -> QrInfo:
    text = raw.strip()
    if len(text) < 84 or not text.upper().startswith("SGWCMAID"): raise TicketError("二维码格式错误")
    ts = text[8:20]
    if not re.fullmatch(r"\d{12}", ts): raise TicketError("二维码时间戳错误")
    generated_at = datetime(2000 + int(ts[0:2]), int(ts[2:4]), int(ts[4:6]), int(ts[6:8]), int(ts[8:10]), int(ts[10:12]), tzinfo=timezone.utc) - timedelta(hours=8)
    expire_at = generated_at + timedelta(minutes=QR_EXPIRE_MINUTES)
    if datetime.now(timezone.utc) >= expire_at: raise TicketError("二维码已过期")
    return QrInfo(raw=text, token=text[20:], generated_at=generated_at, expire_at=expire_at)

def _manual_strip_pkcs7(data: bytes) -> bytes:
    if not data: return data
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16 or pad_len > len(data): return data
    if any(byte != pad_len for byte in data[-pad_len:]): return data
    return data[:-pad_len]

def power_on(client_id: str) -> dict[str, str]:
    query = f"title_id=SDGB&title_ver={POWER_ON_TITLE_VER}&client_id={client_id}"
    plain = b"\x00" * 32 + query.encode("utf-8")
    body = AES.new(POWER_ON_KEY, AES.MODE_CBC, b"\x00" * 16).encrypt(pad(plain, 16))
    response = requests.post(POWER_ON_URL, headers={"User-Agent": "SDGB;Windows/Lite", "Pragma": "DFI"}, data=body, timeout=15, verify=False)
    if response.status_code != 200: raise TicketError("PowerOn 失败")
    raw = response.content
    decrypted = AES.new(POWER_ON_KEY, AES.MODE_CBC, raw[:16]).decrypt(raw[16:])
    raw_text = urllib.parse.unquote(_manual_strip_pkcs7(decrypted).decode("utf-8", "replace"))
    fields = {}
    for pair in raw_text.split("&"):
        eq = pair.find("=")
        if eq > 0: fields[pair[:eq]] = pair[eq + 1 :]
    return fields

def resolve_machine_info(client_id: str) -> MachineInfo:
    fields = power_on(client_id)
    if fields.get("result") != "1": raise TicketError("PowerOn 失败")
    try: place_id = int(fields.get("place_id", "0"), 16)
    except: place_id = 0
    server_url = normalize_base_url(fields.get("uri1", "")) or DEFAULT_TITLE_SERVER_URL
    derived_keychip = f"{client_id[:4]}-{client_id[4:]}{place_id}" if len(client_id) > 4 else client_id
    keychip_id = KEYCHIP_ID.strip() or derived_keychip
    return MachineInfo(client_id, keychip_id, 0, place_id, "", "", server_url)

def chime_auth(qr_raw: str, machine: MachineInfo) -> tuple[int, str]:
    text = qr_raw.strip()
    qr_code_hash = text[20:]
    timestamp = time.strftime("%y%m%d%H%M%S", time.localtime())
    auth_key = hashlib.sha256((CHIME_CHIP_ID + timestamp + CHIME_SALT).encode("utf-8")).hexdigest().upper()
    payload = {"chipID": CHIME_CHIP_ID, "openGameID": "MAID", "key": auth_key, "qrCode": qr_code_hash, "timestamp": timestamp, "titlekey": "SDGB"}
    body = json.dumps(payload, separators=(",", ":"))
    headers = {"Connection": "Keep-Alive", "Host": CHIME_HOST, "User-Agent": "WC_AIME_LIB", "Content-Type": "application/json"}
    response = requests.post(CHIME_URL, data=body, headers=headers, timeout=15)
    obj = response.json()
    if obj.get("errorID") != 0: raise TicketError(f"认证失败: {obj.get('errorID')}")
    return as_int(obj.get("userID")), obj.get("token", "")

class TitleClient:
    def __init__(self, machine: MachineInfo) -> None:
        self.machine = machine
        self.base_url = normalize_base_url(machine.title_server_url)
        self.client_id = machine.client_id
        self.session = requests.Session()
        self.session.verify = False
        self.session.cookies.clear()
        self.cookies = {}
        self.number = 0
    def _capture_cookies(self, response):
        raw = response.headers.get("set-cookie")
        if not raw: return
        for chunk in re.split(r",\s*(?=[A-Za-z0-9_\-]+=)", raw):
            pair = chunk.split(";", 1)[0].strip(); eq = pair.find("=")
            if eq > 0:
                name = pair[:eq].strip(); value = pair[eq + 1 :].strip()
                if name and value: self.cookies[name] = value
        self.session.cookies.clear()
    def _endpoint(self, api_name: str) -> tuple[str, str]:
        endpoint_hash = hashlib.md5((api_name + ENDPOINT_SALT + OBFUSCATE_PARAM).encode("utf-8")).hexdigest()
        return endpoint_hash, f"{self.base_url}{SERVLET_PATH}{endpoint_hash}"
    def call(self, api_name: str, packet: dict, user_id: int, *, allow_empty: bool = False, timeout: int = 10) -> dict:
        endpoint_hash, url = self._endpoint(api_name)
        headers = {"number": str(self.number), "Content-Type": "application/json", "User-Agent": f"{endpoint_hash}#{user_id if user_id else self.client_id}", "charset": "UTF-8", "Mai-Encoding": API_VERSION, "Content-Encoding": "deflate", "Accept-Encoding": ""}
        if user_id != 0 and self.cookies: headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        encrypted = AES.new(AES_KEY.encode("utf-8"), AES.MODE_CBC, AES_IV.encode("utf-8")).encrypt(pad(zlib.compress(json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8")), 16))
        response = self.session.post(url, headers=headers, data=encrypted, timeout=timeout, verify=False)
        self._capture_cookies(response)
        if response.status_code != 200: raise TicketError(f"请求失败: {response.status_code}")
        decrypted = AES.new(AES_KEY.encode("utf-8"), AES.MODE_CBC, AES_IV.encode("utf-8")).decrypt(response.content)
        return json.loads(zlib.decompress(unpad(decrypted, 16)).decode("utf-8"))
    def login(self, user_id: int, token: str, timestamp: int) -> dict:
        data = self.call("UserLoginApi", {"userId": user_id, "accessCode": "", "regionId": self.machine.region_id, "placeId": self.machine.place_id, "clientId": self.machine.client_id, "dateTime": timestamp, "loginDateTime": timestamp + LOGIN_DATETIME_UTC_OFFSET, "isContinue": False, "genericFlag": 0, "token": token}, user_id, timeout=60)
        if as_int(data.get("returnCode"), -1) != 1: raise TicketError("登录失败")
        return data
    def force_logout(self, user_id: int, timestamp: int) -> bool:
        try:
            self.call("UserLogoutApi", {"userId": user_id, "accessCode": "", "regionId": self.machine.region_id, "placeId": self.machine.place_id, "clientId": self.machine.client_id, "loginDateTime": timestamp + LOGIN_DATETIME_UTC_OFFSET, "type": 4}, user_id, allow_empty=True)
            return True
        except: return False

def find_charge(charge_list: list[dict], charge_id: int) -> dict | None:
    for charge in charge_list:
        if as_int(charge.get("chargeId"), 0) == charge_id: return charge
    return None
def held_charges(charge_list: list[dict]) -> list[dict]: return [charge for charge in charge_list if as_int(charge.get("stock"), 0) > 0]
def build_user_chargelog_body(*, machine: MachineInfo, user_id: int, charge_id: int, price: int, play_count: int, player_rating: int, timestamp: int, charge_list: list[dict]) -> dict:
    now_str = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.0"
    valid_str = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    existing = find_charge(charge_list, charge_id)
    old_stock = as_int(existing.get("stock"), 0) if existing else 0
    return {
        "userId": user_id,
        "userChargelog": {"chargeId": charge_id, "price": price, "purchaseDate": now_str, "playCount": play_count, "playerRating": player_rating, "placeId": machine.place_id, "regionId": machine.region_id, "clientId": machine.client_id},
        "userCharge": {"chargeId": charge_id, "stock": old_stock + 1, "purchaseDate": now_str, "validDate": valid_str},
        "loginDateTime": timestamp + LOGIN_DATETIME_UTC_OFFSET,
    }

# ============================================================
# 网页 UI（雪花、粉字、按钮变灰冷却）
# ============================================================
CSS_STYLE = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;700&display=swap');
    body { font-family: 'Noto Sans SC', sans-serif; background: linear-gradient(135deg, #1a1a1a, #0f0f0f); min-height: 100vh; display: flex; justify-content: center; align-items: center; margin: 0; color: #fff; overflow: hidden; }
    .snow { position: fixed; top: -10px; left: 0; right: 0; bottom: 0; pointer-events: none; z-index: 0; background-image: radial-gradient(2px 2px at 20px 30px, #fff, rgba(0,0,0,0)), radial-gradient(2px 2px at 40px 70px, #eee, rgba(0,0,0,0)); background-repeat: repeat; background-size: 200px 200px; animation: snowAnim 10s linear infinite; opacity: 0.3; }
    @keyframes snowAnim { 0% { transform: translateY(0) } 100% { transform: translateY(200px) } }
    .card { background: rgba(40, 40, 40, 0.65); backdrop-filter: blur(15px); border: 1px solid rgba(255, 255, 255, 0.1); padding: 35px 25px; border-radius: 16px; width: 100%; max-width: 320px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); z-index: 1; text-align: center; }
    h2 { color: #ff8c94; margin-top: 0; font-size: 24px; margin-bottom: 20px; text-shadow: 0 0 10px rgba(255, 140, 148, 0.5);}
    input, select { width: 100%; padding: 12px; margin-bottom: 15px; background: rgba(0, 0, 0, 0.6); border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 8px; color: #ff8c94; outline: none; box-sizing: border-box; font-size: 14px;}
    input::placeholder { color: #666; }
    .code-row { display: flex; gap: 10px; }
    .code-row input { flex: 1; margin-bottom: 15px;}
    .code-row button { width: 120px; margin-bottom: 15px; padding: 0; font-size: 13px; }
    button { width: 100%; padding: 14px; background: linear-gradient(135deg, #ff8c94, #ff4757); color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: bold; cursor: pointer; }
    button:disabled { background: #555; color: #aaa; cursor: not-allowed; }
    a { color: #aaa; font-size: 13px; text-decoration: none; display: block; margin-top: 15px; }
    .log-box { background: #000; color: #50fa7b; padding: 15px; border-radius: 8px; height: 200px; overflow-y: auto; font-family: monospace; text-align: left; margin-top: 20px; white-space: pre-wrap; }
</style>
"""

HTML_REGISTER = f"""
<!DOCTYPE html><html><head><meta charset="utf-8"><title>注册</title>{CSS_STYLE}</head><body>
    <div class="snow"></div>
    <div class="card">
        <h2>云端注册</h2>
        <form action="/register" method="POST">
            <input type="text" name="username" placeholder="账号" required>
            <input type="email" name="email" placeholder="邮箱" required>
            <div class="code-row">
                <input type="text" name="code" placeholder="5位验证码" required>
                <button type="button" id="btn" onclick="sendCode()">获取验证码</button>
            </div>
            <input type="password" name="password" placeholder="密码" required>
            <button type="submit">立即注册</button>
        </form>
        <a href="/">已有账号？返回登录</a>
    </div>
    <script>
        function sendCode() {{
            var e = document.getElementsByName("email")[0].value;
            if(!e) return alert("请填邮箱");
            var btn = document.getElementById('btn');
            fetch('/send_code', {{ method: 'POST', headers: {{'Content-Type': 'application/x-www-form-urlencoded'}}, body: 'email=' + encodeURIComponent(e) }})
            .then(r => r.text()).then(t => {{
                alert(t);
                if (t.includes("成功") || t.includes("等待")) {{
                    var c = 90; btn.disabled = true; btn.innerText = c + "秒后重试";
                    var timer = setInterval(function() {{
                        c--; if (c <= 0) {{ clearInterval(timer); btn.disabled = false; btn.innerText = "获取验证码"; }} 
                        else {{ btn.innerText = c + "秒后重试"; }}
                    }}, 1000);
                }}
            }});
        }}
    </script>
</body></html>
"""

HTML_LOGIN = f"""
<!DOCTYPE html><html><head><meta charset="utf-8"><title>登录</title>{CSS_STYLE}</head><body>
    <div class="snow"></div>
    <div class="card">
        <h2>云端验证</h2>
        <form action="/login" method="POST">
            <input type="text" name="username" placeholder="账号" required>
            <input type="password" name="password" placeholder="密码" required>
            <button type="submit">进入系统</button>
        </form>
        <a href="/register">没有账号？点击注册</a>
    </div>
</body></html>
"""

HTML_MAIN = f"""
<!DOCTYPE html><html><head><meta charset="utf-8"><title>发票</title>{CSS_STYLE}</head><body>
    <div class="snow"></div>
    <div class="card">
        <h2>Yozo 控制台</h2>
        <form id="mainForm" onsubmit="event.preventDefault(); startTask();">
            <input type="text" id="qrInput" placeholder="请粘贴二维码数据..." required>
            <select id="ticketSelect"><option value="2">二倍</option><option value="3">三倍</option><option value="5">五倍</option></select>
            <button type="submit" id="subBtn">开始执行</button>
        </form>
        <div class="log-box" id="log">等待操作...</div>
        <a href="/logout">退出登录</a>
    </div>
    <script>
        function startTask() {{
            var q = document.getElementById('qrInput').value, t = document.getElementById('ticketSelect').value;
            var log = document.getElementById('log'), btn = document.getElementById('subBtn');
            btn.disabled = true; log.innerText = "连接中...\\n";
            fetch('/run', {{ method: 'POST', headers: {{'Content-Type': 'application/x-www-form-urlencoded'}}, body: 'qr=' + encodeURIComponent(q) + '&ticket=' + t }})
            .then(res => {{
                const reader = res.body.getReader(); const decoder = new TextDecoder();
                function read() {{ reader.read().then(({{done, value}}) => {{ if (done) {{ btn.disabled = false; return; }} log.innerText += decoder.decode(value); log.scrollTop = log.scrollHeight; read(); }}); }}
                read();
            }});
        }}
    </script>
</body></html>
"""

# ============================================================
# 路由控制
# ============================================================
@app.route('/')
def index():
    if not session.get('logged_in'): return render_template_string(HTML_LOGIN)
    return render_template_string(HTML_MAIN)

@app.route('/send_code', methods=['POST'])
def send_code():
    email = request.form.get('email')
    if not email: return "邮箱不能为空！"
    
    now = time.time()
    if email in verify_codes and "last_sent" in verify_codes[email]:
        if now - verify_codes[email]["last_sent"] < 90:
            return f"别急嘛杂鱼♡，请等待 {int(90 - (now - verify_codes[email]['last_sent']))} 秒后再试！"
            
    code = str(random.randint(10000, 99999))
    verify_codes[email] = {"code": code, "expire": now + 180, "last_sent": now}
    
    try:
        msg = MIMEText(f"<strong>baka验证码：{code}</strong><br>3 分钟内有效。", 'html', 'utf-8')
        msg['Subject'] = Header('baka验证码', 'utf-8')
        msg['From'] = QQ_EMAIL
        msg['To'] = email
        server = smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=10)
        server.login(QQ_EMAIL, QQ_AUTH_CODE)
        server.sendmail(QQ_EMAIL, [email], msg.as_string())
        server.quit()
        return "baka验证码已发送！请查收邮箱。"
    except Exception as e:
        print(f"❌ 发信失败: {e}")
        return "发送失败，请检查网络或授权码。"

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET': return render_template_string(HTML_REGISTER)
    try:
        u = request.form.get('username')
        email = request.form.get('email')
        code = request.form.get('code')
        p = request.form.get('password')
        
        saved = verify_codes.get(email)
        if not saved or time.time() > saved["expire"] or str(saved["code"]) != str(code):
            return "<script>alert('验证码错误或过期'); location.href='/register';</script>"
            
        p_hash = hashlib.sha256(p.encode()).hexdigest()
        res = requests.post(f"{SUPABASE_URL}/rest/v1/users", headers=get_sb_headers(), json={"username": u, "email": email, "password": p_hash}, timeout=15)
        
        if res.status_code in [200, 201]:
            del verify_codes[email]
            return "<script>alert('注册成功'); location.href='/';</script>"
        else:
            print(f"❌ DB Err: {res.text}")
            return "<script>alert('账号已存在或数据库拒绝'); location.href='/register';</script>"
    except Exception as e:
        print(f"❌ Crash: {e}")
        return "<script>alert('网络错误'); location.href='/register';</script>"

@app.route('/login', methods=['POST'])
def login():
    u = request.form.get('username')
    p_hash = hashlib.sha256(request.form.get('password').encode()).hexdigest()
    res = requests.get(f"{SUPABASE_URL}/rest/v1/users?username=eq.{u}&password=eq.{p_hash}", headers=get_sb_headers(), timeout=15)
    
    if res.status_code == 200 and len(res.json()) > 0:
        session['logged_in'] = True
        return redirect('/')
    return "<script>alert('账号或密码错误'); location.href='/';</script>"

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect('/')

@app.route('/run', methods=['POST'])
def run():
    raw_qr = request.form.get('qr')
    ticket_key = request.form.get('ticket')
    def generate():
        logged_in = False
        timestamp = int(time.time())
        user_id = 0
        client = None
        try:
            yield "▶ [1/8] 正在解析二维码...\n"
            qr = parse_qr(raw_qr)
            yield "▶ [2/8] 正在通过 AllNet 获取机台信息...\n"
            machine = resolve_machine_info(CLIENT_ID)
            yield "▶ [3/8] 正在向 Chime 认证二维码...\n"
            user_id, token = chime_auth(qr.raw, machine)
            client = TitleClient(machine)
            yield "▶ [4/8] 尝试账号登录...\n"
            client.login(user_id, token, timestamp)
            logged_in = True
            yield "▶ [5/8] 正在拉取用户数据...\n"
            user_data = client.call("GetUserDataApi", {"userId": user_id}, user_id)
            user = user_data.get("userData") or {}
            charge_resp = client.call("GetUserChargeApi", {"userId": user_id}, user_id)
            charges = [item for item in (charge_resp.get("userChargeList") or []) if isinstance(item, dict)]
            yield "▶ [6/8] 检查持票状态...\n"
            if held_charges(charges): raise TicketError(f"当前已持票！")
            yield f"▶ [7/8] 模拟游玩 {SIMULATED_PLAY_SECONDS} 秒...\n"
            for i in range(SIMULATED_PLAY_SECONDS, 0, -1):
                yield f"剩余 {i}s...\n"
                time.sleep(1)
            yield "▶ [8/8] 上传数据并回查...\n"
            charge_id, price, ticket_name = TICKET_CHOICES[int(ticket_key)]
            body = build_user_chargelog_body(machine=machine, user_id=user_id, charge_id=charge_id, price=price, play_count=as_int(user.get("playCount"), 0), player_rating=as_int(user.get("playerRating"), 0), timestamp=timestamp, charge_list=charges)
            upload = client.call("UpsertUserChargelogApi", body, user_id)
            if as_int(upload.get("returnCode"), -1) != 1: raise TicketError(f"上传失败")
            after_resp = client.call("GetUserChargeApi", {"userId": user_id}, user_id)
            if find_charge(after_resp.get("userChargeList") or [], charge_id): yield "\n🎉 购票成功！\n"
            else: yield "\n⚠️ 回查未到账。\n"
        except Exception as exc:
            yield f"\n❌ 操作失败：{exc}\n"
        finally:
            if logged_in and user_id > 0 and client is not None:
                yield "\n▶ 正在强制登出...\n"
                try: client.force_logout(user_id, timestamp); yield "✔ 已安全登出。\n"
                except: yield "⚠️ 登出失败。\n"
    return Response(generate(), mimetype='text/plain')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)