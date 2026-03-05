import os, re, asyncio, json, sys, io
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

# === פונקציית עזר לניקוי שמות (קריטי להתאמה!) ===
def get_clean_name(name):
    # מסיר תווים מיוחדים ורווחים כפולים
    clean = re.sub(r'[\\/*?:"<>|\']', "", name).strip()
    return clean

# === ניהול זיכרון חכם ===

def load_memory():
    print(">>> 🧠 טוען את מפת הקבצים...")
    memory = {"files": {}, "completed": [], "last_msg_id": 0}
    file_id = None

    # 1. נסיון לטעון קובץ קיים
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
            print(f">>> V נטען קובץ זיכרון קיים ({len(memory['files'])} תיקיות).")
            return memory, file_id
    except: pass

    # 2. אם אין קובץ - סורק את הדרייב ומייצר אותו
    print(">>> ⚠️ לא נמצא זיכרון. סורק את הדרייב כדי למנוע כפילויות (זה ייקח רגע)...")
    
    page_token = None
    while True:
        # מושך את כל התיקיות
        q_folders = f"'{DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        folders = drive_service.files().list(q=q_folders, fields='nextPageToken, files(id, name)', pageSize=1000, supportsAllDrives=True, includeItemsFromAllDrives=True, pageToken=page_token).execute()
        
        for folder in folders.get('files', []):
            # משתמשים בשם הנקי כפי שהוא בדרייב
            f_name = folder['name'] 
            f_id = folder['id']
            memory["files"][f_name] = []
            
            # סריקת הקבצים בתוך התיקייה
            pt_files = None
            while True:
                files_res = drive_service.files().list(q=f"'{f_id}' in parents and trashed=false", fields='nextPageToken, files(name)', pageSize=1000, supportsAllDrives=True, includeItemsFromAllDrives=True, pageToken=pt_files).execute()
                for f in files_res.get('files', []):
                    memory["files"][f_name].append(f['name'])
                pt_files = files_res.get('nextPageToken')
                if not pt_files: break
        
        page_token = folders.get('nextPageToken')
        if not page_token: break
    
    print(f">>> V הסריקה הושלמה! שומר את הקובץ לדרייב...")
    # שומרים מיד כדי שתראה את הקובץ נוצר
    file_id = save_memory_force(memory, file_id)
    return memory, file_id

def save_memory_force(data, file_id):
    """ שומר את הזיכרון לדרייב """
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
    except Exception as e: 
        print(f"Error saving memory: {e}")
        return file_id

# === לוגיקה ראשית ===

def get_or_create_folder(clean_name):
    # שימוש בשם שכבר נוקה
    safe_name = clean_name.replace("'", "\\'")
    q = f"name='{safe_name}' and '{DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    try:
        res = drive_service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get('files', [])
        if res: return res[0]['id']
        return drive_service.files().create(body={'name': clean_name, 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'}, fields='id', supportsAllDrives=True).execute().get('id')
    except: return None

async def main():
    # טעינה וסריקה ראשונית
    memory_data, memory_file_id = load_memory()
    
    current_msg_id = START_FROM_ID if START_FROM_ID > 0 else memory_data.get("last_msg_id", 0)

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== הבוט מתחיל (מדלג עד הודעה {current_msg_id}) ===")
        
        links_map = {}
        async for m in client.iter_messages(MAIN_CHANNEL, limit=500):
            if m.id < current_msg_id: continue
            if m.text:
                for f in re.findall(r't\.me/(?:\+|joinchat/)?([\w\-]{10,})', m.text):
                    if f not in links_map: links_map[f] = m.id

        sorted_items = sorted(links_map.items(), key=lambda item: item[1])
        if not sorted_items: print(">>> הכל מעודכן."); return

        counter = 0
        for identifier, msg_id in sorted_items:
            print(f"\n🔥 [ID: {msg_id}] מעבד: {identifier}")
            memory_data["last_msg_id"] = msg_id
            
            if identifier in memory_data["completed"]:
                print(f"✅ ערוץ זה מסומן כ'הושלם'. מדלג!")
                continue

            try:
                entity = None
                try: 
                    updates = await client(functions.messages.ImportChatInviteRequest(hash=identifier))
                    entity = updates.chats[0]
                except: 
                    try: entity = await client.get_entity(identifier)
                    except: continue

                # התיקון הגדול: מנקים את השם *לפני* שבודקים בזיכרון
                clean_title = get_clean_name(entity.title)
                folder_id = get_or_create_folder(clean_title)
                if not folder_id: continue

                # בדיקה בזיכרון לפי השם הנקי
                if clean_title not in memory_data["files"]: memory_data["files"][clean_title] = []

                channel_ok = True
                async for msg in client.iter_messages(entity, limit=None):
                    if msg.media:
                        f_name = f"file_{msg.id}"
                        for attr in msg.document.attributes:
                            if isinstance(attr, types.DocumentAttributeFilename): f_name = attr.file_name; break
                        
                        # בדיקה מול הרשימה הקיימת
                        if f_name in memory_data["files"][clean_title]:
                            print(f"⏩ קיים: {f_name}")
                            continue

                        print(f"⬇️ מוריד: {f_name}")
                        path = await client.download_media(msg)
                        if not path: 
                            channel_ok = False
                            continue
                        
                        try:
                            final_name = os.path.basename(path)
                            drive_service.files().create(body={'name': final_name, 'parents': [folder_id]}, media_body=MediaFileUpload(path, resumable=True), supportsAllDrives=True).execute()
                            print(f"✅ עלה: {final_name}")
                            os.remove(path)
                            
                            memory_data["files"][clean_title].append(final_name)
                            counter += 1
                            # שמירה כל 5 קבצים
                            if counter % 5 == 0: memory_file_id = save_memory_force(memory_data, memory_file_id)
                        except Exception as e: 
                            print(f"❌ שגיאה: {e}")
                            channel_ok = False

                if channel_ok:
                    print(f"🏆 ערוץ '{clean_title}' הושלם! מסמן בזיכרון.")
                    memory_data["completed"].append(identifier)
                    memory_file_id = save_memory_force(memory_data, memory_file_id)

            except Exception as e: 
                print(f"!!! שגיאה בערוץ: {e}")
            
            memory_file_id = save_memory_force(memory_data, memory_file_id)

if __name__ == '__main__':
    asyncio.run(main())
