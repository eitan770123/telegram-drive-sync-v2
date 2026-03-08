import requests
import os
import re

class TeraBoxDownloader:
    def __init__(self, cookie_ndus):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
            'Cookie': f'ndus={cookie_ndus}',
            'Referer': 'https://www.terabox.com/',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def get_direct_link(self, url):
        """מחלץ את הקישור הישיר להורדה"""
        try:
            # שלב א: טיפול בקיצורי דרך וקבלת ה-URL המלא
            resp = self.session.get(url, allow_redirects=True)
            full_url = resp.url
            
            # שלב ב: חילוץ ה-Short URL Key (surl)
            # הקישור נראה בדרך כלל כך: terabox.com/s/1AbCdEf...
            # ה-surl הוא החלק שאחרי ה-/s/ (לפעמים צריך להוריד את ה-1 בהתחלה, לפעמים לא. ה-API גמיש)
            if "/s/" in full_url:
                short_key = full_url.split("/s/")[-1]
            elif "surl=" in full_url:
                short_key = full_url.split("surl=")[1].split("&")[0]
            else:
                return None, "לא הצלחתי לזהות את מזהה הקובץ."

            # שלב ג: פנייה ל-API של טרהבוקס
            api_url = "https://www.terabox.com/share/list"
            params = {
                'app_id': '250528',
                'shorturl': short_key,
                'root': '1'
            }

            api_resp = self.session.get(api_url, params=params)
            data = api_resp.json()

            # בדיקה אם יש תוצאות
            if 'list' in data and len(data['list']) > 0:
                file_info = data['list'][0]
                dlink = file_info.get('dlink')
                filename = file_info.get('server_filename')
                
                # לפעמים הלינק מגיע ב-HTTPS ולפעמים לא, נתקן ליתר ביטחון
                if dlink and not dlink.startswith('http'):
                     dlink = dlink.replace("\\/", "/") 

                return {
                    'link': dlink,
                    'filename': filename,
                    'size': file_info.get('size')
                }, None
            else:
                # לפעמים הקוקיז פג תוקף
                if data.get('errno') != 0:
                    return None, f"שגיאת API (קוד {data.get('errno')}). ייתכן שהעוגייה פגה."
                return None, "לא נמצאו קבצים בקישור."

        except Exception as e:
            return None, f"שגיאה כללית: {str(e)}"

    def download_file(self, direct_link, filename, destination_folder='downloads'):
        """מוריד את הקובץ פיזית לשרת"""
        if not os.path.exists(destination_folder):
            os.makedirs(destination_folder)
            
        local_path = os.path.join(destination_folder, filename)
        
        try:
            with self.session.get(direct_link, stream=True) as r:
                r.raise_for_status()
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            return local_path
        except Exception as e:
            print(f"Download failed: {e}")
            return None
