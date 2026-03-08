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
# קריאת הסוד (JSON או טקסט רגיל)
RAW_COOKIE = os.environ.get('TERABOX_COOKIE')
MEMORY_FILENAME = 'terabox_memory.json'

sys.stdout.reconfigure(encoding='utf-8')

# --- עיבוד הקוקיז (החלק החדש והחכם) ---
def parse_cookie_string(raw_cookie):
    """ הופך את ה-JSON מהתוסף למחרוזת שטרה-בוקס מבין """
    if not raw_cookie: return None
    
    try:
        # נסיון 1: האם זה JSON מהתוסף Cookie-Editor?
        cookie_json = json.loads(raw_cookie)
        if isinstance(cookie_json, list):
            # המרה לרשימה של name=value
            cookie_parts = [f"{c['name']}={c['value']}" for c in cookie_json if 'name' in c and 'value' in c]
            return "; ".join(cookie_parts)
    except:
        # אם זה לא JSON, כנראה המשתמש הדביק מחרוזת רגילה - נשתמש בה כמו שהיא
        pass
    
    return raw_cookie

FINAL_COOKIE = parse_cookie_string(RAW_COOKIE)

if not FINAL_COOKIE:
    print(">>> ❌ שגיאה: הסוד TERABOX_COOKIE ריק או לא תקין!")
    sys.exit(1)

# --- חיבור לדרייב ---
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
    print(f"   ⏳ מפענח קישור...")
    try:
        short_key = url.split('/')[-1]
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Cookie": FINAL_COOKIE, # השימוש בקוקי המעובד
            "Referer": "https://www.terabox.com/",
            "Origin": "https://www.terabox.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,he;q=0.8"
        }

        session = requests.Session()
        session.headers.update(headers)

        # בדיקת חיבור ראשונית
        try:
            check = session.get("https://www.terabox.com/api/user/getinfo", timeout=10)
            if check.json().get('errno') != 0:
                 print("   ⚠️ אזהרה: הקוקיז כנראה פג תוקף (Login Failed). נסה לייצא שוב.")
        except: pass

        # 1. קבלת פרטי קובץ
        info_url = f"https://www.terabox.com/api/shorturlinfo?shorturl={short_key}&root=1"
        resp = session.get(info_url)
        data = resp.json()
        
        if data.get('errno') != 0:
            print(f"   X שגיאת טרה-בוקס: {data.get('errno')}")
            return None

        file_list = data.get('list', [])
        if not file_list: return None

        file_item = file_list[0]
        filename = file_item['server_filename']
        fs_id = file_item['fs_id']
        
        print(f"   V זוהה: {filename}")

        # 2. קבלת קישור להורדה
        download_api = "https://www.terabox.com/api/download"
        params = {"fidlist": f"[{fs_id}]", "type": "dlink"}
        d_resp = session.get(download_api, params=params)
        dlink = d_resp.json().get('dlink', [{}])[0].get('dlink')
        
        if dlink:
            return {"name": filename, "download_url": dlink, "headers": headers}
    except Exception as e:
        print(f"   X שגיאה: {e}")
    return None

# === ניהול זיכרון ===

def load_memory():
    print(">>> 🧠 טוען זיכרון...")
    memory = {"files": {}, "last_msg_id": 0}
    file_id = None
    try:
        # טוענים את הזיכרון של הבוט הראשי (למניעת כפילויות גלובלית)
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
    
    # טוענים גם זיכרון מקומי לטרה-בוקס (אם קיים)
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
        # מחפשים אם קיים
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
    
    # איסוף כל הקבצים שקיימים בדרייב למניעת כפילויות
    all_existing = set(local_memory.get("files", []))
    if "files" in main_memory and isinstance(main_memory["files"], dict):
        for flist in main_memory["files"].values():
            for f in flist: all_existing.add(f)

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== 🍪 בוט TeraBox (JSON Edition) מתחיל מ-ID: {current_msg_id} ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            
            # אם יש קישור טלגרם - מדלגים
            if re.search(r't\.me/', m.text or ""):
                local_memory["last_msg_id"] = m.id
                continue
            
            tera_links = re.findall(r'(https?://(?:www\.)?(?:terabox\.com|nephobox\.com|teraboxapp\.com)/s/[\w\-]+)', m.text or "")
            
            if tera_links:
                print(f"\n🔎 [ID: {m.id}] נמצאו קישורי TeraBox...")
                for t_url in tera_links:
                    info = get_terabox_download_link(t_url)
                    
                    if info and info["download_url"]:
                        f_name = info["name"]
                        if f_name in all_existing:
                            print(f"   ⏩ קיים בדרייב: {f_name}")
                            continue

                        folder_id = get_or_create_folder("TeraBox_Downloads")
                        print(f"   ⬇️ מוריד: {f_name}")
                        try:
                            with requests.get(info["download_url"], headers=info["headers"], stream=True, timeout=120) as r:
                                r.raise_for_status()
                                with open(f_name, 'wb') as f:
                                    for chunk in r.iter_content(chunk_size=16384): f.write(chunk)
                            
                            media = MediaFileUpload(f_name, resumable=True)
                            drive_service.files().create(body={'name': f_name, 'parents': [folder_id]}, media_body=media, supportsAllDrives=True).execute()
                            print(f"   ✅ עלה!")
                            os.remove(f_name)
                            
                            all_existing.add(f_name)
                            if "files" not in local_memory: local_memory["files"] = []
                            local_memory["files"].append(f_name)
                            save_local_memory(local_memory)
                        except Exception as e:
                            print(f"   ❌ שגיאה: {e}")
                            if os.path.exists(f_name): os.remove(f_name)

            local_memory["last_msg_id"] = m.id
            if m.id % 5 == 0: save_local_memory(local_memory)

if __name__ == '__main__':
    asyncio.run(main())
