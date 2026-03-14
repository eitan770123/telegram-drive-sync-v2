import os, json, requests, sys, io, time
import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# --- הגדרות ---
DRIVE_ROOT_ID = os.environ['DRIVE_FOLDER_ID']
DRIVE_MEMORY_FILE = 'bot_memory_v2.json'
PHOTOS_MEMORY_FILE = 'photos_sync_memory.json'

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
print(">>> V מחובר לגוגל דרייב!")

# --- מנגנון חסינות מפני ניתוקי אינטרנט ---
def execute_with_retry(func, *args, **kwargs):
    """ מפעיל פקודה, ואם יש ניתוק רשת - מנסה שוב עד 5 פעמים """
    for attempt in range(5):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"   ⚠️ גיהוק ברשת (ניסיון {attempt+1}/5). ממתין 3 שניות... ({e})")
            time.sleep(3)
    return func(*args, **kwargs) # ניסיון אחרון

def get_valid_token():
    global creds
    if not creds.valid or creds.expired:
        def refresh_action():
            print("🔄 הטוקן פג תוקף. מחדש אותו עכשיו...")
            creds.refresh(google.auth.transport.requests.Request())
        execute_with_retry(refresh_action)
    return creds.token

# --- עבודה עם קבצי זיכרון ---
def download_json_from_drive(filename):
    try:
        def fetch():
            q = f"name='{filename}' and '{DRIVE_ROOT_ID}' in parents and trashed=false"
            res = drive_service.files().list(q=q, fields='files(id)').execute().get('files', [])
            if res:
                file_id = res[0]['id']
                request = drive_service.files().get_media(fileId=file_id)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done: _, done = downloader.next_chunk()
                fh.seek(0)
                return json.load(fh), file_id
            return None, None
        return execute_with_retry(fetch)
    except: return None, None

def save_json_to_drive(filename, data, file_id):
    def save():
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        media = MediaFileUpload(filename, mimetype='application/json')
        if file_id:
            drive_service.files().update(fileId=file_id, media_body=media).execute()
            return file_id
        else:
            meta = {'name': filename, 'parents': [DRIVE_ROOT_ID]}
            new_f = drive_service.files().create(body=meta, media_body=media, fields='id').execute()
            return new_f.get('id')
    return execute_with_retry(save)

# --- פעולות בדרייב ופוטוס ---
def get_drive_folder_id(folder_name):
    def get_id():
        q = f"name='{folder_name}' and '{DRIVE_ROOT_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        res = drive_service.files().list(q=q, fields='files(id)').execute().get('files', [])
        return res[0]['id'] if res else None
    return execute_with_retry(get_id)

def get_or_create_album(album_title, photos_memory):
    if album_title in photos_memory.get('albums', {}):
        return photos_memory['albums'][album_title]
    
    print(f"   📁 מנסה ליצור אלבום: '{album_title}'...")
    def create():
        url = 'https://photoslibrary.googleapis.com/v1/albums'
        headers = {'Authorization': f'Bearer {get_valid_token()}', 'Content-type': 'application/json'}
        res = requests.post(url, headers=headers, json={"album": {"title": album_title}})
        return res.json()
    
    data = execute_with_retry(create)
    
    if 'id' in data:
        if 'albums' not in photos_memory: photos_memory['albums'] = {}
        photos_memory['albums'][album_title] = data['id']
        return data['id']
    
    print(f"   ❌ שגיאה ביצירת אלבום: {data}")
    return None

def upload_to_photos(file_path, album_id):
    def upload():
        token = get_valid_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-type': 'application/octet-stream',
            'X-Goog-Upload-Protocol': 'raw',
            'X-Goog-Upload-File-Name': os.path.basename(file_path)
        }
        resp = requests.post('https://photoslibrary.googleapis.com/v1/uploads', headers=headers, data=open(file_path, 'rb'))
        upload_token = resp.text
        
        if not upload_token or "error" in upload_token.lower():
            return False
        
        payload = {"albumId": album_id, "newMediaItems": [{"simpleMediaItem": {"uploadToken": upload_token}}]}
        res = requests.post('https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate', headers={'Authorization': f'Bearer {token}'}, json=payload)
        return res.status_code == 200
    
    return execute_with_retry(upload)

# --- הלוגיקה המרכזית ---
def main():
    print("\n=== 🔄 מתחיל סנכרון: Google Drive -> Google Photos ===")
    drive_memory, _ = download_json_from_drive(DRIVE_MEMORY_FILE)
    if not drive_memory:
        print("❌ קובץ bot_memory_v2.json לא נמצא!")
        return

    photos_memory, photos_file_id = download_json_from_drive(PHOTOS_MEMORY_FILE)
    if not photos_memory: photos_memory = {"albums": {}, "uploaded_files": {}}

    total_uploaded = 0
    for folder_name, files_list in drive_memory['files'].items():
        already_uploaded = photos_memory.get('uploaded_files', {}).get(folder_name, [])
        files_to_upload = [f for f in files_list if f not in already_uploaded]
        
        if not files_to_upload: continue
            
        print(f"\n📂 תיקייה: {folder_name} ({len(files_to_upload)} קבצים חדשים להעלאה)")
        folder_id = get_drive_folder_id(folder_name)
        album_id = get_or_create_album(folder_name, photos_memory)
        
        if not folder_id or not album_id: continue

        for file_name in files_to_upload:
            def process_file():
                q = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
                res = drive_service.files().list(q=q, fields='files(id)').execute().get('files', [])
                if not res: return False
                
                f_id = res[0]['id']
                request = drive_service.files().get_media(fileId=f_id)
                with io.FileIO(file_name, 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done: _, done = downloader.next_chunk()
                return True
            
            if execute_with_retry(process_file):
                if upload_to_photos(file_name, album_id):
                    print(f"   ✅ הועלה: {file_name}")
                    if folder_name not in photos_memory['uploaded_files']: photos_memory['uploaded_files'][folder_name] = []
                    photos_memory['uploaded_files'][folder_name].append(file_name)
                    total_uploaded += 1
                    if total_uploaded % 10 == 0: photos_file_id = save_json_to_drive(PHOTOS_MEMORY_FILE, photos_memory, photos_file_id)
                
            if os.path.exists(file_name): os.remove(file_name)
            
        photos_file_id = save_json_to_drive(PHOTOS_MEMORY_FILE, photos_memory, photos_file_id)

    print(f"\n🎉 סיימנו! הועלו סך הכל {total_uploaded} קבצים.")

if __name__ == '__main__':
    main()
