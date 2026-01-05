import json
import sys
import os
import time
import random
import glob
import concurrent.futures
from datetime import datetime

# --- CÀI ĐẶT THƯ VIỆN ---
# Thư viện Google Sheet
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Thư viện Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# ==============================================================================
# CẤU HÌNH (DÀNH CHO GITHUB ACTIONS)
# ==============================================================================

# ID Sheet của bạn
MASTER_SHEET_ID = '1WYj8fx8jLanw5gzb1-zxJSDyRB8aOMh8j6zEosfzJAw' 

# Đường dẫn (Tương đối, nằm cùng thư mục code)
SERVICE_ACCOUNT_FILE = 'service_account.json'
FOLDER_CONFIG = 'configs'

# ==============================================================================
# CÁC HÀM XỬ LÝ
# ==============================================================================

def get_google_sheet_client():
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"❌ Lỗi: Không tìm thấy file '{SERVICE_ACCOUNT_FILE}'")
        print("👉 Hãy kiểm tra lại file YAML xem đã tạo file từ Secret chưa.")
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
    """Ghi dữ liệu tích lũy (Append)"""
    if not client or not data_rows: return

    try:
        sh = client.open_by_key(MASTER_SHEET_ID)
        # Tên Tab: TGDD, FPT...
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
            print(f"   ✅ Đã lưu {len(rows_to_append)} dòng vào tab '{tab_name}'.")
        
    except Exception as e:
        print(f"   ❌ Lỗi Upload Sheet: {e}")

def get_driver():
    """Cấu hình cho Server Linux (GitHub Actions)"""
    opts = Options()
    opts.add_argument("--headless=new") # Bắt buộc trên Server không màn hình
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    
    # Fake User Agent
    opts.add_argument("user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Tối ưu: Chặn load ảnh
    prefs = {"profile.managed_default_content_settings.images": 2}
    opts.add_experimental_option("prefs", prefs)

    try:
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=opts)
    except:
        return webdriver.Chrome(options=opts)

def process_dealer_smart(config_file, gs_client):
    """Mở 1 lần - Quét nhiều link"""
    dealer_name = os.path.basename(config_file).replace('.json', '')
    print(f"\n🔵 XỬ LÝ: {dealer_name.upper()}")

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            products = json.load(f)
    except Exception as e:
        print(f"❌ Lỗi đọc config: {e}")
        return

    results = []
    driver = None

    try:
        print("   🚀 Đang khởi động Chrome...")
        driver = get_driver()
        
        total = len(products)
        for i, product in enumerate(products):
            try:
                driver.get(product['url'])
                time.sleep(2) # Nghỉ ngắn

                result = {
                    "Time": datetime.now().strftime("%H:%M:%S"),
                    "Product": product.get('name', 'Unknown'),
                    "Price": "0",
                    "Status": "Fail",
                    "URL": product['url']
                }

                # Check xem có bị chặn không
                if "Access Denied" in driver.title or "403" in driver.title:
                    result['Status'] = "BLOCKED IP"
                    print(f"   🚫 {product['name']}: Bị chặn IP Cloud!")
                else:
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
                            result['Price'] = clean_price
                            result['Status'] = 'OK'
                
                results.append(result)
                print(f"   [{i+1}/{total}] {result['Status']} - {result['Price']}")

            except Exception:
                results.append({"Time": datetime.now().strftime("%H:%M:%S"), "Product": product['name'], "Price": "0", "Status": "Error", "URL": product['url']})
                print(f"   [{i+1}/{total}] Lỗi quét link này.")

    except Exception as e:
        print(f"❌ Lỗi trình duyệt: {e}")
    finally:
        if driver: driver.quit()

    print("   -> Upload dữ liệu...")
    upload_to_sheet(gs_client, dealer_name, results)

def main():
    print(f"📂 Thư mục hiện tại: {os.getcwd()}")
    
    # 1. Kết nối Google Sheet (File key được tạo ra từ Secret trong YAML)
    gs_client = get_google_sheet_client()
    if not gs_client: return

    # 2. Kiểm tra config
    if not os.path.exists(FOLDER_CONFIG):
        print(f"⚠️ Không thấy thư mục '{FOLDER_CONFIG}'. Hãy commit folder này lên GitHub!")
        return

    config_files = glob.glob(os.path.join(FOLDER_CONFIG, "*.json"))
    print(f"🚀 TÌM THẤY {len(config_files)} ĐẠI LÝ.")
    
    for config_file in config_files:
        process_dealer_smart(config_file, gs_client)
        print("-" * 40)

    print("\n🎉 HOÀN TẤT!")

if __name__ == "__main__":
    main()
