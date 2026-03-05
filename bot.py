import os, re, asyncio
from telethon import TelegramClient, functions
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# טעינת נתונים מהסודות של גיטהאב
API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
MAIN_CHANNEL = os.environ['MAIN_CHANNEL']
DRIVE_FOLDER_ID = os.environ['DRIVE_FOLDER_ID']

# יצירת קובץ הגישה לגוגל
with open('sa.json', 'w') as f:
    f.write(os.environ['GCP_SA_JSON'])

creds = service_account.Credentials.from_service_account_file('sa.json', scopes=['https://www.googleapis.com/auth/drive'])
drive_service = build('drive', 'v3', credentials=creds)

def get_or_create_folder(name):
    q = f"name='{name}' and '{DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = drive_service.files().list(q=q).execute().get('files', [])
    if res: return res[0]['id']
    file_metadata = {'name': name, 'parents': [DRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'}
    return drive_service.files().create(body=file_metadata, fields='id').execute().get('id')

async def main():
    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print(">>> הבוט התחבר בהצלחה!")
        
        links = []
        async for message in client.iter_messages(MAIN_CHANNEL):
            if message.text:
                found = re.findall(r't\.me/(?:\+|joinchat/)?([\w\-]+)', message.text)
                links.extend(found)
        
        for identifier in set(links):
            try:
                # ניסיון הצטרפות אם זה קישור הזמנה
                try:
                    await client(functions.messages.ImportChatInviteRequest(hash=identifier))
                except: pass 
                
                entity = await client.get_entity(identifier)
                folder_id = get_or_create_folder(re.sub(r'[\\/*?:"<>|]', "", entity.title))
                print(f"מעבד את ערוץ: {entity.title}")
                
                async for msg in client.iter_messages(entity, limit=50):
                    if msg.media:
                        file_path = await client.download_media(msg)
                        if file_path:
                            media = MediaFileUpload(file_path, resumable=True)
                            drive_service.files().create(body={'name': os.path.basename(file_path), 'parents': [folder_id]}, media_body=media).execute()
                            os.remove(file_path)
                            print(f"הועלה בהצלחה: {file_path}")
            except Exception as e:
                print(f"שגיאה בעיבוד {identifier}: {e}")

if __name__ == '__main__':
    asyncio.run(main())
