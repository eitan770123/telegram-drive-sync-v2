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

def parse_netscape_cookies(content):
    cookies = {}
    for line in content.splitlines():
        if line.startswith('#') or not line.strip(): continue
        parts = line.split('\t')
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6].strip()
    return cookies

COOKIE_DICT = parse_netscape_cookies(COOKIES_CONTENT)
print(f">>> 🍪 הקוקיז פוענח בהצלחה ({len(COOKIE_DICT)} ערכים).")

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
    print(f"   ⏳ מנסה לפצח: {url}")
    try:
        # טיפול ב-surl
        if 'surl=' in url:
            try:
                surl_val = url.split('surl=')[1].split('&')[0]
                short_key = '1' + surl_val
            except:
                print("      X נכשל בחילוץ surl")
                return None
        else:
            short_key = url.split('/')[-1]

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://www.terabox.com/",
            "Origin": "https://www.terabox.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9"
        }

        session = requests.Session()
        session.headers.update(headers)
        session.cookies.update(COOKIE_DICT)

        # 1. קבלת פרטי קובץ והרשאות שיתוף
        info_url = f"https://www.terabox.com/api/shorturlinfo?shorturl={short_key}&root=1"
        resp = session.get(info_url)
        data = resp.json()
        
        if data.get('errno') != 0:
            print(f"   X שגיאת טרה-בוקס (info): {data.get('errno')}")
            return None

        file_list = data.get('list', [])
        if not file_list: 
            print("   ⚠️ הקישור תקין אך לא נמצאו קבצים.")
            return None

        file_item = file_list[0]
        filename = file_item['server_filename']
        fs_id = file_item['fs_id']
        
        # === התיקון: חילוץ נתוני השיתוף ===
        # בשביל להוריד קובץ משותף, חייבים לשלוח את הפרטים האלה
        shareid = data.get('shareid')
        uk = data.get('uk')
        sign = data.get('sign')
        timestamp = data.get('timestamp')
        
        print(f"   V זוהה: {filename} (ShareID: {shareid})")

        # 2. בקשת הורדה דרך ממשק השיתוף (share/download)
        # זה הפתרון לשגיאה 2
        download_api = "https://www.terabox.com/share/download"
        
        params = {
            "app_id": "250528",
            "web": "1",
            "channel": "dubox",
            "uk": uk,
            "shareid": shareid,
            "timestamp": timestamp,
            "sign": sign,
            "fid_list": f"[{fs_id}]",
            "type": "dlink" # לפעמים צריך ולפעמים לא, לא מזיק
        }
        
        d_resp = session.get(download_api, params=params)
        d_data = d_resp.json()
        
        if d_data.get('errno') != 0:
             print(f"   X שגיאה בקבלת לינק הורדה: {d_data.get('errno')} (נסה לרענן קוקיז)")
             return None

        dlink = d_data.get('dlink')
        
        if dlink:
            return {"name": filename, "download_url": dlink, "headers": headers, "cookies": session.cookies}
            
    except Exception as e:
        print(f"   X שגיאה בפענוח: {e}")
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
        print(f"\n=== 🍪 בוט TeraBox (Share-Link Fix) מתחיל מ-ID: {current_msg_id} ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            
            # אם יש טלגרם מדלגים
            if re.search(r't\.me/', m.text or ""):
                print(f"--- הודעה {m.id}: יש טלגרם, מדלג.")
                local_memory["last_msg_id"] = m.id
                continue
            
            # חיפוש מתירני
            found_urls = re.findall(r'(https?://[^\s]*terabox[^\s]*)', m.text or "")
            
            if found_urls:
                print(f"--- הודעה {m.id}: נמצאו {len(found_urls)} קישורי טרה-בוקס.")
                for t_url in found_urls:
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
                            print(f"   ❌ שגיאה בהורדה: {e}")
                            if os.path.exists(f_name): os.remove(f_name)

            local_memory["last_msg_id"] = m.id
            if m.id % 5 == 0: save_local_memory(local_memory)

if __name__ == '__main__':
    asyncio.run(main())
