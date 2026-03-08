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
MEMORY_FILENAME = 'terabox_memory.json'  # קובץ זיכרון נפרד לבוט הזה!

sys.stdout.reconfigure(encoding='utf-8')

# --- התחברות לגוגל ---
try:
    token_data = json.loads(os.environ['GOOGLE_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_data)
    drive_service = build('drive', 'v3', credentials=creds)
    print(">>> V מחובר לגוגל דרייב!")
except Exception as e:
    print(f">>> X שגיאה בחיבור לגוגל: {e}")
    sys.exit(1)

# === פונקציות עזר ===

def get_clean_name(name):
    if not name: return "Unknown_File"
    return re.sub(r'[\\/*?:"<>|\']', "", name).strip()

def get_terabox_link(url):
    """ מנסה לחלץ קישור הורדה ישיר מ-TeraBox """
    print(f"   ⏳ מנסה לפענח קישור TeraBox...")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"}
        api_url = f"https://terabox-dl.qtcloud.workers.dev/api/get-info?url={url}"
        
        resp = requests.get(api_url, headers=headers, timeout=20)
        try:
            data = resp.json()
        except:
            print("   ⚠️ השרת של TeraBox החזיר תשובה לא תקינה.")
            return None

        if data.get("ok"):
            file_info = data.get("list", [{}])[0]
            name = file_info.get("filename", "terabox_file")
            link = file_info.get("download_link")
            print(f"   V הצלחה! זוהה: {name}")
            return {"name": name, "download_url": link}
        else:
            print(f"   X נכשל: {data.get('message')}")
            return None
    except Exception as e:
        print(f"   X שגיאת תקשורת: {e}")
        return None

# === ניהול זיכרון ===

def load_memory():
    print(">>> 🧠 טוען את מפת TeraBox...")
    memory = {"files": [], "last_msg_id": 0} # מבנה פשוט יותר לטרה בוקס
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
            print(f">>> V נטען זיכרון קיים.")
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
    safe_name = clean_name.replace("'", "\\'")
    q = f"name='{safe_name}' and '{DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    try:
        res = drive_service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get('files', [])
        if res: return res[0]['id']
        return drive_service.files().create(body={'name': clean_name, 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'}, fields='id', supportsAllDrives=True).execute().get('id')
    except: return None

# === הלוגיקה הראשית ===

async def main():
    memory_data, memory_file_id = load_memory()
    current_msg_id = START_FROM_ID if START_FROM_ID > 0 else memory_data.get("last_msg_id", 0)
    
    # וודא שיש רשימת קבצים בזיכרון
    if "files" not in memory_data: memory_data["files"] = []

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== 📦 בוט TeraBox מתחיל (מ-ID: {current_msg_id}) ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            
            memory_data["last_msg_id"] = m.id
            txt = m.text or ""
            
            # חיפוש רק קישורי טרה-בוקס
            tera_links = re.findall(r'(https?://(?:www\.)?(?:terabox\.com|nephobox\.com)/s/[\w\-]+)', txt)
            
            if tera_links:
                print(f"\n🔎 [ID: {m.id}] נמצאו {len(tera_links)} קישורי TeraBox בהודעה.")
            
            for t_url in tera_links:
                info = get_terabox_link(t_url)
                
                if info and info["download_url"]:
                    f_name = get_clean_name(info["name"])
                    
                    # בדיקה בזיכרון אם כבר הורדנו
                    if f_name in memory_data["files"]:
                        print(f"   ⏩ הקובץ '{f_name}' כבר קיים בזיכרון. מדלג.")
                        continue

                    target_folder = "TeraBox_Downloads"
                    folder_id = get_or_create_folder(target_folder)
                    
                    print(f"   ⬇️ מוריד: {f_name}")
                    try:
                        # הורדה בזרם (Stream)
                        with requests.get(info["download_url"], stream=True, timeout=60) as r:
                            r.raise_for_status()
                            with open(f_name, 'wb') as f:
                                for chunk in r.iter_content(chunk_size=8192): 
                                    f.write(chunk)
                        
                        print(f"   ⬆️ מעלה לדרייב...")
                        media = MediaFileUpload(f_name, resumable=True)
                        drive_service.files().create(body={'name': f_name, 'parents': [folder_id]}, media_body=media, supportsAllDrives=True).execute()
                        
                        print(f"   ✅ הושלם!")
                        os.remove(f_name)
                        
                        # עדכון זיכרון ושמירה
                        memory_data["files"].append(f_name)
                        memory_file_id = save_memory_force(memory_data, memory_file_id)
                        
                    except Exception as e:
                        print(f"   ❌ שגיאה בהורדה/העלאה: {e}")
                        if os.path.exists(f_name): os.remove(f_name)
            
            # שומרים את המיקום כל כמה הודעות ליתר ביטחון
            if m.id % 5 == 0:
                memory_file_id = save_memory_force(memory_data, memory_file_id)

if __name__ == '__main__':
    asyncio.run(main())import os, re, asyncio, json, sys, io, requests
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
MEMORY_FILENAME = 'terabox_memory.json'  # קובץ זיכרון נפרד לבוט הזה!

sys.stdout.reconfigure(encoding='utf-8')

# --- התחברות לגוגל ---
try:
    token_data = json.loads(os.environ['GOOGLE_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_data)
    drive_service = build('drive', 'v3', credentials=creds)
    print(">>> V מחובר לגוגל דרייב!")
except Exception as e:
    print(f">>> X שגיאה בחיבור לגוגל: {e}")
    sys.exit(1)

# === פונקציות עזר ===

def get_clean_name(name):
    if not name: return "Unknown_File"
    return re.sub(r'[\\/*?:"<>|\']', "", name).strip()

def get_terabox_link(url):
    """ מנסה לחלץ קישור הורדה ישיר מ-TeraBox """
    print(f"   ⏳ מנסה לפענח קישור TeraBox...")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"}
        api_url = f"https://terabox-dl.qtcloud.workers.dev/api/get-info?url={url}"
        
        resp = requests.get(api_url, headers=headers, timeout=20)
        try:
            data = resp.json()
        except:
            print("   ⚠️ השרת של TeraBox החזיר תשובה לא תקינה.")
            return None

        if data.get("ok"):
            file_info = data.get("list", [{}])[0]
            name = file_info.get("filename", "terabox_file")
            link = file_info.get("download_link")
            print(f"   V הצלחה! זוהה: {name}")
            return {"name": name, "download_url": link}
        else:
            print(f"   X נכשל: {data.get('message')}")
            return None
    except Exception as e:
        print(f"   X שגיאת תקשורת: {e}")
        return None

# === ניהול זיכרון ===

def load_memory():
    print(">>> 🧠 טוען את מפת TeraBox...")
    memory = {"files": [], "last_msg_id": 0} # מבנה פשוט יותר לטרה בוקס
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
            print(f">>> V נטען זיכרון קיים.")
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
    safe_name = clean_name.replace("'", "\\'")
    q = f"name='{safe_name}' and '{DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    try:
        res = drive_service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get('files', [])
        if res: return res[0]['id']
        return drive_service.files().create(body={'name': clean_name, 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'}, fields='id', supportsAllDrives=True).execute().get('id')
    except: return None

# === הלוגיקה הראשית ===

async def main():
    memory_data, memory_file_id = load_memory()
    current_msg_id = START_FROM_ID if START_FROM_ID > 0 else memory_data.get("last_msg_id", 0)
    
    # וודא שיש רשימת קבצים בזיכרון
    if "files" not in memory_data: memory_data["files"] = []

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== 📦 בוט TeraBox מתחיל (מ-ID: {current_msg_id}) ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            
            memory_data["last_msg_id"] = m.id
            txt = m.text or ""
            
            # חיפוש רק קישורי טרה-בוקס
            tera_links = re.findall(r'(https?://(?:www\.)?(?:terabox\.com|nephobox\.com)/s/[\w\-]+)', txt)
            
            if tera_links:
                print(f"\n🔎 [ID: {m.id}] נמצאו {len(tera_links)} קישורי TeraBox בהודעה.")
            
            for t_url in tera_links:
                info = get_terabox_link(t_url)
                
                if info and info["download_url"]:
                    f_name = get_clean_name(info["name"])
                    
                    # בדיקה בזיכרון אם כבר הורדנו
                    if f_name in memory_data["files"]:
                        print(f"   ⏩ הקובץ '{f_name}' כבר קיים בזיכרון. מדלג.")
                        continue

                    target_folder = "TeraBox_Downloads"
                    folder_id = get_or_create_folder(target_folder)
                    
                    print(f"   ⬇️ מוריד: {f_name}")
                    try:
                        # הורדה בזרם (Stream)
                        with requests.get(info["download_url"], stream=True, timeout=60) as r:
                            r.raise_for_status()
                            with open(f_name, 'wb') as f:
                                for chunk in r.iter_content(chunk_size=8192): 
                                    f.write(chunk)
                        
                        print(f"   ⬆️ מעלה לדרייב...")
                        media = MediaFileUpload(f_name, resumable=True)
                        drive_service.files().create(body={'name': f_name, 'parents': [folder_id]}, media_body=media, supportsAllDrives=True).execute()
                        
                        print(f"   ✅ הושלם!")
                        os.remove(f_name)
                        
                        # עדכון זיכרון ושמירה
                        memory_data["files"].append(f_name)
                        memory_file_id = save_memory_force(memory_data, memory_file_id)
                        
                    except Exception as e:
                        print(f"   ❌ שגיאה בהורדה/העלאה: {e}")
                        if os.path.exists(f_name): os.remove(f_name)
            
            # שומרים את המיקום כל כמה הודעות ליתר ביטחון
            if m.id % 5 == 0:
                memory_file_id = save_memory_force(memory_data, memory_file_id)

if __name__ == '__main__':
    asyncio.run(main())
