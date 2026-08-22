# How to Use the Telegram Jellyfin Bot

The same short guide is available inside Telegram with `/guide`. Use
`/language` to select English or فارسی for the current chat.

The bot has two ways to work:

1. **Normal — AI assisted:** select a library, send media, review the suggested
   identity and destination, confirm, and download.
2. **Advanced — manual and recovery tools:** correct a wrong identity, manage
   folders, sort or rename files, repair metadata, resolve conflicts, and undo
   changes. These tools do not depend on AI.

AI identification must be enabled in the deployment `.env`. If n8n is
unavailable, the bot keeps the item undownloaded and offers the existing manual
fallback instead of guessing or changing the selected library.

## English

### Main menu

Send `/menu`. The main buttons are:

- **Downloads** — inspect the queue, prepare downloads, confirm, or cancel.
- **Episodes** — see which episodes are already in a series or library.
- **Jellyfin** — scan the libraries or check the Jellyfin connection.
- **Bot** — status, language, command list, and this guide.
- **Choose Library** — choose the destination that this chat will remember.
- **Advanced** — open manual, correction, and recovery tools.

Private chats and groups receive these as a persistent keyboard. Channels get
the same choices as inline buttons. Opening a menu does not create a Telegram
topic, and replies remain in the topic where the request originated.

Each chat has independent settings, queue, confirmations, and history. Members
of one Telegram group share that group's state.

## Normal workflow — AI assisted

### 1. Choose the destination once

Press **Choose Library** and select one of:

- Animation Series
- Animation Movies
- Video Series
- Video Movies

The selection remains active for this chat until you change it. Selecting a
series library also selects series mode; selecting a movie library selects
movie mode. AI is never allowed to choose or change the library.

### 2. Send media

Send one or several supported videos. You can queue multiple files before
downloading them together. Episodes sent within a short burst are identified as
one compact batch. Mixed series are identified and routed independently.

When AI integration is enabled, the bot sends only the filename, optional
caption, media kind, and selected library key to the n8n identification
workflow. The AI suggests:

- movie title and year; or
- series title, season, and episode.

The existing IMDb fuzzy-search tool can then find the official Jellyfin folder
identity. If a reliable IMDb ID or unique exact-title match already exists in
the selected library, the bot uses that folder automatically. A new series is
confirmed once and all matching queued episodes share that answer. The AI does
not download, move, rename, delete, or scan anything.

### 3. Review and confirm

Before downloading, the compact review shows the filenames that will actually
be saved, together with file count and approximate size. Original release names
and internal temporary names are not used in this review.

- If correct, confirm it.
- If uncertain, the bot should ask one short question.
- If the AI or IMDb service is unavailable, use the offered manual name or open
  **Advanced**. Failure must not silently select a different library.

### 4. Download the queue

Open **Downloads**:

1. **Queue** — verify every item.
2. **Download** — build the final plan.
3. **Confirm** — start only after reviewing the destination.

The bot downloads incomplete data as `.part`, verifies the completed file, and
never overwrites silently. Movies are staged and then imported safely.
AI-confirmed series episodes are organized automatically after their downloads
finish. A successful automatic sort stays quiet; detailed output remains
available through `/sort_status` when troubleshooting is needed.

### 5. Refresh and check

- Open **Jellyfin → Scan library** when an automatic scan did not run.
- The bot updates one scan-status message. After showing that Jellyfin is ready,
  the completed status message is removed automatically.
- Open **Episodes** to check the current series or all series libraries.

You normally need to choose the library only again when you want a different
destination.

## Advanced workflow — no AI required

Open **Advanced** when identification is wrong, a filename is unusual, a folder
needs correction, or an interrupted operation needs repair.

### Folders

- **Current folder** or `/folder` — check the active series folder.
- **Pick existing** or `/folders` — add episodes to an existing series.
- `/setfolder NAME` — manually create/select a series folder.
- `/usefolder NAME` — select an existing folder by name.
- `/renamefolder NAME` — safely rename the current series folder.
- `/unsetfolder` — clear the current folder selection.

Choosing or changing a folder is for series. Movies remember their library and
receive their own folder during import.

### Manual title correction

- `/imdb_search NAME` — search for an official title manually.
- `/imdb_fix_current [NAME]` — repair the current series folder name.
- For a movie awaiting identification, choose **Enter name manually** and
  provide a title, optionally with a year.

Use manual input when the filename contains no usable title or the AI/IMDb
result is wrong. Always review the proposed folder name before confirming.

### Sorting and metadata repair

- `/sort_current` — organize new loose episodes in the current folder.
- `/sort_latest` — organize the latest downloaded series folder.
- `/sort_folder PATH` — run the sorter for a specific valid folder.
- `/resort_current` — rename previously sorted episodes after fixing the
  series folder name. The year and IMDb ID stay in the folder name, not in
  episode filenames.
- `/fix_metadata_current` — manually align episode NFO and artwork names.
- `/sort_status` — show the latest sorter result.

### Conflicts and queue repair

- `/remove ID` — remove one queued item.
- `/clearqueue` — remove eligible queued items for this chat.
- `/resolve ID skip` — leave an existing destination untouched.
- `/resolve ID save_with_suffix` — keep both files safely.
- `/resolve ID overwrite` — replace only after explicit confirmation.
- `/movie_current` — show the latest movie job.
- `/movie_import ID` — retry a movie that is safely waiting in staging.
- `/movie_cancel ID` — cancel an unprocessed movie job.

### Undo and recovery

- `/sort_history` — inspect current-folder revisions.
- `/sort_back` — undo one applied revision.
- `/sort_forward` — reapply one undone revision.
- `/undo_sort_batch ID` — undo a known sorter batch.
- `/movie_undo_last` — return the latest movie import to staging.
- `/movie_undo_batch ID` — undo a known movie-import batch.
- `/recover_current` — manually inspect and reconcile only the current series
  after a crash or power loss.

Undo never overwrites the original path. Keep every `.rename_history.json`
while rollback may be needed.

### Safety checklist

- Verify **Choose Library** before confirming a download.
- For manual series work, verify `/folder` before sorting or renaming.
- Use the bot's rename and IMDb-fix commands instead of renaming an organized
  series directly in the file manager.
- Do not delete staging files or history files while an import or undo may need
  them.
- If a result is uncertain, stop before download and use Advanced tools.

---

## فارسی

ربات دو روش استفاده دارد:

1. **عادی — با کمک هوش مصنوعی:** کتابخانه را انتخاب کنید، فایل را بفرستید،
   نتیجه و مقصد پیشنهادی را بررسی و تأیید کنید و سپس دانلود را شروع کنید.
2. **پیشرفته — ابزار دستی و بازیابی:** برای اصلاح تشخیص اشتباه، مدیریت پوشه،
   مرتب‌سازی، اصلاح متادیتا، حل تداخل و بازگردانی تغییرات. این ابزارها به هوش
   مصنوعی وابسته نیستند.

تشخیص AI باید در فایل `.env` استقرار فعال شود. اگر n8n در دسترس نباشد، ربات
فایل را دانلود نمی‌کند و به‌جای حدس زدن یا تغییر کتابخانه، روش دستی موجود را
پیشنهاد می‌دهد.

### منوی اصلی

دستور `/menu` را ارسال کنید. دکمه‌های اصلی عبارت‌اند از:

- **دانلودها** — مشاهده صف، آماده‌سازی، تأیید یا لغو دانلود.
- **قسمت‌ها** — مشاهده قسمت‌های موجود سریال‌ها.
- **Jellyfin** — اسکن کتابخانه یا بررسی اتصال.
- **ربات** — وضعیت، زبان، فهرست دستورها و راهنما.
- **انتخاب کتابخانه** — انتخاب مقصدی که برای همین چت ذخیره می‌شود.
- **پیشرفته** — ابزارهای دستی، اصلاح و بازیابی.

در چت خصوصی و گروه این گزینه‌ها به‌صورت صفحه‌کلید دائمی و در کانال به‌صورت
دکمه شیشه‌ای نمایش داده می‌شوند. ربات موضوع جدیدی ایجاد نمی‌کند و پاسخ را در
همان موضوعی می‌فرستد که درخواست از آن آمده است.

## روش عادی — با کمک هوش مصنوعی

### ۱. یک بار کتابخانه را انتخاب کنید

**انتخاب کتابخانه** را بزنید و یکی از چهار مقصد را انتخاب کنید:

- Animation Series
- Animation Movies
- Video Series
- Video Movies

این انتخاب تا زمانی که خودتان آن را تغییر ندهید برای همان چت باقی می‌ماند.
کتابخانه سریال حالت سریال و کتابخانه فیلم حالت فیلم را فعال می‌کند. هوش
مصنوعی اجازه انتخاب یا تغییر کتابخانه را ندارد.

### ۲. فایل‌ها را ارسال کنید

یک یا چند فایل ویدیویی پشتیبانی‌شده بفرستید. می‌توانید چند فایل را وارد صف
کنید و همه را با هم دانلود کنید. قسمت‌هایی که با فاصله کوتاه ارسال شوند در یک
دسته کم‌پیام بررسی می‌شوند. اگر فایل‌ها مربوط به چند سریال باشند، هر سریال
به‌صورت مستقل شناسایی و به پوشه درست هدایت می‌شود.

پس از فعال شدن اتصال AI، ربات فقط نام فایل، کپشن اختیاری، نوع رسانه و کلید
کتابخانه انتخاب‌شده را برای تشخیص به n8n می‌فرستد. نتیجه پیشنهادی شامل نام و
سال فیلم یا نام سریال و شماره فصل و قسمت است. سپس ابزار جستجوی تقریبی IMDb
می‌تواند نام رسمی مناسب Jellyfin را پیدا کند. هوش مصنوعی اجازه دانلود، جابه‌جایی،
تغییر نام، حذف یا اسکن را ندارد.

اگر پوشه‌ای با IMDb ID معتبر یا تطبیق یکتای نام دقیق در کتابخانه انتخاب‌شده
وجود داشته باشد، ربات بدون تأیید دوباره از همان پوشه استفاده می‌کند. برای یک
سریال جدید فقط یک بار تأیید گرفته می‌شود و همه قسمت‌های مطابق همان پاسخ را
استفاده می‌کنند.

### ۳. نتیجه را بررسی و تأیید کنید

پیش از دانلود، نام‌هایی که واقعاً در کتابخانه ذخیره خواهند شد، تعداد فایل‌ها
و حجم تقریبی را بررسی کنید. نام اولیه انتشار و نام موقت داخلی نمایش داده
نمی‌شوند.

- اگر درست بود تأیید کنید.
- اگر اطلاعات کافی نبود، ربات باید فقط یک سؤال کوتاه بپرسد.
- اگر AI یا IMDb در دسترس نبود، نام دستی را وارد کنید یا **پیشرفته** را باز
  کنید. خرابی سرویس نباید کتابخانه دیگری را خودکار انتخاب کند.

### ۴. دانلود را شروع کنید

در **دانلودها** به‌ترتیب **صف**، **دانلود** و سپس **تأیید** را بزنید. ربات
فایل ناقص را با پسوند `.part` نگه می‌دارد، فایل کامل را بررسی می‌کند و بدون
اجازه بازنویسی نمی‌کند. فیلم‌ها ابتدا وارد staging و سپس به‌صورت امن وارد
کتابخانه می‌شوند. قسمت‌های سریال که با AI تأیید شده‌اند، بعد از پایان دانلود
به‌صورت خودکار مرتب می‌شوند.
مرتب‌سازی خودکار موفق پیام طولانی نمی‌فرستد؛ در صورت نیاز جزئیات با
`/sort_status` در دسترس است.

### ۵. Jellyfin و قسمت‌ها را بررسی کنید

- اگر اسکن خودکار انجام نشد، **Jellyfin ← اسکن کتابخانه** را بزنید.
- ربات فقط یک پیام وضعیت اسکن را به‌روزرسانی می‌کند و پس از اعلام آماده بودن
  Jellyfin، آن پیام به‌صورت خودکار حذف می‌شود.
- از **قسمت‌ها** برای مشاهده سریال فعلی یا همه کتابخانه‌های سریال استفاده کنید.

تا وقتی مقصد تغییر نکرده است لازم نیست دوباره کتابخانه را انتخاب کنید.

## روش پیشرفته — بدون نیاز به هوش مصنوعی

وقتی تشخیص اشتباه است، نام فایل غیرعادی است، پوشه نیاز به اصلاح دارد یا یک
عملیات ناقص مانده است، **پیشرفته** را باز کنید.

### پوشه‌ها

- `/folder` — بررسی پوشه سریال فعلی.
- `/folders` — انتخاب یک سریال موجود برای افزودن قسمت جدید.
- `/setfolder NAME` — ساخت یا انتخاب دستی پوشه سریال.
- `/usefolder NAME` — انتخاب پوشه موجود با نام.
- `/renamefolder NAME` — تغییر امن نام پوشه سریال فعلی.
- `/unsetfolder` — پاک کردن انتخاب پوشه فعلی.

### اصلاح دستی عنوان

- `/imdb_search NAME` — جستجوی دستی عنوان رسمی.
- `/imdb_fix_current [NAME]` — اصلاح نام پوشه سریال فعلی.
- برای فیلمی که منتظر تشخیص است، **وارد کردن نام دستی** را انتخاب کنید و نام
  فیلم و در صورت نیاز سال را بنویسید.

### مرتب‌سازی و اصلاح متادیتا

- `/sort_current` — مرتب‌سازی قسمت‌های جدید پوشه فعلی.
- `/sort_latest` — مرتب‌سازی آخرین پوشه دانلودشده.
- `/sort_folder PATH` — مرتب‌سازی یک پوشه معتبر مشخص.
- `/resort_current` — اصلاح نام قسمت‌های قبلی بعد از اصلاح نام پوشه سریال.
- `/fix_metadata_current` — هماهنگ کردن دستی نام NFO و تصویرهای قسمت‌ها.
- `/sort_status` — نمایش نتیجه آخرین مرتب‌سازی.

### صف و حل تداخل

- `/remove ID` — حذف یک مورد صف.
- `/clearqueue` — پاک کردن موارد مجاز صف همین چت.
- `/resolve ID skip` — دست نزدن به فایل مقصد موجود.
- `/resolve ID save_with_suffix` — نگه داشتن امن هر دو فایل.
- `/resolve ID overwrite` — جایگزینی فقط پس از تأیید صریح.
- `/movie_current` — وضعیت آخرین عملیات فیلم.
- `/movie_import ID` — تلاش دوباره برای فیلم موجود در staging.
- `/movie_cancel ID` — لغو فیلم پردازش‌نشده.

### بازگردانی و بازیابی

- `/sort_history` — مشاهده نسخه‌های پوشه فعلی.
- `/sort_back` — یک نسخه به عقب.
- `/sort_forward` — اجرای دوباره نسخه بازگردانده‌شده.
- `/undo_sort_batch ID` — بازگردانی یک Batch ID مرتب‌سازی.
- `/movie_undo_last` — برگرداندن آخرین فیلم به staging.
- `/movie_undo_batch ID` — بازگردانی Batch ID فیلم.
- `/recover_current` — بررسی و اصلاح دستی فقط پوشه سریال فعلی پس از خاموشی یا
  توقف ناگهانی.

بازگردانی هیچ‌وقت مسیر اصلی موجود را بازنویسی نمی‌کند. تا زمانی که احتمال
نیاز به بازگردانی وجود دارد فایل‌های `.rename_history.json` را حذف نکنید.

### بررسی ایمنی

- پیش از تأیید دانلود، کتابخانه انتخاب‌شده را بررسی کنید.
- پیش از مرتب‌سازی یا تغییر نام دستی سریال، `/folder` را بررسی کنید.
- پوشه سریال مرتب‌شده را مستقیماً در فایل‌منیجر تغییر نام ندهید.
- تا وقتی انتقال یا بازگردانی ممکن است به staging یا history نیاز داشته باشد،
  آن فایل‌ها را حذف نکنید.
- اگر نتیجه نامطمئن است، پیش از دانلود توقف کنید و از ابزارهای پیشرفته استفاده
  کنید.
