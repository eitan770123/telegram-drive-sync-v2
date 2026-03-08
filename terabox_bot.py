import os, re, asyncio, json, sys, io, subprocess
from telethon import TelegramClient
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# --- הגדרות ---
API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
MAIN_CHANNEL = os.environ['MAIN_CHANNEL']
DRIVE_FOLDER_ID = os.environ['DRIVE_FOLDER_ID']
START_FROM_ID = int(os.environ.get('START_FROM_MSG_ID', 0))
COOKIES_CONTENT = os.environ.get('TERABOX_COOKIES_FILE') # הסוד החדש
MEMORY_FILENAME = 'terabox_memory.json'

sys.stdout.reconfigure(encoding='utf-8')

# --- הכנת קובץ הקוקיז לשימוש ---
if not COOKIES_CONTENT:
    print(">>> ❌ שגיאה: הסוד TERABOX_COOKIES_FILE חסר!")
    sys.exit(1)

# שמירת הקוקיז לקובץ פיזי שהכלי yt-dlp יוכל לקרוא
with open("cookies.txt", "w", encoding="utf-8") as f:
    f.write(COOKIES_CONTENT)

# --- חיבור לדרייב ---
try:
    token_data = json.loads(os.environ['GOOGLE_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_data)
    drive_service = build('drive', 'v3', credentials=creds)
    print(">>> V מחובר לגוגל דרייב!")
except Exception as e:
    print(f">>> X שגיאה בחיבור לגוגל: {e}")
    sys.exit(1)

# === הפונקציה החדשה: שימוש ב-yt-dlp ===

def download_with_ytdlp(url):
    print(f"   ⏳ מפעיל yt-dlp להורדה חכמה...")
    try:
        # פקודת ההורדה:
        # --cookies cookies.txt: משתמש בקובץ שיצרנו
        # -o: קובע את שם הקובץ לשם המקורי מהשרת
        # --print filename: רק מדפיס את שם הקובץ בהתחלה כדי שנדע מה יורד
        
        # שלב 1: מנסים להשיג את שם הקובץ קודם
        cmd_name = [
            "yt-dlp", 
            "--cookies", "cookies.txt",
            "--print", "filename",
            url
        ]
        
        # הרצת בדיקה לקבלת שם הקובץ
        result = subprocess.run(cmd_name, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"   X yt-dlp נכשל בזיהוי: {result.stderr}")
            return None
            
        filename = result.stdout.strip()
        print(f"   V זוהה הקובץ: {filename}")
        
        # שלב 2: הורדה בפועל
        cmd_download = [
            "yt-dlp",
            "--cookies", "cookies.txt",
            "-o", "%(title)s.%(ext)s", # פורמט שמירה נקי
            url
        ]
        
        print(f"   ⬇️ מתחיל הורדה...")
        subprocess.run(cmd_download, check=True)
        
        # מחפשים את הקובץ שירד (לפעמים השם משתנה קצת)
        if os.path.exists(filename):
            return filename
        
        # אם לא מצאנו בדיוק, נחפש קובץ שנוצר בדקה האחרונה
        files = sorted(os.listdir('.'), key=os.path.getmtime)
        for f in reversed(files):
            if f != "cookies.txt" and f != "terabox_bot.py" and not f.endswith(".json"):
                return f
                
    except Exception as e:
        print(f"   X שגיאה בהרצת yt-dlp: {e}")
        
    return None

# === ניהול זיכרון ===
def load_memory():
    print(">>> 🧠 טוען זיכרון...")
    memory = {"files": {}, "last_msg_id": 0}
    file_id = None
    try:
        # טוענים את הזיכרון הראשי (למניעת כפילויות)
        q = f"name='bot_memory_v2.json' and '{DRIVE_FOLDER_ID}' in parents and trashed=false"
        res = drive_service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get('files', [])
        if res:
            file_id = res[0]['id']
            request = drive_service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False: status, done = downloader.next_chunk()
            fh.seek(0)
            memory = json.load(fh)
    except: pass
    
    # זיכרון מקומי
    local_mem = {"files": []}
    try:
        q_local = f"name='{MEMORY_FILENAME}' and '{DRIVE_FOLDER_ID}' in parents and trashed=false"
        res_l = drive_service.files().list(q=q_local, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get('files', [])
        if res_l:
            req_l = drive_service.files().get_media(fileId=res_l[0]['id'])
            fh_l = io.BytesIO()
            dl_l = MediaIoBaseDownload(fh_l, req_l)
            done = False
            while done is False: status, done = dl_l.next_chunk()
            fh_l.seek(0)
            local_mem = json.load(fh_l)
    except: pass

    return memory, local_mem, file_id

def save_local_memory(data):
    try:
        with open(MEMORY_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        media = MediaFileUpload(MEMORY_FILENAME, mimetype='application/json', resumable=True)
        q = f"name='{MEMORY_FILENAME}' and '{DRIVE_FOLDER_ID}' in parents and trashed=false"
        res = drive_service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get('files', [])
        if res:
            drive_service.files().update(fileId=res[0]['id'], media_body=media, supportsAllDrives=True).execute()
        else:
            drive_service.files().create(body={'name': MEMORY_FILENAME, 'parents': [DRIVE_FOLDER_ID]}, media_body=media, supportsAllDrives=True).execute()
    except: pass

def get_or_create_folder(clean_name):
    q = f"name='{clean_name}' and '{DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    try:
        res = drive_service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get('files', [])
        if res: return res[0]['id']
        return drive_service.files().create(body={'name': clean_name, 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'}, fields='id', supportsAllDrives=True).execute().get('id')
    except: return None

# === ראשי ===

async def main():
    main_memory, local_memory, _ = load_memory()
    current_msg_id = START_FROM_ID if START_FROM_ID > 0 else local_memory.get("last_msg_id", 0)
    
    all_existing = set(local_memory.get("files", []))
    if "files" in main_memory and isinstance(main_memory["files"], dict):
        for flist in main_memory["files"].values():
            for f in flist: all_existing.add(f)

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== 🍪 בוט TeraBox (גרסת yt-dlp) מתחיל מ-ID: {current_msg_id} ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            
            if re.search(r't\.me/', m.text or ""):
                local_memory["last_msg_id"] = m.id
                continue
            
            tera_links = re.findall(r'(https?://(?:www\.)?(?:terabox\.com|nephobox\.com|teraboxapp\.com)/s/[\w\-]+)', m.text or "")
            
            if tera_links:
                print(f"\n🔎 [ID: {m.id}] קישורי TeraBox...")
                for t_url in tera_links:
                    
                    # הפעלת ההורדה החדשה
                    downloaded_file = download_with_ytdlp(t_url)
                    
                    if downloaded_file:
                        if downloaded_file in all_existing:
                            print(f"   ⏩ הקובץ '{downloaded_file}' כבר קיים בדרייב.")
                            os.remove(downloaded_file) # מחיקה כי לא צריך
                            continue

                        folder_id = get_or_create_folder("TeraBox_Downloads")
                        print(f"   ⬆️ מעלה לדרייב: {downloaded_file}")
                        
                        try:
                            media = MediaFileUpload(downloaded_file, resumable=True)
                            drive_service.files().create(body={'name': downloaded_file, 'parents': [folder_id]}, media_body=media, supportsAllDrives=True).execute()
                            print(f"   ✅ עלה!")
                            
                            all_existing.add(downloaded_file)
                            if "files" not in local_memory: local_memory["files"] = []
                            local_memory["files"].append(downloaded_file)
                            save_local_memory(local_memory)
                        except Exception as e:
                            print(f"   ❌ שגיאה בהעלאה: {e}")
                        
                        # ניקוי בסוף
                        if os.path.exists(downloaded_file): os.remove(downloaded_file)

            local_memory["last_msg_id"] = m.id
            if m.id % 5 == 0: save_local_memory(local_memory)

if __name__ == '__main__':
    asyncio.run(main())
