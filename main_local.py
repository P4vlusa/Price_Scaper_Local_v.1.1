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
# CẤU HÌNH
# ==============================================================================

# Fix lỗi font tiếng Việt trên Windows Console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# --- THAY ID SHEET CỦA BẠN ---
SPREADSHEET_ID = '1YqO4MVEzAz61jc_WCVSS00LpRlrDb5r0LnuzNi6BYUY'
MASTER_SHEET_NAME = 'Local'

# Số luồng chạy song song (Máy PC để 3-5 là đẹp)
MAX_WORKERS = 4

# --- ĐƯỜNG DẪN TỰ ĐỘNG (THEO GITHUB ACTIONS) ---
# Lấy đường dẫn nơi file này đang nằm
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# File config sẽ nằm trong thư mục con 'configs'
FOLDER_CONFIG = os.path.join(BASE_DIR, 'configs')
# File key sẽ được tạo ra tại chỗ này
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, 'service_account.json')

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# ==============================================================================
# HÀM XỬ LÝ
# ==============================================================================

def kill_old_drivers():
    """Dọn dẹp Chromedriver rác"""
    try:
        if os.name == 'nt':
            subprocess.call("taskkill /F /IM chromedriver.exe /T", shell=True, stderr=subprocess.DEVNULL)
    except: pass

def get_google_sheet_client():
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"❌ Lỗi: Không tìm thấy file '{SERVICE_ACCOUNT_FILE}'")
        print("👉 Kiểm tra lại bước tạo file Secret trong YAML!")
        return None
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"❌ Lỗi kết nối Sheet: {e}")
        return None

def get_driver():
    opts = Options()
    opts.add_argument("--headless=new") # Chạy ẩn
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--log-level=3")
    
    # Chặn ảnh để load nhanh
    prefs = {"profile.managed_default_content_settings.images": 2}
    opts.add_experimental_option("prefs", prefs)

    try:
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=opts)
    except:
        return webdriver.Chrome(options=opts)

def scrape_dealer(config_path):
    """Xử lý 1 đại lý"""
    dealer_name = os.path.basename(config_path).replace('.json', '').upper()
    print(f"🔵 [{dealer_name}] Bắt đầu chạy...")

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
            
            # Cấu trúc 7 cột: Ngày | Giờ | Đại lý | SP | Giá | Trạng thái | Link
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
                # time.sleep(1) # Bật nếu mạng quá nhanh làm web chặn

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
                        row[4] = clean_price
                        row[5] = "OK"
            except:
                pass 

            results.append(row)
            
            # Log nhẹ để biết đang chạy
            if i % 20 == 0:
                print(f"   [{dealer_name}] {i}/{len(products)}...")

    except Exception as e:
        print(f"❌ Lỗi Driver [{dealer_name}]: {e}")
    finally:
        if driver: 
            try: driver.quit()
            except: pass
            
    print(f"✅ [{dealer_name}] Xong {len(results)} dòng.")
    return results

def save_to_sheet_safe(data_rows):
    """Ghi Sheet an toàn (Thread-safe)"""
    if not data_rows: return

    # Kết nối lại client để tránh timeout
    client = get_google_sheet_client()
    if not client: return

    for attempt in range(5):
        try:
            sh = client.open_by_key(SPREADSHEET_ID)
            
            try:
                ws = sh.worksheet(MASTER_SHEET_NAME)
            except:
                ws = sh.add_worksheet(title=MASTER_SHEET_NAME, rows=5000, cols=10)
                ws.append_row(["Ngày", "Thời gian", "Đại lý", "Sản phẩm", "Giá", "Trạng thái", "Link"])
            
            # Ngủ random để tránh đụng hàng khi ghi
            time.sleep(random.uniform(1, 5))
            
            ws.append_rows(data_rows)
            print(f"💾 ĐÃ LƯU {len(data_rows)} DÒNG CỦA ĐẠI LÝ LÊN SHEET!")
            return

        except Exception as e:
            wait = random.uniform(5, 10)
            print(f"⚠️ Sheet bận, chờ {wait:.1f}s... (Lỗi: {e})")
            time.sleep(wait)

def main():
    kill_old_drivers()
    print(f"📂 Thư mục chạy: {BASE_DIR}")
    print(f"📂 Thư mục config: {FOLDER_CONFIG}")

    if not os.path.exists(FOLDER_CONFIG):
        print("❌ Không thấy thư mục 'configs'. Bạn đã push lên GitHub chưa?")
        return

    config_files = glob.glob(os.path.join(FOLDER_CONFIG, "*.json"))
    print(f"🚀 Tìm thấy {len(config_files)} đại lý. Chạy {MAX_WORKERS} luồng...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_file = {executor.submit(scrape_dealer, f): f for f in config_files}
        
        for future in concurrent.futures.as_completed(future_to_file):
            try:
                data = future.result()
                save_to_sheet_safe(data)
            except Exception as exc:
                print(f"❌ Lỗi luồng: {exc}")

    print("\n🎉🎉🎉 HOÀN TẤT TOÀN BỘ!")

if __name__ == "__main__":
    main()
