# How to Use the Telegram Jellyfin Bot

The same guide is available inside Telegram with `/guide`.

Use `/language` at any time to choose English or فارسی for the current chat.
The bot remembers the choice after a restart.

## English

### 1. Start the services

On the computer running the bot:

1. Start `run_local_bot_api.bat` and leave it open.
2. Start `run.bat` and leave it open.

### 2. Open the controls

Send `/menu`. Private chats, groups, and supergroups receive the persistent
category keyboard. Telegram does not support that keyboard in channels, so a
channel uses the **Command categories** inline button instead.

### 3. Select a series

- Send `/folders` to select an existing series.
- Send `/setfolder SERIES NAME` to search for and create a new series folder.
- Confirm the IMDb suggestion or select the manual name.
- Send `/folder` to verify the current selection.

Every chat has its own current folder, queue, confirmations, status, and undo
batches. No chat can remove, cancel, download, or undo another chat's items.
Members inside one Telegram group share that group's state.

### 4. Queue and download episodes

Send supported video files to the authorized bot chat or channel. Then use:

```text
/queue
/download
/confirm_download
```

If the destination exists, use:

```text
/resolve ID skip
/resolve ID overwrite
/resolve ID save_with_suffix
```

### 5. Organize the series

- `/sort_current` sorts new loose files.
- `/resort_current` corrects previously sorted episode names after the series
  folder has been renamed.
- `/fix_metadata_current` manually aligns episode NFO and artwork names.
- `/sort_history` shows the current series revisions.

Afterward, use `/jellyfin_scan`. The bot monitors Jellyfin and sends another
message when the scan completes and the latest library state is ready. Use
`/jellyfin_status` to check live progress.

### 6. Check episodes

- `/episodes` shows the current series.
- `/episodes NAME` shows another series.
- `/library_episodes` summarizes the complete library.

### 7. Undo and recover

- `/sort_back` undoes the latest applied current-series revision.
- `/sort_forward` reapplies an undone revision.
- `/undo_sort_batch ID` undoes one technical batch.
- `/recover_current` manually checks only the current series for operations
  interrupted by a crash or power loss.

Always check `/folder` first. Do not delete `.rename_history.json` files while
you need rollback, and use `/renamefolder` or `/imdb_fix_current` instead of
renaming an organized series directly in Windows Explorer.

### 8. Import a movie

Movie mode uses a separate Jellyfin Movies library and does not use the current
series folder.

1. Send `/movie_mode`, then send one movie video.
2. Choose filename search or manual search.
3. Select the correct IMDb result and confirm the preview. If IMDb is
   unavailable during manual search, you can confirm your manual title.
4. Send `/download`, then `/confirm_download`.
5. The bot downloads to staging, verifies the import plan, moves the movie with
   no overwrite, and can scan Jellyfin automatically.

Use `/movie_current` for its status, `/movie_import ID` to retry a staged file,
and `/movie_undo_last` to return the latest import to staging. Send
`/series_mode` before sending episodes again.

---

## فارسی

### ۱. اجرای سرویس‌ها

روی کامپیوتری که ربات را اجرا می‌کند:

1. فایل `run_local_bot_api.bat` را اجرا کنید و باز نگه دارید.
2. فایل `run.bat` را اجرا کنید و باز نگه دارید.

### ۲. باز کردن کنترل‌ها

دستور `/menu` را ارسال کنید. در گفت‌وگوی خصوصی، گروه و سوپرگروه، صفحه‌کلید
دائمی دسته‌بندی‌ها نمایش داده می‌شود. تلگرام این صفحه‌کلید را در کانال
پشتیبانی نمی‌کند؛ بنابراین در کانال از دکمه **Command categories** استفاده
کنید.

### ۳. انتخاب سریال

- برای انتخاب یک سریال موجود، `/folders` را ارسال کنید.
- برای جستجو و ساخت پوشه جدید، `/setfolder SERIES NAME` را ارسال کنید.
- پیشنهاد IMDb را تأیید کنید یا نام دستی را انتخاب کنید.
- با `/folder` انتخاب فعلی را بررسی کنید.

هر چت پوشه فعلی، صف، تأییدها، وضعیت و دسته‌های بازگردانی مستقل خودش را دارد.
هیچ چتی نمی‌تواند موارد چت دیگر را حذف، لغو، دانلود یا بازگردانی کند. اعضای
یک گروه تلگرام، وضعیت همان گروه را با یکدیگر به اشتراک می‌گذارند.

### ۴. صف و دانلود قسمت‌ها

فایل‌های ویدیویی پشتیبانی‌شده را در چت یا کانال مجاز ارسال کنید. سپس:

```text
/queue
/download
/confirm_download
```

اگر فایل مقصد از قبل وجود داشت:

```text
/resolve ID skip
/resolve ID overwrite
/resolve ID save_with_suffix
```

### ۵. مرتب‌سازی سریال

- `/sort_current` فایل‌های جدید و مرتب‌نشده را مرتب می‌کند.
- `/resort_current` بعد از اصلاح نام پوشه سریال، نام قسمت‌های قبلی را اصلاح
  می‌کند.
- `/fix_metadata_current` نام NFO و تصویرهای مربوط به قسمت‌ها را به‌صورت دستی
  هماهنگ می‌کند.
- `/sort_history` نسخه‌های مرتب‌سازی سریال فعلی را نمایش می‌دهد.

بعد از آن از `/jellyfin_scan` استفاده کنید. ربات وضعیت Jellyfin را بررسی
می‌کند و پس از پایان اسکن و آماده شدن آخرین وضعیت کتابخانه، پیام دیگری
می‌فرستد. برای مشاهده پیشرفت زنده از `/jellyfin_status` استفاده کنید.

### ۶. بررسی قسمت‌ها

- `/episodes` قسمت‌های سریال فعلی را نشان می‌دهد.
- `/episodes NAME` قسمت‌های یک سریال دیگر را نشان می‌دهد.
- `/library_episodes` خلاصه کل کتابخانه را نمایش می‌دهد.

### ۷. بازگردانی و بازیابی

- `/sort_back` آخرین نسخه اعمال‌شده در سریال فعلی را برمی‌گرداند.
- `/sort_forward` نسخه بازگردانده‌شده را دوباره اعمال می‌کند.
- `/undo_sort_batch ID` یک Batch ID مشخص را برمی‌گرداند.
- `/recover_current` فقط پوشه سریال فعلی را برای عملیات ناقص پس از خاموشی یا
  توقف ناگهانی بررسی می‌کند.

همیشه ابتدا `/folder` را بررسی کنید. تا زمانی که به قابلیت بازگردانی نیاز
دارید، فایل‌های `.rename_history.json` را حذف نکنید. برای تغییر نام سریال
مرتب‌شده از `/renamefolder` یا `/imdb_fix_current` استفاده کنید و پوشه را
مستقیماً در Windows Explorer تغییر نام ندهید.

### ۸. وارد کردن فیلم

حالت فیلم از کتابخانه جداگانه Movies استفاده می‌کند و به پوشه سریال فعلی
وابسته نیست.

1. دستور `/movie_mode` را بفرستید و سپس یک فایل فیلم ارسال کنید.
2. جستجو با نام فایل یا جستجوی دستی را انتخاب کنید.
3. نتیجه صحیح IMDb را انتخاب و پیش‌نمایش نام را تأیید کنید. اگر IMDb هنگام
   جستجوی دستی در دسترس نبود، می‌توانید همان نام دستی را تأیید کنید.
4. ابتدا `/download` و سپس `/confirm_download` را ارسال کنید.
5. ربات فیلم را ابتدا در staging دانلود می‌کند، برنامه انتقال را بررسی می‌کند،
   بدون بازنویسی وارد کتابخانه می‌کند و در صورت تنظیم بودن Jellyfin را اسکن
   می‌کند.

برای دیدن وضعیت از `/movie_current`، برای تلاش دوباره فایل staging از
`/movie_import ID` و برای برگرداندن آخرین انتقال از `/movie_undo_last` استفاده
کنید. پیش از فرستادن قسمت‌های سریال دوباره `/series_mode` را بفرستید.
