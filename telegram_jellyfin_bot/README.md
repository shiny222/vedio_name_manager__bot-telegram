# ربات تلگرام برای دانلود ویدیو و آماده‌سازی Jellyfin

این پروژه ویدیوهای ارسالی در یک گروه یا کانال تلگرام را ابتدا در صف SQLite
نگه می‌دارد و فقط پس از دستور و تأیید شما دانلود می‌کند. دانلود از
**Local Telegram Bot API Server** انجام می‌شود، بنابراین محدودیت دانلود Bot API
عمومی را ندارد. نام اصلی فایل تغییر نمی‌کند. مرتب‌ساز در پوشه هم‌سطح
`organizer` مستقل باقی مانده و همچنان با CLI یا فایل BAT خودش قابل استفاده است.

## پیش‌نیازها

- Windows 10/11
- Python 3.10 یا جدیدتر و فعال بودن گزینه `Add Python to PATH`
- یک Bot Token از [BotFather](https://t.me/BotFather)
- `api_id` و `api_hash` از [my.telegram.org](https://my.telegram.org)
- فایل native ویندوز `telegram-bot-api.exe` (بدون Docker)

## چرا Local Bot API؟

Bot API عمومی محدودیت دانلود دارد. سرور رسمی local در حالت `--local` دانلود
بدون محدودیت اندازه را فعال می‌کند و `getFile` می‌تواند مسیر کامل فایل محلی را
برگرداند. این پروژه Local Bot API را فقط روی `127.0.0.1` اجرا می‌کند و آن را
روی شبکه منتشر نمی‌کند.

هنگام انتقال یک بات از API عمومی به سرور local، یک بار متد `logOut` را روی API
عمومی اجرا کنید:

```text
https://api.telegram.org/botYOUR_TOKEN/logOut
```

سپس دیگر همان بات را هم‌زمان روی API عمومی و local اجرا نکنید.

## نصب سریع

1. پوشه `telegram_jellyfin_bot` را باز کنید.
2. روی `install.bat` دوبار کلیک کنید.
3. نصب‌کننده `.venv` را می‌سازد، وابستگی‌ها را نصب می‌کند و اگر لازم باشد
   `config.json` را از روی نمونه می‌سازد.
4. `telegram-bot-api.exe` را داخل پوشه `tools` بگذارید، یا مسیر واقعی آن را در
   `config.json` بنویسید.
5. `config.json` را ویرایش کنید.

`config.json` حاوی توکن و اطلاعات حساس است و توسط `.gitignore` از Git حذف شده
است. آن را برای دیگران نفرستید.

## تنظیم config.json

مهم‌ترین فیلدها:

```json
{
  "bot_token": "توکن BotFather",
  "telegram_api_id": 12345678,
  "telegram_api_hash": "api_hash",
  "telegram_bot_api_exe_path": "tools\\telegram-bot-api.exe",
  "jellyfin_library_path": "D:\\Media\\Jellyfin\\Shows",
  "allowed_chat_ids": [-1001234567890],
  "max_parallel_downloads": 1
}
```

- `jellyfin_library_path`: پوشه اصلی سریال‌ها/انیمه‌ها در Jellyfin.
- `allowed_chat_ids`: شناسه گروه یا کانال مجاز. برای دیدن شناسه، موقتاً لیست را
  خالی بگذارید، بات را اجرا کنید و `/chatid` را بفرستید؛ سپس شناسه را در config
  قرار دهید و بات را از نو اجرا کنید.
- `max_parallel_downloads`: بهتر است ابتدا `1` باشد.
- `confirm_before_download`: اگر `true` باشد، `/confirm_download` لازم است.
- `ask_before_overwrite`: اگر `true` باشد، بات برای فایل تکراری تصمیم می‌خواهد.
- `sorter_command`: آرایه امن آرگومان‌هاست. به‌طور پیش‌فرض Python و اسکریپت را
  از پوشه هم‌سطح `organizer` اجرا می‌کند؛ `{folder}` و `{mode}` جایگزین می‌شوند
  و `shell=True` استفاده نمی‌شود.
- `auto_sort_after_download`: پیش‌فرض `false` است؛ مرتب‌سازی فقط با دستور شماست.
- `jellyfin_server_url`: آدرس سرور، مثلاً `http://127.0.0.1:8096`.
- `jellyfin_api_key`: از Jellyfin Dashboard بخش API Keys ساخته می‌شود.
- `jellyfin_request_timeout_seconds`: مهلت اتصال بات به Jellyfin.

اگر `allowed_chat_ids` خالی باشد، تمام چت‌هایی که به بات دسترسی دارند مجاز
خواهند بود. برای امنیت آن را حتماً پر کنید.

## اجرا بدون Docker

در پنجره اول:

1. روی `run_local_bot_api.bat` دوبار کلیک کنید.
2. این فایل executable را با `--local`، `api_id`، `api_hash`، آدرس
   `127.0.0.1` و پورت config اجرا می‌کند.
3. پنجره را باز نگه دارید.

در پنجره دوم روی `run.bat` دوبار کلیک کنید و آن را نیز باز نگه دارید.

اگر Bot API local اجرا نباشد، بات خطای اتصال می‌دهد و هر سه ثانیه دوباره تلاش
می‌کند. توکن در log نوشته نمی‌شود.

## اضافه کردن بات به گروه یا کانال

- بات را به گروه اضافه کنید.
- برای کانال، بات را به‌عنوان administrator اضافه کنید تا `channel_post` را
  دریافت کند و بتواند پیام وضعیت بفرستد.
- اگر Privacy Mode مانع دریافت فایل‌های گروه است، تنظیم مناسب آن را از
  BotFather بررسی کنید.
- هر دو نوع update یعنی `message` و `channel_post` پشتیبانی می‌شوند.

## روش معمول استفاده

ابتدا فقط نام فولدر را بدهید، نه مسیر کامل:

```text
/setfolder My Course
```

با Library نمونه، مقصد این می‌شود:

```text
D:\Media\Jellyfin\Shows\My Course
```

حالا ویدیوها را در گروه/کانال بفرستید. بات آن‌ها را **دانلود نمی‌کند** و فقط
پیام «ویدیو به صف اضافه شد» می‌دهد. هر آیتم فولدر مقصد زمان دریافت خودش را
نگه می‌دارد.

سپس:

```text
/queue
/download
```

بات مقصد، تعداد، حجم تقریبی و چند نام فایل را نشان می‌دهد. برای شروع:

```text
/confirm_download
```

فایل ابتدا با پسوند `.part` نوشته می‌شود و فقط پس از تکمیل به نام اصلی تبدیل
می‌شود. اگر برنامه متوقف شود، وضعیت `downloading` در اجرای بعد به صف برمی‌گردد
و فایل `.part` در `/status` قابل مشاهده است؛ تلاش بعدی دانلود را امن از ابتدا
شروع می‌کند.

## فایل تکراری

هیچ overwrite خودکاری انجام نمی‌شود. در صورت وجود فایل، بات دستوری شبیه موارد
زیر نشان می‌دهد:

```text
/resolve 12 skip
/resolve 12 overwrite
/resolve 12 save_with_suffix
```

`save_with_suffix` تنها با انتخاب صریح شما نامی مانند `Video (1).mkv` می‌سازد.
حالت `overwrite` نیز فقط بعد از انتخاب صریح شما اجرا می‌شود.

## دستورهای بات

- `/menu` — نمایش منوی دکمه‌ای؛ داخل کانال نیز قابل استفاده است
- `/setfolder NAME` — تنظیم مقصد فعلی
- `/folders` — نمایش فولدرهای موجود به‌شکل دکمه‌ای و انتخاب مقصد
- `/usefolder NAME` — انتخاب یک فولدر موجود با نام دقیق
- `/renamefolder NAME` — اصلاح امن نام فولدر فعلی و مقصد موارد صف
- `/folder` — نمایش مقصد و مسیر کامل
- `/unsetfolder` — پاک کردن مقصد فعلی
- `/queue` — نمایش صف
- `/remove ID` — حذف یک مورد
- `/clearqueue` — پاک کردن موارد فعال صف
- `/download` — نمایش خلاصه و درخواست تأیید
- `/confirm_download` — شروع دانلود
- `/status` — تعداد وضعیت‌ها و فایل‌های ناقص
- `/cancel` — درخواست توقف دانلود
- `/resolve ID ACTION` — تصمیم درباره فایل تکراری
- `/sort_current` — مرتب‌سازی فولدر فعلی
- `/sort_latest` — مرتب‌سازی آخرین فولدر دانلود
- `/sort_folder NAME` — مرتب‌سازی فولدر مشخص داخل Library
- `/sort_status` — وضعیت آخرین اجرای مرتب‌ساز
- `/undo_sort_last` — برگرداندن آخرین Batch مرتب‌سازی
- `/undo_sort_batch ID` — برگرداندن Batch مشخص با شناسه
- `/jellyfin_scan` — درخواست Scan کامل Library از Jellyfin
- `/jellyfin_status` — آزمایش اتصال و نمایش آخرین درخواست Scan
- `/episodes [NAME]` — نمایش اپیزودها و شماره‌های Missing یک سریال
- `/library_episodes` — خلاصه فصل‌ها و اپیزودهای تمام Library
- `/chatid` — نمایش chat ID
- `/help` — راهنما

در کانال، تلگرام با لمس یک command آن را فوراً ارسال می‌کند و امکان قراردادن
command معمولی در input برای ویرایش را نمی‌دهد. بنابراین `/help` برای
دستورهای آرگومان‌دار دکمه **Copy** نمایش می‌دهد؛ دکمه را بزنید، متن را Paste
کنید و نام فولدر یا Batch ID را در انتهای آن بنویسید.

## مرتب‌ساز مستقل

بات پس از دانلود نام فایل را تغییر نمی‌دهد. فقط وقتی `/sort_latest`،
`/sort_current` یا `/sort_folder` را بفرستید، `organizer.py` با subprocess امن
و timeout اجرا می‌شود. خروجی کامل در `logs/bot.log` و ۳۰۰۰ کاراکتر آخر در
تلگرام نمایش داده می‌شود.

مرتب‌ساز همچنان جداگانه قابل اجراست. ابتدا `organizer\install.bat` را یک بار
اجرا کنید، سپس از داخل پوشه `organizer`:

```powershell
.\.venv\Scripts\python.exe organizer.py dry-run --series-folder "D:\Media\Jellyfin\Shows\My Course"
.\.venv\Scripts\python.exe organizer.py run --series-folder "D:\Media\Jellyfin\Shows\My Course"
```

## State و مسیرها

- صف و تنظیمات: `data/state.db`
- log: `logs/bot.log`
- فایل‌های ناقص: کنار فایل مقصد با پسوند `.part`
- تنظیمات حساس: `config.json`

SQLite باعث می‌شود صف، فولدر فعلی، وضعیت دانلودها و اجرای sorter پس از restart
حفظ شوند.

## اتصال به Jellyfin

در Jellyfin وارد `Dashboard` شوید و از بخش `API Keys` یک کلید مخصوص این بات
بسازید. سپس در `config.json` قرار دهید:

```json
"jellyfin_server_url": "http://127.0.0.1:8096",
"jellyfin_api_key": "YOUR_JELLYFIN_API_KEY",
"jellyfin_request_timeout_seconds": 30
```

اگر Jellyfin روی سیستم دیگری است، IP همان سیستم را جایگزین `127.0.0.1` کنید.
کلید API را در GitHub قرار ندهید. فرمان `/jellyfin_scan` درخواست
`POST /Library/Refresh` را می‌فرستد. پذیرش این درخواست به معنی شروع Scan است؛
خود Scan در پس‌زمینه Jellyfin ادامه می‌یابد.

## تشخیص اپیزودهای جدید

هنگام اضافه‌شدن ویدیو به صف، بات شماره فصل و اپیزود را از نام فایل تشخیص
می‌دهد و فایل‌های موجود در فولدر سریال را بررسی می‌کند. نتیجه مشخص می‌کند که
اپیزود جدید است، از قبل در Library وجود دارد، یا قبلاً در صف ثبت شده است.
بات فایل تکراری را خودکار حذف نمی‌کند و تصمیم نهایی با کاربر می‌ماند.

دستور `/episodes` جزئیات یک سریال را همراه gapهای Missing نشان می‌دهد و
`/library_episodes` نمای کلی تمام سریال‌ها را می‌سازد. فولدرهای `_Unsorted` و
`_Conflicts` در موجودی قطعی اپیزودها محاسبه نمی‌شوند.

## تست‌ها

از پوشه مادر پروژه اجرا کنید:

```powershell
python -m unittest discover -s telegram_jellyfin_bot\tests -v
```

تست واقعی `getMe` فقط وقتی Local API و config آماده‌اند اجرا می‌شود:

```powershell
set RUN_LOCAL_API_TEST=1
python -m unittest telegram_jellyfin_bot.tests.test_core.LocalAPIIntegrationTest -v
```

## انتقال به سیستم دیگر

1. Python را نصب کنید.
2. پوشه پروژه و `telegram-bot-api.exe` را منتقل کنید.
3. `install.bat` را اجرا کنید.
4. `config.json` را تنظیم کنید.
5. ابتدا `run_local_bot_api.bat` و سپس `run.bat` را اجرا کنید.

فایل `config.json` و دیتابیس state را فقط از مسیر امن منتقل کنید.

## عیب‌یابی

- **Python پیدا نشد:** Python را دوباره با گزینه Add to PATH نصب کنید.
- **telegram-bot-api.exe پیدا نشد:** مسیر آن را در config اصلاح کنید.
- **Connection refused:** پنجره `run_local_bot_api.bat` باید باز باشد.
- **Unauthorized:** توکن اشتباه است یا بات از API عمومی logOut نشده است.
- **بات پیام کانال را نمی‌بیند:** دسترسی administrator و chat ID را بررسی کنید.
- **فایل وارد صف نمی‌شود:** پسوند و `allowed_video_extensions` را بررسی کنید.
- **sorter اجرا نمی‌شود:** مسیر Python و `organizer.py` در `sorter_command` را
  بررسی کنید.
- **فایل `.part` باقی مانده:** دانلود قطع شده؛ `/download` را دوباره اجرا کنید.

منابع رسمی: [Telegram Bot API](https://core.telegram.org/bots/api) و
[telegram-bot-api server](https://github.com/tdlib/telegram-bot-api).
