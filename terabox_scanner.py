name: TeraBox BRUTE FORCE Scanner

on:
  workflow_dispatch:

jobs:
  scan-job:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install Dependencies
        run: pip install telethon

      - name: Create Aggressive Scanner
        run: |
          cat << 'EOF' > terabox_scanner.py
          import os, re, asyncio, sys
          from telethon import TelegramClient

          # --- הגדרות ---
          API_ID = int(os.environ['TG_API_ID'])
          API_HASH = os.environ['TG_API_HASH']
          MAIN_CHANNEL = os.environ['MAIN_CHANNEL']
          
          # סורק את כל ההיסטוריה בלי הגבלה
          LIMIT_MSG = None 

          sys.stdout.reconfigure(encoding='utf-8')

          async def main():
              print(f"\n=== 🚨 סריקה אגרסיבית (ללא סינון) ===")
              print(f"--- מציג כל הודעה שיש בה קישור ל-TeraBox ---\n")

              found_links = []
              scanned_count = 0

              async with TelegramClient('anon', API_ID, API_HASH) as client:
                  # הסרת ה-Reverse כדי לראות את ההודעות החדשות קודם
                  async for m in client.iter_messages(MAIN_CHANNEL, limit=LIMIT_MSG):
                      scanned_count += 1
                      if scanned_count % 100 == 0:
                          print(f"   ...סרקתי {scanned_count} הודעות...")

                      text = m.text or ""
                      # חיפוש כל הוריאציות של הקישורים
                      tera_urls = re.findall(r'(https?://[^\s\)]*(?:terabox|1024tera|nephobox|momerybox)[^\s\)]*)', text)
                      
                      if not tera_urls:
                          continue

                      # בדיקת סטטוס (רק לידע כללי, לא לסינון)
                      has_media = "כן" if m.media else "לא"
                      has_tg = "כן" if "t.me" in text else "לא"
                      
                      # שמירת הנתונים
                      for url in tera_urls:
                          clean_url = url.rstrip(').,;]')
                          title = text.split('\n')[0][:40].replace('\r', '').strip() if text else "ללא כותרת"
                          
                          found_links.append({
                              "id": m.id,
                              "date": m.date.strftime("%d/%m/%Y"),
                              "title": title,
                              "url": clean_url,
                              "media": has_media,
                              "tg_link": has_tg
                          })

              print("\n" + "="*60)
              print(f"📊 סיכום סריקה:")
              print(f"סה\"כ נסרקו: {scanned_count} הודעות")
              print(f"נמצאו: {len(found_links)} קישורים")
              print("="*60 + "\n")

              if found_links:
                  print("👇 רשימת הקישורים המלאה: 👇\n")
                  for item in found_links:
                      # הדפסה בפורמט ברור:
                      # [תאריך] [ID] - שם (האם יש מדיה? האם יש טלגרם?) -> קישור
                      print(f"[{item['date']}] [ID:{item['id']}] {item['title']}")
                      print(f"   מדיה: {item['media']} | לינק טלגרם: {item['tg_link']}")
                      print(f"   🔗 {item['url']}\n")
                      print("-" * 30)
              else:
                  print("❌ מוזר מאוד. עדיין 0 קישורים. בדוק אם ה-MAIN_CHANNEL נכון.")

          if __name__ == '__main__':
              asyncio.run(main())
          EOF

      - name: Run Scanner
        env:
          TG_API_ID: ${{ secrets.TG_API_ID }}
          TG_API_HASH: ${{ secrets.TG_API_HASH }}
          MAIN_CHANNEL: ${{ secrets.MAIN_CHANNEL }}
          PYTHONUNBUFFERED: "1"
        run: python terabox_scanner.py
