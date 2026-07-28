# Xray Config Forge 🔨

تولید خودکار کانفیگ‌های **سالم و تست‌شده‌ی** Xray (VLESS / Trojan روی WebSocket و XHTTP، و VLESS+REALITY برای VPS)، اتصال آن‌ها به Xray، و اجرا روی سه محیط: **GitHub Codespaces**، **Cloudflare Workers** و **VPS**.

ابزار فقط با کتابخانه‌ی استاندارد پایتون کار می‌کند (بدون `pip install`). هسته‌ی مولد از محیط اجرا جداست، پس یک بار می‌نویسی و همه‌جا استفاده می‌کنی.

---

## ⚠️ قبل از هر چیز، دو نکته‌ی صادقانه

1. **قوانین GitHub:** اجرای پروکسی/VPN روی Codespaces خلاف Acceptable Use Policy گیت‌هاب است (استفاده‌ی «proxy/CDN-like» و بار نامتناسب روی سرورها منع شده) و می‌تواند به **تعلیق یا مسدود شدن اکانت** منجر شود. برای استفاده‌ی شخصی و موقت است، نه سرویس عمومی. اگر اکانت گیت‌هابت برایت مهم است، مسیر **Cloudflare Workers** را انتخاب کن.
2. **Codespace پایدار نیست:** بعد از حدود ۳۰ دقیقه بی‌کاری خاموش می‌شود، سقف رایگان ماهانه دارد (~۱۲۰ core-hour)، و **public بودن پورت با هر ری‌استارت به private برمی‌گردد** (باید دوباره public شود). یعنی «همیشه‌روشن» نیست.

**توصیه:** Codespace برای آزمایش و استفاده‌ی موقت. برای همیشه‌روشن → **Cloudflare Workers** (رایگان، کم‌ریسک) یا **VPS** (پایدارترین، با REALITY).

این ابزار برای دور زدن سانسور و دسترسی شخصی به اینترنت آزاد است؛ مسئولیت رعایت قوانین محل خودت با خودت است.

---

## چه می‌کند

```
generator/   → از چند ورودی ساده می‌سازد: کانفیگ سرور Xray + لینک‌های اشتراک + subscription(base64) + کلاینت آماده
healthcheck/ → Xray را واقعاً بالا می‌آورد و یک درخواست را از داخل پروکسی عبور می‌دهد؛ فقط کانفیگ «متصل» را تأیید می‌کند
runtimes/
  codespace/ → devcontainer + اسکریپت نصب/اجرا + public کردن پورت تونل
  cf-worker/ → Worker آماده‌ی Cloudflare (VLESS over WS)
  vps/       → نصب‌کننده‌ی VPS با VLESS+REALITY به‌صورت systemd service
```

خروجی‌ها در `output/` ساخته می‌شوند: `subscription.txt` (این را در اپ موبایل import کن)، `share_links.txt`، `xray_server.json`، `client_remote.json`، `summary.json`.

---

## شروع سریع — GitHub Codespaces

1. این ریپو را روی گیت‌هاب push کن.
2. دکمه‌ی سبز **Code → Codespaces → Create codespace** را بزن. (devcontainer خودش Xray را نصب می‌کند.)
3. داخل ترمینال Codespace:

```bash
bash runtimes/codespace/start.sh
```

این اسکریپت: کانفیگ می‌سازد → Xray را بالا می‌آورد → پورت تونل را public می‌کند → لینک‌ها و subscription را چاپ می‌کند → و **سلامت اتصال زنده را تست می‌کند**.

- برای چند پروتکل:  `PROTOCOLS="vless-ws,trojan-ws" bash runtimes/codespace/start.sh`
- لینک subscription برای موبایل (اگر `SERVE_SUB=1`):
  `https://<CODESPACE_NAME>-9000.app.github.dev/subscription.txt`
- توقف:  `bash runtimes/codespace/stop.sh`

> اگر پیام «could not set port public» دیدی: در تب **PORTS** روی پورت راست‌کلیک → **Port Visibility → Public**. (توکن پیش‌فرض Codespace گاهی scope لازم را ندارد.)

---

## تست سلامت (Health Check)

```bash
# لوکال: سرور و کلاینت را کنار هم بالا می‌آورد و ترافیک را از پروکسی عبور می‌دهد (بدون نیاز به تونل)
python3 healthcheck/check.py --mode local

# ریموت: کلاینت را به هاست عمومی (تونل زنده‌ی Codespace / Worker / VPS) وصل می‌کند
python3 healthcheck/check.py --mode remote
```

خروجی هر endpoint را `PASS`/`FAIL` نشان می‌دهد و کد خروجی برای CI مناسب است (۰ = همه سالم).

---

## استفاده‌ی دستی از مولد

```bash
# ساخت لوکال برای تست (بدون TLS، روی loopback)
python3 generator/generate.py --host 127.0.0.1 --public-port 8080 \
  --internal-base-port 8080 --no-edge-tls --protocols "vless-ws,trojan-ws,vless-xhttp"

# ساخت برای یک دامنه/CDN با TLS لبه
python3 generator/generate.py --host example.com --public-port 443 --protocols vless-ws
```

پرچم‌های مهم: `--protocols` (مثل `vless-ws,trojan-ws,vless-xhttp`)، `--host`، `--public-port`،
`--edge-tls/--no-edge-tls`، `--reality`، `--path-prefix`، `--codespace-name`.

---

## VPS با REALITY (پایدارترین)

روی یک سرور تازه‌ی Ubuntu/Debian، به‌صورت root:

```bash
sudo PORT=443 DEST=www.microsoft.com:443 SNI=www.microsoft.com bash runtimes/vps/setup.sh
```

VLESS + TCP + REALITY (Vision) می‌سازد، کانفیگ را validate می‌کند، Xray را به‌صورت systemd service نصب و enable می‌کند و لینک کلاینت را چاپ می‌کند. کلید x25519 و short id خودکار تولید می‌شوند.

---

## Cloudflare Workers (توصیه‌شده برای همیشه‌روشن)

راهنمای کامل: `runtimes/cf-worker/README.md`. خلاصه:

```bash
cd runtimes/cf-worker
wrangler login
wrangler secret put UUID
wrangler deploy
```

---

## چرا روی Codespace فقط ws/xhttp؟ (سازوکار فنی)

تونل عمومی Codespace از `https://<name>-<port>.app.github.dev` عبور می‌کند و **HTTP/HTTPS-محور** است؛ TLS در لبه‌ی خود گیت‌هاب terminate می‌شود. بنابراین:

- ترنسپورت باید HTTP-محور باشد: **WebSocket** (مطمئن‌ترین) یا **XHTTP**.
- کلاینت به `...app.github.dev:443` با **TLS** وصل می‌شود؛ `sni` و `host` = همان زیردامنه.
- Xray داخل کانتینر روی پورت داخلی (مثل 8080) **بدون TLS** گوش می‌دهد.
- **REALITY و TCP خام روی Codespace کار نمی‌کند** (به کنترل مستقیم TLS/TCP نیاز دارد) — آن را برای VPS نگه دار.
- هر پورت forwardشده زیردامنه‌ی مخصوص خودش را دارد؛ مولد این را خودکار مدیریت می‌کند (`--codespace-name`).

> **نکته‌ی نسخه‌های جدید Xray (v26+):** هنگام اجرا ممکن است اخطار deprecation ببینی —
> WebSocket به‌سمت **XHTTP (H2/H3)** و Trojan بدون Flow به‌سمت **VLESS با Flow** در حال منسوخ‌شدن‌اند.
> هر دو هنوز کامل کار می‌کنند (در همین بیلد تست شده‌اند)، ولی برای آینده‌نگری **VLESS + XHTTP** انتخاب استراتژیک است؛
> WebSocket را وقتی نگه دار که از عبور مطمئن‌تر از تونل/CDN می‌خواهی.

---

## اپ‌های کلاینت

`subscription.txt` را در این‌ها import کن: **v2rayNG** (اندروید)، **v2rayN** (ویندوز)، **Hiddify**, **Streisand / v2box / Shadowrocket** (iOS). یا یک `share_link` را مستقیم paste کن.

---

## عیب‌یابی

- `FAIL` در health check → لاگ: `output/_run/xray.log` و `output/_healthcheck/*.log`.
- ریموت FAIL ولی لوکال PASS → پورت هنوز public نشده، یا Codespace خاموش/ری‌استارت شده.
- کندی → WebSocket را به‌جای XHTTP امتحان کن؛ روی VPS از REALITY استفاده کن.
- هیچ‌وقت `output/` واقعی (UUID/پسورد/کلید) را commit نکن (در `.gitignore` هست).

نسخه‌ی تست‌شده‌ی Xray-core: **v26.3.27**.
