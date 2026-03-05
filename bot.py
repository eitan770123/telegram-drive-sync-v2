import os, re, asyncio, json
from telethon import TelegramClient, functions
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# טעינת משתנים
API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
MAIN_CHANNEL = os.environ['MAIN_CHANNEL']
DRIVE_FOLDER_ID = os.environ['DRIVE_FOLDER_ID']

# טעינת הטוקן האישי שלך (במקום Service Account)
token_data = json.loads(os.environ['GOOGLE_TOKEN'])
creds = Credentials.from_authorized_user_info(token_data)
drive_service = build('drive', 'v3', credentials=creds)

def get_or_create_folder(name):
    # ניקוי שם התיקייה כדי למנוע שגיאות
    clean_name = re.sub(r'[\\/*?:"<>|\']', "", name).strip()
    safe_name = clean_name.replace("'", "\\'")
    
    q = f"name='{safe_name}' and '{DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    
    try:
        res = drive_service.files().list(q=q).execute().get('files', [])
        if res: return res[0]['id']
        
        # יצירת תיקייה חדשה
        return drive_service.files().create(
            body={'name': clean_name, 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'},
            fields='id'
        ).execute().get('id')
    except Exception as e:
        print(f"Error creating folder {clean_name}: {e}")
        return None

async def main():
    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(">>> הבוט מחובר כמשתמש ומעלה בשמך!")
        
        links = set()
        async for m in client.iter_messages(MAIN_CHANNEL, limit=100):
            if m.text:
                found = re.findall(r't\.me/(?:\+|joinchat/)?([\w\-]{10,})', m.text)
                for f in found: links.add(f)
        
        for identifier in links:
            try:
                entity = None
                try:
                    updates = await client(functions.messages.ImportChatInviteRequest(hash=identifier))
                    entity = updates.chats[0]
                except:
                    try: entity = await client.get_entity(identifier)
                    except: continue

                if not entity: continue
                folder_id = get_or_create_folder(entity.title)
                if not folder_id: continue

                print(f"--- מעלה קבצים מ: {entity.title} ---")
                async for msg in client.iter_messages(entity, limit=50):
                    if msg.media:
                        file_path = await client.download_media(msg)
                        if file_path:
                            try:
                                # ניסיון העלאה לדרייב
                                media = MediaFileUpload(file_path, resumable=True)
                                drive_service.files().create(
                                    body={'name': os.path.basename(file_path), 'parents': [folder_id]},
                                    media_body=media
                                ).execute()
                                
                                # === נקודת הביטחון ===
                                # השורה הבאה קורית *רק* אם ההעלאה הצליחה ב-100%
                                print(f"הועלה בהצלחה: {os.path.basename(file_path)}")
                                os.remove(file_path) 
                                
                            except Exception as e:
                                # אם הייתה שגיאה (כמו חוסר מקום), הקובץ לא יימחק!
                                print(f"!!! שגיאה בהעלאת {os.path.basename(file_path)}: {e}")
                                # כאן אין פקודת מחיקה, אז הקובץ נשמר בטוח.
                                
            except Exception as e:
                print(f"שגיאה כללית ב-{identifier}: {e}")

if __name__ == '__main__':
    asyncio.run(main())
