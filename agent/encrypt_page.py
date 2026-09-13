#!/usr/bin/env python3
"""Оборачивает готовую HTML-страницу в зашифрованную обёртку с паролем.
Использование: python3 encrypt_page.py <in.html> <out.html> [пароль]
Пароль: аргумент, иначе переменная PAGE_PASS, иначе строка PAGE_PASS= из .env / config/env.txt.
Страница сжимается gzip, затем AES-256-GCM; ключ из пароля через PBKDF2-HMAC-SHA256 (600 000 итераций). Расшифровка — в браузере (WebCrypto).
Включён вотчдог свежести для iOS-веб-клипа («На экран Домой»): при возврате из bfcache
или после >10 минут в фоне страница перезагружается с cache-busting-параметром.
"""
import os, sys, json, base64, secrets, gzip
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

HERE = os.path.dirname(os.path.abspath(__file__))
ITER = 600_000

def passphrase():
    if len(sys.argv) > 3: return sys.argv[3]
    if os.environ.get('PAGE_PASS'): return os.environ['PAGE_PASS']
    for p in ('.env', 'config/env.txt', 'config/.env'):
        f = os.path.join(HERE, p)
        if os.path.exists(f):
            for line in open(f, encoding='utf-8'):
                if line.startswith('PAGE_PASS='): return line.split('=', 1)[1].strip()
    sys.exit('PAGE_PASS не найден')

src, dst = sys.argv[1], sys.argv[2]
pw = passphrase()
html = open(src, encoding='utf-8').read()
title = 'Утро Ecovelle'
salt, iv = secrets.token_bytes(16), secrets.token_bytes(12)
key = PBKDF2HMAC(hashes.SHA256(), 32, salt, ITER).derive(pw.encode('utf-8'))
ct = AESGCM(key).encrypt(iv, gzip.compress(html.encode('utf-8'), 9), None)
blob = json.dumps({'gz': 1, 'salt': base64.b64encode(salt).decode(), 'iv': base64.b64encode(iv).decode(), 'iter': ITER, 'ct': base64.b64encode(ct).decode()})

wrapper = f'''<title>{title}</title>
<style>
:root{{color-scheme:light;--bg:#f4f4f0;--surface:#fcfcfb;--line:#e1e0d9;--ink:#141412;--ink-2:#52514e;--muted:#898781;--accent:#1c5cab;--crit:#d03b3b}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{color-scheme:dark;--bg:#111214;--surface:#1a1a19;--line:#2c2c2a;--ink:#f4f4f0;--ink-2:#c3c2b7;--accent:#6da7ec}}}}
:root[data-theme="dark"]{{color-scheme:dark;--bg:#111214;--surface:#1a1a19;--line:#2c2c2a;--ink:#f4f4f0;--ink-2:#c3c2b7;--accent:#6da7ec}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
#gate{{min-height:70vh;display:flex;align-items:center;justify-content:center;padding:24px}}
#gate form{{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:28px 26px;width:min(380px,100%);display:flex;flex-direction:column;gap:12px}}
#gate h1{{font-size:20px;margin:0;letter-spacing:-.01em}}
#gate p{{margin:0;color:var(--ink-2);font-size:14px}}
#gate input{{font:inherit;font-size:16px;padding:11px 12px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink)}}
#gate input:focus{{outline:2px solid var(--accent);outline-offset:1px}}
#gate button{{font:inherit;font-weight:600;padding:11px;border:0;border-radius:9px;background:var(--accent);color:#fff;cursor:pointer}}
#gate button:disabled{{opacity:.6}}
#err{{color:var(--crit);font-size:13.5px;min-height:1.2em}}
#gate label{{display:flex;gap:8px;align-items:center;font-size:13.5px;color:var(--ink-2)}}
</style>
<div id="gate"><form id="f">
<h1>Утро Ecovelle</h1>
<p>Сводка защищена паролем. Введите его, чтобы открыть.</p>
<input id="pw" type="password" autocomplete="current-password" placeholder="Пароль" autofocus>
<label><input type="checkbox" id="rem" style="width:auto;padding:0" checked> Запомнить на этом устройстве</label>
<button id="btn" type="submit">Открыть</button>
<div id="err"></div>
</form></div>
<script id="enc" type="application/json">{blob}</script>
<script>
const E=JSON.parse(document.getElementById('enc').textContent);
const b64=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function unlock(pw){{
  const km=await crypto.subtle.importKey('raw',new TextEncoder().encode(pw),'PBKDF2',false,['deriveKey']);
  const key=await crypto.subtle.deriveKey({{name:'PBKDF2',salt:b64(E.salt),iterations:E.iter,hash:'SHA-256'}},km,{{name:'AES-GCM',length:256}},false,['decrypt']);
  const pt=await crypto.subtle.decrypt({{name:'AES-GCM',iv:b64(E.iv)}},key,b64(E.ct));
  if(!E.gz) return new TextDecoder().decode(pt);
  if(!('DecompressionStream' in window)) throw new Error('old-browser');
  const ds=new Blob([pt]).stream().pipeThrough(new DecompressionStream('gzip'));
  return await new Response(ds).text();
}}
// вотчдог свежести: iOS-веб-клип («На экран Домой») замораживает страницу и при
// повторном открытии показывает старый снимок. Перезагружаем с cache-busting.
function bust(){{location.replace(location.pathname+'?r='+Date.now())}}
function arm(){{
  const t=Date.now();
  addEventListener('pageshow',e=>{{if(e.persisted)bust()}});
  document.addEventListener('visibilitychange',()=>{{if(!document.hidden&&Date.now()-t>6e5)bust()}});
}}
function render(html){{
  // самый надёжный способ во всех браузерах (включая Safari): полностью переписать документ расшифрованной страницей
  const full='<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"></head><body>'+html+'</body></html>';
  document.open();document.write(full);document.close();
  arm(); // document.open() снёс старые слушатели — вешаем заново на новый документ
  if(location.search.includes('debug')){{setTimeout(()=>{{const d=document.createElement('pre');d.style.cssText='position:fixed;bottom:0;left:0;right:0;background:#000;color:#0f0;font:11px monospace;padding:6px;z-index:99999;white-space:pre-wrap';d.textContent='UA: '+navigator.userAgent+' | styleSheets: '+document.styleSheets.length+' rules: '+[...document.styleSheets].map(s=>{{try{{return s.cssRules.length}}catch(e){{return 'err'}}}}).join(',')+' | card radius: '+(document.querySelector('.card')?getComputedStyle(document.querySelector('.card')).borderRadius:'no .card');document.body.appendChild(d)}},1500)}}
}}
const f=document.getElementById('f'),err=document.getElementById('err'),btn=document.getElementById('btn'),inp=document.getElementById('pw');
f.addEventListener('submit',async e=>{{e.preventDefault();err.textContent='';btn.disabled=true;btn.textContent='Открываю…';
  try{{const html=await unlock(inp.value);try{{if(document.getElementById('rem').checked)localStorage.setItem('utro_pw',inp.value)}}catch(_){{}}render(html)}}
  catch(e){{err.textContent=e&&e.message==='old-browser'?'Браузер устарел — откройте в Safari/Chrome последней версии':'Неверный пароль';btn.disabled=false;btn.textContent='Открыть';inp.select()}}}});
arm();
(async()=>{{let saved=null;try{{saved=localStorage.getItem('utro_pw')}}catch(_){{}}if(saved){{try{{render(await unlock(saved))}}catch(_){{try{{localStorage.removeItem('utro_pw')}}catch(__){{}}}}}}}})();
</script>
'''
open(dst, 'w', encoding='utf-8').write(wrapper)
print('encrypted', src, '->', dst, len(wrapper), 'bytes')
