import os, json, requests, sys, io
import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# --- הגדרות ---
DRIVE_ROOT_ID = os.environ['DRIVE_FOLDER_ID'] # התיקייה הראשית בדרייב שבה נמצאים קובצי הזיכרון
DRIVE_MEMORY_FILE = 'bot_memory_v2.json' # הקובץ של הדרייב (רשימת המלאי)
PHOTOS_MEMORY_FILE = 'photos_sync_memory.json' # הקובץ החדש שיזכור מה עלה לפוטוס

sys.stdout.reconfigure(encoding='utf-8')

# --- התחברות לגוגל ---
def get_creds():
    token_data = json.loads(os.environ['GOOGLE_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_data)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(google.auth.transport.requests.Request())
    return creds

creds = get_creds()
drive_service = build('drive', 'v3', credentials=creds)
access_token = creds.token
print(">>> V מחובר לגוגל דרייב ופוטוס!")

# --- עבודה עם קבצי זיכרון בדרייב ---
def download_json_from_drive(filename):
    """ מוריד קובץ JSON מהדרייב (בשביל לקרוא את הזיכרון) """
    try:
        q = f"name='{filename}' and '{DRIVE_ROOT_ID}' in parents and trashed=false"
        res = drive_service.files().list(q=q, fields='files(id)').execute().get('files', [])
        if res:
            file_id = res[0]['id']
            request = drive_service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            fh.seek(0)
            return json.load(fh), file_id
    except Exception as e:
        print(f"   ⚠️ שגיאה בהורדת {filename}: {e}")
    return None, None

def save_json_to_drive(filename, data, file_id):
    """ מעלה/מעדכן קובץ JSON בדרייב """
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    media = MediaFileUpload(filename, mimetype='application/json')
    if file_id:
        drive_service.files().update(fileId=file_id, media_body=media).execute()
    else:
        meta = {'name': filename, 'parents': [DRIVE_ROOT_ID]}
        new_f = drive_service.files().create(body=meta, media_body=media, fields='id').execute()
        file_id = new_f.get('id')
    return file_id

# --- פעולות בדרייב ובפוטוס ---
def get_drive_folder_id(folder_name):
    """ מוצא את ה-ID של התיקייה בדרייב לפי השם שלה """
    q = f"name='{folder_name}' and '{DRIVE_ROOT_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = drive_service.files().list(q=q, fields='files(id)').execute().get('files', [])
    if res: return res[0]['id']
    return None

def download_file_from_drive(folder_id, file_name):
    """ מוריד קובץ פיזי מהדרייב למחשב (לצורך העלאה לפוטוס) """
    q = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
    res = drive_service.files().list(q=q, fields='files(id)').execute().get('files', [])
    if not res: return None
    
    file_id = res[0]['id']
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.FileIO(file_name, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return file_name

def get_or_create_album(album_title, photos_memory):
    """ יוצר אלבום בפוטוס (או מביא קיים מהזיכרון) """
    if album_title in photos_memory.get('albums', {}):
        return photos_memory['albums'][album_title]
    
    print(f"   📁 יוצר אלבום חדש בפוטוס: '{album_title}'...")
    url = 'https://photoslibrary.googleapis.com/v1/albums'
    headers = {'Authorization': f'Bearer {access_token}', 'Content-type': 'application/json'}
    res = requests.post(url, headers=headers, json={"album": {"title": album_title}})
    data = res.json()
    
    if 'id' in data:
        if 'albums' not in photos_memory: photos_memory['albums'] = {}
        photos_memory['albums'][album_title] = data['id']
        return data['id']
    return None

def upload_to_photos(file_path, album_id):
    """ מעלה את הקובץ לגוגל פוטוס ומכניס לאלבום """
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-type': 'application/octet-stream',
        'X-Goog-Upload-Protocol': 'raw',
        'X-Goog-Upload-File-Name': os.path.basename(file_path)
    }
    with open(file_path, 'rb') as f:
        token = requests.post('https://photoslibrary.googleapis.com/v1/uploads', headers=headers, data=f).text
    
    if not token or "error" in token.lower(): return False
    
    payload = {"albumId": album_id, "newMediaItems": [{"simpleMediaItem": {"uploadToken": token}}]}
    res = requests.post('https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate', headers={'Authorization': f'Bearer {access_token}'}, json=payload)
    
    try:
        status = res.json()['newMediaItemResults'][0]['status']['message']
        return status in ["Success", "OK"]
    except: return False

# --- הלוגיקה המרכזית ---
def main():
    print("\n=== 🔄 מתחיל סנכרון: Google Drive -> Google Photos ===")
    
    # 1. טעינת מלאי הדרייב (הקובץ הישן)
    drive_memory, _ = download_json_from_drive(DRIVE_MEMORY_FILE)
    if not drive_memory or 'files' not in drive_memory:
        print(f"❌ לא הצלחתי למצוא/לקרוא את '{DRIVE_MEMORY_FILE}' בדרייב. ודא שהוא שם.")
        return

    # 2. טעינת זיכרון הפוטוס (או יצירת חדש)
    photos_memory, photos_file_id = download_json_from_drive(PHOTOS_MEMORY_FILE)
    if not photos_memory:
        print(">>> ℹ️ לא נמצא זיכרון פוטוס קודם. מתחיל מאפס.")
        photos_memory = {"albums": {}, "uploaded_files": {}}
    if 'uploaded_files' not in photos_memory: photos_memory['uploaded_files'] = {}

    total_uploaded = 0

    # 3. מעבר על כל התיקיות בדרייב
    for folder_name, files_list in drive_memory['files'].items():
        print(f"\n📂 בודק תיקייה/אלבום: '{folder_name}' ({len(files_list)} קבצים)")
        
        # רשימת הקבצים שכבר הועלו בעבר מהתיקייה הזו
        already_uploaded = photos_memory['uploaded_files'].get(folder_name, [])
        
        # סינון: האם יש בכלל קבצים חדשים?
        files_to_upload = [f for f in files_list if f not in already_uploaded]
        
        if not files_to_upload:
            print("   ✅ כל התמונות בתיקייה זו כבר קיימות בפוטוס. מדלג.")
            continue
            
        print(f"   ⏳ נמצאו {len(files_to_upload)} קבצים חדשים להעלאה.")
        
        # מוצאים את התיקייה הפיזית בדרייב כדי להוריד ממנה
        folder_id = get_drive_folder_id(folder_name)
        if not folder_id:
            print(f"   ⚠️ לא מצאתי את התיקייה בדרייב. מדלג.")
            continue
            
        # מביאים או יוצרים את האלבום בפוטוס
        album_id = get_or_create_album(folder_name, photos_memory)
        if not album_id: continue

        # העלאת הקבצים החסרים
        folder_uploads = 0
        for file_name in files_to_upload:
            print(f"   ⬇️ מוריד מדרייב: {file_name}")
            file_path = download_file_from_drive(folder_id, file_name)
            
            if file_path:
                print(f"   ⬆️ מעלה לפוטוס: {file_name}")
                success = upload_to_photos(file_path, album_id)
                
                if success:
                    print("   ✅ הצליח!")
                    # עדכון הזיכרון
                    if folder_name not in photos_memory['uploaded_files']:
                        photos_memory['uploaded_files'][folder_name] = []
                    photos_memory['uploaded_files'][folder_name].append(file_name)
                    
                    folder_uploads += 1
                    total_uploaded += 1
                    
                    # שמירת זיכרון כל 10 תמונות
                    if total_uploaded % 10 == 0:
                        photos_file_id = save_json_to_drive(PHOTOS_MEMORY_FILE, photos_memory, photos_file_id)
                else:
                    print("   ❌ נכשל.")
                
                os.remove(file_path) # ניקוי
                
        if folder_uploads > 0:
            # שמירת זיכרון בסוף כל תיקייה
            photos_file_id = save_json_to_drive(PHOTOS_MEMORY_FILE, photos_memory, photos_file_id)

    print("\n" + "="*50)
    print(f"🎉 סנכרון הושלם! סה\"כ הועלו {total_uploaded} קבצים חדשים לגוגל פוטוס.")
    print("="*50 + "\n")

if __name__ == '__main__':
    main()
