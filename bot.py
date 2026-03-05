import os, re, asyncio, json, sys, io, requests
from telethon import TelegramClient, functions, types
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# --- הגדרות ---
API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
MAIN_CHANNEL = os.environ['MAIN_CHANNEL']
DRIVE_FOLDER_ID = os.environ['DRIVE_FOLDER_ID']
START_FROM_ID = int(os.environ.get('START_FROM_MSG_ID', 0))
MEMORY_FILENAME = 'bot_memory_v2.json'

sys.stdout.reconfigure(encoding='utf-8')

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
    return re.sub(r'[\\/*?:"<>|\']', "", name).strip()

def load_memory():
    memory = {"files": {}, "completed": [], "last_msg_id": 0}
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
            if "completed" not in memory: memory["completed"] = []
            print(f">>> V זיכרון נטען.")
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

# === פונקציית חילוץ מ-TeraBox ===
def get_terabox_link(url):
    """ מנסה לחלץ קישור הורדה ישיר מ-TeraBox באמצעות API חיצוני """
    try:
        # שימוש בשירות API חינמי (לפעמים דורש רענון)
        api_url = f"https://terabox-dl.qtcloud.workers.dev/api/get-info?url={url}"
        resp = requests.get(api_url, timeout=15).json()
        if resp.get("ok"):
            file_info = resp.get("list", [{}])[0]
            return {
                "name": file_info.get("filename"),
                "download_url": file_info.get("download_link")
            }
    except: pass
    return None

async def main():
    memory_data, memory_file_id = load_memory()
    current_msg_id = START_FROM_ID if START_FROM_ID > 0 else memory_data.get("last_msg_id", 0)

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== סורק הודעות מ-ID: {current_msg_id} ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=1000, reverse=True):
            if m.id <= current_msg_id: continue
            
            memory_data["last_msg_id"] = m.id
            
            # 1. חיפוש קישורי טלגרם (ערוצים)
            tg_links = re.findall(r't\.me/(?:\+|joinchat/)?([\w\-]{10,})', m.text or "")
            
            # 2. חיפוש קישורי TeraBox
            tera_links = re.findall(r'(https?://(?:www\.)?terabox\.com/s/[\w\-]+)', m.text or "")
            
            # עיבוד ערוצי טלגרם
            for identifier in tg_links:
                if identifier in memory_data["completed"]: continue
                try:
                    entity = await client.get_entity(identifier)
                    clean_title = get_clean_name(entity.title)
                    f_id = drive_service.files().create(body={'name': clean_title, 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'}, fields='id', supportsAllDrives=True).execute().get('id')
                    if clean_title not in memory_data["files"]: memory_data["files"][clean_title] = []
                    
                    async for msg in client.iter_messages(entity, limit=None):
                        if msg.media:
                            f_name = f"file_{msg.id}"
                            try:
                                if hasattr(msg.media, 'document'):
                                    for attr in msg.media.document.attributes:
                                        if isinstance(attr, types.DocumentAttributeFilename): f_name = attr.file_name; break
                            except: pass
                            
                            if f_name in memory_data["files"][clean_title]: continue
                            
                            path = await client.download_media(msg)
                            if path:
                                drive_service.files().create(body={'name': os.path.basename(path), 'parents': [f_id]}, media_body=MediaFileUpload(path, resumable=True), supportsAllDrives=True).execute()
                                os.remove(path)
                                memory_data["files"][clean_title].append(f_name)
                                if len(memory_data["files"][clean_title]) % 5 == 0: memory_file_id = save_memory_force(memory_data, memory_file_id)
                    memory_data["completed"].append(identifier)
                except: pass

            # עיבוד קישורי TeraBox
            for t_url in tera_links:
                print(f"📦 נמצא קישור TeraBox: {t_url}")
                info = get_terabox_link(t_url)
                if info and info["download_url"]:
                    f_name = info["name"]
                    target_folder = "TeraBox_Downloads"
                    folder_id = drive_service.files().create(body={'name': target_folder, 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'}, fields='id', supportsAllDrives=True).execute().get('id')
                    
                    if target_folder not in memory_data["files"]: memory_data["files"][target_folder] = []
                    if f_name in memory_data["files"][target_folder]:
                        print(f"⏩ TeraBox: {f_name} כבר קיים.")
                        continue
                    
                    print(f"⬇️ מוריד מ-TeraBox: {f_name}")
                    r = requests.get(info["download_url"], stream=True)
                    with open(f_name, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
                    
                    drive_service.files().create(body={'name': f_name, 'parents': [folder_id]}, media_body=MediaFileUpload(f_name, resumable=True), supportsAllDrives=True).execute()
                    os.remove(f_name)
                    memory_data["files"][target_folder].append(f_name)
                    memory_file_id = save_memory_force(memory_data, memory_file_id)

            memory_file_id = save_memory_force(memory_data, memory_file_id)

if __name__ == '__main__':
    asyncio.run(main())
