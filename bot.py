import os, re, asyncio, json, sys, io, requests, time
from telethon import TelegramClient, functions, types
from telethon.errors import UserAlreadyParticipantError, InviteHashExpiredError, FloodWaitError
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
    if not name: return "Unknown_Folder"
    return re.sub(r'[\\/*?:"<>|\']', "", name).strip()

def get_terabox_link(url):
    print(f"   ⏳ מנסה לפענח TeraBox...")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"}
        api_url = f"https://terabox-dl.qtcloud.workers.dev/api/get-info?url={url}"
        resp = requests.get(api_url, headers=headers, timeout=15)
        
        # בדיקה אם התגובה תקינה לפני שמנסים לקרוא אותה
        try:
            data = resp.json()
        except json.JSONDecodeError:
            print("   ⚠️ השרת של TeraBox החזיר תשובה לא תקינה (חסימה או עומס).")
            return None

        if data.get("ok"):
            file_info = data.get("list", [{}])[0]
            name = file_info.get("filename", "terabox_file")
            print(f"   V הצלחה! קובץ: {name}")
            return {"name": name, "download_url": file_info.get("download_link")}
        else:
            print(f"   X כישלון: {data.get('message')}")
            return None
    except Exception as e:
        print(f"   X שגיאת חיבור ל-TeraBox: {e}")
        return None

# === פונקציה חכמה לכניסה לערוצים ===
async def smart_join(client, identifier):
    """ מנסה להיכנס לערוץ בכל הדרכים, כולל אם כבר נמצאים בו """
    try:
        # 1. בדיקה אם הקישור תקין ומה ה-ID של הערוץ
        invite = await client(functions.messages.CheckChatInviteRequest(hash=identifier))
        
        # אם הצלחנו לקבל פרטים, בוא ננסה להביא את היישות
        if hasattr(invite, 'chat'):
            # מנסים לגשת ישירות (אולי אנחנו כבר בפנים?)
            try:
                return await client.get_entity(invite.chat.id)
            except:
                # אם לא, מצטרפים
                await client(functions.messages.ImportChatInviteRequest(hash=identifier))
                return await client.get_entity(invite.chat.id)
                
    except UserAlreadyParticipantError:
        # אנחנו כבר בפנים! אבל ה-hash לא נותן ישות ישירה.
        # טריק: בודקים שוב את ההזמנה כדי לקבל את ה-ID האמיתי
        try:
            invite = await client(functions.messages.CheckChatInviteRequest(hash=identifier))
            if hasattr(invite, 'chat'):
                return await client.get_entity(invite.chat.id)
        except: pass
        print("   (הבוט כבר בערוץ, אבל לא מצליח לזהות את ה-ID שלו)")
        return None

    except InviteHashExpiredError:
        print("   ⚠️ הקישור הזה פג תוקף (מת). אין מה לעשות.")
        return None
    except FloodWaitError as e:
        print(f"   ⏳ הצפה בטלגרם. ממתין {e.seconds} שניות...")
        await asyncio.sleep(e.seconds)
        return await smart_join(client, identifier)
    except Exception as e:
        print(f"   ⚠️ שגיאה בכניסה לערוץ: {e}")
        return None

    return None

# === ניהול זיכרון ===

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

# === ראשי ===

async def main():
    memory_data, memory_file_id = load_memory()
    current_msg_id = START_FROM_ID if START_FROM_ID > 0 else memory_data.get("last_msg_id", 0)

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(f"\n=== 🚀 הבוט מתחיל (סריקה מ-ID: {current_msg_id}) ===")
        
        async for m in client.iter_messages(MAIN_CHANNEL, limit=3000, reverse=True):
            if m.id <= current_msg_id: continue
            memory_data["last_msg_id"] = m.id
            
            txt = m.text or ""
            tg_links = re.findall(r't\.me/(?:\+|joinchat/)?([\w\-]{10,})', txt)
            tera_links = re.findall(r'(https?://(?:www\.)?(?:terabox\.com|nephobox\.com)/s/[\w\-]+)', txt)
            
            # --- טלגרם ---
            for identifier in tg_links:
                if identifier in memory_data["completed"]: continue

                print(f"\n🔥 [ID: {m.id}] מעבד ערוץ טלגרם...")
                entity = await smart_join(client, identifier)
                
                if not entity:
                    continue # הקישור מת או שאי אפשר להיכנס

                clean_title = get_clean_name(entity.title)
                print(f"   📂 ערוץ זוהה: {clean_title}")
                
                folder_id = get_or_create_folder(clean_title)
                if not folder_id: continue
                if clean_title not in memory_data["files"]: memory_data["files"][clean_title] = []

                channel_ok = True
                async for msg in client.iter_messages(entity, limit=None):
                    if msg.media:
                        f_name = f"file_{msg.id}"
                        try:
                            if hasattr(msg.media, 'document') and msg.media.document:
                                for attr in msg.media.document.attributes:
                                    if isinstance(attr, types.DocumentAttributeFilename):
                                        f_name = attr.file_name; break
                        except: pass

                        if f_name in memory_data["files"][clean_title]: continue

                        print(f"   ⬇️ מוריד: {f_name}")
                        path = await client.download_media(msg)
                        if path:
                            try:
                                final_name = os.path.basename(path)
                                drive_service.files().create(body={'name': final_name, 'parents': [folder_id]}, media_body=MediaFileUpload(path, resumable=True), supportsAllDrives=True).execute()
                                print(f"   ✅ עלה")
                                os.remove(path)
                                memory_data["files"][clean_title].append(f_name)
                                if len(memory_data["files"][clean_title]) % 5 == 0: memory_file_id = save_memory_force(memory_data, memory_file_id)
                            except: channel_ok = False
                
                if channel_ok:
                    memory_data["completed"].append(identifier)
                    memory_file_id = save_memory_force(memory_data, memory_file_id)

            # --- TeraBox ---
            for t_url in tera_links:
                print(f"\n📦 [ID: {m.id}] קישור TeraBox...")
                info = get_terabox_link(t_url)
                if info and info["download_url"]:
                    f_name = get_clean_name(info["name"])
                    target_folder = "TeraBox_Downloads"
                    folder_id = get_or_create_folder(target_folder)
                    if target_folder not in memory_data["files"]: memory_data["files"][target_folder] = []
                    
                    if f_name in memory_data["files"][target_folder]:
                        print("   ⏩ קיים בדרייב.")
                        continue
                    
                    print(f"   ⬇️ מוריד: {f_name}")
                    try:
                        with requests.get(info["download_url"], stream=True, timeout=60) as r:
                            r.raise_for_status()
                            with open(f_name, 'wb') as f:
                                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
                        
                        media = MediaFileUpload(f_name, resumable=True)
                        drive_service.files().create(body={'name': f_name, 'parents': [folder_id]}, media_body=media, supportsAllDrives=True).execute()
                        print("   ✅ עלה בהצלחה")
                        os.remove(f_name)
                        memory_data["files"][target_folder].append(f_name)
                        memory_file_id = save_memory_force(memory_data, memory_file_id)
                    except Exception as e:
                        print(f"   ❌ שגיאה בהורדה: {e}")
                        if os.path.exists(f_name): os.remove(f_name)
            
            memory_file_id = save_memory_force(memory_data, memory_file_id)

if __name__ == '__main__':
    asyncio.run(main())
