import os, re, asyncio, sys
from telethon import TelegramClient

# --- הגדרות ---
API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
MAIN_CHANNEL = os.environ['MAIN_CHANNEL']

# כמות הודעות לסריקה אחורה (שים 0 לסריקה של הכל, או מספר כמו 5000)
LIMIT_MSG = 5000 

sys.stdout.reconfigure(encoding='utf-8')

async def main():
    print(f"\n=== 🕵️‍♂️ מתחיל סריקה לאיתור קישורי TeraBox 'יתומים' ===")
    print(f"--- בודק רק הודעות שאין בהן קובץ טלגרם או קישור לערוץ אחר ---\n")

    found_links = []
    scanned_count = 0

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        # סריקה אחורה מההודעה הכי חדשה
        async for m in client.iter_messages(MAIN_CHANNEL, limit=LIMIT_MSG):
            scanned_count += 1
            if scanned_count % 100 == 0:
                print(f"   ...סרקתי {scanned_count} הודעות...")

            # 1. האם יש בכלל טרה-בוקס?
            text = m.text or ""
            tera_urls = re.findall(r'(https?://[^\s\)]*(?:terabox|1024tera|nephobox)[^\s\)]*)', text)
            
            if not tera_urls:
                continue

            # 2. האם יש אלטרנטיבה בטלגרם?
            # בודקים אם יש קובץ מצורף (Media) או קישור ל-t.me
            has_media = m.media is not None
            has_tg_link = re.search(r't\.me/', text)
            
            if has_media or has_tg_link:
                # מדלגים - כי את זה הבוט הרגיל כבר יוריד
                continue
            
            # אם הגענו לפה - זה קישור טרה-בוקס "יתום" (שחייב להוריד ידנית)
            for url in tera_urls:
                # ניקוי הקישור
                clean_url = url.rstrip(').,;]')
                
                # חילוץ כותרת קצרה לזיהוי
                title = text.split('\n')[0][:50] if text else "ללא כותרת"
                title = title.replace('\r', '').strip()
                
                found_links.append({
                    "id": m.id,
                    "date": m.date.strftime("%Y-%m-%d"),
                    "title": title,
                    "url": clean_url
                })

    # --- סיכום והדפסה ---
    print("\n" + "="*50)
    print(f"📊 סיכום סריקה:")
    print(f"סה\"כ נסרקו: {scanned_count} הודעות")
    print(f"נמצאו: {len(found_links)} קישורים שדורשים הורדה ידנית")
    print("="*50 + "\n")

    if found_links:
        print("👇 הנה הרשימה המלאה (תעתיק ותשמור): 👇\n")
        for item in found_links:
            # פורמט נוח להעתקה
            print(f"{item['url']}  |  {item['title']} (ID: {item['id']})")
    else:
        print("✅ הכל נקי! לא נמצאו קישורי טרה-בוקס ללא גיבוי בטלגרם.")

if __name__ == '__main__':
    asyncio.run(main())
