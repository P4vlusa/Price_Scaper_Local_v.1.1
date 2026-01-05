import json
import sys
import os
import io
import time
import random
import glob
import concurrent.futures
from datetime import datetime

# Thư viện Google Sheet
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Thư viện Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# --- DÁN ĐOẠN FIX VÀO ĐÂY ---
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
# ----------------------------
# --- CẤU HÌNH ---
FOLDER_CONFIG = 'configs_local'
MAX_WORKERS = 10  # Số luồng chạy song song
SERVICE_ACCOUNT_FILE = 'C:\\01. Python\\Google_Token\\service_account.json'

# --- QUAN TRỌNG: THAY ID FILE SHEET CỦA BẠN VÀO ĐÂY ---
# (Lấy ID từ file Master Sheet bạn đã tạo và share quyền Editor)
MASTER_SHEET_ID = '1WYj8fx8jLanw5gzb1-zxJSDyRB8aOMh8j6zEosfzJAw' 
# -----------------------------------------------------

# ==============================================================================
# 2. CÁC HÀM XỬ LÝ
# ==============================================================================

def kill_old_processes():
    """Hàm dọn dẹp: Tắt hết Chrome/ChromeDriver cũ bị treo"""
    print("🧹 Đang dọn dẹp các process Chrome cũ...")
    try:
        if os.name == 'nt': # Nếu là Windows
            subprocess.call("taskkill /F /IM chrome.exe /T", shell=True, stderr=subprocess.DEVNULL)
            subprocess.call("taskkill /F /IM chromedriver.exe /T", shell=True, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def get_google_sheet_client():
    """Kết nối tới Google Sheet"""
    print(f"🔑 Đang đọc file key tại: {SERVICE_ACCOUNT_FILE}")
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"❌ Lỗi: Không tìm thấy file 'service_account.json'.")
        return None

    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
        client = gspread.authorize(creds)
        print("✅ Kết nối Google Sheet thành công!")
        return client
    except Exception as e:
        print(f"❌ Lỗi kết nối Google Sheet: {e}")
        return None

def upload_to_sheet(client, dealer_name, data_rows):
    """Ghi dữ liệu dạng tích lũy (Append)"""
    if not client or not data_rows: return

    try:
        sh = client.open_by_key(MASTER_SHEET_ID)
        tab_name = dealer_name.strip().replace(" ", "_").upper()
        
        worksheet = None
        is_new_sheet = False

        try:
            worksheet = sh.worksheet(tab_name)
        except:
            print(f"   ✨ Tab '{tab_name}' chưa có. Đang tạo mới...")
            worksheet = sh.add_worksheet(title=tab_name, rows=2000, cols=10)
            is_new_sheet = True

        current_date_str = datetime.now().strftime("%d/%m/%Y")
        
        if is_new_sheet:
            header = ["Date", "Time", "Dealer", "Product", "Price", "Status", "URL"]
            worksheet.append_row(header)

        rows_to_append = []
        for item in data_rows:
            row = [
                current_date_str, item['Time'], dealer_name,
                item['Product'], item['Price'], item['Status'], item['URL']
            ]
            rows_to_append.append(row)
            
        if rows_to_append:
            worksheet.append_rows(rows_to_append)
            print(f"   ✅ Đã nối thêm {len(rows_to_append)} dòng vào tab '{tab_name}'.")
        
    except Exception as e:
        print(f"   ❌ Lỗi Upload Sheet: {e}")

def get_driver():
    """Cấu hình Selenium Ổn định nhất cho Windows Runner"""
    opts = Options()
    
    # 1. Chạy ẩn (Headless) - Bắt buộc cho Runner
    opts.add_argument("--headless=new") 
    
    # 2. Tham số chống lỗi Crash & Disconnected
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage") 
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    
    # QUAN TRỌNG: Đã xóa dòng '--remote-debugging-port' để tránh xung đột luồng
    
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    opts.add_argument("--log-level=3")

    try:
        # Tự động tải driver đúng phiên bản
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=opts)
    except Exception as e:
        # Fallback nếu lỗi service
        return webdriver.Chrome(options=opts)

def scrape_product(product):
    """Hàm lấy giá"""
    driver = None
    result = {
        "Time": datetime.now().strftime("%H:%M:%S"),
        "Product": product.get('name', 'Unknown'),
        "Price": "0",
        "Status": "Fail",
        "URL": product.get('url', '')
    }

    try:
        driver = get_driver()
        driver.get(product['url'])
        
        # Random nghỉ để trang load
        time.sleep(random.uniform(3, 6))

        selector = product.get('selector')
        sel_type = product.get('type', 'css')
        element = None
        
        if sel_type == 'xpath':
            element = driver.find_element(By.XPATH, selector)
        else:
            element = driver.find_element(By.CSS_SELECTOR, selector)
            
        if element:
            raw_text = element.text
            clean_price = ''.join(filter(str.isdigit, raw_text))
            if clean_price:
                result['Price'] = clean_price
                result['Status'] = 'OK'
            else:
                result['Status'] = 'No Price Found'
                
    except Exception as e:
        # Log lỗi ngắn gọn nếu cần
        # print(f"Error: {e}")
        result['Status'] = 'Error/Block'
    finally:
        if driver: 
            try: driver.quit()
            except: pass
        
    return result

def process_dealer(config_file, gs_client):
    """Xử lý 1 đại lý"""
    dealer_name = os.path.basename(config_file).replace('.json', '')
    print(f"\n🔵 ĐANG XỬ LÝ: {dealer_name.upper()}")

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            products = json.load(f)
    except Exception as e:
        print(f"❌ Lỗi đọc config: {e}")
        return

    results = []
    
    # Chạy đa luồng
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(scrape_product, p) for p in products]
        
        total = len(products)
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            data = future.result()
            results.append(data)
            print(f"   [{i+1}/{total}] {data['Status']} - {data['Product'][:25]}...")

    print(f"   -> Đang upload dữ liệu...")
    upload_to_sheet(gs_client, dealer_name, results)

# ==============================================================================
# 3. CHƯƠNG TRÌNH CHÍNH
# ==============================================================================
def main():
    # Bước 0: Dọn dẹp process cũ trước khi chạy
    kill_old_processes()

    print(f"📂 Thư mục làm việc: {CURRENT_DIR}")
    
    # 1. Kết nối Google Sheet
    gs_client = get_google_sheet_client()
    if not gs_client:
        print("⛔ Dừng chương trình.")
        return

    # 2. Kiểm tra config
    if not os.path.exists(FOLDER_CONFIG):
        os.makedirs(FOLDER_CONFIG)
        sample = [{"name":"iPhone 15","url":"https://www.thegioididong.com/dtdd/iphone-15","selector":".box-price-present","type":"css"}]
        with open(os.path.join(FOLDER_CONFIG, 'tgdd.json'), 'w', encoding='utf-8') as f:
            json.dump(sample, f, indent=2)

    # 3. Chạy
    config_files = glob.glob(os.path.join(FOLDER_CONFIG, "*.json"))
    print(f"🚀 TÌM THẤY {len(config_files)} ĐẠI LÝ.")
    
    for config_file in config_files:
        process_dealer(config_file, gs_client)
        print("-" * 40)

    print("\n🎉🎉🎉 HOÀN TẤT!")

if __name__ == "__main__":
    main()def get_google_sheet_client():
    """Kết nối tới Google Sheet"""
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"❌ Lỗi kết nối Google Sheet: {e}")
        return None

def upload_to_sheet(client, dealer_name, data_rows):
    """Đẩy dữ liệu lên 1 Tab mới trong Sheet"""
    if not client or not data_rows: return

    try:
        # Mở file Master
        sh = client.open_by_key(MASTER_SHEET_ID)
        
        # Tạo tên Tab ngắn gọn: TênĐạiLý_Ngày (Ví dụ: TGDD_29Dec)
        # Lưu ý: Tên Tab không được quá dài hoặc trùng lặp
        short_date = datetime.now().strftime("%d%b")
        tab_name = f"{dealer_name[:10]}_{short_date}"
        
        # Kiểm tra xem Tab có chưa, nếu có rồi thì xóa đi tạo lại (để cập nhật mới nhất)
        try:
            worksheet = sh.worksheet(tab_name)
            sh.del_worksheet(worksheet)
            print(f"   ⚠️ Đã xóa Tab cũ '{tab_name}' để ghi mới.")
        except:
            pass # Chưa có thì thôi

        # Tạo Tab mới
        print(f"   Cloud: Đang tạo Tab '{tab_name}'...")
        rows = len(data_rows) + 5
        worksheet = sh.add_worksheet(title=tab_name, rows=rows, cols=10)
        
        # Ghi dữ liệu (Dùng update cho nhanh)
        # Header
        header = ["Time", "Product", "Price", "Status", "URL"]
        
        # Chuẩn bị mảng dữ liệu để đẩy lên 1 lần (Batch update)
        all_values = [header]
        for item in data_rows:
            row = [
                item['Time'],
                item['Product'],
                item['Price'],
                item['Status'],
                item['URL']
            ]
            all_values.append(row)
            
        # Ghi toàn bộ (bắt đầu từ ô A1)
        worksheet.update('A1', all_values)
        print(f"   ✅ Đã upload thành công {len(data_rows)} dòng lên Sheet!")
        
    except Exception as e:
        print(f"   ❌ Lỗi Upload Sheet: {e}")

def get_driver():
    """Cấu hình Selenium (Tự động nhận diện GitHub/Local)"""
    opts = Options()
    opts.add_argument("--headless") 
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    if os.environ.get('GITHUB_ACTIONS') == 'true':
        return webdriver.Chrome(options=opts)
    else:
        try:
            return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
        except:
            return webdriver.Chrome(options=opts)

def scrape_product(product):
    """Hàm lấy giá 1 sản phẩm"""
    driver = None
    result = {
        "Time": datetime.now().strftime("%H:%M:%S"),
        "Product": product.get('name', 'Unknown'),
        "Price": "0",
        "Status": "Fail",
        "URL": product.get('url', '')
    }

    try:
        driver = get_driver()
        driver.get(product['url'])
        time.sleep(random.uniform(2, 4)) # Chờ load

        # Lấy giá
        selector = product.get('selector')
        sel_type = product.get('type', 'css')
        element = None
        
        if sel_type == 'xpath':
            element = driver.find_element(By.XPATH, selector)
        else:
            element = driver.find_element(By.CSS_SELECTOR, selector)
            
        if element:
            raw_text = element.text
            clean_price = ''.join(filter(str.isdigit, raw_text))
            if clean_price:
                result['Price'] = clean_price
                result['Status'] = 'OK'
                
    except Exception:
        pass # Lỗi thì giữ nguyên Status Fail
    finally:
        if driver: driver.quit()
        
    return result

def process_dealer(config_file, gs_client):
    """Xử lý 1 đại lý: Quét xong -> Upload luôn"""
    dealer_name = os.path.basename(config_file).replace('.json', '')
    print(f"\n🔵 BẮT ĐẦU: {dealer_name.upper()}")

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            products = json.load(f)
    except:
        print(f"❌ Lỗi đọc file config: {config_file}")
        return

    results = []
    
    # Chạy đa luồng quét giá
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(scrape_product, p) for p in products]
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            data = future.result()
            results.append(data)
            # In tiến độ dạng gọn: [1/100] OK
            print(f"   [{i+1}/{len(products)}] {data['Status']} - {data['Product'][:20]}...", end='\r')

    print(f"\n   ✅ Quét xong {len(results)} sản phẩm. Đang upload...")
    
    # Upload lên Sheet
    upload_to_sheet(gs_client, dealer_name, results)

def main():
    # 1. Kết nối Google Sheet trước để check
    print("🔌 Đang kết nối Google Services...")
    gs_client = get_google_sheet_client()
    if not gs_client:
        print("⛔ Không kết nối được Google Sheet. Dừng chương trình.")
        return

    # 2. Tìm file config
    if not os.path.exists(FOLDER_CONFIG):
        os.makedirs(FOLDER_CONFIG)
        # Tạo file mẫu nếu chưa có
        sample = [{"name":"Test iPhone","url":"https://www.thegioididong.com/dtdd/iphone-15-pro-max","selector":".box-price-present","type":"css"}]
        with open(os.path.join(FOLDER_CONFIG, 'test_mau.json'), 'w') as f:
            json.dump(sample, f)

    config_files = glob.glob(os.path.join(FOLDER_CONFIG, "*.json"))
    print(f"🚀 TÌM THẤY {len(config_files)} ĐẠI LÝ.")

    # 3. Chạy từng đại lý
    for config_file in config_files:
        process_dealer(config_file, gs_client)
        print("-" * 30)

    print("\n🎉🎉🎉 HOÀN TẤT TOÀN BỘ!")

if __name__ == "__main__":
    main()

