import json
import sys
import io
import os
import time
import random
import glob
import subprocess
import concurrent.futures
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# ==============================================================================
# 1. CẤU HÌNH HỆ THỐNG
# ==============================================================================

# Fix lỗi hiển thị tiếng Việt trên Windows Console (Bắt buộc)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# --- THAY ID GOOGLE SHEET CỦA BẠN VÀO ĐÂY ---
SPREADSHEET_ID = '1YqO4MVEzAz61jc_WCVSS00LpRlrDb5r0LnuzNi6BYUY'
MASTER_SHEET_NAME = 'Sheet2'

# Số lượng luồng chạy song song (Máy PC để 3-5 là ổn định)
MAX_WORKERS = 4

# --- CẤU HÌNH ĐƯỜNG DẪN HYBRID ---

# 1. Đường dẫn Key Cố Định (Lấy từ ổ C cho an toàn, không lo lỗi GitHub)
FIXED_KEY_PATH = r'C:\Users\Pavlusa\OneDrive\Work\Python\Google_Token\service_account.json'

# 2. Đường dẫn Config (Lấy từ thư mục code do GitHub tải về)
# Lý do: Để bạn có thể cập nhật/thêm bớt link sản phẩm từ xa thông qua GitHub
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FOLDER_CONFIG = os.path.join(BASE_DIR, 'configs')

# Logic chọn file Key:
if os.path.exists(FIXED_KEY_PATH):
    SERVICE_ACCOUNT_FILE = FIXED_KEY_PATH
    print(f"🔑 Đang sử dụng Key Local tại: {SERVICE_ACCOUNT_FILE}")
else:
    # Dự phòng: Nếu không thấy ở ổ C thì tìm trong thư mục code
    SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, 'service_account.json')
    print(f"⚠️ Không thấy Key ổ C, đang tìm tại: {SERVICE_ACCOUNT_FILE}")

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# ==============================================================================
# 2. CÁC HÀM XỬ LÝ
# ==============================================================================

def kill_old_drivers():
    """Dọn dẹp Chromedriver cũ bị treo để giải phóng RAM"""
    print("🧹 Đang dọn dẹp driver rác...")
    try:
        if os.name == 'nt':
            subprocess.call("taskkill /F /IM chromedriver.exe /T", shell=True, stderr=subprocess.DEVNULL)
    except: pass

def get_google_sheet_client():
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"❌ Lỗi: Không tìm thấy file Key tại {SERVICE_ACCOUNT_FILE}")
        print(f"👉 Hãy tạo thư mục C:\\AutoPrice và copy file service_account.json vào đó!")
        return None
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"❌ Lỗi kết nối Google Sheet: {e}")
        return None

def get_driver():
    """Cấu hình Selenium tối ưu cho chạy ẩn"""
    opts = Options()
    opts.add_argument("--headless=new") # Chạy ẩn giao diện
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--log-level=3")
    
    # Tắt load ảnh để chạy nhanh hơn
    prefs = {"profile.managed_default_content_settings.images": 2}
    opts.add_experimental_option("prefs", prefs)

    try:
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=opts)
    except:
        return webdriver.Chrome(options=opts)

def scrape_dealer(config_path):
    """Hàm xử lý trọn gói cho 1 đại lý"""
    dealer_name = os.path.basename(config_path).replace('.json', '').upper()
    print(f"🔵 [{dealer_name}] Đang khởi động...")

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            products = json.load(f)
    except Exception as e:
        print(f"❌ Lỗi đọc file {dealer_name}: {e}")
        return []

    driver = None
    results = []

    try:
        driver = get_driver()
        
        for i, product in enumerate(products):
            current_time = datetime.now()
            
            # Cấu trúc dòng dữ liệu (7 cột)
            # Ngày | Giờ | Đại lý | Sản phẩm | Giá | Trạng thái | Link
            row = [
                current_time.strftime("%d/%m/%Y"), 
                current_time.strftime("%H:%M:%S"), 
                dealer_name,                       
                product.get('name', 'Unknown'),    
                "0",                               
                "Fail",                            
                product.get('url', '')             
            ]

            try:
                driver.get(product['url'])
                # time.sleep(1) # Bật lên nếu mạng quá nhanh làm web chặn

                selector = product.get('selector')
                sel_type = product.get('type', 'css')
                
                element = None
                if sel_type == 'xpath':
                    element = driver.find_element(By.XPATH, selector)
                else:
                    element = driver.find_element(By.CSS_SELECTOR, selector)
                
                if element:
                    clean_price = ''.join(filter(str.isdigit, element.text))
                    if clean_price:
                        row[4] = clean_price # Cập nhật giá
                        row[5] = "OK"        # Cập nhật trạng thái
            
            except Exception:
                pass # Lỗi thì bỏ qua, mặc định là Fail

            results.append(row)

            # Log tiến độ (cứ 20 sản phẩm in 1 lần)
            if i % 20 == 0:
                 print(f"   [{dealer_name}] {i}/{len(products)}...")

    except Exception as e:
        print(f"❌ Lỗi Driver [{dealer_name}]: {e}")
    finally:
        if driver: 
            try: driver.quit()
            except: pass
            
    print(f"✅ [{dealer_name}] Hoàn tất. Thu được {len(results)} dòng.")
    return results

def save_to_sheet_safe(data_rows):
    """Ghi vào Sheet an toàn (Thread-safe) với cơ chế Retry"""
    if not data_rows: return

    # Kết nối lại client mỗi lần ghi để tránh timeout session
    client =
