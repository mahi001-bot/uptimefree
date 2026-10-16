import os
import time
import sqlite3
import threading
import socket
import ipaddress
from urllib.parse import urlparse
from datetime import datetime, timezone

from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

DB = "sufyan_uptime.db"
INTERVAL = 1
TIMEOUT = 2
MAX_URLS = 20
lock = threading.RLock()
workers = set()

HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SUFYAN Uptime</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#080d18;color:#eef3ff;
font-family:Arial,sans-serif;padding:25px}
main{max-width:1100px;margin:auto}
h1{color:#70aaff}
p{color:#9eacc4}
.card{background:#111b2d;border:1px solid #263650;
padding:20px;border-radius:14px;margin:15px 0}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.number{font-size:30px;font-weight:bold;margin-top:10px}
input{background:#080f1e;border:1px solid #354763;
color:white;padding:13px;border-radius:8px;flex:1;min-width:200px}
button{border:0;border-radius:8px;padding:12px;
font-weight:bold;cursor:pointer;background:#72aaff;color:#081323}
form{display:flex;gap:10px;flex-wrap:wrap}
table{width:100%;border-collapse:collapse;min-width:700px}
th,td{text-align:left;padding:13px;border-bottom:1px solid #263650}
th{color:#9eacc4;font-size:12px}
.wrap{overflow-x:auto}
.online{color:#40e3a2}.offline{color:#ff7088}
.wait{color:#f5c86d}
.url{overflow-wrap:anywhere}
.small{font-size:12px;color:#9eacc4}
@media(max-width:600px){body{padding:12px}.stats{grid-template-columns:1fr}}
</style>
</head>
<body>
<main>
<h1>SUFYAN UPTIME</h1>
<p>Lightweight Website Monitoring Dashboard</p>

<div class="stats">
<div class="card">TOTAL WEBSITES
<div class="number" id="total">0</div></div>
<div class="card">ONLINE
<div class="number online" id="online">0</div></div>
<div class="card">OFFLINE
<div class="number offline" id="offline">0</div></div>
</div>

<div class="card">
<h3>+ Add Website</h3>
<form id="form">
<input id="url" placeholder="https://example.com"
required maxlength="2048">
<button>Add URL</button>
</form>
<p id="msg" class="small">Checking interval: 1 second</p>
</div>

<div class="card">
<h3>Website Status</h3>
<div class="wrap">
<table>
<thead><tr>
<th>STATUS</th><th>WEBSITE</th><th>RESPONSE</th>
<th>UPTIME</th><th>LAST CHECK</th><th>ACTION</th>
</tr></thead>
<tbody id="rows"></tbody>
</table>
</div>
</div>
<p class="small">SUFYAN • Uptime Monitor</p>
</main>

<script>
const $=id=>document.getElementById(id);

function duration(n){
 if(n==null)return "—";
 n=Math.floor(n);
 let d=Math.floor(n/86400);n%=86400;
 let h=Math.floor(n/3600);n%=3600;
 let m=Math.floor(n/60);let s=n%60;
 return (d?d+"d ":"")+(h?h+"h ":"")+
 (m?m+"m ":"")+s+"s";
}

function esc(s){
 return String(s).replace(/[&<>"']/g,c=>({
 "&":"&amp;","<":"&lt;",">":"&gt;",
 '"':"&quot;","'":"&#39;"
 })[c]);
}

async function refresh(){
 try{
  const r=await fetch("/api/status");
  const data=await r.json();
  $("total").textContent=data.length;
  $("online").textContent=data.filter(x=>x.status==="ONLINE").length;
  $("offline").textContent=data.filter(x=>x.status==="OFFLINE").length;

  $("rows").innerHTML=data.map(x=>`
  <tr>
  <td class="${x.status==="ONLINE"?"online":
  x.status==="OFFLINE"?"offline":"wait"}">${esc(x.status)}</td>
  <td class="url">${esc(x.url)}</td>
  <td>${x.ms==null?"—":esc(x.ms)+" ms"}</td>
  <td>${x.status==="ONLINE"?duration(x.uptime):"—"}</td>
  <td class="small">${esc(x.last||"Waiting")}</td>
  <td><button onclick="removeSite(${x.id})">Remove</button></td>
  </tr>`).join("") ||
  '<tr><td colspan="6">Add a website to begin.</td></tr>';
 }catch(e){}
}

$("form").onsubmit=async e=>{
 e.preventDefault();
 $("msg").textContent="Adding website...";
 try{
  const r=await fetch("/api/urls",{
   method:"POST",
   headers:{"Content-Type":"application/json"},
   body:JSON.stringify({url:$("url").value.trim()})
  });
  const d=await r.json();
  if(!r.ok)throw Error(d.error);
  $("url").value="";
  $("msg").textContent="Website added!";
  refresh();
 }catch(e){$("msg").textContent=e.message||"Error adding URL";}
};

async function removeSite(id){
 if(!confirm("Remove this URL?"))return;
 await fetch("/api/urls/"+id,{method:"DELETE"});
 refresh();
}
refresh();
setInterval(refresh,1000);
</script>
</body>
</html>
"""

def connect():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with connect() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS sites(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'CHECKING',
            ms REAL,
            checked REAL,
            since REAL
        )
        """)

def validate_url(url):
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return "URL must start with http:// or https://"
        if p.username or p.password:
            return "URL username/password is not allowed"
        host = p.hostname.lower()
        if host == "localhost" or host.endswith(".local"):
            return "Local URLs are not allowed"

        addresses = socket.getaddrinfo(
            host, p.port or (443 if p.scheme == "https" else 80),
            type=socket.SOCK_STREAM
        )
        for a in addresses:
            ip = ipaddress.ip_address(a[4][0].split("%")[0])
            if not ip.is_global:
                return "Private IP addresses are not allowed"
        return None
    except Exception:
        return "Invalid URL or hostname"

def check_site(site_id, url):
    import urllib.request
    start = time.monotonic()
    status = "OFFLINE"
    ms = None

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "SufyanUptime/1.0"}
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            status = "ONLINE" if response.status < 400 else "OFFLINE"
        ms = round((time.monotonic()-start)*1000, 1)
    except Exception:
        ms = round((time.monotonic()-start)*1000, 1)

    now = time.time()
    with lock:
        with connect() as c:
            row = c.execute(
                "SELECT status,since FROM sites WHERE id=?", (site_id,)
            ).fetchone()
            if not row:
                return
            since = (
                row["since"] if status == "ONLINE"
                and row["status"] == "ONLINE" and row["since"]
                else now if status == "ONLINE" else None
            )
            c.execute(
                "UPDATE sites SET status=?,ms=?,checked=?,since=? WHERE id=?",
                (status, ms, now, since, site_id)
            )

def monitor(site_id, url):
    while True:
        with connect() as c:
            exists = c.execute(
                "SELECT 1 FROM sites WHERE id=?", (site_id,)
            ).fetchone()
        if not exists:
            return
        start = time.monotonic()
        check_site(site_id, url)
        time.sleep(max(0.05, INTERVAL-(time.monotonic()-start)))

def start_worker(site_id, url):
    with lock:
        if site_id in workers:
            return
        workers.add(site_id)
    threading.Thread(
        target=monitor, args=(site_id,url), daemon=True
    ).start()

@app.route("/")
def home():
    return render_template_string(HTML)

@app.route("/api/status")
def status_api():
    now = time.time()
    with connect() as c:
        rows = c.execute("SELECT * FROM sites ORDER BY id DESC").fetchall()
    result = []
    for r in rows:
        result.append({
            "id":r["id"],
            "url":r["url"],
            "status":r["status"],
            "ms":r["ms"],
            "last":datetime.fromtimestamp(
                r["checked"],timezone.utc
            ).strftime("%H:%M:%S UTC") if r["checked"] else None,
            "uptime":int(now-r["since"])
                if r["status"]=="ONLINE" and r["since"] else None
        })
    return jsonify(result)

@app.route("/api/urls",methods=["POST"])
def add_url():
    data=request.get_json(silent=True) or {}
    url=str(data.get("url","")).strip()
    error=validate_url(url)
    if error:
        return jsonify(error=error),400

    with lock:
        with connect() as c:
            count=c.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
            if count>=MAX_URLS:
                return jsonify(error="Maximum 20 URLs allowed"),400
            try:
                cur=c.execute(
                    "INSERT INTO sites(url,status) VALUES(?,'CHECKING')",
                    (url,)
                )
                site_id=cur.lastrowid
            except sqlite3.IntegrityError:
                return jsonify(error="URL already added"),409

    start_worker(site_id,url)
    return jsonify(ok=True)

@app.route("/api/urls/<int:site_id>",methods=["DELETE"])
def delete_url(site_id):
    with lock:
        with connect() as c:
            c.execute("DELETE FROM sites WHERE id=?",(site_id,))
        workers.discard(site_id)
    return jsonify(ok=True)

@app.route("/health")
def health():
    return jsonify(ok=True,service="SUFYAN Uptime")

init_db()
with connect() as c:
    for row in c.execute("SELECT id,url FROM sites").fetchall():
        start_worker(row["id"],row["url"])

if __name__=="__main__":
    app.run(host="0.0.0.0",
            port=int(os.environ.get("PORT",10000)),
            threaded=True)
