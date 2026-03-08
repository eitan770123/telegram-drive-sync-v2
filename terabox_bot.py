import os, re, asyncio, json, sys, io, requests, time, random
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
# חילוץ bdstoken אם קיים (עוזר לפעולות בחשבון)
BDSTOKEN = COOKIE_DICT.get('bdstoken', '')
print(f">>> 🍪 הקוקיז נטען ({len(COOKIE_DICT)} ערכים).")

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

def get_or_create_folder(clean_name):
    q = f"name='{clean_name}' and '{DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    try:
        res = drive_service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get('files', [])
        if res: return res[0]['id']
        return drive_service.files().create(body={'name': clean_name, 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'}, fields='id', supportsAllDrives=True).execute().get('id')
    except: return None

# === פונקציות עבודה מול TeraBox API ===

def create_session():
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://www.terabox.com/",
        "Origin": "https://www.terabox.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    session.headers.update(headers)
    session.cookies.update(COOKIE_DICT)
    return session

def process_terabox_link(url):
    session = create_session()
    
    # 1. ניקוי ועיבוד הקישור
    url = url.rstrip(').,;]')
    print(f"   ⏳ מעבד: {url}")
    
    short_key = ""
    if 'surl=' in url:
        try:
            val = url.split('surl=')[1].split('&')[0]
            short_key = '1' + val
        except: pass
    else:
        short_key = url.split('/')[-1]
    
    if not short_key: return None

    try:
        # 2. קבלת מידע על הקובץ הציבורי
        info_url = f"https://www.terabox.com/api/shorturlinfo?shorturl={short_key}&root=1"
        resp = session.get(info_url)
        data = resp.json()
        
        if data.get('errno') != 0:
            print(f"   X שגיאת פרטי קובץ: {data.get('errno')}")
            return None
        
        file_list = data.get('list', [])
        if not file_list: return None
        
        # לוקחים את הקובץ הראשון
        target_file = file_list[0]
        filename = target_file['server_filename']
        fs_id = target_file['fs_id'] # זה ה-ID הציבורי
        
        # פרמטרים לשמירה
        shareid = data.get('shareid')
        uk = data.get('uk')
        sign = data.get('sign')
        timestamp = data.get('timestamp')
        
        print(f"   V זוהה: {filename}")
        
        # 3. שמירת הקובץ לחשבון שלך (Save to My TeraBox)
        # זה עוקף את החסימה של ההורדה הציבורית
        save_url = "https://www.terabox.com/share/save"
        save_params = {
            "app_id": "250528",
            "bdstoken": BDSTOKEN,
            "shareid": shareid,
            "uk": uk,
            "sign": sign,
            "timestamp": timestamp,
            "fid_list": f"[{fs_id}]",
            "path": "/" # שומר לתיקייה הראשית
        }
        
        print("   💾 שומר לחשבון שלך...")
        save_resp = session.post(save_url, data=save_params)
        save_data = save_resp.json()
        
        if save_data.get('errno') != 0:
            # אם הקובץ כבר קיים (12000), זה בסדר, נמשיך להורדה
            if save_data.get('errno') == 12000:
                print("   ℹ️ הקובץ כבר קיים בחשבון שלך.")
            else:
                print(f"   X שגיאה בשמירה: {save_data.get('errno')}")
                return None
        
        # 4. מציאת ה-ID הפרטי של הקובץ בחשבון שלך
        # אנחנו צריכים את ה-fs_id החדש (הפרטי) כדי להוריד
        # נחפש בתיקייה הראשית את הקובץ עם השם הזה
        list_url = "https://www.terabox.com/api/list"
        list_params = {"app_id": "250528", "order": "time", "desc": "1", "dir": "/", "num": "100"}
        
        list_resp = session.get(list_url, params=list_params)
        list_data = list_resp.json()
        
        private_fs_id = None
        for f in list_data.get('list', []):
            if f['server_filename'] == filename:
                private_fs_id = f['fs_id']
                break
        
        if not private_fs_id:
            # אם לא מצאנו לפי שם, אולי נשתמש ב-fs_id המקורי (לפעמים זה עובד)
            private_fs_id = fs_id
            
        # 5. הורדה פרטית (Private Download)
        # הורדה מהחשבון שלך כמעט אף פעם לא נחסמת ב-400310
        d_api = "https://www.terabox.com/api/download"
        d_params = {
            "app_id": "250528",
            "fidlist": f"[{private_fs_id}]",
            "type": "dlink"
        }
        
        d_resp = session.get(d_api, params=d_params)
        d_data = d_resp.json()
        
        dlink = d_data.get('dlink')
        if not dlink and isinstance(d_data.get('dlink'), list) and len(d_data.get('dlink')) > 0:
             dlink = d_data.get('dlink')[0].get('dlink')
             
        if dlink:
            return {
                "name": filename,
                "url": dlink,
                "headers": session.headers,
                "cookies": session.cookies,
                "fs_id_to_delete": private_fs_id # נחזיר גם את ה-ID למחיקה
            }
        else:
            print(f"   X לא התקבל לינק הורדה פרטי: {d_data}")
            
    except Exception as e:
        print(f"   X שגיאה בתהליך: {e}")
        
    return None

def delete_from_terabox(fs_id, session):
    """ מוחק את הקובץ מהחשבון כדי לא לסתום אותו """
    del_url = "https://www.terabox.com/api/filemanager"
    params = {
        "app_id": "250528",
        "oper": "delete",
        "target": f"[{fs_id}]",
        "bdstoken": BDSTOKEN
    }
    try:
        session.post(del_url, params=params)
        # print("   🗑️ הקובץ נמחק מהחשבון הזמני.")
    except: pass

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

# === ראשי ===

async def main():
    main_memory, local_memory, _ = load_memory()
    current_msg_id = START_FROM_ID if START_FROM_ID > 0 else local_memory.get("last_msg_id", 0)
    
    all_existing = set(local_memory.get("files", []))
    if "files" in main_memory and isinstance(main_memory["files"], dict):
        for flist in main_memory["files"].values():
            for f in flist: all_existing.add(f)
            
    print(f">>> 🛡️ הגנת כפילויות פעילה ({len(all_existing)} קבצים).")

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== 🍪 בוט TeraBox (Save-and-Download) מתחיל מ-ID: {current_msg_id} ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            
            found_urls = re.findall(r'(https?://[^\s\)]*terabox[^\s\)]*)', m.text or "")
            
            if found_urls:
                print(f"--- הודעה {m.id}: נמצאו {len(found_urls)} קישורים.")
                for raw_url in found_urls:
                    
                    # שימוש בלוגיקה החדשה: שמירה -> הורדה
                    file_info = process_terabox_link(raw_url)
                    
                    if file_info:
                        f_name = file_info['name']
                        d_url = file_info['url']
                        
                        if is_file_already_in_drive(f_name, all_existing):
                            print(f"   ⏩ הקובץ '{f_name}' כבר קיים. מדלג.")
                            # ניקוי מהענן גם אם דילגנו
                            delete_from_terabox(file_info['fs_id_to_delete'], requests.Session()) 
                            continue

                        folder_id = get_or_create_folder("TeraBox_Downloads")
                        print(f"   ⬇️ מוריד (מצב פרטי)...")
                        
                        try:
                            with requests.get(d_url, headers=file_info['headers'], cookies=file_info['cookies'], stream=True, timeout=120) as r:
                                r.raise_for_status()
                                with open(f_name, 'wb') as f:
                                    for chunk in r.iter_content(chunk_size=16384): 
                                        if chunk: f.write(chunk)
                            
                            print(f"   ⬆️ מעלה לדרייב...")
                            media = MediaFileUpload(f_name, resumable=True)
                            drive_service.files().create(body={'name': f_name, 'parents': [folder_id]}, media_body=media, supportsAllDrives=True).execute()
                            print(f"   ✅ הושלם!")
                            
                            os.remove(f_name)
                            all_existing.add(f_name)
                            if "files" not in local_memory: local_memory["files"] = []
                            local_memory["files"].append(f_name)
                            save_local_memory(local_memory)
                            
                        except Exception as e:
                            print(f"   ❌ שגיאה בהורדה: {e}")
                            if os.path.exists(f_name): os.remove(f_name)
                        
                        # ניקוי סופי מהענן של טרה-בוקס
                        try:
                            clean_session = create_session()
                            delete_from_terabox(file_info['fs_id_to_delete'], clean_session)
                        except: pass

            local_memory["last_msg_id"] = m.id
            if m.id % 5 == 0: save_local_memory(local_memory)

if __name__ == '__main__':
    asyncio.run(main())
