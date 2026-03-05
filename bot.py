import os, re, asyncio, json, sys, io, requests
from telethon import TelegramClient, functions, types
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# --- הגדרות מערכת ---
API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
MAIN_CHANNEL = os.environ['MAIN_CHANNEL']
DRIVE_FOLDER_ID = os.environ['DRIVE_FOLDER_ID']
START_FROM_ID = int(os.environ.get('START_FROM_MSG_ID', 0))
MEMORY_FILENAME = 'bot_memory_v2.json'

# הגדרת קידוד להדפסה תקינה בלוגים
sys.stdout.reconfigure(encoding='utf-8')

# --- התחברות לגוגל ---
try:
    token_data = json.loads(os.environ['GOOGLE_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_data)
    drive_service = build('drive', 'v3', credentials=creds)
    print(">>> V מחובר לגוגל דרייב בהצלחה!")
except Exception as e:
    print(f">>> X שגיאה קריטית בחיבור לגוגל: {e}")
    sys.exit(1)

# === פונקציות עזר ===

def get_clean_name(name):
    """ מנקה שמות מתווים בעייתיים לדרייב """
    if not name: return "Unknown_Folder"
    return re.sub(r'[\\/*?:"<>|\']', "", name).strip()

def get_terabox_link(url):
    """ מנסה לחלץ קישור הורדה ישיר מ-TeraBox """
    print(f"   ⏳ מנסה לפענח קישור TeraBox...")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        # שימוש ב-API חיצוני לפענוח
        api_url = f"https://terabox-dl.qtcloud.workers.dev/api/get-info?url={url}"
        
        resp = requests.get(api_url, headers=headers, timeout=20)
        data = resp.json()
        
        if data.get("ok"):
            file_info = data.get("list", [{}])[0]
            d_link = file_info.get("download_link")
            name = file_info.get("filename", "terabox_file")
            print(f"   V הצלחה! זוהה הקובץ: {name}")
            return {"name": name, "download_url": d_link}
        else:
            print(f"   X נכשל: {data.get('message', 'הקישור לא החזיר מידע תקין')}")
            return None
    except Exception as e:
        print(f"   X שגיאת תקשורת עם TeraBox: {e}")
        return None

# === ניהול זיכרון (Cache) ===

def load_memory():
    """ טוען את קובץ הזיכרון כדי לדעת מה כבר הורדנו """
    print(">>> 🧠 טוען את מפת הקבצים...")
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
            print(f">>> V נטען זיכרון קיים ({len(memory['files'])} תיקיות רשומות).")
    except Exception as e:
        print(f">>> ⚠️ לא נמצא זיכרון או שגיאה בטעינה ({e}). מתחיל חדש.")

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
    except: return file_id

def get_or_create_folder(clean_name):
    """ יוצר תיקייה בדרייב או מחזיר את ה-ID שלה אם קיימת """
    safe_name = clean_name.replace("'", "\\'")
    q = f"name='{safe_name}' and '{DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    try:
        res = drive_service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get('files', [])
        if res: return res[0]['id']
        
        return drive_service.files().create(
            body={'name': clean_name, 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'},
            fields='id', supportsAllDrives=True
        ).execute().get('id')
    except: return None

# === הלוגיקה הראשית ===

async def main():
    memory_data, memory_file_id = load_memory()
    
    # קביעת נקודת התחלה
    current_msg_id = START_FROM_ID if START_FROM_ID > 0 else memory_data.get("last_msg_id", 0)

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== 🚀 הבוט מתחיל לעבוד (סורק הודעות חדשות מ-ID: {current_msg_id}) ===")
        
        # סריקה אחורה כדי למצוא את כל הקישורים (מגבלה של 3000 הודעות אחורה)
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            
            # עדכון המיקום הנוכחי בזיכרון
            memory_data["last_msg_id"] = m.id
            
            # חיפוש קישורים בהודעה
            txt = m.text or ""
            tg_links = re.findall(r't\.me/(?:\+|joinchat/)?([\w\-]{10,})', txt)
            tera_links = re.findall(r'(https?://(?:www\.)?(?:terabox\.com|nephobox\.com)/s/[\w\-]+)', txt)
            
            # --- טיפול בערוצי טלגרם ---
            for identifier in tg_links:
                if identifier in memory_data["completed"]:
                    continue # דילוג שקט על ערוצים גמורים

                print(f"\n🔥 [ID: {m.id}] זוהה ערוץ טלגרם: {identifier}")
                try:
                    entity = None
                    try: 
                        updates = await client(functions.messages.ImportChatInviteRequest(hash=identifier))
                        entity = updates.chats[0]
                    except: 
                        try: entity = await client.get_entity(identifier)
                        except: 
                            print("   ⚠️ לא ניתן לגשת לערוץ (אולי הקישור פג תוקף).")
                            continue

                    clean_title = get_clean_name(entity.title)
                    folder_id = get_or_create_folder(clean_title)
                    if not folder_id: continue

                    if clean_title not in memory_data["files"]: memory_data["files"][clean_title] = []

                    channel_ok = True
                    print(f"   📂 סורק את הערוץ: {clean_title}")
                    
                    async for msg in client.iter_messages(entity, limit=None):
                        if msg.media:
                            # זיהוי שם הקובץ בצורה בטוחה
                            f_name = f"file_{msg.id}"
                            try:
                                if hasattr(msg.media, 'document') and msg.media.document:
                                    for attr in msg.media.document.attributes:
                                        if isinstance(attr, types.DocumentAttributeFilename):
                                            f_name = attr.file_name
                                            break
                            except: pass # הגנה מקריסה על שמות מוזרים

                            # בדיקת כפילות בזיכרון
                            if f_name in memory_data["files"][clean_title]:
                                continue

                            print(f"   ⬇️ מוריד מטלגרם: {f_name}")
                            path = await client.download_media(msg)
                            
                            if path:
                                try:
                                    final_name = os.path.basename(path)
                                    print(f"   ⬆️ מעלה לדרייב...")
                                    media = MediaFileUpload(path, resumable=True)
                                    drive_service.files().create(body={'name': final_name, 'parents': [folder_id]}, media_body=media, supportsAllDrives=True).execute()
                                    
                                    print(f"   ✅ הושלם: {final_name}")
                                    os.remove(path)
                                    memory_data["files"][clean_title].append(final_name)
                                    
                                    # שמירה כל 5 קבצים
                                    if len(memory_data["files"][clean_title]) % 5 == 0:
                                        memory_file_id = save_memory_force(memory_data, memory_file_id)
                                except Exception as e:
                                    print(f"   ❌ שגיאה בהעלאה: {e}")
                                    channel_ok = False
                                    if os.path.exists(path): os.remove(path)

                    if channel_ok:
                        print(f"   🏆 הערוץ '{clean_title}' הושלם בהצלחה!")
                        memory_data["completed"].append(identifier)
                        memory_file_id = save_memory_force(memory_data, memory_file_id)

                except Exception as e:
                    print(f"   ❌ שגיאה כללית בערוץ: {e}")

            # --- טיפול בקישורי TeraBox ---
            for t_url in tera_links:
                print(f"\n📦 [ID: {m.id}] זוהה קישור TeraBox: {t_url}")
                
                info = get_terabox_link(t_url)
                if info and info["download_url"]:
                    f_name = get_clean_name(info["name"])
                    target_folder_name = "TeraBox_Downloads"
                    
                    # יצירת תיקיית ההורדות אם לא קיימת
                    folder_id = get_or_create_folder(target_folder_name)
                    if target_folder_name not in memory_data["files"]: memory_data["files"][target_folder_name] = []
                    
                    # בדיקת כפילות
                    if f_name in memory_data["files"][target_folder_name]:
                        print(f"   ⏩ הקובץ '{f_name}' כבר קיים בדרייב. מדלג.")
                        continue
                    
                    print(f"   ⬇️ מוריד מהאינטרנט: {f_name}")
                    try:
                        # הורדה בזרם (Stream) כדי לחסוך זיכרון
                        with requests.get(info["download_url"], stream=True, timeout=60) as r:
                            r.raise_for_status()
                            with open(f_name, 'wb') as f:
                                for chunk in r.iter_content(chunk_size=8192): 
                                    f.write(chunk)
                        
                        print(f"   ⬆️ מעלה לדרייב...")
                        media = MediaFileUpload(f_name, resumable=True)
                        drive_service.files().create(body={'name': f_name, 'parents': [folder_id]}, media_body=media, supportsAllDrives=True).execute()
                        
                        print(f"   ✅ הושלם בהצלחה!")
                        os.remove(f_name)
                        memory_data["files"][target_folder_name].append(f_name)
                        memory_file_id = save_memory_force(memory_data, memory_file_id)
                        
                    except Exception as e:
                        print(f"   ❌ שגיאה בהורדה/העלאה של TeraBox: {e}")
                        if os.path.exists(f_name): os.remove(f_name)

            # שמירת ביניים אחרי כל הודעה ראשית שנסרקה
            memory_file_id = save_memory_force(memory_data, memory_file_id)

if __name__ == '__main__':
    asyncio.run(main())
