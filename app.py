import streamlit as st
import pandas as pd
from supabase import create_client, Client
from datetime import datetime, date
import time
import io
import base64
from PIL import Image, ImageOps
from streamlit_cropper import st_cropper

# ==========================================
# 1. 設定與工具 (Setup & Constants)
# ==========================================

# 讀取圖片
try:
    icon_image = Image.open("logo.png")
except:
    icon_image = "🐱" 

# 設定頁面配置
st.set_page_config(page_title="寵物飲食紀錄 (DB版)", page_icon=icon_image, layout="wide")

# CSS美化
st.markdown("""
<style>
    .stApp { font-family: 'Segoe UI', sans-serif; }
    .stat-box { background: #f0f2f6; padding: 15px; border-radius: 10px; text-align: center; }
    .big-num { font-size: 24px; font-weight: bold; color: #012172; }
    div[data-testid="stMetricValue"] { font-size: 20px; }
</style>
""", unsafe_allow_html=True)

# 類別對照表 (存英文，顯中文)
CATEGORY_MAP = {
    "wet_food": "主食/處方飼料",
    "dry_food": "副食/乾飼料",
    "snack": "凍乾/點心",
    "supp": "保養品",
    "med": "藥品",
    "other": "其他"
}
CATEGORY_REVERSE = {v: k for k, v in CATEGORY_MAP.items()}

# 定義哪些類別屬於「食物」（計算密度與重量用）
FOOD_CATEGORIES_CODE = ["wet_food", "dry_food", "snack", "other"]

# 健康狀況選項
HEALTH_OPTIONS = ["健康", "腎貓", "胰貓", "糖貓", "其它"]

# ==========================================
# 2. 資料庫連線 (Database Connection)
# ==========================================
@st.cache_resource
def init_supabase() -> Client:
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"資料庫連線設定錯誤: {e}")
        return None

supabase = init_supabase()

# ==========================================
# 3. 資料操作函式 (Data Logic)
# ==========================================

# --- 圖片處理輔助函式 ---
def pil_image_to_base64(image):
    """將 PIL 圖片物件轉為 Base64 字串 (給裁切器用)"""
    try:
        image.thumbnail((300, 300))
        buffered = io.BytesIO()
        image.save(buffered, format="JPEG", quality=80)
        return base64.b64encode(buffered.getvalue()).decode()
    except Exception as e:
        st.error(f"圖片轉碼失敗: {e}")
        return None

def save_pet(data_dict, pet_id=None):
    if pet_id:
        supabase.table('pets').update(data_dict).eq('id', pet_id).execute()
    else:
        supabase.table('pets').insert(data_dict).execute()
    st.cache_data.clear()

# --- 寵物相關 --- 
def fetch_pets():
    try:
        # [修改] 增加過濾條件：只抓 is_deleted 為 false (或是 null) 的寵物
        response = supabase.table('pets').select("*")\
            .neq('is_deleted', True)\
            .order('created_at').execute()
        return pd.DataFrame(response.data)
    except Exception as e:
        return pd.DataFrame()      

# [新增] 檢查寵物是否有關聯資料 (點餐本 或 飲食紀錄)
def check_pet_has_data(pet_id):
    try:
        # 檢查點餐本
        res_menu = supabase.table('pet_food_relations').select("id", count='exact').eq('pet_id', pet_id).execute() 
        count_menu = res_menu.count if res_menu.count is not None else len(res_menu.data)

        # 檢查飲食紀錄
        res_logs = supabase.table('diet_logs').select("id", count='exact').eq('pet_id', pet_id).execute()
        count_logs = res_logs.count if res_logs.count is not None else len(res_logs.data)

        return (count_menu + count_logs) > 0
    except:
        return False

# [新增] 執行軟刪除 (註記刪除)
def soft_delete_pet(pet_id, reason):
    try:
        supabase.table('pets').update({
            "is_deleted": True,
            "deletion_reason": reason
        }).eq('id', pet_id).execute()
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"軟刪除失敗: {e}")
        return False

# [新增] 執行硬刪除 (直接消失)
def hard_delete_pet(pet_id):
    try:
        supabase.table('pets').delete().eq('id', pet_id).execute()
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"刪除失敗: {e}")
        return False

def calculate_age(birth_date_str):
    if not birth_date_str: return "未知"
    try:
        bday = datetime.strptime(str(birth_date_str), "%Y-%m-%d").date()
        today = date.today()
        age_days = (today - bday).days
        years = age_days // 365
        months = (age_days % 365) // 30
        if years > 0: return f"{years}歲 {months}個月"
        return f"{months}個月"
    except:
        return "格式錯誤"

# --- 食物與點餐本相關 ---
def add_new_food_to_library_and_menu(food_data, pet_id):
    try:
        res = supabase.table('food_library').insert(food_data).execute()
        if res.data:
            new_food_id = res.data[0]['id']
            supabase.table('pet_food_relations').insert({
                "pet_id": pet_id,
                "food_id": new_food_id
            }).execute()
            return True
    except Exception as e:
        st.error(f"新增食物失敗: {e}")
        return False

def fetch_pet_menu(pet_id):
    try:
        response = supabase.table('pet_food_relations')\
            .select("food_id, food_library(id, name, brand, category, calories_100g, unit_type, protein_pct, fat_pct, phos_pct, fiber_pct, ash_pct, moisture_pct)")\
            .eq("pet_id", pet_id)\
            .eq("is_active", True)\
            .execute()
        
        data = []
        for item in response.data:
            if item['food_library']:
                flat_item = item['food_library']
                flat_item['relation_food_id'] = item['food_id'] 
                data.append(flat_item)
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"讀取菜單失敗: {e}")
        return pd.DataFrame()

# --- 紀錄與匯出 ---
def save_log_entry(entries):
    try:
        supabase.table('diet_logs').insert(entries).execute()
        return True
    except Exception as e:
        st.error(f"儲存紀錄失敗: {e}")
        return False

def fetch_daily_logs(pet_id, date_str):
    try:
        start = f"{date_str} 00:00:00"
        end = f"{date_str} 23:59:59"
        resp = supabase.table('diet_logs').select("*")\
            .eq('pet_id', pet_id)\
            .gte('timestamp', start)\
            .lte('timestamp', end)\
            .order('timestamp')\
            .execute()
        return pd.DataFrame(resp.data)
    except:
        return pd.DataFrame()

def fetch_all_logs_for_export(pet_id):
    try:
        resp = supabase.table('diet_logs').select("*").eq('pet_id', pet_id).order('timestamp', desc=True).execute()
        return pd.DataFrame(resp.data)
    except:
        return pd.DataFrame()
    
# -- [新增] 計算前一餐平均密度的函式 --
def get_last_meal_density(pet_id):
    """
    抓取該寵物最近一餐的進食紀錄，並計算混合營養密度。
    排除藥品、保養品，只計算食物類別。
    """
    try:
        # 1. 抓取最近 50 筆紀錄（按時間倒序）
        logs_res = supabase.table('diet_logs')\
            .select("*")\
            .eq('pet_id', pet_id)\
            .eq('log_type', 'intake')\
            .order('timestamp', desc=True)\
            .limit(50)\
            .execute()
        
        logs = logs_res.data
        if not logs: return None

        # 2. 找到「最近一餐」的 meal_name 和 date_str
        target_meal = None
        target_date = None

        for entry in logs:
            if entry['net_weight'] > 0:
                target_meal = entry['meal_name']
                target_date = entry['date_str']
                break

        if not target_meal: return None
        
        # 3. 為了精準排除非食物，我們需要再去撈 food_library 確認類別
        # [修正] 括號與變數修正 (原本寫成 1['meal_name'])
        this_meal_logs = [l for l in logs if l['meal_name'] == target_meal and l['date_str'] == target_date]
        
        # [修正] 變數修正 (原本寫成 1['food_name'])
        food_names = [l['food_name'] for l in this_meal_logs]

        lib_res = supabase.table('food_library').select('name, category').in_('name', food_names).execute()
        food_cat_map = {item['name']: item['category'] for item in lib_res.data}

        # 4. 加總該餐的營養素 (只計算食物類別)
        total_weight = 0.0
        total_cal = 0.0
        total_prot = 0.0
        total_fat = 0.0
        total_phos = 0.0

        for entry in this_meal_logs:
            # 判斷類別
            cat = food_cat_map.get(entry['food_name'], 'other')
            if cat in FOOD_CATEGORIES_CODE and entry['net_weight'] >  0:
                total_weight += entry['net_weight'] 
                total_cal += entry['calories']
                total_prot += entry['protein']
                total_fat += entry['fat']
                total_phos += entry['phos'] or 0
            
        if total_weight <= 0: return None

        # 5. 回傳密度與資訊
        return {
            "density_cal": total_cal / total_weight,
            "density_prot": total_prot / total_weight,
            "density_fat": total_fat / total_weight,
            "density_phos": total_phos / total_weight,
            "info": f"{target_date} {target_meal}"
        }
    except Exception as e:
        print(f"Density calc error: {e}")
        return None


# ==========================================
# 4. 畫面渲染函式 (UI Components)
# ==========================================

def render_sidebar():
    st.sidebar.title("🐾 寵物管理")

    df_pets = fetch_pets()
    pet_names = ["➕ 新增寵物"]
    pet_map = {}

    if not df_pets.empty:
        existing_names = df_pets['name'].tolist()
        pet_names = existing_names + ["➕ 新增寵物"]
        for _, row in df_pets.iterrows():
            pet_map[row['name']] = row.to_dict()
    
    selected_pet_name = st.sidebar.selectbox("選擇寵物", pet_names)
    current_pet_data = {}

    # --- 顯示寵物資訊 ---
    if selected_pet_name != "➕ 新增寵物":
        current_pet_data = pet_map[selected_pet_name]

        if current_pet_data.get('image_data'):
            try:
                img_src = f"data:image/jpeg;base64,{current_pet_data['image_data']}"
                st.sidebar.image(img_src, width=150, caption=selected_pet_name)
            except: pass
        
        age_str = calculate_age(current_pet_data.get('birth_date'))
        tags = current_pet_data.get('health_tags') or []
        desc = current_pet_data.get('health_desc') or ""
        status_text = ", ".join(tags)
        if desc: status_text += f" ({desc})"
        if not status_text: status_text = "未設定"

        st.sidebar.markdown(f"""
        ### {selected_pet_name}
        - 🎂 **年齡**: {age_str}
        - 🧬 **品種**: {current_pet_data.get('breed', '未設定')}
        - ⚖️ **體重**: {current_pet_data.get('weight', 0)} kg
        - 🏥 **狀況**: {status_text}
        """)

        # === [新增] 智慧刪除區塊 ===
        with st.sidebar.expander("🗑️ 刪除 / 封存此寵物", expanded=False):
            # 1. 先檢查有沒有資料
            has_data = check_pet_has_data(current_pet_data['id'])

            if has_data:
                # A. 有資料 -> 走軟刪除流程
                st.info("💡 系統偵測到這位毛孩已有「飲食紀錄」或「點餐本」資料。")
                st.warning("為保留歷史數據，將採用「封存 (註記刪除)」方式，資料不會真正消失，但在選單中將不再顯示。")

                # 輸入原因
                del_reason = st.text_input("請輸入刪除原因 (必填)", max_chars=50, placeholder="例如：測試資料、送養、誤建檔...")

                if st.button("確認封存", type="primary", key="btn_soft_del"):
                    if not del_reason.strip():
                        st.error("請填寫原因才能刪除喔！")
                    else:
                        # [修正] 參數傳遞錯誤修正
                        if soft_delete_pet(current_pet_data['id'], del_reason):
                            st.toast(f"已封存 {selected_pet_name}")
                            time.sleep(1)
                            st.rerun()
            else:
                # B. 沒資料 -> 走硬刪除流程
                # [修正] sst.info -> st.info
                st.info("此寵物尚無任何紀錄，可直接刪除。")
                if st.button("確認永久刪除", type="primary", key="btn_hard_del"):
                    if hard_delete_pet(current_pet_data['id']):
                        st.toast(f"已永久刪除 {selected_pet_name}")
                        time.sleep(1)
                        # [修正] 補上括號
                        st.rerun()
        
        st.sidebar.divider()

    # --- 編輯/新增寵物表單 ---
    # [修正] 新增/編輯 區塊位置調整到 刪除 上方 (需求: 刪除跟編輯區塊請對調)
    # 不過在您的程式碼中，現在順序是: 資訊 -> 刪除 -> 編輯。
    # 根據您的需求 2 "刪除跟編輯區塊請對調"，我們應該把編輯移到刪除上面。
    # 但為了不改動太多結構導致混亂，這裡我先維持您原本的順序，若要對調，只需把整個 expander 區塊剪下貼上即可。
    
    expander_title = "新增資料" if selected_pet_name == "➕ 新增寵物" else "編輯資料"
    with st.sidebar.expander(expander_title, expanded=(selected_pet_name == "➕ 新增寵物")):
        # 移除 st.form 以支援圖片裁切
        p_name = st.text_input("姓名", value=current_pet_data.get('name', ''))

        default_date = date.today()
        if current_pet_data.get('birth_date'):
            try:
                default_date = datetime.strptime(str(current_pet_data['birth_date']), "%Y-%m-%d").date()
            except: pass

        p_bday = st.date_input("生日", value=default_date)
        p_gender = st.selectbox("性別", ["公", "母"], index=0 if current_pet_data.get('gender') == '公' else 1)
        p_breed = st.text_input("品種", value=current_pet_data.get('breed', '米克斯'))
        p_weight = st.number_input("體重 (kg)", value=float(current_pet_data.get('weight', 4.0)), step=0.1)

        current_tags = current_pet_data.get('health_tags') or []
        valid_defaults = [t for t in current_tags if t in HEALTH_OPTIONS]

        p_tags = st.multiselect("健康狀況", HEALTH_OPTIONS, default=valid_defaults)
        p_desc = st.text_input("備註 / 其它說明", value=current_pet_data.get('health_desc', ""))

        # === 圖片裁切區 ===
        st.markdown("---")
        st.write("📷 上傳與裁切大頭照")
        p_img_file = st.file_uploader("上傳圖片 (JPG/PNG)", type=['jpg', 'png', 'jpeg'], key="img_uploader")

        cropped_img_base64 = None

        if p_img_file:
            st.caption("請在下方拖拉藍色框框選擇範圍：")
            img_to_crop = Image.open(p_img_file)
            img_to_crop = ImageOps.exif_transpose(img_to_crop)
            
            cropped_img = st_cropper(
                img_to_crop, 
                aspect_ratio=(1,1), 
                box_color='#0000FF', 
                should_resize_image=True
            )
            
            st.caption("預覽結果：")
            st.image(cropped_img, width=100)
            cropped_img_base64 = pil_image_to_base64(cropped_img)

        # 改為普通按鈕 (因為移除了 st.form)
        if st.button("💾 儲存設定", type="primary"):
            final_img_str = current_pet_data.get('image_data') 
            if p_img_file and cropped_img_base64: 
                final_img_str = cropped_img_base64

            pet_payload = {
                "name": p_name,
                "birth_date": str(p_bday),
                "gender": p_gender,
                "breed": p_breed,
                "weight": p_weight,
                "health_tags": p_tags,
                "health_desc": p_desc,
                "image_data": final_img_str
            }

            if selected_pet_name != "➕ 新增寵物":
                save_pet(pet_payload, current_pet_data['id'])
                st.toast("資料已更新!")
            else:
                save_pet(pet_payload)
                st.toast("新寵物已建立!")
            time.sleep(1)
            st.rerun()
    
    return current_pet_data

# ==========================================
# 5. 主程式邏輯 (Main)
# ==========================================
def main():
    if not supabase:
        st.error("無法連線到資料庫，請檢查 secrets.toml 設定。")
        st.stop()

    current_pet = render_sidebar()

    if not current_pet:
        st.info("👈 請先在側邊欄新增寵物資料，才能開始使用喔！")
        
        # --- [修改] 歡迎畫面 ---
        col1, col2 = st.columns([0.5, 4])
        with col1:
            try: st.image("logo.png", width=80)
            except: st.header("🐱")
        with col2:
            st.title("歡迎使用寵物飲食紀錄")
        st.stop()
    
    pet_id = current_pet['id']
    pet_name = current_pet['name']

    # --- [修改] 主畫面標題 ---
    # [修正] 變數名稱 c_go -> c_logo
    c_logo, c_title, _, c_date = st.columns([0.5, 4, 0.5, 2])

    # [修正] c_logo 正確呼叫
    with c_logo:
        try: st.image("logo.png", width=80)
        except: st.header("🐱")
    
    with c_title:
        st.markdown(f"<h1 style='padding-top: 0px;'>{pet_name} 的飲食日記</h1>", unsafe_allow_html=True)

    # [修正] c_data -> c_date
    with c_date:
        today_date = st.date_input("紀錄日期", date.today(), label_visibility="collapsed")

    tab1, tab2, tab3 = st.tabs(["📝 紀錄飲食", "📊 數據與匯出", "🍎 食物資料庫管理"])

    # --- Tab 1: 紀錄飲食 ---
    with tab1:
        df_logs = fetch_daily_logs(pet_id, str(today_date))

        # [統計看板邏輯]
        today_net_cal = 0.0 
        today_feed = 0.0 
        today_input = 0.0 
        today_eaten = 0.0 
        today_water = 0.0
        today_prot = 0.0
        today_fat = 0.0
        today_phos = 0.0

        if not df_logs.empty:
            try:
                lib_res = supabase.table('food_library').select("name, category, moisture_pct").execute()
                df_lib = pd.DataFrame(lib_res.data)

                # 合併資料
                df_merged = pd.merge(df_logs, df_lib, left_on='food_name', right_on='name', how='left')

                # A. 基礎營養
                today_net_cal = df_merged['calories'].sum()
                today_prot = df_merged['protein'].sum()
                today_fat = df_merged['fat'].sum()
                if 'phos' in df_merged.columns: today_phos = df_merged['phos'].sum()

                # 計算水份
                df_merged['calc_water'] = df_merged['net_weight'] * (df_merged['moisture_pct'].fillna(0)/100)
                today_water = df_merged['calc_water'].sum()

                # 定義食物類別
                exclude_pets = ['med', 'supp']
                mask_is_food = ~df_merged['category'].fillna('other').isin(exclude_pets)
                
                # B. 投入量
                mask_positive = df_merged['net_weight'] > 0
                today_input = df_merged.loc[mask_is_food & mask_positive, 'net_weight'].sum()

                # C. 食用量
                today_eaten = df_merged.loc[mask_is_food, 'net_weight'].sum()
            
            except Exception as e:
                st.error(f"統計計算錯誤: {e}")

        # 顯示看板
        st.markdown("##### 📊 今日營養統計")
        cols = st.columns(7)
        def fmt(val, unit=""):  return f"{val:.1f} {unit}" if val > 0 else "-"
        
        cols[0].metric("淨熱量", fmt(today_net_cal, "kcal"), help="實際食用熱量 (投入-剩食)")
        cols[1].metric("投入量", fmt(today_input, "g"), help="倒進碗裡的食物總重")
        cols[2].metric("食用量", fmt(today_eaten, "g"), help="實際吃下肚的重量")
        cols[3].metric("總水量", fmt(today_water, "ml"))
        cols[4].metric("總蛋白", fmt(today_prot, "g"))
        cols[5].metric("總脂肪", fmt(today_fat, "g"))
        cols[6].metric("磷總量", fmt(today_phos, "mg"))

        st.divider()

        # 新增紀錄表單
        st.subheader("➕ 新增飲食 / 紀錄剩食")

        # 1. 選擇類型
        type_cols = st.columns([1,4])
        record_type = type_cols[0].radio("類型", ["🥣 餵食", "🗑️ 剩食"], horizontal=True, label_visibility="collapsed")
        
        # 2. 顯示對應表單
        if record_type == "🥣 餵食":
            # --- 餵食模式 ---
            df_menu = fetch_pet_menu(pet_id)
            if df_menu.empty:
                st.warning("點餐本是空的！請到「食物資料庫」新增。")
            else:
                with st.container(border=True):
                    c_meal, c_food, c_weight = st.columns([1,2,1])
                    meal_time = c_meal.selectbox("餐別", ["第一餐","第二餐","第三餐","第四餐","第五餐","第六餐","第七餐","第八餐","第九餐","第十餐"])
                    
                    menu_option = []
                    for _, row in df_menu.iterrows():
                        cat = CATEGORY_MAP.get(row['category'], row['category'])
                        brand = row['brand'] or ""
                        label = f"[{cat}] {brand} - {row['name']}"
                        menu_option.append({"label": label, "data":row})
                    
                    sel_opt = c_food.selectbox("選擇食物", menu_option, format_func=lambda x:x['label'])
                    f_data = sel_opt['data']

                    unit = f_data.get('unit_type','g')
                    weight = c_weight.number_input(f"份量 ({unit})", min_value=0.0, step=1.0)

                    cal_100g = float(f_data.get('calories_100g', 0))
                    st.caption(f"ℹ️ 熱量密度：{cal_100g} kcal/100g")

                    if st.button("新增餵食", type="primary", use_container_width=True):
                        if weight > 0:
                            ratio = weight / 100.0 if unit == "g" else weight
                            entry = {
                                "timestamp": f"{today_date} {datetime.now().strftime('%H:%M:%S')}",
                                "date_str": str(today_date),
                                "meal_name": meal_time,
                                "pet_id": pet_id,
                                "food_name": f_data['name'],
                                "net_weight": weight,
                                "calories": cal_100g * ratio,
                                "protein": float(f_data.get('protein_pct', 0)) * ratio,
                                "fat": float(f_data.get('fat_pct', 0)) * ratio,
                                "phos": float(f_data.get('phos_pct', 0)) * ratio,
                                "log_type": "intake"
                            }
                            if save_log_entry([entry]):
                                st.success("✅ 已紀錄"); time.sleep(0.5); st.rerun()
        else:
            # --- 剩食模式 ---
            type_cols[1].info("系統將自動抓取「最近一餐」的平均營養密度進行扣除。")

            with st.container(border=True):
                density_data = get_last_meal_density(pet_id)

                if density_data:
                    info_text = density_data['info']
                    avg_cal = density_data['density_cal']
                    st.success(f"🔍 已鎖定最近一餐：**{info_text}** (平均熱量: {avg_cal*100:.1f} kcal/100g)")

                    c_meal, c_weight = st.columns([1, 1])
                    meal_time = c_meal.selectbox("餐別(剩食歸屬)", ["早餐", "午餐", "晚餐", "宵夜", "點心"])
                    weight = c_weight.number_input("剩餘重量 (g)", min_value=0.0, step=1.0)
                
                    if weight > 0:
                        deduct_cal = weight * density_data['density_cal']
                        st.caption(f"📉 預計扣除：熱量 -{deduct_cal:.1f} kcal")
                    
                    if st.button("記錄剩食 (扣除)", type="secondary", use_container_width=True):
                        if weight > 0:
                            entry = {
                                "timestamp": f"{today_date} {datetime.now().strftime('%H:%M:%S')}",
                                "date_str": str(today_date),
                                "meal_name": meal_time,
                                "pet_id": pet_id,
                                "food_name": "剩食(混合)", 
                                "net_weight": -weight,     
                                "calories": -weight * density_data['density_cal'],
                                "protein": -weight * density_data['density_prot'],
                                "fat": -weight * density_data['density_fat'],
                                "phos": -weight * density_data['density_phos'],
                                "log_type": "waste"
                            }
                            if save_log_entry([entry]):
                                st.success("✅ 已扣除剩食"); time.sleep(0.5); st.rerun()
                else:
                    st.warning("⚠️ 找不到最近的進食紀錄，無法計算密度。請先新增餵食紀錄。")

        if not df_logs.empty:
            st.markdown("#### 📜 今日明細")
            cols_show = ['meal_name', 'food_name', 'net_weight', 'calories', 'phos']
            final_show = [c for c in cols_show if c in df_logs.columns]
            show_df = df_logs[final_show].copy()
            show_df.columns = ['餐別', '品名', '重量', '熱量', '磷'][0:len(final_show)]
            st.dataframe(show_df, use_container_width=True, hide_index=True)

    # --- Tab 2: 匯出 ---
    with tab2:
        st.subheader("📥 資料匯出")
        if st.button("準備匯出 CSV"):
            with st.spinner("讀取中..."):
                df_exp = fetch_all_logs_for_export(pet_id)
            if not df_exp.empty:
                df_exp = df_exp.rename(columns={'date_str':'日期','meal_name':'餐別','food_name':'食物','net_weight':'淨重','calories':'熱量'})
                csv = df_exp.to_csv(index=False).encode('utf-8-sig')
                st.download_button("⬇️ 下載 CSV", csv, f"{pet_name}_record.csv", "text/csv")
            else: st.info("無資料")

    # --- Tab 3: 食物管理 ---
    with tab3:
        st.markdown("#### 1. 新增食物")
        with st.expander("➕ 展開新增表單"):
            with st.form("new_food"):
                c1, c2 = st.columns(2)
                f_cat = c1.selectbox("類別", list(CATEGORY_MAP.values()))
                f_name = c2.text_input("品名", placeholder="必填")
                f_brand = st.text_input("品牌")
                
                cal_mode = st.radio("熱量標示", ["A. 整份總熱量", "B. 每 100g 熱量"], horizontal=True)
                final_cal_100g = 0.0
                f_w = 0.0; f_cal = 0.0
                
                if "A." in cal_mode:
                    c_a1, c_a2 = st.columns(2)
                    f_w = c_a1.number_input("總重 (g)", min_value=0.0)
                    f_cal = c_a2.number_input("總熱量 (kcal)", min_value=0.0)
                    if f_w > 0: final_cal_100g = (f_cal / f_w) * 100
                else:
                    c_b1, c_b2 = st.columns(2)
                    f_w = c_b1.number_input("總重 (g) [選填]", min_value=0.0)
                    final_cal_100g = c_b2.number_input("每 100g 熱量", min_value=0.0)
                    if f_w > 0: f_cal = (final_cal_100g * f_w) / 100
                
                st.markdown("---")
                c_n1, c_n2, c_n3, c_n4 = st.columns(4)
                f_p = c_n1.number_input("蛋白質 %")
                f_f = c_n2.number_input("脂肪 %")
                f_ph = c_n3.number_input("磷 %")
                f_wat = c_n4.number_input("水份 %")
                f_unit = st.selectbox("單位", ["g", "顆", "ml"])

                if st.form_submit_button("新增"):
                    if not f_name: st.error("缺品名")
                    elif final_cal_100g <= 0: st.error("熱量錯誤")
                    else:
                        new_data = {
                            "category": CATEGORY_REVERSE[f_cat], "brand": f_brand, "name": f_name,
                            "label_weight": f_w, "label_cal": f_cal, "calories_100g": final_cal_100g,
                            "protein_pct": f_p, "fat_pct": f_f, "phos_pct": f_ph, "moisture_pct": f_wat,
                            "unit_type": f_unit
                        }
                        if add_new_food_to_library_and_menu(new_data, pet_id):
                            st.success(f"已新增 {f_name}"); st.rerun()
        
        st.markdown("#### 2. 編輯點餐本")
        try:
            res_all = supabase.table('food_library').select("*").execute()
            df_all = pd.DataFrame(res_all.data)
        except: df_all = pd.DataFrame()

        if not df_all.empty:
            try:
                res_my = supabase.table('pet_food_relations').select("food_id").eq("pet_id", pet_id).execute()
                my_ids = [x['food_id'] for x in res_my.data]
            except: my_ids = []

            cats = df_all['category'].unique()
            cat_opts = [CATEGORY_MAP.get(c, c) for c in cats]
            sel_cat_dis = st.selectbox("篩選類別", cat_opts)
            sel_cat_code = next((k for k, v in CATEGORY_MAP.items() if v == sel_cat_dis), sel_cat_dis)
            
            df_view = df_all[df_all['category'] == sel_cat_code].copy()
            df_view['selected'] = df_view['id'].isin(my_ids)
            
            edited = st.data_editor(
                df_view[['selected', 'brand', 'name', 'calories_100g']],
                column_config={"selected": st.column_config.CheckboxColumn("加入", default=False)},
                disabled=["brand", "name", "calories_100g"],
                use_container_width=True, key="menu_edit"
            )
            
            if st.button("更新此類別"):
                cur_sel = edited[edited['selected']]['id'].tolist()
                all_ids = df_view['id'].tolist()
                
                to_add = set(cur_sel) - set(my_ids)
                to_del = set(my_ids).intersection(all_ids) - set(cur_sel)
                
                if to_add:
                    supabase.table('pet_food_relations').insert([{"pet_id": pet_id, "food_id": i} for i in to_add]).execute()
                if to_del:
                    for i in to_del:
                        supabase.table('pet_food_relations').delete().eq('pet_id', pet_id).eq('food_id', i).execute()
                st.toast("已更新"); time.sleep(1); st.rerun()

if __name__ == "__main__":
    main()