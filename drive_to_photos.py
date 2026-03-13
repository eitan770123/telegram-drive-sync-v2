import os, json, requests, sys, io
import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# --- הגדרות ---
DRIVE_ROOT_ID = os.environ['DRIVE_FOLDER_ID']
DRIVE_MEMORY_FILE = 'bot_memory_v2.json'
PHOTOS_MEMORY_FILE = 'photos_sync_memory.json'

sys.stdout.reconfigure(encoding='utf-8')

def get_creds():
    token_data = json.loads(os.environ['GOOGLE_TOKEN'])
    creds = Credentials.from_authorized_user_info(token_data)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(google.auth.transport.requests.Request())
    return creds

creds = get_creds()
drive_service = build('drive', 'v3', credentials=creds)
access_token = creds.token
print(">>> V מחובר לגוגל דרייב!")

def download_json_from_drive(filename):
    try:
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
    except: pass
    return None, None

def save_json_to_drive(filename, data, file_id):
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

def get_drive_folder_id(folder_name):
    q = f"name='{folder_name}' and '{DRIVE_ROOT_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = drive_service.files().list(q=q, fields='files(id)').execute().get('files', [])
    return res[0]['id'] if res else None

def get_or_create_album(album_title, photos_memory):
    if album_title in photos_memory.get('albums', {}):
        return photos_memory['albums'][album_title]
    
    print(f"   📁 מנסה ליצור אלבום: '{album_title}'...")
    url = 'https://photoslibrary.googleapis.com/v1/albums'
    headers = {'Authorization': f'Bearer {access_token}', 'Content-type': 'application/json'}
    res = requests.post(url, headers=headers, json={"album": {"title": album_title}})
    data = res.json()
    
    if 'id' in data:
        if 'albums' not in photos_memory: photos_memory['albums'] = {}
        photos_memory['albums'][album_title] = data['id']
        return data['id']
    
    print(f"   ❌ שגיאה ביצירת אלבום! תגובת גוגל: {data}")
    return None

def upload_to_photos(file_path, album_id):
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-type': 'application/octet-stream',
        'X-Goog-Upload-Protocol': 'raw',
        'X-Goog-Upload-File-Name': os.path.basename(file_path)
    }
    resp = requests.post('https://photoslibrary.googleapis.com/v1/uploads', headers=headers, data=open(file_path, 'rb'))
    token = resp.text
    
    if not token or "error" in token.lower():
        print(f"      ❌ שגיאה בהעלאת קובץ: {token}")
        return False
    
    payload = {"albumId": album_id, "newMediaItems": [{"simpleMediaItem": {"uploadToken": token}}]}
    res = requests.post('https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate', headers={'Authorization': f'Bearer {access_token}'}, json=payload)
    return res.status_code == 200

def main():
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
            
        print(f"\n📂 תיקייה: {folder_name} ({len(files_to_upload)} חדשים)")
        folder_id = get_drive_folder_id(folder_name)
        album_id = get_or_create_album(folder_name, photos_memory)
        
        if not folder_id or not album_id: continue

        for file_name in files_to_upload:
            # הורדה מדרייב
            q = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
            res = drive_service.files().list(q=q, fields='files(id)').execute().get('files', [])
            if not res: continue
            
            f_id = res[0]['id']
            request = drive_service.files().get_media(fileId=f_id)
            with io.FileIO(file_name, 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done: _, done = downloader.next_chunk()
            
            if upload_to_photos(file_name, album_id):
                print(f"   ✅ הועלה: {file_name}")
                if folder_name not in photos_memory['uploaded_files']: photos_memory['uploaded_files'][folder_name] = []
                photos_memory['uploaded_files'][folder_name].append(file_name)
                total_uploaded += 1
                if total_uploaded % 10 == 0: photos_file_id = save_json_to_drive(PHOTOS_MEMORY_FILE, photos_memory, photos_file_id)
            
            if os.path.exists(file_name): os.remove(file_name)
            
        photos_file_id = save_json_to_drive(PHOTOS_MEMORY_FILE, photos_memory, photos_file_id)

    print(f"\n🎉 סיימנו! הועלו {total_uploaded} קבצים.")

if __name__ == '__main__':
    main()
