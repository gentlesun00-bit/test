import streamlit as st
import requests
import json
from PIL import Image
import sqlite3
from datetime import datetime, timedelta 
import re 
import os
import io

# --- (OCR 서비스는 안정적인 ocr.space) ---
# **주의: ocr.space API 키를 본인의 것으로 변경해주세요!**
api_key = "K87046469388957" 
# ------------------------------------------------

# --- DB 관련 함수들 (가격 컬럼 완전히 제거) ---
DB_FILE = "my_inventory.db" 

def setup_database():
    """ 가격 컬럼이 없는 안정적인 테이블 구조를 만듭니다. """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # 안정적인 구조를 위해 기존 불필요한 컬럼 제거
    try: cursor.execute("ALTER TABLE fridge DROP COLUMN price")
    except: pass
    try: cursor.execute("ALTER TABLE warehouse DROP COLUMN price")
    except: pass
    try: cursor.execute("ALTER TABLE fridge DROP COLUMN expiry_date")
    except: pass
    try: cursor.execute("ALTER TABLE warehouse DROP COLUMN expiry_date")
    except: pass
    
    # 가격, 유통기한이 없는 최종 테이블 구조
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fridge (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT NOT NULL,
        purchase_date TEXT NOT NULL
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS warehouse (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT NOT NULL,
        purchase_date TEXT NOT NULL
    )""")
    conn.commit()
    conn.close()

def save_item(item_name, location):
    """ 지정된 위치(냉장고/창고)에 아이템을 저장합니다. """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    today_date = datetime.today().strftime("%Y-%m-%d")
    
    target_table = "fridge" if location == "냉장고" else "warehouse"
    
    cursor.execute(f"INSERT INTO {target_table} (item_name, purchase_date) VALUES (?, ?)", 
                   (item_name, today_date))
        
    conn.commit()
    conn.close()
    
def delete_item(item_id, location):
    """ 재고를 삭제합니다. """
    target_table = "fridge" if location == "냉장고" else "warehouse"
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM {target_table} WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()

def get_inventory():
    """ 냉장고와 창고의 모든 재고를 가져옵니다. """
    conn = sqlite3.connect(DB_FILE)
    cursor_fridge = conn.execute("SELECT id, item_name, purchase_date FROM fridge ORDER BY id DESC")
    fridge_items = cursor_fridge.fetchall()
    cursor_warehouse = conn.execute("SELECT id, item_name, purchase_date FROM warehouse ORDER BY id DESC")
    warehouse_items = cursor_warehouse.fetchall()
    conn.close()
    return fridge_items, warehouse_items

# --- OCR 관련 함수들 (안정화) ---
def ocr_space_file(filename, api_key):
    """ ocr.space API를 사용해 이미지에서 텍스트를 추출합니다. """
    payload = {'apikey': api_key, 'language': 'kor', 'OCREngine': 2}
    
    try:
        with open(filename, 'rb') as f:
            r = requests.post('https://api.ocr.space/parse/image', files={filename: f}, data=payload)
        
        try:
            response = r.json()
        except json.JSONDecodeError:
            st.error(f"[API 응답 오류] 서버가 JSON이 아닌 텍스트를 반환했습니다.")
            return None

        if response.get('IsErroredOnProcessing'):
            st.error(f"[OCR API 오류] {response.get('ErrorMessage')}")
            return None
            
        if not response.get('ParsedResults'): 
             return ""
        return response['ParsedResults'][0]['ParsedText']
        
    except Exception as e:
        st.error(f"OCR API 호출 중 예외 발생: {e}")
        return None

# --- (핵심 수정) 최종 품목 안정화 청소부 (Ver. 57) ---
def clean_item_name(name, junk_keywords):
    if name is None: return None
    name = name.strip()
    
    # [1] 가격/수량 정보 제거 (맨 뒤의 모든 숫자/쉼표/점/공백 덩어리를 제거)
    name = re.sub(r'([\d,.\s]+)+$', '', name).strip() 
    
    # [2] 코드/괄호 제거
    name = re.sub(r'^\s*(\d{1,4}\s*)?', '', name).strip()
    name = re.sub(r'\[.*?\]', '', name).strip()
    name = re.sub(r'\(.*\)', '', name).strip()
    name = re.sub(r'[가-힣]+\)\s*', '', name).strip()
    
    # [3] 최종 특수문자 제거
    name = re.sub(r'[^가-힣A-Z0-9 -]', '', name)
    name = name.strip()
    
    # [4] Junk 키워드 포함 시 탈락 (가장 먼저)
    if any(junk in name.upper() for junk in junk_keywords): return None

    # [5] (핵심 추가) 영어 + 숫자 조합 제거 (상품 코드/빌 번호 등)
    if re.search(r'[A-Za-z]+', name) and re.search(r'\d+', name):
        return None
        
    # [6] 유효성 검사 (숫자만 있거나, 너무 짧거나)
    if len(name) > 1 and not name.isdigit(): return name
    return None

def parse_ocr_text(raw_text):
    """ 품목명만 추출하는 안정화 로직 (가격 추출 포기) """
    JUNK_KEYWORDS = [
        '합계', '금액', '부가세', '면세', '과세', '물품가액', '과세물품가액', '면세물품가액', '봉투값',
        '할인', '결제', '승인', '카드', '현금', '영수증', '번호', '신용카드', '매출전표',
        '대표', '사업자', '주소', '전화', '매장', '본사', '점', '빌', 'MFY', 'SIDE',
        '감사합니다', '안녕히', '방문', '소계', '총', '구매액', '받을금액', '받은금액', '거스름돈',
        'TOTAL', 'TAX', 'VAT', 'CASH', 'CARD', 'PRICE', 'QTY', 'ITEM', 'SUBTOTAL', 'EAT-IN', 'INCL', 'ORD', 'CSO',
        '다이소', '아성다이손', '국민가게', '하나로마트', '농협', 'ELEVEN', '세븐', 'emart',
        '고객용', '주문번호', '제품받는곳', '토스뱅크', '할부', '삼성페이', '신한카드', 'CATID',
        '멤버십', '포인트', '적립', '대상', '가용', '상품명', '단가', '수량', '코드', '거래일시',
        '교환', '환불', '지참', '구입', '포장', '훼손', '불가', '취소', '소요', '샷 추가', '이마트',
        '판매', 'POS', 'PAY', '물품', 
        # (핵심 추가) 사용자 요청 금지 단어
        '변경', 'RPA', 'MB', '문의', '비자', '일시불', 'SCO', '고객', 'SSG', 'PAY',
        '서울특별시', '경기도', 
        # (핵심 추가) 패턴 기반 단어 필터링
        'KB', 'IC', # 카드 종류
    ]
    items = set()
    lines = raw_text.split('\n')
    
    # --- 지역명, 코드, 숫자 조합 패턴 필터링 ---
    for line in lines:
        line_cleaned_for_parsing = line.strip() 
        
        # 1. '-숫자-숫자' 조합을 가진 줄 제거 (전화번호, 사업자번호 등)
        if re.search(r'\d+-\d+', line_cleaned_for_parsing):
             continue
             
        # 2. 'xx시'로 시작하는 조합 제거 (주소)
        if re.match(r'^\s*[가-힣]+시\s', line_cleaned_for_parsing):
             continue

        cleaned_name = clean_item_name(line_cleaned_for_parsing, JUNK_KEYWORDS)
        
        if cleaned_name:
            items.add(cleaned_name)

    return list(items)

# --- 메인 웹페이지 UI ---
st.set_page_config(layout="wide")
st.title("🧾 AI 영수증 재고 관리")

setup_database() # DB 준비

if 'step' not in st.session_state: st.session_state.step = 1
if 'items_to_save' not in st.session_state: st.session_state.items_to_save = []
if 'raw_text' not in st.session_state: st.session_state.raw_text = None
if 'img_file_bytes' not in st.session_state: st.session_state.img_file_bytes = None


uploaded_file = st.file_uploader("영수증 사진을 업로드하세요", type=["jpg", "png", "jpeg"])

# --- 수동 추가 기능 (사이드바) ---
with st.sidebar:
    st.header("➕ 재고 수동 추가")
    with st.form("manual_add_form"):
        manual_item = st.text_input("품목명 (예: 사과)", key="manual_item_name")
        manual_location = st.selectbox("위치", ["냉장고", "창고"], key="manual_location")
        manual_submitted = st.form_submit_button("수동으로 저장하기")
        if manual_submitted and manual_item:
            save_item(manual_item, manual_location)
            st.success(f"'{manual_item}'을 {manual_location}에 저장했습니다.")
            st.experimental_rerun() # 새로고침

if uploaded_file is not None:
    img = Image.open(uploaded_file); max_width = 1024
    if img.width > max_width:
        ratio = max_width / img.width; new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), Image.LANCZOS)
    temp_image_path = "temp_image.jpg"; img.save(temp_image_path, "JPEG", quality=90)
    st.image(temp_image_path, caption="업로드된 영수증", width=400)

    if st.button("영수증 분석 시작하기"):
        with st.spinner("분석 중..."):
            raw_text = ocr_space_file(temp_image_path, api_key)
        
        if raw_text is not None and raw_text:
            st.info("텍스트 추출 성공!"); st.session_state.raw_text = raw_text
            items_list = parse_ocr_text(raw_text) # 수정된 함수 사용
            st.session_state.items_to_save = items_list
            st.session_state.step = 2
            st.rerun()
        elif raw_text == "":
             st.warning("영수증에서 텍스트를 감지했지만, 품목을 추출하지 못했습니다.")
        else:
            st.error("텍스트를 추출하지 못했습니다. 다시 시도해주세요.")


# --- 2단계: 분석된 항목 분류 ---
if st.session_state.step == 2 and 'items_to_save' in st.session_state and st.session_state.items_to_save:
    st.subheader("2. 분석된 항목을 분류해주세요 (무료모델의 한계로 잘못된 품목이 나옵니다, 선택안함으로 설정하세요. )")
    with st.form(key="item_classification_form"):
        choices = {}
        for item_name in st.session_state.items_to_save:
            choices[item_name] = st.selectbox(f"**{item_name}**", options=["(선택 안함)", "냉장고", "창고"], key=item_name)
        submitted = st.form_submit_button("이대로 저장하기")
        if submitted:
            saved_count = 0
            for item_name, location in choices.items():
                if location != "(선택 안함)":
                    save_item(item_name, location); saved_count += 1
            st.success(f"총 {saved_count}개의 항목을 저장했습니다! ✅")
            st.session_state.step = 1; st.session_state.pop('raw_text', None); st.session_state.pop('items_to_save', None)
            st.experimental_rerun() # 새로고침

# --- 3단계: 재고 목록 및 삭제 기능 ---
st.subheader("--- 🏠 현재 재고 현황 ---")

col1, col2 = st.columns(2) 

# --- 재고 목록 (냉장고) ---
with col1:
    st.markdown("#### ❄️ 냉장고")
    fridge_items, _ = get_inventory() # 냉장고 재고만 가져옴
    if not fridge_items:
        st.write("텅 비어있습니다.")
    else:
        # (핵심: 품목 합치기 및 삭제 로직)
        item_counts = {}
        item_details = {}
        for item in fridge_items:
            item_id, item_name, purchase_date = item[0], item[1], item[2]
            if item_name not in item_counts:
                item_counts[item_name] = 0
                item_details[item_name] = []
            item_counts[item_name] += 1
            item_details[item_name].append(item_id) # 삭제할 때 사용할 ID 리스트

        # 그룹화된 품목 출력
        for item_name, count in item_counts.items():
            col_item, col_delete = st.columns([4, 1])
            
            with col_item:
                # 품목 합치기 기능 (시각적)
                st.write(f"- {item_name} ({count}개)") 
            with col_delete:
                # 삭제 버튼 (1개씩 차감)
                if st.button("사용", key=f"del_f_{item_name}"):
                    # 가장 오래된(작은 ID) 항목 1개만 삭제하여 FIFO 구현 (선입선출)
                    oldest_id = min(item_details[item_name])
                    delete_item(oldest_id, "냉장고")
                    st.experimental_rerun()

# --- 재고 목록 (창고) ---
_, warehouse_items = get_inventory() # 창고 재고만 가져옴
with col2:
    st.markdown("#### 📦 창고")
    if not warehouse_items:
        st.write("텅 비어있습니다.")
    else:
        # (핵심: 품목 합치기 및 삭제 로직)
        item_counts = {}
        item_details = {}
        for item in warehouse_items:
            item_id, item_name, purchase_date = item[0], item[1], item[2]
            if item_name not in item_counts:
                item_counts[item_name] = 0
                item_details[item_name] = []
            item_counts[item_name] += 1
            item_details[item_name].append(item_id) # 삭제할 때 사용할 ID 리스트

        # 그룹화된 품목 출력
        for item_name, count in item_counts.items():
            col_item, col_delete = st.columns([4, 1])
            
            with col_item:
                # 품목 합치기 기능 (시각적)
                st.write(f"- {item_name} ({count}개)") 
            with col_delete:
                # 삭제 버튼 (1개씩 차감)
                if st.button("사용", key=f"del_w_{item_name}"):
                    oldest_id = min(item_details[item_name])
                    delete_item(oldest_id, "창고")
                    st.experimental_rerun()

# (디버깅용) 원본 텍스트 보기
if 'raw_text' in st.session_state and st.session_state.raw_text:
    with st.expander("API가 반환한 원본 텍스트 보기 (디버깅용)"):
        st.text(st.session_state.raw_text)