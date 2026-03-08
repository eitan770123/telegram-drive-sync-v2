import os, re, asyncio, json, sys, io, requests
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
TERABOX_COOKIE = os.environ.get('TERABOX_COOKIE')
MEMORY_FILENAME = 'terabox_memory.json' # זיכרון נפרד כדי לא לגעת בבוט הראשי

sys.stdout.reconfigure(encoding='utf-8')

# --- בדיקות ---
if not TERABOX_COOKIE:
    print(">>> ❌ שגיאה: לא הוגדר הסוד TERABOX_COOKIE!")
    sys.exit(1)

try:
    token_data = json.loads(os.environ['GOOGLE_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_data)
    drive_service = build('drive', 'v3', credentials=creds)
    print(">>> V מחובר לגוגל דרייב!")
except Exception as e:
    print(f">>> X שגיאה בחיבור לגוגל: {e}")
    sys.exit(1)

# === פונקציות טרה-בוקס ===

def get_clean_name(name):
    if not name: return "Unknown_File"
    return re.sub(r'[\\/*?:"<>|\']', "", name).strip()

def get_terabox_download_link(url):
    print(f"   ⏳ מפענח קישור (עם Cookie מלא)...")
    try:
        short_key = url.split('/')[-1]
        
        # אנחנו משתמשים בכל הקוקי כמו שהוא, כולל הכל
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Cookie": TERABOX_COOKIE,  # שינוי קריטי: בלי התוספת של ndus=
            "Referer": "https://www.terabox.com/",
            "Origin": "https://www.terabox.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,he;q=0.8"
        }

        session = requests.Session()
        session.headers.update(headers)

        # בדיקה מקדימה - האם אנחנו בכלל מחוברים?
        # מנסים לגשת ל-API פשוט כדי לראות אם הקוקי עובד
        try:
            check = session.get("https://www.terabox.com/api/user/getinfo", timeout=10)
            if check.json().get('errno') != 0:
                 print("   ⚠️ אזהרה: נראה שהקוקיז לא תקין או פג תוקף (Login Failed).")
        except: pass

        # 1. קבלת פרטי קובץ
        info_url = f"https://www.terabox.com/api/shorturlinfo?shorturl={short_key}&root=1"
        resp = session.get(info_url)
        data = resp.json()
        
        if data.get('errno') == 400210:
            print(f"   X שגיאה 400210: הקוקיז עדיין לא תקין. נא להוציא את כל ה-Request Header.")
            return None
            
        file_list = data.get('list', [])
        if not file_list: 
            print("   ⚠️ הקישור ריק או לא תקין.")
            return None

        file_item = file_list[0]
        filename = file_item['server_filename']
        fs_id = file_item['fs_id']
        
        print(f"   V זוהה: {filename}")

        # 2. קבלת קישור להורדה
        download_api = "https://www.terabox.com/api/download"
        params = {"fidlist": f"[{fs_id}]", "type": "dlink"}
        
        d_resp = session.get(download_api, params=params)
        d_data = d_resp.json()
        
        dlink = d_data.get('dlink', [{}])[0].get('dlink')
        
        if dlink:
            return {"name": filename, "download_url": dlink, "headers": headers}
            
    except Exception as e:
        print(f"   X שגיאה: {e}")
    return None
# === ניהול זיכרון ===

def load_memory():
    print(">>> 🧠 טוען את זיכרון TeraBox...")
    memory = {"files": [], "last_msg_id": 0}
    file_id = None
    try:
        q = f"name='{MEMORY_FILENAME}' and '{DRIVE_FOLDER_ID}' in parents and trashed=false"
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
    return memory, file_id

def save_memory_force(data, file_id):
    try:
        with open(MEMORY_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        media = MediaFileUpload(MEMORY_FILENAME, mimetype='application/json', resumable=True)
        if file_id:
            drive_service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
        else:
            res = drive_service.files().create(body={'name': MEMORY_FILENAME, 'parents': [DRIVE_FOLDER_ID]}, media_body=media, supportsAllDrives=True).execute()
            return res['id']
        return file_id
    except: return file_id

def get_or_create_folder(clean_name):
    q = f"name='{clean_name}' and '{DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    try:
        res = drive_service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get('files', [])
        if res: return res[0]['id']
        return drive_service.files().create(body={'name': clean_name, 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'}, fields='id', supportsAllDrives=True).execute().get('id')
    except: return None

# === ראשי ===

async def main():
    memory_data, memory_file_id = load_memory()
    current_msg_id = START_FROM_ID if START_FROM_ID > 0 else memory_data.get("last_msg_id", 0)
    
    if "files" not in memory_data: memory_data["files"] = []

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== 🍪 בוט TeraBox (סלקטיבי) מתחיל מ-ID: {current_msg_id} ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            
            # לא מעדכנים את last_msg_id גלובלית כדי לא להפריע לבוט השני,
            # אלא אם כן תרצה שהבוט הזה ירוץ עצמאית לגמרי. כרגע הוא רץ במקביל.
            
            txt = m.text or ""
            
            # בדיקת קישורים
            tera_links = re.findall(r'(https?://(?:www\.)?(?:terabox\.com|nephobox\.com|teraboxapp\.com)/s/[\w\-]+)', txt)
            tg_links = re.findall(r't\.me/', txt) # בדיקה פשוטה אם יש קישור טלגרם כלשהו

            if tera_links:
                print(f"\n🔎 [ID: {m.id}] נמצא קישור TeraBox...")
                
                # === חוק הדילוג החדש ===
                if len(tg_links) > 0:
                    print(f"   ✋ ההודעה מכילה גם קישור לטלגרם (t.me).")
                    print(f"   ⏩ משאיר את העבודה לבוט הראשי ומדלג על טרה-בוקס!")
                    continue
                # =======================

                for t_url in tera_links:
                    info = get_terabox_download_link(t_url)
                    
                    if info and info["download_url"]:
                        f_name = info["name"]
                        
                        # בדיקה בזיכרון המקומי של הבוט הזה
                        if f_name in memory_data["files"]:
                            print(f"   ⏩ הקובץ '{f_name}' כבר קיים בזיכרון.")
                            continue

                        target_folder = "TeraBox_Downloads"
                        folder_id = get_or_create_folder(target_folder)
                        
                        print(f"   ⬇️ מוריד: {f_name}")
                        try:
                            with requests.get(info["download_url"], headers=info["headers"], stream=True, timeout=120) as r:
                                r.raise_for_status()
                                with open(f_name, 'wb') as f:
                                    for chunk in r.iter_content(chunk_size=16384): f.write(chunk)
                            
                            print(f"   ⬆️ מעלה לדרייב...")
                            media = MediaFileUpload(f_name, resumable=True)
                            drive_service.files().create(body={'name': f_name, 'parents': [folder_id]}, media_body=media, supportsAllDrives=True).execute()
                            
                            print(f"   ✅ הושלם!")
                            os.remove(f_name)
                            
                            memory_data["files"].append(f_name)
                            memory_file_id = save_memory_force(memory_data, memory_file_id)
                            
                        except Exception as e:
                            print(f"   ❌ שגיאה בהורדה: {e}")
                            if os.path.exists(f_name): os.remove(f_name)

            # שמירה כל 5 הודעות
            if m.id % 5 == 0:
                memory_file_id = save_memory_force(memory_data, memory_file_id)

if __name__ == '__main__':
    asyncio.run(main())
