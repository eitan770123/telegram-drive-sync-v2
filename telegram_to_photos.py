import os, json, asyncio, requests, sys, io
from telethon import TelegramClient
import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# --- הגדרות בסיס ---
API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
MAIN_CHANNEL = os.environ['MAIN_CHANNEL']
DRIVE_FOLDER_ID = os.environ['DRIVE_FOLDER_ID'] # התיקייה בדרייב בה נשמור את קובץ הזיכרון
LIMIT_MSG = 2000 # כמה הודעות לסרוק אחורה (אפשר לשים None כדי לסרוק הכל)

MEMORY_FILENAME = 'photos_memory.json'

sys.stdout.reconfigure(encoding='utf-8')

# --- התחברות לגוגל ---
def get_google_credentials():
    token_data = json.loads(os.environ['GOOGLE_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_data)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
    return creds

try:
    creds = get_google_credentials()
    access_token = creds.token # טוקן בשביל פוטוס
    drive_service = build('drive', 'v3', credentials=creds) # שירות בשביל הזיכרון בדרייב
    print(">>> V מחובר לגוגל (דרייב + פוטוס)!")
except Exception as e:
    print(f">>> X שגיאה בחיבור לגוגל: {e}")
    sys.exit(1)

# --- ניהול זיכרון (המוח של הבוט) ---
def load_memory():
    """ מוריד את קובץ הזיכרון מהדרייב. אם לא קיים - יוצר זיכרון ריק. """
    print(">>> 🧠 טוען זיכרון מהדרייב...")
    memory = {"uploaded_msgs": [], "albums": {}}
    file_id = None
    
    try:
        q = f"name='{MEMORY_FILENAME}' and '{DRIVE_FOLDER_ID}' in parents and trashed=false"
        res = drive_service.files().list(q=q, spaces='drive', fields='files(id)').execute().get('files', [])
        
        if res:
            file_id = res[0]['id']
            request = drive_service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            fh.seek(0)
            memory = json.load(fh)
            print(f">>> ✅ זיכרון נטען! ({len(memory['uploaded_msgs'])} תמונות הועלו בעבר)")
        else:
            print(">>> ℹ️ לא נמצא זיכרון קודם. מתחיל זיכרון חדש.")
    except Exception as e:
        print(f">>> ⚠️ שגיאה בטעינת זיכרון: {e}")
        
    return memory, file_id

def save_memory(memory_data, file_id):
    """ מעלה את קובץ הזיכרון המעודכן חזרה לדרייב """
    try:
        with open(MEMORY_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(memory_data, f, ensure_ascii=False, indent=2)
            
        media = MediaFileUpload(MEMORY_FILENAME, mimetype='application/json', resumable=True)
        
        if file_id:
            # עדכון קובץ קיים
            drive_service.files().update(fileId=file_id, media_body=media).execute()
        else:
            # יצירת קובץ חדש
            file_metadata = {'name': MEMORY_FILENAME, 'parents': [DRIVE_FOLDER_ID]}
            new_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            file_id = new_file.get('id')
            
    except Exception as e:
        print(f"   ⚠️ שגיאה בשמירת הזיכרון לדרייב: {e}")
        
    return file_id

# --- ניהול גוגל פוטוס ---
def get_or_create_album(album_title, memory):
    """ בודק אם האלבום קיים בזיכרון, אם לא -> יוצר אותו בפוטוס ושומר בזיכרון """
    album_title = album_title.strip()
    if not album_title: album_title = "תמונות כלליות מטלגרם"
    
    # בדיקה בזיכרון (כדי לא ליצור כפילויות)
    if album_title in memory['albums']:
        return memory['albums'][album_title]
        
    print(f"   📁 יוצר אלבום חדש בפוטוס: '{album_title}'...")
    create_url = 'https://photoslibrary.googleapis.com/v1/albums'
    headers = {'Authorization': f'Bearer {access_token}', 'Content-type': 'application/json'}
    payload = {"album": {"title": album_title}}
    
    res = requests.post(create_url, headers=headers, json=payload)
    data = res.json()
    
    if 'id' in data:
        album_id = data['id']
        # שמירה בזיכרון!
        memory['albums'][album_title] = album_id
        return album_id
    else:
        print(f"   ❌ שגיאה ביצירת אלבום: {data}")
        return None

def upload_photo_to_album(file_path, album_id):
    """ מעלה את התמונה בפועל לאלבום הרצוי """
    filename = os.path.basename(file_path)
    
    # 1. העלאת הקובץ לשרתי גוגל
    upload_url = 'https://photoslibrary.googleapis.com/v1/uploads'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-type': 'application/octet-stream',
        'X-Goog-Upload-Protocol': 'raw',
        'X-Goog-Upload-File-Name': filename
    }
    
    with open(file_path, 'rb') as f:
        resp = requests.post(upload_url, headers=headers, data=f)
    upload_token = resp.text
    
    if not upload_token or "error" in upload_token.lower(): return False

    # 2. שיוך לאלבום
    create_url = 'https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate'
    create_payload = {
        "albumId": album_id,
        "newMediaItems": [{"simpleMediaItem": {"uploadToken": upload_token}}]
    }
    create_headers = {'Authorization': f'Bearer {access_token}', 'Content-type': 'application/json'}
    
    res = requests.post(create_url, headers=create_headers, json=create_payload)
    result = res.json()
    
    try:
        status = result['newMediaItemResults'][0]['status']['message']
        if status in ["Success", "OK"]: return True
    except: pass
        
    return False

# --- הלוגיקה הראשית ---
async def main():
    print("\n=== 🤖 בוט טלגרם -> גוגל פוטוס (עם זיכרון חכם) ===")
    
    # 1. טעינת הזיכרון
    memory, memory_file_id = load_memory()
    uploaded_msgs_set = set(memory['uploaded_msgs']) # לחיפוש מהיר

    # משתנים למעקב אחרי קבוצות של תמונות (אלבומים)
    current_album_name = "תמונות כלליות"
    last_group_id = None
    new_uploads_count = 0

    async with TelegramClient('anon', API_ID, API_HASH) as client:
        print("\n⏳ סורק הודעות (מהישן לחדש)...")
        
        # אנחנו סורקים מהישן לחדש (reverse=True) כדי לזהות כותרות של אלבומים בסדר הנכון
        async for m in client.iter_messages(MAIN_CHANNEL, limit=LIMIT_MSG, reverse=True):
            
            # בדיקה האם ההודעה כבר הועלתה בעבר
            if m.id in uploaded_msgs_set:
                # הבוט מדלג בשקט
                continue
                
            # אם יש תמונה מצורפת
            if m.media and hasattr(m.media, 'photo'):
                
                # --- זיהוי שם האלבום ---
                if m.grouped_id:
                    if m.grouped_id != last_group_id:
                        # קבוצה חדשה - לוקחים את הכותרת מההודעה הראשונה בקבוצה
                        last_group_id = m.grouped_id
                        current_album_name = m.text.split('\n')[0][:45] if m.text else "אלבום ללא כותרת"
                else:
                    # תמונה בודדת
                    current_album_name = m.text.split('\n')[0][:45] if m.text else "תמונות בודדות"
                
                current_album_name = current_album_name.replace('\r', '').strip()
                
                print(f"\n--- הודעה חדשה [ID: {m.id}] ---")
                print(f"   ⬇️ מוריד תמונה...")
                file_path = await m.download_media()
                
                if file_path:
                    # מביא את ה-ID של האלבום (או יוצר אותו אם לא קיים)
                    album_id = get_or_create_album(current_album_name, memory)
                    
                    if album_id:
                        print(f"   ⬆️ מעלה לאלבום: '{current_album_name}'...")
                        success = upload_photo_to_album(file_path, album_id)
                        
                        if success:
                            print("   ✅ הועלה בהצלחה!")
                            new_uploads_count += 1
                            
                            # **הכנסה לזיכרון!**
                            memory['uploaded_msgs'].append(m.id)
                            uploaded_msgs_set.add(m.id)
                            
                            # שמירת הזיכרון לדרייב כל 5 תמונות (כדי לא לאבד מידע אם קורס)
                            if new_uploads_count % 5 == 0:
                                memory_file_id = save_memory(memory, memory_file_id)
                        else:
                            print("   ❌ שגיאה בהעלאת התמונה לפוטוס.")
                    
                    # ניקוי הקובץ מהשרת המקומי
                    os.remove(file_path)

        # שמירה סופית של הזיכרון בסוף הריצה
        if new_uploads_count > 0:
            save_memory(memory, memory_file_id)

        print("\n" + "="*50)
        print(f"🎉 סיימנו! הועלו {new_uploads_count} תמונות חדשות לגוגל פוטוס.")
        print("="*50 + "\n")

if __name__ == '__main__':
    asyncio.run(main())
