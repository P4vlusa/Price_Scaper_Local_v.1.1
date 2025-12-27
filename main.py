import json
import csv
import sys
import os
import time
import random
import concurrent.futures
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# --- CẤU HÌNH HỆ THỐNG ---
# 1. ID thư mục Google Drive (Thay bằng ID thật của bạn)
PARENT_FOLDER_ID = 'DÁN_ID_THƯ_MỤC_DRIVE_VÀO_ĐÂY'

# 2. Tên file key (Đảm bảo file này nằm cùng thư mục)
SERVICE_ACCOUNT_FILE = 'service_account.json'
SCOPES = ['https://www.googleapis.com/auth/drive']

# 3. Cấu hình luồng (Server yếu thì giảm xuống 3, mạnh thì tăng lên 5-10)
MAX_WORKERS = 4 

def get_drive_service():
    """Kết nối API Google Drive"""
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        print(f"❌ Lỗi kết nối Google Drive (Kiểm tra file json key): {e}")
        return None

def create_daily_folder(service):
    """Tạo folder theo ngày trên Drive"""
    if not service: return None
    
    folder_name = datetime.now().strftime("%Y-%m-%d")
    
    # Kiểm tra folder đã tồn tại chưa
    query = f"name='{folder_name}' and '{PARENT_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get('files', [])

    if files:
        print(f"📂 Đã có folder: {folder_name}")
        return files[0]['id']
    else:
        print(f"📁 Đang tạo folder mới: {folder_name}")
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [PARENT_FOLDER_ID]
        }
        folder = service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')

def get_price_selenium(product):
    """Hàm cốt lõi: Vào web lấy giá"""
    
    # --- CẤU HÌNH CHROME CHỐNG CHẶN ---
    chrome_options = Options()
    chrome_options.add_argument("--headless") # Chạy ẩn
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080") # Giả lập màn hình Full HD
    chrome_options.add_argument("--disable-blink-features=AutomationControlled") # Ẩn dấu hiệu Robot
    # User Agent giống máy thật
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    
    result = None
    try:
        # Random thời gian nghỉ để giống người dùng (3-5 giây)
        time.sleep(random.uniform(1, 3))
        
        print(f"▶️ Check: {product['name']}...")
        driver.get(product['url'])
        
        # Đợi web tải (quan trọng với web nặng)
        time.sleep(5) 
        
        # DEBUG: In ra tiêu đề để kiểm tra có bị chặn không
        # Nếu tiêu đề là "Access Denied" hoặc "403" -> Bị chặn
        page_title = driver.title
        # print(f"   ℹ️ Title: {page_title}") 

        element = None
        selector = product.get('selector')
        sel_type = product.get('type', 'css')
        
        # Tìm phần tử giá
        if sel_type == 'xpath':
            element = driver.find_element(By.XPATH, selector)
        else:
            element = driver.find_element(By.CSS_SELECTOR, selector)
            
        if element:
            raw_text = element.text
            # Lọc chỉ lấy số
            clean_price = ''.join(filter(str.isdigit, raw_text))
            
            if clean_price:
                print(f"   ✅ Giá: {clean_price} - {product['name']}")
                result = {
                    "Time": datetime.now().strftime("%H:%M:%S"),
                    "Product": product['name'],
                    "Price": clean_price,
                    "Source": product.get('source', 'Unknown'), # Thêm nguồn nếu có
                    "URL": product['url']
                }
            else:
                 print(f"   ⚠️ Thấy element nhưng rỗng text: {product['name']}")
        
    except Exception as e:
        # Chỉ in lỗi ngắn gọn để dễ nhìn
        print(f"   ❌ Lỗi {product['name']}: Không tìm thấy Selector hoặc Web chặn.")
    finally:
        driver.quit()
        
    return result

def main():
    # --- XỬ LÝ THAM SỐ
