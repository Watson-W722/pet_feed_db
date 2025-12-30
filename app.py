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
    icon_image = Image.open("page_icon.png")
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
# [修正] item() -> items()
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
        # 統一縮小到 300x300 以內 (節省資料庫空間)
        image.thumbnail((300, 300))
        buffered = io.BytesIO()
        # 轉成 JPEG
        image.save(buffered, format="JPEG", quality=80)
        # [修正] b64decode -> b64encode (我們要編碼存進去，不是解碼)
        return base64.b64encode(buffered.getvalue()).decode()
    except Exception as e:
        st.error(f"圖片轉碼失敗: {e}")
        return None

# --- 寵物相關 ---
def fetch_pets():
    try:
        response = supabase.table('pets').select("*").order('created_at').execute()
        return pd.DataFrame(response.data)
    except Exception as e:
        return pd.DataFrame()

def save_pet(data_dict, pet_id=None):
    if pet_id:
        # [修正] 更新舊資料要用 update
        supabase.table('pets').update(data_dict).eq('id', pet_id).execute()
    else:
        # 新增資料用 insert
        supabase.table('pets').insert(data_dict).execute()
    st.cache_data.clear()

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
# [修正] 補上冒號
def add_new_food_to_library_and_menu(food_data, pet_id):
    try:
        # [修正] food_iibrary -> food_library
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
    # [修正] 補上冒號
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
                # [修正] 賦值邏輯修正
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

# [修正] data_str -> date_str
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

# [修正] ped_id -> pet_id
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
        # 邏輯：找到第一筆 net_weight > 0 的紀錄，視為最近一餐的標記
        target_meal = None
        target_date = None

        for entry in logs:
            if entry['net_weight'] > 0:
                target_meal = entry['meal_name']
                target_date = entry['date_str']
                break

        if not target_meal: return None
        # 3. 為了精準排除非食物，我們需要再去撈 food_library 確認類別
        # 先把這餐的 food_name 都抓出來
        this_meal_logs = [l for l in logs if l['meal_name']] == target_meal and 1['date_str'] == target_date
        food_names = [1['food_name']for l in this_meal_logs]

        lib_res = supabase.table('food_library').select('name, category').in_('name', food_names).execute()
        food_cat_map = {item['name']: item['category'] for item in lib_res.data}

        # 4. 加總該餐的營養素 (只計算食物類別
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
            return{
                "density_cal": total_cal / total_weight,
                "density_prot": total_prot / total_weight,
                "density_fat": total_fat / total_weight,
                "density_phos": total_phos / total_weight,
                "info": f"{(target_date) {target_meal}}"
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
            pet_map[row['name']] = row
    
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
        
        # [修正] birth_data -> birth_date
        age_str = calculate_age(current_pet_data.get('birth_date'))
        tags = current_pet_data.get('health_tags') or []
        desc = current_pet_data.get('health_desc') or ""
        status_text = ", ".join(tags)
        if desc: status_text += f"({desc})"
        if not status_text: status_text = "未設定"

        # [修正] bread -> breed
        st.sidebar.markdown(f"""
        ### {selected_pet_name}
        - 🎂 **年齡**: {age_str}
        - 🧬 **品種**: {current_pet_data.get('breed', '未設定')}
        - ⚖️ **體重**: {current_pet_data.get('weight', 0)} kg
        - 🏥 **狀況**: {status_text}
        """)

        st.sidebar.divider()

    # --- 編輯/新增寵物表單 ---
    # [修正] expander 括號
    with st.sidebar.expander(f"{'新增' if selected_pet_name == '➕ 新增寵物' else '編輯'} 資料"):
        with st.form("pet_form"):
            p_name = st.text_input("姓名", value=current_pet_data.get('name', ''))

            default_date = date.today()
            if current_pet_data.get('birth_date'):
                try:
                    default_date = datetime.strptime(str(current_pet_data['birth_date']), "%Y-%m-%d").date()
                except: pass

            p_bday = st.date_input("生日", value=default_date)
            p_gender = st.selectbox("性別", ["公", "母"], index=0 if current_pet_data.get('gender') == '公' else 1)
            # [修正] bread -> breed
            p_breed = st.text_input("品種", value=current_pet_data.get('breed', '米克斯'))
            p_weight = st.number_input("體重 (kg)", value=float(current_pet_data.get('weight', 4.0)), step=0.1)

            current_tags = current_pet_data.get('health_tags') or []
            valid_defaults = [t for t in current_tags if t in HEALTH_OPTIONS]

            p_tags = st.multiselect("健康狀況", HEALTH_OPTIONS, default=valid_defaults)
            p_desc = st.text_input("備註 / 其它說明", value=current_pet_data.get('health_desc', ""))

            # === 圖片裁切區 ===
            st.markdown("---")
            st.write("📷 上傳與裁切大頭照")
            # [修正] type 格式列表
            p_img_file = st.file_uploader("上傳圖片 (JPG/PNG)", type=['jpg', 'png', 'jpeg'])

            cropped_img_base64 = None

            if p_img_file:
                st.caption("請在下方拖拉藍色框框選擇範圍：")
                img_to_crop = Image.open(p_img_file)
                img_to_crop = ImageOps.exif_transpose(img_to_crop)
                cropped_img = st_cropper(img_to_crop, aspect_ratio=(1,1), box_color='#0000FF', should_resize_image=True)
                st.caption("預覽結果：")
                st.image(cropped_img, width=100)
                cropped_img_base64 = pil_image_to_base64(cropped_img)

            if st.form_submit_button("💾 儲存設定"):
                final_img_str = current_pet_data.get('image_data') 
                # [修正] ropped -> cropped
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
        # [修正] 加上括號
        st.stop()

    current_pet = render_sidebar()

    if not current_pet:
        st.info("👈 請先在側邊欄新增寵物資料，才能開始使用喔！")
        st.title("🐱 歡迎使用寵物飲食紀錄")
        # st.write("這是您的個人資料庫版本，資料將永久保存在雲端。請依照左側指示建立第一位主子。")
        st.stop()
    
    pet_id = current_pet['id']
    pet_name = current_pet['name']

    c1, c2 = st.columns([3,1])
    with c1: st.title(f"🍽️ {pet_name} 的飲食日記")
    with c2: today_date = st.date_input("紀錄日期", date.today())

    tab1, tab2, tab3 = st.tabs(["📝 紀錄飲食", "📊 數據與匯出", "🍎 食物資料庫管理"])

    # --- Tab 1: 紀錄飲食 ---
    with tab1:
        df_logs = fetch_daily_logs(pet_id, str(today_date))

        # [統計看板邏輯]
        today_net_cal = 0.0 # A. 總熱量 (實際食用)
        today_feed = 0.0 # B. 投入量 (不含水藥，不扣剩食)
        today_input = 0.0 # C. 食用量 (不含水藥，扣除剩食)
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

                # A. 基礎營養 (直接加總，正負會抵銷)
                today_cal = df_merged['calories'].sum()
                today_prot = df_merged['protein'].sum()
                today_fat = df_merged['fat'].sum()
                today_phos = df_merged['phos'].sum()

                # 計算水份 (淨重 * 水份% / 100)
                # 剩食時 net_weight 為負數，這裡算出來的水份也會是負數，剛好抵銷
                df_merged['calc_water'] = df_merged['net_weight'] * (df_merged['moisture_pct'].fillna(0)/100)
                today_water = df_merged['calc_water'].sum()

                # 定義食物類別 (排除藥、保養品)
                exclude_pets = ['med', 'supp']
                mask_is_food = ~df_merged['category'].fillna('other').isin(exclude_pets)
                
                # B. 投入量（只算 food 且 weight > 0）
                mask_positive = df_merged['net_weight'] > 0
                today_input = df_merged.loc[mask_is_food & mask_positive, 'net_weight'].sum()

                # C. 食用量（只算 food，包含正負數加總）
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
            # --- 餵食模式（原本的選單） ---
            df_menu = fetch_pet_menu(pet_id)
            if df_menu.empty:
                st.warning("點餐本是空的！請到「食物資料庫」新增。")
            else:
                with st.container(border=True):
                    c_meal, c_food, c_weight = st.columns([1,2,1])

                    meal_time = c_meal.selectbox("餐別", ["第一餐","第二餐","第三餐","第四餐","第五餐","第六餐","第七餐","第八餐","第九餐","第十餐"])

                # 製作選單選項：[類別] 品牌 - 品名
                menu_option = []
                for _, row in df_menu.iterrows():
                    # 處理中文類別
                    cat = CATEGORY_MAP.get(row['category'], row['category'])
                    # 處理 None 值
                    brand = row['brand'] or ""
                    label = f"[{cat}] {brand} - {row['name']}"
                    menu_option.append({"label": label, "data":row})

                
                sel_opt = c_food.selectbox("選擇食物", menu_option, format_func=lambda x:x['label'])
                f_data = sel_opt['data']

                # 單位提示
                unit = f_data.get('unit_type','g')
                weight = c_weight.number_input(f"份量 ({unit})", min_value=0.0, step=1.0)

                # 顯示營養密度提示
                cal_100g = float(f_data.get('calories_100g', 0))
                st.caption(f"ℹ️ 熱量密度：{cal_100g} kcal/100g")

                if st.button("新增餵食", type="primary", use_container_width=True):
                    if weight > 0:
                        # 計算營養
                        ratio = weight / 100.0 if unit == "g" else weight
                        entry = {
                            "timestamp": f"{today_date}{datetime.now().strftime('%H:%M:%S')}",
                            "date_str": str(today_date),
                            "meal_name": meal_time,
                            "pet_id": pet_id,
                            "food_name": f_data['name'],
                            "new_weight": weight,
                            "calories": cal_100g * ratio,
                            "protein": float(f_data.get('protein_pct', 0)) * ratio,
                            "fat": float(f_data.get('fat_pct', 0)) * ratio,
                            "phos": float(f_data.get('phos_pct', 0)) * ratio,
                            "log_type": "intake"
                        }
                        if save_log_entry([entry]):
                            st.success("✅ 已紀錄"); time.sleep(0,5); st.rerun()
                else:
                    # --- 剩食模式 (自動計算平均密度) ---
                    type_cols[1].info("系統將自動抓取「最近一餐」的平均營養密度進行扣除。")

                    with st.container(border=True):
                        # 1. 抓取上一餐密度
                        density_data = get_last_meal_density(pet_id)

                        if density_data:
                            info_text = density_data['info']
                            avg_cal = density_data['density_cal']
                            st.success(f"🔍 已鎖定最近一餐：**{info_text}** (平均熱量: {avg_cal*100:.1f} kcal/100g)")

                        

