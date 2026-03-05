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
MEMORY_FILENAME = 'bot_memory_v2.json' # שם קובץ חדש לגרסה החדשה

sys.stdout.reconfigure(encoding='utf-8')

try:
    token_data = json.loads(os.environ['GOOGLE_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_data)
    drive_service = build('drive', 'v3', credentials=creds)
    print(">>> V מחובר לגוגל דרייב!")
except Exception as e:
    print(f">>> X שגיאה בחיבור לגוגל: {e}")
    sys.exit(1)

# === ניהול זיכרון חכם ===

def load_memory():
    """ טוען את הזיכרון: אלו ערוצים הושלמו ואלו קבצים קיימים """
    print(">>> 🧠 טוען את מפת הזיכרון מהדרייב...")
    # מבנה הזיכרון: "completed" שומר רשימה של ערוצים שסיימנו ב-100%
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
            # אם חסר המפתח בזיכרון ישן, נוסיף אותו
            if "completed" not in memory: memory["completed"] = []
            print(f">>> V הזיכרון נטען! ({len(memory['completed'])} ערוצים מסומנים כגמורים).")
            return memory, file_id
    except: pass

    print(">>> ⚠️ יוצר קובץ זיכרון חדש...")
    return memory, file_id

def save_memory(data, file_id):
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

# === לוגיקה ראשית ===

def get_or_create_folder(name):
    clean_name = re.sub(r'[\\/*?:"<>|\']', "", name).strip().replace("'", "\\'")
    q = f"name='{clean_name}' and '{DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    try:
        res = drive_service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get('files', [])
        if res: return res[0]['id']
        return drive_service.files().create(body={'name': clean_name.replace("\\'", "'"), 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'}, fields='id', supportsAllDrives=True).execute().get('id')
    except: return None

async def main():
    memory_data, memory_file_id = load_memory()
    # ברירת מחדל: אם יש ID ידני, תשתמש בו. אחרת, תמשיך מהאחרון בזיכרון.
    current_msg_id = START_FROM_ID if START_FROM_ID > 0 else memory_data.get("last_msg_id", 0)

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== הבוט מתחיל (סורק קישורים החל מהודעה {current_msg_id}) ===")
        
        links_map = {}
        # סורק את הערוץ הראשי כדי למצוא קישורים לתיקיות
        async for m in client.iter_messages(MAIN_CHANNEL, limit=500):
            if m.id < current_msg_id: continue
            if m.text:
                for f in re.findall(r't\.me/(?:\+|joinchat/)?([\w\-]{10,})', m.text):
                    if f not in links_map: links_map[f] = m.id

        sorted_items = sorted(links_map.items(), key=lambda item: item[1])
        if not sorted_items: print(">>> הכל מעודכן. אין קישורים חדשים."); return

        counter = 0
        for identifier, msg_id in sorted_items:
            print(f"\n🔥 [ID: {msg_id}] בודק קישור: {identifier}")
            memory_data["last_msg_id"] = msg_id
            
            # --- השיפור החדש: דילוג על ערוצים גמורים ---
            if identifier in memory_data["completed"]:
                print(f"✅ ערוץ זה מסומן כ'הושלם' בזיכרון. מדלג עליו!")
                continue
            # -------------------------------------------

            try:
                entity = None
                try: 
                    updates = await client(functions.messages.ImportChatInviteRequest(hash=identifier))
                    entity = updates.chats[0]
                except: 
                    try: entity = await client.get_entity(identifier)
                    except: continue

                folder_id = get_or_create_folder(entity.title)
                if not folder_id: continue

                if entity.title not in memory_data["files"]: memory_data["files"][entity.title] = []

                print(f"📂 נכנס לסריקה מלאה של: {entity.title}")
                
                # משתנה לבדיקה האם סיימנו את כל הערוץ בהצלחה
                channel_completed_successfully = True

                # limit=None אומר: תסרוק את כל הערוץ עד הסוף!
                async for msg in client.iter_messages(entity, limit=None):
                    if msg.media:
                        f_name = f"file_{msg.id}"
                        for attr in msg.document.attributes:
                            if isinstance(attr, types.DocumentAttributeFilename): f_name = attr.file_name; break
                        
                        # דילוג מהיר אם הקובץ קיים
                        if f_name in memory_data["files"][entity.title]:
                            # print(f"⏩ קיים: {f_name}") # אפשר להוריד את ההערה אם רוצים לראות הכל
                            continue

                        print(f"⬇️ מוריד: {f_name}")
                        path = await client.download_media(msg)
                        if not path: 
                            print("⚠️ דילוג על קובץ שנכשל בהורדה")
                            # אם נכשלנו בקובץ, לא נסמן את הערוץ כהושלם
                            channel_completed_successfully = False 
                            continue
                        
                        try:
                            final_name = os.path.basename(path)
                            drive_service.files().create(body={'name': final_name, 'parents': [folder_id]}, media_body=MediaFileUpload(path, resumable=True), supportsAllDrives=True).execute()
                            print(f"✅ עלה: {final_name}")
                            os.remove(path)
                            
                            memory_data["files"][entity.title].append(final_name)
                            counter += 1
                            if counter % 5 == 0: memory_file_id = save_memory(memory_data, memory_file_id)
                        except Exception as e: 
                            print(f"❌ שגיאה בהעלאה: {e}")
                            channel_completed_successfully = False

                # --- אם הגענו לפה, הערוץ נסרק עד הסוף! ---
                if channel_completed_successfully:
                    print(f"🏆 סיימנו את הערוץ '{entity.title}' בהצלחה מלאה! מסמן כ-Done.")
                    memory_data["completed"].append(identifier)
                    memory_file_id = save_memory(memory_data, memory_file_id)
                else:
                    print(f"⚠️ הערוץ '{entity.title}' נסרק, אך היו שגיאות. לא מסמן כ-Done כדי שנחזור אליו בפעם הבאה.")

            except Exception as e:
                print(f"!!! שגיאה כללית בערוץ: {e}")
            
            memory_file_id = save_memory(memory_data, memory_file_id)

if __name__ == '__main__':
    asyncio.run(main())
