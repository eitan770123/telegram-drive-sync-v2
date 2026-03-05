import os, re, asyncio
from telethon import TelegramClient, functions, types
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# נתונים מה-Secrets
API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
MAIN_CHANNEL = os.environ['MAIN_CHANNEL']
DRIVE_FOLDER_ID = os.environ['DRIVE_FOLDER_ID']

with open('sa.json', 'w') as f: f.write(os.environ['GCP_SA_JSON'])

creds = service_account.Credentials.from_service_account_file('sa.json', scopes=['https://www.googleapis.com/auth/drive'])
drive_service = build('drive', 'v3', credentials=creds)

def get_or_create_folder(name):
    clean_name = re.sub(r'[\\/*?Internal:"<>|]', "", name)
    q = f"name='{clean_name}' and '{DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = drive_service.files().list(q=q).execute().get('files', [])
    if res: return res[0]['id']
    return drive_service.files().create(body={'name': clean_name, 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'}, fields='id').execute().get('id')

async def main():
    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(">>> הבוט מחובר וסורק קישורים...")
        
        processed_ids = set()
        async for message in client.iter_messages(MAIN_CHANNEL, limit=100):
            if message.text:
                # מוצא גם קישורים עם + וגם קישורים רגילים
                found = re.findall(r't\.me/(?:\+|joinchat/)?([\w\-]+)', message.text)
                for item in found: processed_ids.add(item)
        
        for identifier in processed_ids:
            try:
                entity = None
                # ניסיון ראשון: לזהות כקישור הזמנה
                try:
                    updates = await client(functions.messages.ImportChatInviteRequest(hash=identifier))
                    entity = updates.chats[0]
                    print(f"הצטרפתי בהצלחה לערוץ: {entity.title}")
                except Exception:
                    # אם כבר הצטרפנו או שזה קישור ציבורי
                    try:
                        entity = await client.get_entity(identifier)
                    except:
                        # ניסיון אחרון עם הקישור המלא
                        try: entity = await client.get_entity(f"https://t.me/+{identifier}")
                        except: pass

                if not entity:
                    print(f"!!! לא הצלחתי למצוא את הערוץ עבור: {identifier}")
                    continue

                folder_id = get_or_create_folder(entity.title)
                print(f"--- מתחיל להוריד מ: {entity.title} ---")
                
                async for msg in client.iter_messages(entity, limit=50):
                    if msg.media:
                        file_path = await client.download_media(msg)
                        if file_path:
                            media = MediaFileUpload(file_path, resumable=True)
                            drive_service.files().create(body={'name': os.path.basename(file_path), 'parents': [folder_id]}, media_body=media).execute()
                            os.remove(file_path)
                            print(f"הועלה: {os.path.basename(file_path)}")
            except Exception as e:
                print(f"שגיאה כללית בעיבוד {identifier}: {e}")

if __name__ == '__main__':
    asyncio.run(main())
