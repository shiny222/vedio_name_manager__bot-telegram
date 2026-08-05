"""Small offline English/Persian localization helpers for the Telegram UI."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


LANGUAGE_MENU = {
    "inline_keyboard": [[
        {"text": "🇬🇧 English", "callback_data": "language:en"},
        {"text": "🇮🇷 فارسی", "callback_data": "language:fa"},
    ]]
}


BUTTON_FA = {
    "All series": "همه سریال‌ها",
    "Cancel": "لغو",
    "Cancel movie": "لغو فیلم",
    "Cancel unprocessed movie": "لغو فیلم پردازش‌نشده",
    "Choose a bot category or send a video…": "یک دسته را انتخاب کنید یا ویدیو بفرستید…",
    "Clear selection": "پاک کردن انتخاب",
    "Confirm movie": "تأیید فیلم",
    "Copy /movie_import": "کپی /movie_import",
    "Copy /movie_undo_batch": "کپی /movie_undo_batch",
    "Current series": "سریال فعلی",
    "Enter movie mode": "ورود به حالت فیلم",
    "Enter name manually": "وارد کردن نام دستی",
    "Fix episode metadata": "اصلاح نام متادیتای قسمت‌ها",
    "Latest movie job": "آخرین عملیات فیلم",
    "Movie mode": "حالت فیلم",
    "Movies": "فیلم‌ها",
    "Next ➡️": "بعدی ➡️",
    "One revision back": "یک نسخه به عقب",
    "One revision forward": "یک نسخه به جلو",
    "Recover current": "بازیابی پوشه فعلی",
    "Recover current folder": "بازیابی پوشه فعلی",
    "Rename sorted files": "تغییر نام فایل‌های مرتب‌شده",
    "Retry import": "تلاش دوباره برای انتقال",
    "Search manually": "جستجوی دستی",
    "Search using filename": "جستجو با نام فایل",
    "Search with a different name": "جستجو با نام دیگر",
    "Series mode": "حالت سریال",
    "Sort back": "بازگشت مرتب‌سازی",
    "Sort forward": "اجرای دوباره مرتب‌سازی",
    "Sort history": "تاریخچه مرتب‌سازی",
    "Sort latest": "مرتب‌سازی آخرین دانلود",
    "Sort new files": "مرتب‌سازی فایل‌های جدید",
    "Sorter status": "وضعیت مرتب‌ساز",
    "Undo latest batch": "بازگردانی آخرین دسته",
    "Undo latest movie": "بازگردانی آخرین فیلم",
    "↩️ Undo & Recovery": "↩️ بازگردانی و بازیابی",
    "↩️ Undo latest sort": "↩️ بازگردانی آخرین مرتب‌سازی",
    "⚙️ Bot": "⚙️ ربات",
    "⚡ Quick Menu": "⚡ منوی سریع",
    "⚡ Quick menu": "⚡ منوی سریع",
    "⛔ Cancel": "⛔ لغو",
    "✅ Confirm": "✅ تأیید",
    "✅ Confirm download": "✅ تأیید دانلود",
    "✏️ Set/rename folder": "✏️ تنظیم یا تغییر نام پوشه",
    "❌ Cancel": "❌ لغو",
    "❓ Command list": "❓ فهرست دستورها",
    "❓ Help": "❓ راهنما",
    "⬅️ Categories": "⬅️ دسته‌ها",
    "⬅️ Previous": "⬅️ قبلی",
    "⬇️ Download": "⬇️ دانلود",
    "🆔 Chat ID": "🆔 شناسه چت",
    "🎛 Main menu": "🎛 منوی اصلی",
    "🎛 Open main menu": "🎛 باز کردن منوی اصلی",
    "🎞 Episodes": "🎞 قسمت‌ها",
    "🎬 Jellyfin": "🎬 Jellyfin",
    "📁 Current folder": "📁 پوشه فعلی",
    "📁 Folders": "📁 پوشه‌ها",
    "📊 Status": "📊 وضعیت",
    "📋 Copy /episodes": "📋 کپی /episodes",
    "📋 Copy /episodes NAME": "📋 کپی /episodes NAME",
    "📋 Copy /imdb_fix_current": "📋 کپی /imdb_fix_current",
    "📋 Copy /imdb_search": "📋 کپی /imdb_search",
    "📋 Copy /remove": "📋 کپی /remove",
    "📋 Copy /renamefolder": "📋 کپی /renamefolder",
    "📋 Copy /resolve": "📋 کپی /resolve",
    "📋 Copy /setfolder": "📋 کپی /setfolder",
    "📋 Copy /sort_folder": "📋 کپی /sort_folder",
    "📋 Copy /undo_sort_batch": "📋 کپی /undo_sort_batch",
    "📋 Copy /usefolder": "📋 کپی /usefolder",
    "📋 Queue": "📋 صف",
    "📖 How to use": "📖 روش استفاده",
    "📖 How to use (English / فارسی)": "📖 روش استفاده و انتخاب زبان",
    "📚 All series": "📚 همه سریال‌ها",
    "📥 Downloads": "📥 دانلودها",
    "📺 Episodes": "📺 قسمت‌ها",
    "🔄 Scan Jellyfin": "🔄 اسکن Jellyfin",
    "🔄 Scan library": "🔄 اسکن کتابخانه",
    "🔎 IMDb": "🔎 IMDb",
    "🔎 IMDb title search": "🔎 جستجوی نام در IMDb",
    "🔢 Undo by batch ID": "🔢 بازگردانی با Batch ID",
    "🗂 Command categories": "🗂 دسته‌بندی دستورها",
    "🗂 Pick existing": "🗂 انتخاب پوشه موجود",
    "🗂 Pick existing folder": "🗂 انتخاب پوشه موجود",
    "🗑 Clear queue": "🗑 پاک کردن صف",
    "🟢 Connection status": "🟢 وضعیت اتصال",
    "🟢 Jellyfin Status": "🟢 وضعیت Jellyfin",
    "🧹 Sort current": "🧹 مرتب‌سازی پوشه فعلی",
    "🧹 Sort latest": "🧹 مرتب‌سازی آخرین دانلود",
    "🧹 Sorting": "🧹 مرتب‌سازی",
    "🌐 Language": "🌐 زبان",
}


# These are sentence fragments rather than individual English words, so paths,
# filenames, titles, command names, and technical exception details stay intact.
TEXT_FA = {
    "Language changed to Persian.": "زبان ربات به فارسی تغییر کرد.",
    "Language changed to English.": "زبان ربات به انگلیسی تغییر کرد.",
    "Choose your language:\nزبان خود را انتخاب کنید:": "زبان خود را انتخاب کنید:\nChoose your language:",
    "Choose the guide language:\nزبان راهنما را انتخاب کنید:": "زبان راهنما را انتخاب کنید:\nChoose the guide language:",
    "Persistent category keyboard enabled below the message box.": "صفحه‌کلید دائمی دسته‌بندی زیر کادر پیام فعال شد.",
    "Category keyboard enabled. Choose a category below, or keep using slash commands.": "صفحه‌کلید دسته‌بندی فعال شد. یک دسته را انتخاب کنید یا از دستورهای / استفاده کنید.",
    "Download and sorting control menu:": "منوی کنترل دانلود و مرتب‌سازی:",
    "Choose a command category:": "یک دسته دستور را انتخاب کنید:",
    "Download commands:": "دستورهای دانلود:",
    "Folder commands:": "دستورهای پوشه:",
    "Sorting commands:": "دستورهای مرتب‌سازی:",
    "Undo and recovery commands:": "دستورهای بازگردانی و بازیابی:",
    "Independent movie workflow:": "ابزار مستقل فیلم:",
    "Jellyfin commands:": "دستورهای Jellyfin:",
    "IMDb fuzzy-search commands:": "دستورهای جستجوی تقریبی IMDb:",
    "Episode inventory commands:": "دستورهای فهرست قسمت‌ها:",
    "Bot information and help:": "اطلاعات و راهنمای ربات:",
    "To set a folder:\n/setfolder My Anime\n\nTo rename the current folder:\n/renamefolder Correct Anime Name\n\nThe buttons below copy editable command templates. Paste one, then add the name.": "برای تنظیم پوشه:\n/setfolder My Anime\n\nبرای تغییر نام پوشه فعلی:\n/renamefolder Correct Anime Name\n\nدکمه‌های زیر الگوهای قابل‌ویرایش دستورها را کپی می‌کنند. یکی را جای‌گذاری کنید و سپس نام را اضافه کنید.",
    "To undo a specific sorter batch:\n/undo_sort_batch BATCH_ID\n\nExample:\n/undo_sort_batch 20260628-024900-a1b2c3d4": "برای بازگردانی یک دسته مشخص مرتب‌سازی:\n/undo_sort_batch BATCH_ID\n\nمثال:\n/undo_sort_batch 20260628-024900-a1b2c3d4",
    "To find the official name and create a Jellyfin folder:\n/imdb_search dr ston\n\nTo search and safely rename the current folder:\n/imdb_fix_current\n\nYou can also provide a different search phrase:\n/imdb_fix_current dr ston": "برای پیدا کردن نام رسمی و ساخت پوشه Jellyfin:\n/imdb_search dr ston\n\nبرای جستجو و تغییر امن نام پوشه فعلی:\n/imdb_fix_current\n\nمی‌توانید عبارت جستجوی دیگری هم وارد کنید:\n/imdb_fix_current dr ston",
    "Unknown command. Send /help.": "دستور ناشناخته است. /help را ارسال کنید.",
    "Correct format:": "فرمت صحیح:",
    "Current target folder:": "پوشه مقصد فعلی:",
    "Target folder cleared.": "پوشه مقصد پاک شد.",
    "The queue is empty.": "صف خالی است.",
    "waiting for movie identification": "منتظر تشخیص فیلم",
    "no folder": "بدون پوشه",
    "more file(s)": "فایل دیگر",
    "Removed from the queue.": "از صف حذف شد.",
    "No removable item was found.": "مورد قابل حذفی پیدا نشد.",
    "Removed ": "حذف شد: ",
    " item(s) from the queue.": " مورد از صف.",
    "Queue (": "صف (",
    "Queue ID for commands:": "شناسه صف برای دستورها:",
    "Queue ID:": "شناسه صف:",
    "Video added to the queue.": "ویدیو به صف اضافه شد.",
    "This video is already in the queue.": "این ویدیو از قبل در صف است.",
    "This video file is not supported and was not added to the queue.": "این فایل ویدیویی پشتیبانی نمی‌شود و به صف اضافه نشد.",
    "This file extension is not allowed in allowed_video_extensions.": "پسوند این فایل در allowed_video_extensions مجاز نیست.",
    "The file was not added to the queue:": "فایل به صف اضافه نشد:",
    "There are no ready files in the queue.": "فایل آماده‌ای در صف وجود ندارد.",
    "There are no ready files to download.": "فایل آماده‌ای برای دانلود وجود ندارد.",
    "There is no unconfirmed download request for this chat.": "درخواست دانلود تأییدنشده‌ای برای این چت وجود ندارد.",
    "These files do not have a target folder:": "این فایل‌ها پوشه مقصد ندارند:",
    "Final download destination:": "مقصد نهایی دانلود:",
    "Approx size:": "حجم تقریبی:",
    "Count:": "تعداد:",
    "Send /confirm_download to start, or /cancel.": "برای شروع /confirm_download و برای لغو /cancel را ارسال کنید.",
    "Download started.": "دانلود شروع شد.",
    "Download completed:": "دانلود کامل شد:",
    "Downloads finished.": "دانلودها پایان یافت.",
    "File already exists.": "فایل از قبل وجود دارد.",
    "already exists:": "از قبل وجود دارد:",
    "Send one of these:": "یکی از این دستورها را ارسال کنید:",
    "skipped; it already exists.": "رد شد؛ از قبل وجود دارد.",
    "No files were completed or imported. Fix the reported error, then use /download to retry.": "هیچ فایلی کامل یا منتقل نشد. خطای گزارش‌شده را رفع کنید و برای تلاش دوباره /download را بفرستید.",
    "Completed movies will now be imported automatically.": "فیلم‌های کامل‌شده اکنون به‌صورت خودکار منتقل می‌شوند.",
    "Some files did not complete; use /download to retry them.": "برخی فایل‌ها کامل نشدند؛ برای تلاش دوباره /download را بفرستید.",
    "Use /sort_latest to organize the latest downloaded series folder.": "برای مرتب‌سازی آخرین پوشه سریال دانلودشده از /sort_latest استفاده کنید.",
    "Download error for file": "خطای دانلود فایل",
    "Download timeout for file": "پایان مهلت دانلود فایل",
    "Telegram stopped sending data for": "تلگرام ارسال داده را متوقف کرد؛ مدت انتظار:",
    "Any incomplete .part file was kept; use /download to retry.": "هر فایل ناقص .part نگه داشته شد؛ برای تلاش دوباره /download را بفرستید.",
    "Operation cancelled.": "عملیات لغو شد.",
    "Cancel request registered.": "درخواست لغو ثبت شد.",
    "There is no active operation.": "عملیات فعالی وجود ندارد.",
    "Another download is already running.": "یک دانلود دیگر در حال اجراست.",
    "A download is already running.": "یک دانلود در حال اجراست.",
    "A download is already running for this chat.": "یک دانلود برای این چت در حال اجراست.",
    "The downloader is busy with another chat. Your queue was not changed.": "دانلودر مشغول کار برای چت دیگری است. صف شما تغییر نکرد.",
    "The downloader is busy. Your confirmation is still saved; try again shortly.": "دانلودر مشغول است. تأیید شما ذخیره مانده است؛ کمی بعد دوباره تلاش کنید.",
    "Wait for the current download to finish first.": "ابتدا منتظر پایان دانلود فعلی بمانید.",
    "Movie mode enabled.": "حالت فیلم فعال شد.",
    "Movie mode is disabled.": "حالت فیلم غیرفعال است.",
    "Movie mode is not configured.": "حالت فیلم تنظیم نشده است.",
    "Movie mode is not configured yet.": "حالت فیلم هنوز تنظیم نشده است.",
    "Series mode enabled.": "حالت سریال فعال شد.",
    "Send one movie video.": "یک ویدیوی فیلم ارسال کنید.",
    "Movie received but not downloaded yet.": "فیلم دریافت شد اما هنوز دانلود نشده است.",
    "How should I identify it?": "نام فیلم چگونه تشخیص داده شود؟",
    "Filename:": "نام فایل:",
    "Movie identity confirmed.": "مشخصات فیلم تأیید شد.",
    "Use /download to review the destination, then /confirm_download.": "برای بررسی مقصد /download و سپس برای شروع /confirm_download را بفرستید.",
    "Searching IMDb movies for:": "در حال جستجوی فیلم در IMDb برای:",
    "Choose the correct movie result.": "نتیجه صحیح فیلم را انتخاب کنید.",
    "Source:": "منبع:",
    "Confirm this movie identity:": "مشخصات این فیلم را تأیید کنید:",
    "Title:": "عنوان:",
    "Year:": "سال:",
    "not specified": "مشخص نشده",
    "not available": "در دسترس نیست",
    "Folder:": "پوشه:",
    "File:": "فایل:",
    "Send the movie title, preferably with its year.": "نام فیلم را ترجیحاً همراه سال ارسال کنید.",
    "Send a different movie title, preferably with its year.": "نام دیگری برای فیلم، ترجیحاً همراه سال، ارسال کنید.",
    "The filename did not contain a useful movie title.": "نام فایل شامل عنوان قابل‌استفاده‌ای برای فیلم نبود.",
    "IMDb did not recognize the filename.": "IMDb نام فایل را تشخیص نداد.",
    "IMDb did not return a movie result.": "IMDb نتیجه‌ای برای فیلم برنگرداند.",
    "IMDb search is unavailable:": "جستجوی IMDb در دسترس نیست:",
    "This movie is already registered in the queue.": "این فیلم از قبل در صف ثبت شده است.",
    "This movie is no longer waiting for identification.": "این فیلم دیگر منتظر تشخیص نام نیست.",
    "This movie choice is no longer active.": "این انتخاب فیلم دیگر فعال نیست.",
    "This movie result expired.": "نتیجه جستجوی فیلم منقضی شد.",
    "This movie confirmation expired.": "مهلت تأیید فیلم تمام شد.",
    "This movie is no longer waiting for a name.": "این فیلم دیگر منتظر نام نیست.",
    "Movie queue item cancelled.": "مورد فیلم از صف لغو شد.",
    "Movie could not be cancelled.": "لغو فیلم ممکن نبود.",
    "Current movie job cancelled.": "عملیات فعلی فیلم لغو شد.",
    "The movie job could not be cancelled.": "لغو عملیات فیلم ممکن نبود.",
    "There is no removable current movie job.": "عملیات فیلم قابل‌حذفی وجود ندارد.",
    "No movie job exists yet.": "هنوز عملیات فیلمی وجود ندارد.",
    "Latest movie queue ID:": "شناسه آخرین فیلم در صف:",
    "Current mode:": "حالت فعلی:",
    "Movie:": "فیلم:",
    "Original file:": "فایل اصلی:",
    "waiting for identification": "منتظر تشخیص نام",
    "Checking movie import plan for queue ID": "در حال بررسی برنامه انتقال فیلم با شناسه صف",
    "Movie import plan verified. Importing without overwriting:": "برنامه انتقال فیلم تأیید شد. انتقال بدون بازنویسی:",
    "Movie imported successfully.": "فیلم با موفقیت منتقل شد.",
    "Destination:": "مقصد:",
    "Batch ID:": "شناسه دسته:",
    "This movie job is closed; send another movie while remaining in movie mode.": "این عملیات فیلم بسته شد؛ در صورت باقی ماندن در حالت فیلم، فیلم بعدی را ارسال کنید.",
    "Movie download is safe in staging, but import failed for queue ID": "دانلود فیلم در staging امن است، اما انتقال برای شناسه صف ناموفق بود",
    "Fix the problem and use /movie_import": "مشکل را رفع کنید و از /movie_import استفاده کنید",
    " to retry.": " را برای تلاش دوباره اجرا کنید.",
    "No downloaded movie is waiting for import.": "فیلم دانلودشده‌ای منتظر انتقال نیست.",
    "The staged movie file is missing:": "فایل فیلم در staging پیدا نشد:",
    "Movie undo started:": "بازگردانی فیلم شروع شد:",
    "latest movie batch": "آخرین دسته فیلم",
    "Movie undo completed.": "بازگردانی فیلم کامل شد.",
    "Movie undo was incomplete; conflicting files were skipped.": "بازگردانی فیلم ناقص بود؛ فایل‌های متعارض رد شدند.",
    "Restored:": "بازگردانده‌شده:",
    "Skipped:": "ردشده:",
    "Movie undo failed:": "بازگردانی فیلم ناموفق بود:",
    "Movies cannot be restored while a download is running.": "هنگام اجرای دانلود نمی‌توان فیلم‌ها را بازگرداند.",
    "This chat has no imported movie batch to undo.": "این چت دسته فیلم منتقل‌شده‌ای برای بازگردانی ندارد.",
    "That movie batch does not belong to this chat.": "آن دسته فیلم متعلق به این چت نیست.",
    "Status:": "وضعیت:",
    "Tracked background tasks:": "کارهای پس‌زمینه در حال پیگیری:",
    "Incomplete .part files:": "فایل‌های ناقص .part:",
    "No files have been registered yet.": "هنوز فایلی ثبت نشده است.",
    "Jellyfin accepted the scan request.": "Jellyfin درخواست اسکن را پذیرفت.",
    "I will notify you when the library scan finishes.": "پس از پایان اسکن کتابخانه به شما اطلاع می‌دهم.",
    "I will monitor it and notify you when it finishes.": "آن را پیگیری می‌کنم و پس از پایان به شما اطلاع می‌دهم.",
    "Jellyfin library scan completed": "اسکن کتابخانه Jellyfin کامل شد",
    "Jellyfin connection is not configured.": "اتصال Jellyfin تنظیم نشده است.",
    "Jellyfin connection is not ready yet.": "اتصال Jellyfin هنوز آماده نیست.",
    "Searching IMDb for:": "در حال جستجو در IMDb برای:",
    "Folder renamed:": "نام پوشه تغییر کرد:",
    "Folder rename failed:": "تغییر نام پوشه ناموفق بود:",
    "No current folder is selected.": "هیچ پوشه فعلی انتخاب نشده است.",
    "No current folder is selected. Use /folders or /setfolder first.": "هیچ پوشه فعلی انتخاب نشده است. ابتدا از /folders یا /setfolder استفاده کنید.",
    "No current folder is set. Use /setfolder first.": "پوشه فعلی تنظیم نشده است. ابتدا /setfolder را اجرا کنید.",
    "No target folder is set. Use /setfolder NAME": "پوشه مقصد تنظیم نشده است. از /setfolder NAME استفاده کنید.",
    "No existing series folders were found.": "پوشه سریال موجودی پیدا نشد.",
    "No series folders were found inside the Jellyfin library.": "هیچ پوشه سریالی در کتابخانه Jellyfin پیدا نشد.",
    "No completed download has been recorded yet.": "هنوز دانلود کامل‌شده‌ای ثبت نشده است.",
    "No folder was specified.": "نام پوشه مشخص نشده است.",
    "Use /episodes Anime Name": "از /episodes Anime Name استفاده کنید",
    "or select one first with /setfolder.": "یا ابتدا با /setfolder یک پوشه انتخاب کنید.",
    "Choose an existing folder": "یک پوشه موجود را انتخاب کنید",
    "Existing folder selected as the target for new episodes:": "پوشه موجود به‌عنوان مقصد قسمت‌های جدید انتخاب شد:",
    "Target folder set after confirmation:": "پوشه مقصد پس از تأیید تنظیم شد:",
    "New files added to the queue after this will go to this folder.": "فایل‌های جدیدی که پس از این وارد صف شوند به این پوشه می‌روند.",
    "Send /folders to see existing folders.": "برای دیدن پوشه‌های موجود /folders را ارسال کنید.",
    "Send /setfolder NAME first.": "ابتدا /setfolder NAME را ارسال کنید.",
    "Folder not found:": "پوشه پیدا نشد:",
    "This folder does not exist:": "این پوشه وجود ندارد:",
    "This folder choice is no longer valid.": "این انتخاب پوشه دیگر معتبر نیست.",
    "Folder change cancelled.": "تغییر پوشه لغو شد.",
    "Safely renaming the folder and updating rollback paths...": "در حال تغییر امن نام پوشه و به‌روزرسانی مسیرهای بازگردانی…",
    "The new name is the same as the current name.": "نام جدید با نام فعلی یکسان است.",
    "You cannot rename the folder while a download or sort is running.": "هنگام دانلود یا مرتب‌سازی نمی‌توان نام پوشه را تغییر داد.",
    "Rename was not done because the destination folder already exists:": "تغییر نام انجام نشد زیرا پوشه مقصد از قبل وجود دارد:",
    "Rename failed and the bot state was not changed.": "تغییر نام ناموفق بود و وضعیت ربات تغییر نکرد.",
    "queued target(s) and rollback paths too.": "مقصد صف و مسیرهای بازگردانی نیز به‌روزرسانی شدند.",
    "\nUpdated ": "\nبه‌روزرسانی شد: ",
    "Decision saved. Send /download to continue.": "تصمیم ذخیره شد. برای ادامه /download را بفرستید.",
    "This file is not waiting for an overwrite decision.": "این فایل منتظر تصمیم بازنویسی نیست.",
    "The ID must be a number.": "شناسه باید عدد باشد.",
    "Format: /resolve ID skip|overwrite|save_with_suffix": "فرمت: /resolve ID skip|overwrite|save_with_suffix",
    "Files cannot be restored while a download is running.": "هنگام اجرای دانلود نمی‌توان فایل‌ها را بازگرداند.",
    "Sorting started:": "مرتب‌سازی شروع شد:",
    "Sorting completed successfully.": "مرتب‌سازی با موفقیت کامل شد.",
    "Sorting finished with errors.": "مرتب‌سازی با خطا پایان یافت.",
    "Sorter error:": "خطای مرتب‌ساز:",
    "The sorter has not run yet.": "مرتب‌ساز هنوز اجرا نشده است.",
    "This chat has no recorded sort batch to undo.": "این چت دسته مرتب‌سازی ثبت‌شده‌ای برای بازگردانی ندارد.",
    "That sort batch does not belong to this chat.": "آن دسته مرتب‌سازی متعلق به این چت نیست.",
    "Latest run": "آخرین اجرا",
    "Sort undo started:": "بازگردانی مرتب‌سازی شروع شد:",
    "Sort undo error:": "خطای بازگردانی مرتب‌سازی:",
    "Undo completed successfully.": "بازگردانی با موفقیت کامل شد.",
    "Undo was incomplete or had errors.": "بازگردانی ناقص بود یا خطا داشت.",
    "Completed.": "کامل شد.",
    "Could not complete the action.": "عملیات کامل نشد.",
    "Scanning library files...": "در حال بررسی فایل‌های کتابخانه…",
    "Sending Jellyfin library scan request...": "در حال ارسال درخواست اسکن کتابخانه Jellyfin…",
    "A Jellyfin library scan is already running.": "اسکن کتابخانه Jellyfin از قبل در حال اجراست.",
    "Jellyfin library scan is running": "اسکن کتابخانه Jellyfin در حال اجراست",
    "Jellyfin scan progress:": "پیشرفت اسکن Jellyfin:",
    "Live scan state:": "وضعیت زنده اسکن:",
    "Live progress:": "پیشرفت زنده:",
    "Last Jellyfin task result:": "نتیجه آخرین کار Jellyfin:",
    "Live scan state unavailable:": "وضعیت زنده اسکن در دسترس نیست:",
    "Latest scan request:": "آخرین درخواست اسکن:",
    "Scan result:": "نتیجه اسکن:",
    "Progress:": "پیشرفت:",
    "not reported": "گزارش نشده",
    "not recorded yet": "هنوز ثبت نشده",
    "not recorded": "ثبت نشده",
    "Jellyfin scan error:": "خطای اسکن Jellyfin:",
    "Jellyfin connection failed:": "اتصال Jellyfin ناموفق بود:",
    "Jellyfin connection is working.": "اتصال Jellyfin برقرار است.",
    "Server:": "سرور:",
    "Server: unknown": "سرور: نامشخص",
    "Version: unknown": "نسخه: نامشخص",
    "Batch ID: unknown": "شناسه دسته: نامشخص",
    "The latest library state is ready.": "آخرین وضعیت کتابخانه آماده است.",
    "Completed at:": "زمان پایان:",
    "Jellyfin stopped scanning, but it did not report a successful completion.": "اسکن Jellyfin متوقف شد اما پایان موفق گزارش نشد.",
    "The scan was not cancelled. Use /jellyfin_status to check it.": "اسکن لغو نشده است؛ برای بررسی از /jellyfin_status استفاده کنید.",
    "Check the Jellyfin dashboard or server logs.": "داشبورد Jellyfin یا گزارش‌های سرور را بررسی کنید.",
    "Jellyfin accepted the scan, but the bot stopped waiting before Jellyfin reported completion.": "Jellyfin اسکن را پذیرفت، اما ربات پیش از گزارش پایان اسکن از انتظار خارج شد.",
    "Result:": "نتیجه:",
    "Suggested folder name:": "نام پیشنهادی پوشه:",
    "Choose the correct IMDb result:": "نتیجه صحیح IMDb را انتخاب کنید:",
    "This IMDb result expired.": "نتیجه IMDb منقضی شده است.",
    "This confirmation expired.": "مهلت این تأیید تمام شده است.",
    "The selected folder changed after this IMDb search.": "پس از این جستجوی IMDb پوشه انتخاب‌شده تغییر کرده است.",
    "The folder used for this IMDb search no longer exists.": "پوشه استفاده‌شده برای این جستجوی IMDb دیگر وجود ندارد.",
    "Nothing was renamed.": "هیچ نامی تغییر نکرد.",
    "Your entered name will be offered as the fallback.": "نام واردشده شما به‌عنوان گزینه جایگزین پیشنهاد می‌شود.",
    "Do you confirm?": "آیا تأیید می‌کنید؟",
    "Final folder format: Title (Year) [imdbid-ID]": "قالب نهایی پوشه: Title (Year) [imdbid-ID]",
    "The manual name is not valid:": "نام دستی معتبر نیست:",
    "The manual name is not valid either:": "نام دستی نیز معتبر نیست:",
    "Send another title.": "عنوان دیگری ارسال کنید.",
    "This movie queue item was not found.": "این مورد فیلم در صف پیدا نشد.",
    "IMDb filename search is unavailable:": "جستجوی نام فایل در IMDb در دسترس نیست:",
    "Send the movie title manually.": "نام فیلم را به‌صورت دستی ارسال کنید.",
    "The bot will let you use that exact name if IMDb remains unavailable.": "اگر IMDb همچنان در دسترس نباشد، ربات اجازه می‌دهد همان نام را استفاده کنید.",
    "Movie library:": "کتابخانه فیلم:",
    "Action:": "عملیات:",
    "Started at:": "زمان شروع:",
    "Stopped at:": "زمان توقف:",
    "Time:": "زمان:",
    "Version:": "نسخه:",
    "Current folder:": "پوشه فعلی:",
    "chat_id for this chat:": "شناسه این چت:",
    "Select an existing series folder": "یک پوشه سریال موجود را انتخاب کنید",
    "New episode detected:": "قسمت جدید تشخیص داده شد:",
    "already exists in the library:": "از قبل در کتابخانه وجود دارد:",
    "is already queued": "از قبل در صف است",
    "no recognizable episodes were found.": "قسمت قابل‌شناسایی پیدا نشد.",
    "No recognizable episodes were found in the library.": "هیچ قسمت قابل‌شناسایی در کتابخانه پیدا نشد.",
    "Jellyfin library episode summary": "خلاصه قسمت‌های کتابخانه Jellyfin",
    "Total:": "مجموع:",
    "episode(s)": "قسمت",
    "Missing:": "گمشده:",
    "latest E": "آخرین E",
    "result shortened; use /episodes NAME for details": "نتیجه کوتاه شد؛ برای جزئیات از /episodes NAME استفاده کنید",
}


STATUS_FA = {
    "awaiting_identification": "منتظر تشخیص نام",
    "cancelled": "لغوشده",
    "completed": "کامل‌شده",
    "downloading": "در حال دانلود",
    "failed": "ناموفق",
    "imported": "منتقل‌شده",
    "movie_import_failed": "انتقال فیلم ناموفق",
    "movie_undo_partial": "بازگردانی ناقص فیلم",
    "movie_undone": "فیلم بازگردانده‌شده",
    "queued": "در صف",
    "skipped": "ردشده",
    "waiting_overwrite": "منتظر تصمیم بازنویسی",
}


def _translate_dynamic_text(text: str) -> str:
    """Translate count/status sentences without modifying names or paths."""
    text = re.sub(
        r"Downloads finished\. (\d+) of (\d+) file\(s\) completed\.",
        lambda match: (
            f"دانلودها پایان یافت. {match.group(1)} از {match.group(2)} فایل کامل شد."
        ),
        text,
    )
    text = re.sub(
        r"Queue \((\d+) file\(s\)\):",
        lambda match: f"صف ({match.group(1)} فایل):",
        text,
    )
    text = re.sub(
        r"^\.\.\. and (\d+) more file\(s\)$",
        lambda match: f"... و {match.group(1)} فایل دیگر",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^Total: (\d+) episode\(s\)$",
        lambda match: f"مجموع: {match.group(1)} قسمت",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"(\d+) eps \(latest E(\d+)\)",
        lambda match: f"{match.group(1)} قسمت (آخرین E{match.group(2)})",
        text,
    )

    def queue_line(match: re.Match[str]) -> str:
        kind = "سریال" if match.group(1) == "Series" else "فیلم"
        status = STATUS_FA.get(match.group(5), match.group(5))
        return (
            f"{kind} · {match.group(2)} مورد {match.group(3)} "
            f"(شناسه صف #{match.group(4)}) [{status}] "
        )

    return re.sub(
        r"^(Series|Movie) · (.*) item (\d+) \(Queue ID #(\d+)\) \[([^\]]+)\] ",
        queue_line,
        text,
        flags=re.MULTILINE,
    )


def language_code(value: str) -> str:
    return "fa" if value == "fa" else "en"


def translate_text(text: str, language: str) -> str:
    if language_code(language) != "fa":
        return text
    translated = _translate_dynamic_text(text)
    for english, persian in sorted(
        TEXT_FA.items(), key=lambda item: len(item[0]), reverse=True
    ):
        translated = translated.replace(english, persian)
    return translated


def translate_markup(markup: dict | None, language: str) -> dict | None:
    if markup is None or language_code(language) != "fa":
        return markup
    result: dict[str, Any] = deepcopy(markup)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"text", "input_field_placeholder"} and isinstance(
                    child, str
                ):
                    value[key] = BUTTON_FA.get(child, child)
                elif key != "copy_text":
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    return result


def reply_category_action(text: str, actions: dict[str, str]) -> str | None:
    if text in actions:
        return actions[text]
    reverse = {BUTTON_FA.get(label, label): action for label, action in actions.items()}
    return reverse.get(text)
