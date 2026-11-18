# مكتبة تيليغرام — TG Library

[العربية](#العربية) · [English](#english)

---

<div dir="rtl">

## العربية

برنامج يعمل على جهازك ويُنزّل الكتب والملفات من قنوات ومجموعات ومحادثات تيليغرام (الخاصة والبوتات والرسائل المحفوظة)،
ويحفظ معلوماتها في قاعدة بيانات، ولا يحفظ الكتاب نفسه مرتين حتى لو نُشر في أكثر من قناة أو
رُفع من جديد باسم آخر. إذا انقطع الإنترنت أو الكهرباء أو أُعيد تشغيل الجهاز، يكمل التنزيل من
حيث توقف.

### الطريقة الأسهل: برنامج ويندوز (بدون Docker)

1. فك ضغط `TGLibrary-Windows.zip` في أي مجلد (أو شغّل `TGLibrary-Setup.exe` إن توفّر)، ثم افتح
   **`TGLibrary.exe`**.
2. في أول تشغيل تظهر نافذة إعداد: الصق **api_id** و **api_hash** (انظر الخطوة 2 أدناه)، واختر مجلد
   حفظ الكتب، ثم اضغط «حفظ وتشغيل».
3. يفتح البرنامج في نافذته الخاصة ويدخل تلقائياً بدون كلمة مرور. سجّل الدخول إلى تيليغرام كما هو موضّح
   في «التشغيل» أدناه.

- إغلاق النافذة يوقف البرنامج، ويكمل التنزيل من حيث توقف عند فتحه مجدداً.
- لتغيير الإعدادات: `TGLibrary.exe --setup` (أو اختصار «TG Library - Settings» في قائمة ابدأ).
- البيانات وملف الجلسة في `%LOCALAPPDATA%\TGLibrary` — احفظها كما في «النسخ الاحتياطي».

بناء البرنامج (للمطوّر): `powershell -ExecutionPolicy Bypass -File desktop\build.ps1`

### ما تحتاجه (طريقة Docker)

1. **Docker Desktop** — نزّله من <https://www.docker.com/products/docker-desktop/> وثبّته
   ثم شغّله (على ويندوز اقبل تفعيل WSL 2 إذا طُلب منك).
2. **رقم API من تيليغرام** (مرة واحدة فقط):
   1. افتح <https://my.telegram.org> وسجّل الدخول برقم هاتفك.
   2. اختر **API development tools**.
   3. املأ الحقلين *App title* و *Short name* بأي اسم (مثلاً `My Library`) واضغط **Create application**.
   4. انسخ **App api_id** و **App api_hash**. لا تشاركهما مع أحد.
3. **قرص فيه مساحة كافية** للكتب (قد تصل إلى عدة تيرابايت).

### الإعداد

1. انسخ ملف `.env.example` باسم `.env` في المجلد نفسه.
2. افتح `.env` بالمفكرة (Notepad) واملأ:

   ```ini
   TG_API_ID=1234567
   TG_API_HASH=abcdef0123456789abcdef0123456789
   APP_PASSWORD=اختر-كلمة-مرور-قوية
   STORAGE_HOST_PATH=D:/TGLibrary
   ```

   - `APP_PASSWORD`: كلمة المرور التي ستكتبها لفتح البرنامج في المتصفح.
   - `STORAGE_HOST_PATH`: المجلد الذي ستُحفظ فيه الكتب. اختر قرصاً كبيراً، واكتب المسار
     بشرطة مائلة للأمام `/` كما في المثال. سيُنشأ المجلد تلقائياً.

### التشغيل

افتح موجّه الأوامر (PowerShell) داخل مجلد البرنامج واكتب:

```sh
docker compose up -d --build
```

ثم افتح المتصفح على: <http://127.0.0.1:8000>

- اكتب كلمة مرور البرنامج.
- اكتب رقم هاتفك في تيليغرام (مع رمز الدولة مثل `‎+966…`)، ثم الرمز الذي يصلك في تطبيق
  تيليغرام، ثم كلمة مرور التحقق بخطوتين إن كانت مفعّلة.
- اذهب إلى **المحادثات** وفعّل متابعة القنوات أو المجموعات أو المحادثات الخاصة التي تريد كتبها.
  المتابعة **تفحص فقط**: تظهر الملفات في **المكتبة ← متاحة للتنزيل**، وتختار أنت ما يُنزَّل (ملفاً
  ملفاً، أو كل ملفات محادثة). ويمكن تفعيل «نزّل الملفات الجديدة تلقائياً» لمحادثة بعينها من «خيارات».
- **اقرأ الرسائل** (بجانب كل محادثة): يعرض رسائل القناة أو المجموعة أو المحادثة الخاصة للقراءة فقط،
  مع الصور، ويمكن تنزيل أي ملف فيها إلى المكتبة. لا يُرسل شيئاً ولا يعلّم الرسائل كمقروءة.
- **البحث في تيليغرام**: ابحث باسم كتاب في كل محادثاتك، أو الصق رابط رسالة (`t.me/...`) لتنزيل ملفها.
- الكتب تُحفظ على القرص في مجلد لكل محادثة: `library/<اسم المحادثة>/<اسم الملف>`.
- في **المكتبة**: تصفّح حسب المحادثة، والأجزاء المتعددة لكتاب واحد تظهر معاً، وصور الأغلفة، وقارئ
  مدمج لملفات PDF و EPUB، وحذف كتاب.
- في **الإعدادات**: ساعات التنزيل، وحد السرعة، والملخص اليومي في «الرسائل المحفوظة». يبدأ الفحص والتنزيل تلقائياً.

يعمل البرنامج في الخلفية ويعود للعمل تلقائياً بعد إعادة تشغيل الجهاز (ما دام Docker Desktop
يعمل عند بدء ويندوز). لإيقافه: `docker compose stop`. لتشغيله مجدداً: `docker compose start`.

### الاستخدام من جهاز آخر في البيت

البرنامج افتراضياً لا يُفتح إلا من الجهاز نفسه. لفتحه من هاتف أو حاسوب آخر في الشبكة المنزلية
نفسها:

1. في `docker-compose.yml` غيّر السطر `"127.0.0.1:${PORT:-8000}:8000"` إلى `"${PORT:-8000}:8000"`.
2. نفّذ `docker compose up -d`.
3. افتح من الجهاز الآخر `http://<عنوان-جهازك>:8000` (اعرف العنوان بالأمر `ipconfig`). قد يطلب
   جدار حماية ويندوز السماح بالاتصال.

### ⚠️ تنبيهات أمنية مهمة

- **ملف الجلسة `data/telegram.session` يساوي الدخول الكامل إلى حسابك في تيليغرام.** من يحصل
  عليه يستطيع قراءة رسائلك والتصرف باسمك. لا ترسله لأحد ولا ترفعه إلى أي موقع.
- **لا تفتح البرنامج على الإنترنت مباشرة.** إن احتجت ذلك فضعه خلف وسيط عكسي (reverse proxy)
  يستخدم HTTPS، واضبط `COOKIE_SECURE=true` في `.env`، واستخدم كلمة مرور قوية.
- يمكنك رؤية جلسة البرنامج في تيليغرام: الإعدادات ← الأجهزة، باسم **TG Library**. إنهاؤها من
  هناك يقطع وصول البرنامج فوراً.
- الملفات المنزّلة تأتي من أشخاص لا تعرفهم. البرنامج لا يفك الملفات المضغوطة، وافحص ما تفتحه
  ببرنامج مكافحة الفيروسات.

### النسخ الاحتياطي

- **مجلد `data/`** فيه قاعدة البيانات وملف الجلسة وسجلّ البرنامج. لنسخه احتياطياً:
  `docker compose stop` ثم انسخ المجلد كاملاً، ثم `docker compose start`. لا تنسخه والبرنامج
  يعمل، فقد تكون النسخة غير مكتملة. احفظ النسخة في مكان آمن (فيها ملف الجلسة).
- **مجلد الكتب** (`STORAGE_HOST_PATH`) فيه الكتب نفسها داخل `library/`. انسخه كما تنسخ أي مجلد.
  أسماء الملفات فيه رموز (بصمة المحتوى)؛ الأسماء الحقيقية في قاعدة البيانات وتظهر عند التنزيل
  من صفحة **المكتبة**.

### التحديث

```sh
git pull
docker compose up -d --build
```

قاعدة البيانات تُحدَّث تلقائياً عند التشغيل.

### عند حدوث مشكلة

- سجلّ البرنامج في `data/logs/app.log`، أو بالأمر `docker compose logs --tail 100`.
- «تيليغرام طلب الانتظار»: طبيعي عند التنزيل الكثير. ينتظر البرنامج ثم يكمل وحده.
- «مساحة التخزين ممتلئة»: أفرغ مساحة ثم اضغط **استئناف التنزيل** في الصفحة الرئيسية.

</div>

---

## English

A self-hosted web app that downloads books and documents from your Telegram
channels, groups, private chats, bots and Saved Messages, keeps their metadata in SQLite, stores each
unique file only once, and resumes interrupted downloads after restarts, network
drops or power loss. The UI is Arabic and right-to-left.

### Windows desktop app (no Docker)

Unzip `TGLibrary-Windows.zip` (or run `TGLibrary-Setup.exe`) and open
`TGLibrary.exe`. The first run asks for the Telegram `api_id` / `api_hash` and
a books folder; the app then runs the server in-process and opens the UI in its
own Edge app window, signed in automatically through a per-launch link
(`/api/auth/desktop`, enabled only when `DESKTOP_TOKEN` is set). Closing the
window stops the app. `TGLibrary.exe --setup` reopens the setup window. Data,
session and logs live in `%LOCALAPPDATA%\TGLibrary`.

Build it with `powershell -ExecutionPolicy Bypass -File desktop\build.ps1`
(PyInstaller, see `desktop/`). Output: `dist\TGLibrary\` (the app folder),
`dist\TGLibrary-Windows.zip`, and `dist\TGLibrary-Setup.exe` when
[Inno Setup 6](https://jrsoftware.org/isdl.php) is installed.

### Requirements (Docker)

- Docker (Docker Desktop on Windows/macOS).
- A Telegram API ID and hash: log in at <https://my.telegram.org>, open
  **API development tools**, create an application with any title, and copy
  `api_id` and `api_hash`.
- A drive with room for the library (hundreds of GB to several TB).

### Setup and run

```sh
cp .env.example .env     # then edit it
docker compose up -d --build
```

| Variable | Meaning |
| --- | --- |
| `TG_API_ID`, `TG_API_HASH` | From my.telegram.org |
| `APP_PASSWORD` | Password for the web UI (required) |
| `STORAGE_HOST_PATH` | Host folder for downloaded files, e.g. `D:/TGLibrary` |
| `PORT` | Host port (default 8000) |
| `COOKIE_SECURE` | Set `true` when served over HTTPS |

Open <http://127.0.0.1:8000>, enter the app password, then log in to Telegram
(phone → code → two-step password, or scan a QR code from the phone app).

- Tracking a chat on **المحادثات** (Chats) only scans it: its files are listed
  as *available* in the Library and downloaded when you pick them (one by one,
  a selection, or a whole chat). A per-chat option downloads new files
  automatically.
- **اقرأ الرسائل** (Read messages, next to each chat) shows the messages of a
  channel, group or private chat, read-only: text, pictures, replies and
  forwards, with a button to add any file to the library. Nothing is sent or
  marked as read, and messages aren't stored: pages are fetched from Telegram
  as you scroll.
- **البحث في تيليغرام** (Search) finds files across all your chats, and
  downloads the file from a pasted `t.me` message link.
- Files are stored as `library/<chat name>/<file name>`, once per unique
  content (in the chat where it was first posted).
- The Library browses by chat, groups multi-volume works, shows cover
  thumbnails, has a built-in PDF/EPUB reader, and can delete books.
- Settings include download hours, a speed limit and a daily summary sent to
  your Saved Messages.

Cover thumbnails use PyMuPDF (AGPL-3.0); fine for personal self-hosting.

Volumes: `./data` → `/data` (database, Telegram session, logs, secret key) and
`STORAGE_HOST_PATH` → `/storage` (`library/` holds files named by SHA-256;
`tmp/` holds partial downloads).

**LAN access:** the port is bound to `127.0.0.1` by default. Change it to
`"${PORT:-8000}:8000"` in `docker-compose.yml` to reach it from other devices
on your network.

### Security

- `data/telegram.session` grants full access to the Telegram account. Never
  share it or commit it; treat backups of `data/` as secrets.
- Do not expose the app to the internet without HTTPS and a reverse proxy
  (and `COOKIE_SECURE=true`). It controls your Telegram account.
- The session appears in Telegram → Settings → Devices as **TG Library**;
  terminating it there revokes access immediately.
- Downloaded files come from strangers. Only PDF and plain text open inline in
  the browser; everything else is served as a sandboxed download. Archives are
  never extracted.

### Backups

Stop the container (`docker compose stop`), copy `data/`, start it again.
SQLite runs in WAL mode, so copying while running can produce an inconsistent
copy. The storage folder can be copied at any time.

### How it works

- **One process.** Telethon allows a single client per session, so uvicorn runs
  one worker; the scanner, scheduler and download workers are asyncio tasks in
  the same event loop as FastAPI.
- **Scanning.** Each tracked chat is read newest-first with
  `InputMessagesFilterDocument` from `last_scanned_msg_id`, which advances only
  after a complete pass. The pass's position (`scan_top_msg_id`,
  `scan_resume_msg_id`) is saved with every batch, so a scan cut short by a lost
  connection or a restart continues below where it stopped, then picks up what
  was posted since; inserts are idempotent. Flood waits are slept through.
  Tracked chats are rescanned every N hours (setting).
- **Deduplication.** A known `tg_document_id` (forwards) only adds an
  appearance. A new document with the same size and normalized name (Arabic
  letter variants, tashkeel, `@channel` tags, separators folded) is a duplicate
  and is never downloaded. After download, identical SHA-256 content is a
  duplicate too, and only one copy stays on disk.
- **Downloads.** N workers claim jobs atomically (priority, then age). Each
  download re-fetches its message for a fresh file reference, falls back to
  other appearances if the message was deleted, and writes to
  `tmp/{id}.part`. Data is fsynced before progress is recorded; on restart the
  file is truncated to the last durable 512 KiB boundary and continued.
  Flood waits pause all workers; disk-full pauses the engine; network errors
  back off without counting attempts; other errors retry with exponential
  backoff up to a maximum, then mark the file failed.
- **Realtime.** `GET /api/events` streams a status snapshot every second
  (progress, speed, engine state, scan progress, counters) plus change events.

### Development

```sh
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements-dev.txt   # bin/ on Linux/macOS
.venv/Scripts/python -m pytest

cd frontend && npm install && npm run dev   # proxies /api to 127.0.0.1:8000
```

Run the backend locally with `DATA_DIR`, `STORAGE_DIR` and the Telegram
variables set, from `backend/`:
`uvicorn tglib.app.main:app --port 8000 --workers 1`.

Tests use an in-memory fake Telegram client (`tests/fake_telegram.py`) that
implements the same `TelegramGateway` interface as the Telethon adapter and can
inject flood waits, expired file references and dropped connections.

Layout: `backend/tglib/{app,api,db,engine}`, migrations in `backend/alembic`,
UI in `frontend/` (React, Vite, TypeScript, Tailwind), Docker files in
`docker/`, tests in `tests/`.

### Not in this version

Text extraction and full-text search, OCR, metadata enrichment (ISBN),
near-duplicate detection by content, archive extraction, takeout sessions and
multiple accounts. `blobs` is the anchor for per-file extracted data later.
