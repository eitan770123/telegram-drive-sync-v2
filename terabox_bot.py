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
COOKIES_CONTENT = os.environ.get('TERABOX_COOKIES_FILE') 
MEMORY_FILENAME = 'terabox_memory.json'

sys.stdout.reconfigure(encoding='utf-8')

# --- הכנת קובץ הקוקיז לשימוש על הדיסק ---
if not COOKIES_CONTENT:
    print(">>> ❌ שגיאה: הסוד TERABOX_COOKIES_FILE חסר!")
    sys.exit(1)

# שמירת הקוקיז לקובץ פיזי ש-yt-dlp יוכל לקרוא
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

# === פונקציות עזר ===

def normalize_name(name):
    base_name = os.path.splitext(name)[0]
    clean = re.sub(r'[^a-zA-Z0-9א-ת]', '', base_name).lower()
    return clean

def is_file_already_in_drive(new_filename, existing_files_set):
    if new_filename in existing_files_set: return True
    new_clean = normalize_name(new_filename)
    if len(new_clean) < 3: return False
    for existing_file in existing_files_set:
        existing_clean = normalize_name(existing_file)
        if new_clean == existing_clean: return True
        if len(new_clean) > 4 and len(existing_clean) > 4:
            if new_clean in existing_clean or existing_clean in new_clean: return True
    return False

def fix_terabox_url(url):
    """ מתקן קישורי surl לפורמט ש-yt-dlp אוהב """
    url = url.rstrip(').,;]')
    if 'surl=' in url:
        try:
            val = url.split('surl=')[1].split('&')[0]
            return f"https://terabox.com/s/1{val}"
        except:
            return url
    return url

def download_with_ytdlp_wrapper(url):
    """ מפעיל את yt-dlp כאילו היה תוכנה חיצונית """
    print(f"   ⏳ מפעיל מנוע הורדה חיצוני (yt-dlp)...")
    
    # קודם כל משיגים את שם הקובץ (בלי להוריד) כדי לבדוק כפילויות
    # yt-dlp --print filename ...
    cmd_info = [
        "yt-dlp",
        "--cookies", "cookies.txt",
        "--print", "filename",
        "--no-warnings",
        url
    ]
    
    filename = "Unknown"
    try:
        # הרצה לקבלת שם
        res = subprocess.run(cmd_info, capture_output=True, text=True, timeout=30)
        if res.returncode == 0 and res.stdout.strip():
            filename = res.stdout.strip()
            print(f"   V זוהה הקובץ: {filename}")
        else:
            print(f"   ⚠️ לא הצלחתי לזהות שם מראש, אנסה להוריד בכל זאת.")
    except: pass

    # פקודת ההורדה המלאה
    cmd_download = [
        "yt-dlp",
        "--cookies", "cookies.txt",
        "-o", "%(title)s.%(ext)s", # שמירה בשם המקורי
        "--no-progress", # כדי לא להציף את הלוג
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36",
        url
    ]

    try:
        subprocess.run(cmd_download, check=True)
        
        # חיפוש הקובץ שירד (לפי זמן יצירה - החדש ביותר)
        files = sorted([f for f in os.listdir('.') if os.path.isfile(f)], key=os.path.getmtime)
        for f in reversed(files):
            # מסננים קבצי מערכת שלנו
            if f not in ["terabox_bot.py", "cookies.txt", MEMORY_FILENAME, "bot_memory_v2.json"]:
                if not f.endswith(".json"): # לוודא שזה לא קובץ זבל
                    return f
    except subprocess.CalledProcessError as e:
        print(f"   X ההורדה נכשלה (שגיאת yt-dlp).")
    except Exception as e:
        print(f"   X שגיאה כללית: {e}")
        
    return None

# === ניהול זיכרון ===
def load_memory():
    print(">>> 🧠 טוען זיכרון...")
    memory = {"files": {}, "last_msg_id": 0}
    file_id = None
    try:
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
    
    print(f">>> 🛡️ הגנה חכמה פעילה: טענתי {len(all_existing)} קבצים להשוואה.")

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== 🍪 בוט TeraBox (Hybrid Mode) מתחיל מ-ID: {current_msg_id} ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            
            # חיפוש קישורים
            found_urls = re.findall(r'(https?://[^\s\)]*terabox[^\s\)]*)', m.text or "")
            
            if found_urls:
                print(f"--- הודעה {m.id}: נמצאו {len(found_urls)} קישורים.")
                for raw_url in found_urls:
                    # 1. תיקון הקישור בעזרת פייתון
                    fixed_url = fix_terabox_url(raw_url)
                    print(f"   🔧 קישור לטיפול: {fixed_url}")

                    # הערה: אנחנו מנסים להוריד ישר, כי yt-dlp יבדוק את השם לבד
                    # אבל בשביל בדיקת כפילות יעילה, אנחנו סומכים על הפונקציה הפנימית ב-wrapper
                    
                    # 2. שליחה ל-yt-dlp להורדה
                    downloaded_file = download_with_ytdlp_wrapper(fixed_url)
                    
                    if downloaded_file:
                        # 3. בדיקת כפילות מאוחרת (אחרי ההורדה) - לא אידאלי אבל הכי בטוח כרגע
                        # או שהפונקציה wrapper כבר זיהתה את השם קודם
                        
                        if is_file_already_in_drive(downloaded_file, all_existing):
                            print(f"   ⏩ הקובץ '{downloaded_file}' כבר קיים בדרייב. מוחק ומדלג.")
                            os.remove(downloaded_file)
                            continue

                        # 4. העלאה לדרייב
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
                        
                        # ניקוי
                        if os.path.exists(downloaded_file): os.remove(downloaded_file)

            local_memory["last_msg_id"] = m.id
            if m.id % 5 == 0: save_local_memory(local_memory)

if __name__ == '__main__':
    asyncio.run(main())
