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

The library choice is persistent. You do **not** choose a destination for every
upload. Change it only when the next files belong in another one of the four
libraries. Each queue item records the choice active when it arrived, so
changing the current library does not reroute older queued items.

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

For a burst of episodes, the normal chat pattern is compact:

1. the bot posts one identification-started message for the batch;
2. it checks each filename in sequence;
3. it edits that message into one title/episode ready/needs-attention summary;
4. it asks only for identities that cannot be routed safely.

When every item in the batch is ready, the summary ends with the clickable
`/download` command for the next step.

It does not send a separate confidence, temporary-name, IMDb-progress, and
sorter-success message for every episode. Those details remain available in
status/log output when a problem must be diagnosed.

### 3. Review and confirm

Before downloading, the compact review shows the filenames that will actually
be saved, together with file count and approximate size. Original release names
and internal temporary names are not used in this review.

- If correct, confirm it.
- If uncertain, the bot should ask one short question.
- If the AI or IMDb service is unavailable, use the offered manual name or open
  **Advanced**. Failure must not silently select a different library.

There are two separate approvals:

- **Identity approval:** required only for a new or uncertain title. One answer
  is shared by matching episodes in the same batch.
- **Download approval:** `/download` shows one plan for all ready queue items;
  `/confirm_download` starts that plan.

An existing series folder that matches by exact IMDb ID, exact canonical folder
name, or a unique normalized title is used automatically. Fuzzy score alone is
not enough to silently select an existing folder.

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
- The bot updates one scan-status message and keeps the final
  `✅ Jellyfin is ready.` confirmation visible in the chat.
- Open **Episodes** to check the current series or all series libraries.

You normally need to choose the library only again when you want a different
destination.

## Common upload scenarios

| What you send | What the bot does |
|---|---|
| One episode for an existing series | Reuses a reliable existing folder without asking again |
| Six episodes of one new series | Asks once for identity and includes all six in one download review |
| Mixed episodes from several series | Routes each identified series independently inside the selected series library |
| A filename with no usable title | Leaves it undownloaded and asks for manual information/current-folder fallback |
| One or several movies | Treats each as an independent identity job; all ready jobs can still be reviewed/downloaded from the queue |
| A destination file already exists | Stops that item for `skip`, `save_with_suffix`, or explicit `overwrite` |
| n8n or IMDb is unavailable | Keeps the item safe in the queue and offers manual tools instead of guessing |

Do not mix movies and episodes under one selected library. Choose the correct
movie or series library first; selecting the library automatically changes
mode.

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
- `/sort_folder NAME` — run the sorter for a named folder in the selected library.
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
- `/movie_cancel` — cancel the latest unprocessed movie job.

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

## Command quick reference

### Normal and information

- `/start` — choose a language on first use or reopen help/menu later.
- `/menu` — open the category menu.
- `/guide` — open the short in-bot English/Persian guide.
- `/language` — change the language saved for this chat.
- `/libraries` — choose a configured media library.
- `/use_library KEY` — choose a library directly by key.
- `/status` — show queue, partial-download, and background-task state.
- `/chatid` — show this Telegram chat ID.
- `/help` — show all commands and copyable templates.

### Downloads

- `/queue` — list active items owned by this chat.
- `/download` — build and display the real final download plan.
- `/confirm_download` — start the reviewed plan.
- `/remove ID` — remove one eligible queue item.
- `/clearqueue` — clear eligible queue items for this chat.
- `/cancel` — request cancellation of the current operation.
- `/resolve ID skip|save_with_suffix|overwrite` — resolve one destination
  conflict.

### Series folders and organization

- `/series_mode` — choose a series library.
- `/folder`, `/folders`, `/setfolder NAME`, `/usefolder NAME`,
  `/renamefolder NAME`, `/unsetfolder` — manual current-folder tools.
- `/sort_current`, `/sort_latest`, `/sort_folder NAME` — run the organizer.
- `/resort_current` — rename organized episode files after correcting the
  series folder identity.
- `/fix_metadata_current` — rename only matched episode NFO/artwork sidecars.
- `/sort_status` — show retained sorter diagnostics.
- `/episodes [NAME]`, `/library_episodes` — episode inventory.
- `/imdb_search NAME`, `/imdb_fix_current [NAME]` — manual IMDb identity tools.

### History, movies, and Jellyfin

- `/sort_history`, `/sort_back`, `/sort_forward` — navigate current-folder sort
  revisions.
- `/undo_sort_last`, `/undo_sort_batch ID` — restore sorter batches.
- `/recover_current` — reconcile a journaled interruption only in the current
  folder.
- `/movie_mode` — choose a movie library.
- `/movie_current`, `/movie_cancel` — inspect/cancel an unprocessed movie job.
- `/movie_import [ID]` — retry a completed movie still in staging.
- `/movie_undo_last`, `/movie_undo_batch ID` — return imported movies to
  staging.
- `/jellyfin_scan`, `/jellyfin_status` — request/inspect Jellyfin refresh.

Commands with names, IDs, or policies have copy buttons under `/help` and the
Advanced submenus. In channels, copy the template, paste it, and add the value.

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

انتخاب کتابخانه دائمی است و برای هر فایل دوباره پرسیده نمی‌شود. فقط زمانی آن
را تغییر دهید که فایل‌های بعدی باید وارد کتابخانه دیگری شوند. هر فایل صف،
کتابخانه فعال هنگام دریافت را نگه می‌دارد؛ تغییر انتخاب فعلی مقصد موارد قدیمی
صف را عوض نمی‌کند.

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

برای چند قسمت که پشت سر هم فرستاده شوند، روند پیام‌ها خلاصه است:

1. یک پیام شروع تشخیص برای کل دسته نمایش داده می‌شود؛
2. نام فایل‌ها به‌ترتیب بررسی می‌شوند؛
3. همان پیام به یک خلاصه نام/قسمت و آماده/نیازمند بررسی تبدیل می‌شود؛
4. فقط مواردی که مقصد امن ندارند سؤال جداگانه می‌گیرند.

وقتی همه موارد دسته آماده باشند، دستور قابل‌کلیک `/download` در پایان خلاصه
برای مرحله بعد نمایش داده می‌شود.

برای هر قسمت پیام جداگانه درصد اطمینان، نام موقت، پیشرفت IMDb و موفقیت sorter
فرستاده نمی‌شود. هنگام خطا، جزئیات از وضعیت و لاگ قابل بررسی است.

### ۳. نتیجه را بررسی و تأیید کنید

پیش از دانلود، نام‌هایی که واقعاً در کتابخانه ذخیره خواهند شد، تعداد فایل‌ها
و حجم تقریبی را بررسی کنید. نام اولیه انتشار و نام موقت داخلی نمایش داده
نمی‌شوند.

- اگر درست بود تأیید کنید.
- اگر اطلاعات کافی نبود، ربات باید فقط یک سؤال کوتاه بپرسد.
- اگر AI یا IMDb در دسترس نبود، نام دستی را وارد کنید یا **پیشرفته** را باز
  کنید. خرابی سرویس نباید کتابخانه دیگری را خودکار انتخاب کند.

دو نوع تأیید جدا وجود دارد:

- **تأیید هویت:** فقط برای عنوان جدید یا نامطمئن؛ قسمت‌های مطابق در همان دسته
  یک پاسخ مشترک می‌گیرند.
- **تأیید دانلود:** `/download` برنامه نهایی همه موارد آماده را نشان می‌دهد و
  `/confirm_download` همان برنامه را شروع می‌کند.

پوشه موجود فقط با IMDb ID دقیق، نام کامل دقیق یا تطبیق یکتای عنوان استفاده
می‌شود. امتیاز fuzzy به‌تنهایی اجازه انتخاب بی‌سؤال پوشه موجود را نمی‌دهد.

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
- ربات فقط یک پیام وضعیت اسکن را به‌روزرسانی می‌کند و پیام نهایی
  `✅ Jellyfin آماده است.` را در چت نگه می‌دارد.
- از **قسمت‌ها** برای مشاهده سریال فعلی یا همه کتابخانه‌های سریال استفاده کنید.

تا وقتی مقصد تغییر نکرده است لازم نیست دوباره کتابخانه را انتخاب کنید.

## حالت‌های رایج ارسال فایل

| فایل ارسالی | رفتار ربات |
|---|---|
| یک قسمت از سریال موجود | استفاده خودکار از پوشه‌ای که تطبیق مطمئن دارد |
| شش قسمت از یک سریال جدید | یک تأیید برای هویت و یک بررسی دانلود برای هر شش فایل |
| قسمت‌های مخلوط چند سریال | هدایت مستقل هر سریال در کتابخانه سریال انتخاب‌شده |
| فایل بدون نام قابل تشخیص | نگه داشتن در صف و درخواست اطلاعات دستی یا پوشه فعلی |
| یک یا چند فیلم | هویت مستقل برای هر فیلم؛ امکان بررسی و دانلود همه موارد آماده از صف |
| فایل مقصد موجود است | توقف همان مورد برای `skip`، `save_with_suffix` یا `overwrite` صریح |
| n8n یا IMDb قطع است | عدم حدس زدن و ارائه روش دستی پیش از دانلود |

فیلم و قسمت سریال را زیر یک کتابخانه انتخاب‌شده مخلوط نکنید. ابتدا کتابخانه
درست فیلم یا سریال را انتخاب کنید؛ انتخاب کتابخانه حالت را نیز تغییر می‌دهد.

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
- `/sort_folder NAME` — مرتب‌سازی پوشه نام‌برده در کتابخانه انتخاب‌شده.
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
- `/movie_cancel` — لغو آخرین فیلم پردازش‌نشده.

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

## فهرست سریع دستورها

### عادی و اطلاعات

- `/start` شروع/بازکردن راهنما؛ `/menu` منوی دسته‌بندی؛ `/guide` راهنمای داخل
  ربات؛ `/language` زبان چت.
- `/libraries` انتخاب کتابخانه؛ `/use_library KEY` انتخاب مستقیم با کلید.
- `/status` وضعیت صف و کارها؛ `/chatid` شناسه چت؛ `/help` فهرست و دکمه کپی.

### دانلود

- `/queue` صف؛ `/download` برنامه نام و مقصد نهایی؛ `/confirm_download` شروع.
- `/remove ID` حذف یک مورد؛ `/clearqueue` پاک کردن موارد مجاز؛ `/cancel` لغو.
- `/resolve ID skip|save_with_suffix|overwrite` حل تداخل مقصد.

### سریال و مرتب‌سازی

- `/series_mode` انتخاب کتابخانه سریال.
- `/folder`، `/folders`، `/setfolder NAME`، `/usefolder NAME`،
  `/renamefolder NAME` و `/unsetfolder` ابزارهای دستی پوشه.
- `/sort_current`، `/sort_latest` و `/sort_folder NAME` اجرای مرتب‌ساز.
- `/resort_current` اصلاح نام قسمت‌های قبلی بعد از اصلاح نام پوشه.
- `/fix_metadata_current` اصلاح فقط NFO و تصویرهای قسمت قابل تطبیق.
- `/sort_status` جزئیات مرتب‌ساز؛ `/episodes [NAME]` و `/library_episodes` موجودی.
- `/imdb_search NAME` و `/imdb_fix_current [NAME]` اصلاح دستی IMDb.

### تاریخچه، فیلم و Jellyfin

- `/sort_history`، `/sort_back`، `/sort_forward` حرکت بین نسخه‌های مرتب‌سازی.
- `/undo_sort_last` و `/undo_sort_batch ID` بازگردانی؛ `/recover_current`
  بازیابی فقط پوشه فعلی.
- `/movie_mode` انتخاب کتابخانه فیلم؛ `/movie_current` و `/movie_cancel` وضعیت
  یا لغو؛ `/movie_import [ID]` تلاش دوباره از staging.
- `/movie_undo_last` و `/movie_undo_batch ID` بازگردانی فیلم به staging.
- `/jellyfin_scan` و `/jellyfin_status` شروع و بررسی اسکن Jellyfin.

برای دستورهایی که نام، ID یا policy لازم دارند از دکمه‌های کپی در `/help` یا
منوی پیشرفته استفاده کنید. در کانال، متن را کپی و paste کنید و مقدار را در
انتهای آن بنویسید.
