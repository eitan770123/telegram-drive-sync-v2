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
COOKIES_CONTENT = os.environ.get('TERABOX_COOKIES_FILE') 
MEMORY_FILENAME = 'terabox_memory.json'

sys.stdout.reconfigure(encoding='utf-8')

# --- בדיקת סוד ---
if not COOKIES_CONTENT:
    print(">>> ❌ שגיאה: הסוד TERABOX_COOKIES_FILE חסר!")
    sys.exit(1)

# --- פונקציית הקסם: המרת cookies.txt לשימוש בפייתון ---
def parse_netscape_cookies(content):
    """ הופך את הטקסט של cookies.txt למילון שפייתון מבין """
    cookies = {}
    for line in content.splitlines():
        # דילוג על הערות ושורות ריקות
        if line.startswith('#') or not line.strip():
            continue
        
        parts = line.split('\t')
        # פורמט נטסקייפ מכיל 7 עמודות בדרך כלל
        if len(parts) >= 7:
            name = parts[5]
            value = parts[6].strip()
            cookies[name] = value
    return cookies

# טעינת הקוקיז לזיכרון
COOKIE_DICT = parse_netscape_cookies(COOKIES_CONTENT)
print(f">>> 🍪 הקוקיז פוענח בהצלחה ({len(COOKIE_DICT)} ערכים).")

# --- חיבור לדרייב ---
try:
    token_data = json.loads(os.environ['GOOGLE_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_data)
    drive_service = build('drive', 'v3', credentials=creds)
    print(">>> V מחובר לגוגל דרייב!")
except Exception as e:
    print(f">>> X שגיאה בחיבור לגוגל: {e}")
    sys.exit(1)

# === פונקציות טרה-בוקס משופרות ===

def get_clean_name(name):
    if not name: return "Unknown_File"
    return re.sub(r'[\\/*?:"<>|\']', "", name).strip()

def get_terabox_download_link(url):
    print(f"   ⏳ מנסה לפצח את הקישור...")
    try:
        # טיפול בקישורים מסוג sharing/link?surl=...
        # החוק של טרה-בוקס: אם הקישור הוא surl=abcde, הקוד האמיתי הוא 1abcde
        if 'surl=' in url:
            short_key = '1' + url.split('surl=')[1].split('&')[0]
        else:
            short_key = url.split('/')[-1]

        # שימוש ב-User Agent של כרום רגיל
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://www.terabox.com/",
            "Origin": "https://www.terabox.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9"
        }

        session = requests.Session()
        session.headers.update(headers)
        # טעינת הקוקיז שהמרנו מהקובץ
        session.cookies.update(COOKIE_DICT)

        # 1. קבלת פרטי קובץ
        info_url = f"https://www.terabox.com/api/shorturlinfo?shorturl={short_key}&root=1"
        resp = session.get(info_url)
        data = resp.json()
        
        if data.get('errno') != 0:
            err = data.get('errno')
            if err == 400210:
                print(f"   X שגיאה 400210: הקוקיז נדחה (אולי IP שונה).")
            else:
                print(f"   X שגיאת טרה-בוקס: {err}")
            return None

        file_list = data.get('list', [])
        if not file_list: 
            print("   ⚠️ הקישור תקין אך לא נמצאו קבצים.")
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
        
        if d_data.get('errno') != 0:
             print(f"   X שגיאה בקבלת לינק: {d_data.get('errno')}")
             return None

        dlink = d_data.get('dlink', [{}])[0].get('dlink')
        
        if dlink:
            return {"name": filename, "download_url": dlink, "headers": headers, "cookies": session.cookies}
            
    except Exception as e:
        print(f"   X שגיאה כללית בפענוח: {e}")
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

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== 🍪 בוט TeraBox (Parser Edition) מתחיל מ-ID: {current_msg_id} ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            
            # אם יש טלגרם מדלגים
            if re.search(r't\.me/', m.text or ""):
                local_memory["last_msg_id"] = m.id
                continue
            
            # זיהוי כל סוגי הקישורים כולל surl
            tera_links = re.findall(r'(https?://(?:www\.)?(?:terabox\.com|nephobox\.com|teraboxapp\.com)/(?:s/|sharing/link\?surl=)[\w\-]+)', m.text or "")
            
            if tera_links:
                print(f"\n🔎 [ID: {m.id}] קישורי TeraBox...")
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
                            # שימוש בקוקיז המעובדים גם להורדה
                            with requests.get(info["download_url"], headers=info["headers"], cookies=info["cookies"], stream=True, timeout=120) as r:
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
