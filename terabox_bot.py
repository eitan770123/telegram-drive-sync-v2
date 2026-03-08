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
TERABOX_COOKIE = os.environ.get('TERABOX_COOKIE') # הסוד החדש
MEMORY_FILENAME = 'terabox_memory.json'

sys.stdout.reconfigure(encoding='utf-8')

# --- בדיקות מקדימות ---
if not TERABOX_COOKIE:
    print(">>> ❌ שגיאה: לא הוגדר הסוד TERABOX_COOKIE בהגדרות!")
    sys.exit(1)

try:
    token_data = json.loads(os.environ['GOOGLE_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_data)
    drive_service = build('drive', 'v3', credentials=creds)
    print(">>> V מחובר לגוגל דרייב!")
except Exception as e:
    print(f">>> X שגיאה בחיבור לגוגל: {e}")
    sys.exit(1)

# === פונקציות טרה-בוקס (השיטה עם הקוקיז) ===

def get_terabox_download_link(url):
    """ משתמש בקוקיז כדי לקבל קישור הורדה אמיתי """
    print(f"   ⏳ מנסה לפרוץ את הקישור עם הקוקיז שלך...")
    
    # 1. חילוץ ה-Short URL
    short_key = url.split('/')[-1]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Cookie": f"ndus={TERABOX_COOKIE}",
        "Referer": "https://www.terabox.com/"
    }

    session = requests.Session()
    session.headers.update(headers)

    try:
        # שלב א: קבלת מידע על הקובץ
        info_url = f"https://www.terabox.com/api/shorturlinfo?shorturl={short_key}&root=1"
        resp = session.get(info_url)
        data = resp.json()
        
        if data.get('errno') != 0:
            print(f"   X שגיאת API (קוד {data.get('errno')}): כנראה הקוקי פג תוקף או הקישור מת.")
            return None

        file_list = data.get('list', [])
        if not file_list:
            print("   X התיקייה ריקה או לא נמצאו קבצים.")
            return None

        # אנחנו לוקחים כרגע את הקובץ הראשון (לרוב זה קובץ בודד)
        file_item = file_list[0]
        fs_id = file_item['fs_id']
        filename = file_item['server_filename']
        
        print(f"   V זוהה הקובץ: {filename}")

        # שלב ב: בקשת קישור להורדה
        download_api = "https://www.terabox.com/api/download"
        params = {
            "fidlist": f"[{fs_id}]",
            "type": "dlink"
        }
        
        d_resp = session.get(download_api, params=params)
        d_data = d_resp.json()
        
        if d_data.get('errno') != 0:
             print("   X לא הצלחתי לייצר קישור הורדה.")
             return None
             
        dlink = d_data.get('dlink', [{}])[0].get('dlink')
        if dlink:
            return {"name": filename, "download_url": dlink, "headers": headers}
            
    except Exception as e:
        print(f"   X שגיאה בתהליך: {e}")
    
    return None

# === ניהול זיכרון ===
def load_memory():
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
        print(f"\n=== 🍪 בוט TeraBox (מצב מורשה) מתחיל מ-ID: {current_msg_id} ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            memory_data["last_msg_id"] = m.id
            
            tera_links = re.findall(r'(https?://(?:www\.)?(?:terabox\.com|nephobox\.com|teraboxapp\.com)/s/[\w\-]+)', m.text or "")
            
            if tera_links:
                print(f"\n🔎 [ID: {m.id}] נמצא קישור TeraBox...")

            for t_url in tera_links:
                info = get_terabox_download_link(t_url)
                
                if info and info["download_url"]:
                    f_name = info["name"]
                    
                    if f_name in memory_data["files"]:
                        print(f"   ⏩ הקובץ '{f_name}' כבר קיים בזיכרון.")
                        continue

                    target_folder = "TeraBox_Downloads"
                    folder_id = get_or_create_folder(target_folder)
                    
                    print(f"   ⬇️ מוריד: {f_name}")
                    try:
                        # שימוש ב-Headers הנכונים להורדה
                        with requests.get(info["download_url"], headers=info["headers"], stream=True, timeout=120) as r:
                            r.raise_for_status()
                            with open(f_name, 'wb') as f:
                                for chunk in r.iter_content(chunk_size=8192*2): 
                                    f.write(chunk)
                        
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

            if m.id % 5 == 0:
                memory_file_id = save_memory_force(memory_data, memory_file_id)

if __name__ == '__main__':
    asyncio.run(main())
