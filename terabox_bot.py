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

# === מנוע TeraBox App ===

class TeraBoxAppEngine:
    def __init__(self, cookies):
        self.session = requests.Session()
        self.session.cookies.update(cookies)
        # זהות של אפליקציית אנדרואיד אמיתית
        self.session.headers.update({
            "User-Agent": "TeraBox/3.33.0 (Linux; Android 13; SM-S908B; Build/TP1A.220624.014)",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Referer": "https://www.terabox.com/wap/home", # חשוב למובייל
        })
        self.bdstoken = cookies.get('bdstoken', '')

    def fetch_bdstoken_if_missing(self):
        if self.bdstoken: return
        print("   🕵️‍♂️ מחפש bdstoken...")
        try:
            # במובייל זה נמצא ב-wap/home
            resp = self.session.get("https://www.terabox.com/wap/home")
            match = re.search(r'"bdstoken"\s*:\s*"([^"]+)"', resp.text)
            if match:
                self.bdstoken = match.group(1)
                print(f"   🔑 נמצא: {self.bdstoken[:10]}...")
            else:
                # נסיון נוסף בדף הראשי
                resp2 = self.session.get("https://www.terabox.com/main")
                match2 = re.search(r'"bdstoken"\s*:\s*"([^"]+)"', resp2.text)
                if match2:
                    self.bdstoken = match2.group(1)
                    print(f"   🔑 נמצא (main): {self.bdstoken[:10]}...")
        except: pass

    def process_link(self, url):
        self.fetch_bdstoken_if_missing()
        url = url.rstrip(').,;]')
        
        # חילוץ surl
        short_key = ""
        if 'surl=' in url:
            try:
                val = url.split('surl=')[1].split('&')[0]
                short_key = '1' + val
            except: pass
        else:
            # Handle URLs like https://terabox.com/s/1d0H4Zq85b5_B6qTjK-J8tA
            # and https://terabox.com/s/d0H4Zq85b5_B6qTjK-J8tA
            short_key = url.split('/')[-1]
            if short_key and not short_key.startswith('1'):
                short_key = '1' + short_key
        
        if not short_key: return None

        try:
            # 1. קבלת פרטי קובץ (שימוש ב-API הרגיל, הוא עובד טוב)
            api_url = f"https://www.terabox.com/api/shorturlinfo?shorturl={short_key}&root=1"
            resp = self.session.get(api_url)
            data = resp.json()
            
            if data.get('errno') != 0:
                print(f"   X שגיאת פרטים: {data.get('errno')}")
                return None
            
            file_list = data.get('list', [])
            if not file_list: return None
            
            target_file = file_list[0]
            filename = target_file['server_filename']
            fs_id = target_file['fs_id']
            
            print(f"   V זוהה: {filename}")
            
            # 2. שמירה לחשבון - התיקון הקריטי
            # אנחנו נשלח את הכל בתוך ה-DATA (POST Body) ולא ב-URL
            # ונשתמש בנתיב המותאם למובייל אם הראשי נכשל
            
            save_url = "https://www.terabox.com/share/save"
            
            post_data = {
                "app_id": "250528",
                "bdstoken": self.bdstoken,
                "fid_list": f"[{fs_id}]",
                "path": "/",
                "uk": str(data.get('uk')),
                "shareid": str(data.get('shareid')),
                "sign": str(data.get('sign')),
                "timestamp": str(data.get('timestamp')),
                "clienttype": "0" # 0 = Web/Mobile Web
            }
            
            # עדכון HEADER ספציפי לשמירה
            self.session.headers.update({
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"https://www.terabox.com/wap/share/filelist?surl={short_key[1:]}"
            })
            
            time.sleep(1)
            save_resp = self.session.post(save_url, data=post_data)
            
            # אם עדיין 405, ננסה טריק נוסף: להכניס את bdstoken ל-URL
            if save_resp.status_code == 405:
                print("   ⚠️ שגיאת 405, מנסה נתיב חלופי...")
                save_url_alt = f"https://www.terabox.com/share/save?app_id=250528&bdstoken={self.bdstoken}&clienttype=0"
                save_resp = self.session.post(save_url_alt, data=post_data)

            try:
                save_data = save_resp.json()
            except:
                print(f"   ❌ שגיאת שמירה (HTML): {save_resp.status_code}")
                # print(save_resp.text[:300]) # לדיבאג
                return None

            if save_data.get('errno') not in [0, 12000]:
                print(f"   X שגיאת שמירה API: {save_data.get('errno')}")
                return None

            # 3. מציאת ה-ID הפרטי החדש
            # נשתמש ב-filemanager list שהוא אמין יותר
            list_url = f"https://www.terabox.com/api/filemanager?oper=list&app_id=250528&order=time&desc=1&dir=/&num=100&bdstoken={self.bdstoken}"
            
            list_resp = self.session.get(list_url)
            list_data = list_resp.json()
            
            private_fs_id = None
            if list_data.get('list'):
                for f in list_data['list']:
                    if f['server_filename'] == filename:
                        private_fs_id = f['fs_id']
                        break
            
            if not private_fs_id: private_fs_id = fs_id 

            # 4. הורדה פרטית (משתמשים ב-User Agent של האפליקציה)
            d_api = "https://www.terabox.com/api/download"
            d_params = {
                "app_id": "250528",
                "fidlist": f"[{private_fs_id}]",
                "type": "dlink"
            }
            
            d_resp = self.session.get(d_api, params=d_params)
            d_data = d_resp.json()
            
            dlink = None
            if 'dlink' in d_data:
                if isinstance(d_data['dlink'], list) and len(d_data['dlink']) > 0:
                    dlink = d_data['dlink'][0]['dlink']
                elif isinstance(d_data['dlink'], str):
                    dlink = d_data['dlink']

            if dlink:
                return {
                    "name": filename,
                    "url": dlink,
                    "fs_id_to_delete": private_fs_id
                }
            else:
                print(f"   X לא התקבל dlink פרטי.")

        except Exception as e:
            print(f"   X קריסה בתהליך: {e}")
        
        return None

    def delete_file(self, fs_id):
        try:
            del_url = "https://www.terabox.com/api/filemanager"
            params = {
                "app_id": "250528",
                "oper": "delete",
                "target": f"[{fs_id}]",
                "bdstoken": self.bdstoken,
                "clienttype": "0"
            }
            self.session.post(del_url, params=params)
        except: pass
    
    def download_to_disk(self, url, filename):
        try:
            # שימוש ב-headers של האפליקציה להורדה
            headers = self.session.headers.copy()
            # לפעמים Host מפריע בהורדה
            if 'Host' in headers: del headers['Host']
            
            with self.session.get(url, headers=headers, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(filename, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk: f.write(chunk)
            return True
        except Exception as e:
             print(f"   X שגיאת רשת בהורדה: {e}")
             return False

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

    # אתחול המנוע
    tera_engine = TeraBoxAppEngine(COOKIE_DICT)

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== 🍪 בוט TeraBox (Official App Mode) מתחיל מ-ID: {current_msg_id} ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            
            found_urls = re.findall(r'(https?://[^\s\)]*terabox[^\s\)]*)', m.text or "")
            
            if found_urls:
                print(f"--- הודעה {m.id}: נמצאו {len(found_urls)} קישורים.")
                for raw_url in found_urls:
                    
                    print(f"   ⏳ מעבד: {raw_url}")
                    file_info = tera_engine.process_link(raw_url)
                    
                    if file_info:
                        f_name = file_info['name']
                        d_url = file_info['url']
                        
                        if is_file_already_in_drive(f_name, all_existing):
                            print(f"   ⏩ הקובץ '{f_name}' כבר קיים. מדלג.")
                            tera_engine.delete_file(file_info['fs_id_to_delete'])
                            continue

                        folder_id = get_or_create_folder("TeraBox_Downloads")
                        print(f"   ⬇️ מוריד (App Mode)...")
                        
                        success = tera_engine.download_to_disk(d_url, f_name)
                        
                        if success:
                            print(f"   ⬆️ מעלה לדרייב...")
                            try:
                                media = MediaFileUpload(f_name, resumable=True)
                                drive_service.files().create(body={'name': f_name, 'parents': [folder_id]}, media_body=media, supportsAllDrives=True).execute()
                                print(f"   ✅ הושלם!")
                                
                                os.remove(f_name)
                                all_existing.add(f_name)
                                if "files" not in local_memory: local_memory["files"] = []
                                local_memory["files"].append(f_name)
                                save_local_memory(local_memory)
                            except Exception as e:
                                print(f"   ❌ שגיאת העלאה: {e}")
                                if os.path.exists(f_name): os.remove(f_name)
                        
                        # ניקוי
                        tera_engine.delete_file(file_info['fs_id_to_delete'])

            local_memory["last_msg_id"] = m.id
            if m.id % 5 == 0: save_local_memory(local_memory)

if __name__ == '__main__':
    asyncio.run(main())
