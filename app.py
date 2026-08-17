import json
import os
import sys
from datetime import datetime, timezone, timedelta
import gspread
import pandas as pd
from PIL import Image
import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# --- 設定項目 ---
try:
    MY_API_KEY = st.secrets.get("GEMINI_API_KEY", "AQ.Ab8RN6KZA_e6l-GreYHWYoZKZXPZVfEk3qwL2UehTQKBFBc4Og")
    SPREADSHEET_ID = st.secrets.get("SPREADSHEET_ID", "1RPpypQ_UiiwNkTX923Q_c1suxMlS2DhvxkOvV0I98O8")
except Exception:
    MY_API_KEY = "AQ.Ab8RN6KZA_e6l-GreYHWYoZKZXPZVfEk3qwL2UehTQKBFBc4Og"
    SPREADSHEET_ID = "1RPpypQ_UiiwNkTX923Q_c1suxMlS2DhvxkOvV0I98O8"

# --- 1. データ構造定義 ---
# 食材ごとの内訳
class FoodItemDetail(BaseModel):
    name: str = Field(description="食材名・料理名")
    portion: str = Field(description="推定分量（例: 200g, 1切れ, 1個など）")
    calories: int = Field(description="カロリー(kcal)")
    protein_g: float = Field(description="タンパク質(g)")
    fat_g: float = Field(description="脂質(g)")
    carbs_g: float = Field(description="炭水化物(g)")

# 全体のデータ構造
class NutritionData(BaseModel):
    food_name: str = Field(description="食事全体の名前やメニュー内容")
    calories: int = Field(description="推定総カロリー(kcal)")
    protein_g: float = Field(description="タンパク質(g)")
    fat_g: float = Field(description="脂質(g)")
    carbs_g: float = Field(description="炭水化物(g)")
    micro_notes: str = Field(description="食物繊維量(g)や、豊富に含まれる主要ビタミン・ミネラル等の栄養要約（例: 食物繊維 4.5g / ビタミンB群・D・鉄分が豊富）", default="")
    items: list[FoodItemDetail] = Field(description="構成される各食材・料理ごとの推定内訳リスト", default=[])

# --- 2. Gemini API解析関数（OCRモード対応） ---
def analyze_nutrition(input_data, api_key: str, is_ocr: bool = False) -> dict:
    client = genai.Client(api_key=api_key)
    
    if is_ocr:
        prompt = (
            "提供された画像またはテキストは食品パッケージの「栄養成分表示」です。"
            "印刷・記載されている商品名、カロリー/熱量(kcal)、タンパク質(g)、脂質(g)、炭水化物(g)、"
            "および食物繊維や食塩相当量・ビタミンなどの記載をそのまま正確に読み取って抽出してください。"
            "商品名が読み取れる場合はfood_nameに設定し、記載の数値を各項目に正確にセットしてください。"
        )
    else:
        prompt = (
            "提供された食事内容（テキストまたは画像）から、総カロリー(kcal)とPFC（タンパク質・脂質・炭水化物(g)）を計算してください。"
            "また、食物繊維量(g)や主要ビタミン・ミネラルの特徴をmicro_notesにまとめ、各食材ごとの内訳（分量、カロリー、PFC）もitemsに分解して出力してください。"
        )
    
    if isinstance(input_data, list):
        contents = [prompt] + input_data
    else:
        contents = [prompt, input_data]
        
    response = client.models.generate_content(
        model='gemini-flash-latest',
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=NutritionData,
        ),
    )
    return json.loads(response.text)

# --- 今日の食事AIアドバイス生成関数（追加） ---
def get_daily_advice(today_df: pd.DataFrame, targets: dict, c_sum: float, p_sum: float, f_sum: float, carbs_sum: float, api_key: str) -> str:
    client = genai.Client(api_key=api_key)
    
    meal_lines = []
    for _, row in today_df.iterrows():
        meal_lines.append(f"- [{row.get('区分', '')}] {row.get('食事内容', '')} ({row.get('カロリー', 0)}kcal, P:{row.get('タンパク質', 0)}g, F:{row.get('脂質', 0)}g, C:{row.get('炭水化物', 0)}g)")
    meals_text = "\n".join(meal_lines)
    
    prompt = f"""
あなたは優秀なボディメイク専門の管理栄養士です。
以下の本日の食事記録と目標値を分析し、
1. 良かった点（PFCバランスや食材の質など）
2. 今後の改善点・アドバイス（次の食事で補うべき栄養や注意点）
を、分かりやすく簡潔に（200〜300文字程度で）アドバイスしてください。

【本日の目標】
- カロリー: {targets['cal']} kcal / タンパク質: {targets['p']} g / 脂質: {targets['f']} g / 炭水化物: {targets['c']} g

【本日の摂取実績】
- 合計カロリー: {int(c_sum)} kcal (目標差: {int(targets['cal'] - c_sum)} kcal)
- タンパク質: {round(p_sum, 1)} g (目標差: {round(targets['p'] - p_sum, 1)} g)
- 脂質: {round(f_sum, 1)} g (目標差: {round(targets['f'] - f_sum, 1)} g)
- 炭水化物: {round(carbs_sum, 1)} g (目標差: {round(targets['c'] - carbs_sum, 1)} g)

【本日の食事内容】
{meals_text}
"""
    response = client.models.generate_content(
        model='gemini-flash-latest',
        contents=prompt
    )
    return response.text
# --- 3. スプレッドシート操作関数 ---
# --- 3. スプレッドシート操作関数（キャッシュ最適化版） ---
def get_spreadsheet():
    try:
        if "GCP_JSON_TEXT" in st.secrets and st.secrets["GCP_JSON_TEXT"]:
            json_text = st.secrets["GCP_JSON_TEXT"]
            with open("credentials_cloud.json", "w", encoding="utf-8") as f:
                f.write(json_text)
            return gspread.service_account(filename="credentials_cloud.json").open_by_key(SPREADSHEET_ID)
        elif "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            if "private_key" in creds_dict:
                creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
            return gspread.service_account_from_dict(creds_dict).open_by_key(SPREADSHEET_ID)
    except Exception:
        pass
        
    return gspread.service_account(filename="credentials.json").open_by_key(SPREADSHEET_ID)

def get_worksheet():
    sh = get_spreadsheet()
    return sh.get_worksheet(0)

def get_weight_worksheet():
    sh = get_spreadsheet()
    try:
        return sh.worksheet("体重記録")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="体重記録", rows=100, cols=10)
        ws.append_row(["日付", "体重", "体脂肪率", "便の状態", "備考"])
        return ws

def get_target_worksheet():
    sh = get_spreadsheet()
    try:
        return sh.worksheet("目標設定")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="目標設定", rows=5, cols=4)
        ws.append_row(["カロリー", "タンパク質", "脂質", "炭水化物"])
        ws.append_row([2350, 141.0, 54.8, 323.1])
        return ws

# --- キャッシュ付きデータ読み込み関数（60秒間通信をスキップして高速化） ---
@st.cache_data(ttl=60)
def load_all_data():
    ws = get_worksheet()
    rows = ws.get_all_values()
    if not rows or len(rows) <= 1:
        return pd.DataFrame()
    headers = [h if h != "" else f"_blank_{i}" for i, h in enumerate(rows[0])]
    df = pd.DataFrame(rows[1:], columns=headers)
    blank_cols = [c for c in df.columns if c.startswith("_blank_")]
    if blank_cols:
        df = df.drop(columns=blank_cols)
    return df

@st.cache_data(ttl=60)
def load_weight_data():
    ws = get_weight_worksheet()
    rows = ws.get_all_values()
    if not rows or len(rows) <= 1:
        return pd.DataFrame()
    headers = [h if h != "" else f"_blank_{i}" for i, h in enumerate(rows[0])]
    df = pd.DataFrame(rows[1:], columns=headers)
    blank_cols = [c for c in df.columns if c.startswith("_blank_")]
    if blank_cols:
        df = df.drop(columns=blank_cols)
    return df

@st.cache_data(ttl=300)
def load_target_data() -> dict:
    try:
        ws = get_target_worksheet()
        rows = ws.get_all_values()
        if len(rows) >= 2:
            rows.pop(0)
            target_row = rows.pop(0)
            cal_str, p_str, f_str, c_str = target_row[:4]
            return {
                "cal": int(pd.to_numeric(cal_str, errors="coerce") or 2350),
                "p": float(pd.to_numeric(p_str, errors="coerce") or 141.0),
                "f": float(pd.to_numeric(f_str, errors="coerce") or 54.8),
                "c": float(pd.to_numeric(c_str, errors="coerce") or 323.1)
            }
    except Exception:
        pass
    return {"cal": 2350, "p": 141.0, "f": 54.8, "c": 323.1}

# --- 保存・更新・削除関数（変更時にキャッシュを自動クリア） ---
def save_to_spreadsheet(data: dict, meal_type: str):
    ws = get_worksheet()
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)
    
    row = [
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M"),
        meal_type,
        data["food_name"],
        data["calories"],
        data["protein_g"],
        data["fat_g"],
        data["carbs_g"],
        data.get("micro_notes", "")
    ]
    ws.append_row(row)
    st.cache_data.clear()

def save_weight_data(date_str: str, weight: float, fat: float, stool: str, memo: str):
    ws = get_weight_worksheet()
    ws.append_row([date_str, weight, fat, stool, memo])
    st.cache_data.clear()

def save_target_data(cal: int, p: float, f: float, c: float):
    ws = get_target_worksheet()
    ws.update("A2:D2", [[cal, p, f, c]])
    st.cache_data.clear()

def delete_spreadsheet_row(row_index: int):
    ws = get_worksheet()
    ws.delete_rows(row_index + 2)
    st.cache_data.clear()

def delete_weight_row(row_index: int):
    ws = get_weight_worksheet()
    ws.delete_rows(row_index + 2)
    st.cache_data.clear()

def update_spreadsheet_row(row_index: int, updated_data: list):
    ws = get_worksheet()
    target_row = row_index + 2
    cell_range = f"A{target_row}:I{target_row}"
    ws.update(cell_range, [updated_data])
    st.cache_data.clear()

def update_weight_row(row_index: int, updated_data: list):
    ws = get_weight_worksheet()
    target_row = row_index + 2
    cell_range = f"A{target_row}:E{target_row}"
    ws.update(cell_range, [updated_data])
    st.cache_data.clear()
# --- 4. Streamlit UI 画面構築 ---
st.set_page_config(page_title="食事・体重管理", layout="centered", page_icon="💪")

st.markdown("""
<style>
    /* 全体背景・フォント（明るい背景＋水色系） */
    .stApp {
        background: linear-gradient(180deg, #f4fbfd 0%, #eaf6f9 100%);
        color: #1b2b34;
    }

    /* メインタイトル */
    .app-header {
        text-align: center;
        padding: 1.2rem 0 1.5rem 0;
        border-bottom: 1px solid #bfe3ec;
        margin-bottom: 1.5rem;
    }
    .app-header h1 {
        font-size: 1.9rem;
        font-weight: 800;
        letter-spacing: 0.02em;
        color: #0891b2;
        margin: 0;
    }
    .app-header p {
        color: #4b6a75;
        font-size: 0.85rem;
        margin-top: 0.2rem;
    }

    /* サイドバー */
    section[data-testid="stSidebar"] {
        background-color: #eaf6f9;
        border-right: 1px solid #bfe3ec;
    }
    section[data-testid="stSidebar"] * {
        color: #1b2b34;
    }

    /* metric カード風装飾 */
    div[data-testid="stMetric"] {
        background-color: #ffffff;
        border: 1px solid #bfe3ec;
        border-radius: 12px;
        padding: 0.9rem 0.7rem;
        box-shadow: 0 2px 6px rgba(8, 145, 178, 0.08);
    }
    div[data-testid="stMetricValue"] {
        color: #0891b2;
        font-weight: 700;
    }
    div[data-testid="stMetricLabel"] {
        color: #4b6a75;
    }

    /* 見出し類（st.subheader / st.write ### など） */
    h1, h2, h3, h4, h5, h6 {
        color: #0e7490;
    }

    /* 通常テキスト・キャプション */
    p, span, label, .stMarkdown, .stCaption {
        color: #1b2b34;
    }

    /* ボタン */
    div.stButton > button {
        border-radius: 10px;
        border: 1px solid #0891b2;
        color: #0891b2;
        background-color: #ffffff;
        font-weight: 600;
        transition: all 0.2s ease;
    }
    div.stButton > button:hover {
        background-color: #0891b2;
        color: #ffffff;
        border-color: #0891b2;
    }
    div.stButton > button[kind="primary"] {
        background-color: #0e7490;
        border-color: #0e7490;
        color: #fff;
    }
    div.stButton > button[kind="primary"]:hover {
        background-color: #0891b2;
    }

    /* タブ */
    button[data-baseweb="tab"] {
        font-weight: 600;
        color: #4b6a75;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #0891b2;
    }
    div[data-baseweb="tab-highlight"] {
        background-color: #0891b2;
    }

    /* expander */
    details {
        background-color: #ffffff;
        border: 1px solid #bfe3ec;
        border-radius: 10px;
    }
    details summary {
        color: #1b2b34;
    }

    /* info / success ボックス（Streamlit標準の配色を活かしつつ角丸だけ） */
    div[data-testid="stAlert"] {
        border-radius: 10px;
    }

    /* dataframe */
    div[data-testid="stDataFrame"] {
        border-radius: 10px;
        overflow: hidden;
        border: 1px solid #bfe3ec;
    }

    /* input, selectbox, number_input などの枠 */
    div[data-baseweb="input"], div[data-baseweb="select"] {
        border-color: #bfe3ec !important;
    }
</style>

<div class="app-header">
    <h1>💪 食事・体重管理</h1>
    <p>Nutrition & Body Composition Tracker</p>
</div>
""", unsafe_allow_html=True)
# --- サイドバー：1日の目標PFC設定（追加） ---
# --- サイドバー：1日の目標PFC設定（自動読み込み・保存対応） ---
targets_saved = load_target_data()

with st.sidebar:
    st.markdown("""
    <div style="padding-bottom:0.5rem;">
        <span style="font-size:1.1rem; font-weight:700; color:#0891b2;">🎯 1日の目標設定</span>
    </div>
    """, unsafe_allow_html=True)
    st.caption("数値を変更して「目標値を保存」を押すと、リロード後もこの設定が維持されます。")
    target_cal = st.number_input("目標カロリー (kcal)", min_value=1000, max_value=5000, value=targets_saved["cal"], step=50, key="target_cal")
    target_p = st.number_input("目標 タンパク質 (g)", min_value=0.0, max_value=300.0, value=targets_saved["p"], step=5.0, key="target_p")
    target_f = st.number_input("目標 脂質 (g)", min_value=0.0, max_value=200.0, value=targets_saved["f"], step=5.0, key="target_f")
    target_c = st.number_input("目標 炭水化物 (g)", min_value=0.0, max_value=600.0, value=targets_saved["c"], step=10.0, key="target_c")
    
    if st.button("💾 この目標値を保存する", key="btn_save_target"):
        save_target_data(target_cal, target_p, target_f, target_c)
        st.success("目標値をスプレッドシートに保存しました！")
    
main_tab1, main_tab2 = st.tabs(["  🥗 食事・PFC管理  ", "  📈 体重・コンディション記録  "])
# ==========================================
# タブ1：食事・PFC管理
# ==========================================
with main_tab1:
    all_df = load_all_data()
    meal_type = st.selectbox("食事区分を選択してください", ["朝食", "昼食", "夕食", "間食"])
    input_options = ["テキスト入力", "画像アップロード", "カメラで撮影"]
# 過去メニュー辞書の作成（Geminiを介さず直接数値を取得するため）
    past_food_dict = {}
    if not all_df.empty and "食事内容" in all_df.columns:
        for _, row in all_df.iterrows():
            f_name = row.get("食事内容")
            if f_name:
                past_food_dict[f_name] = {
                    "food_name": f_name,
                    "calories": int(pd.to_numeric(row.get("カロリー", 0), errors="coerce") or 0),
                    "protein_g": float(pd.to_numeric(row.get("タンパク質", 0.0), errors="coerce") or 0.0),
                    "fat_g": float(pd.to_numeric(row.get("脂質", 0.0), errors="coerce") or 0.0),
                    "carbs_g": float(pd.to_numeric(row.get("炭水化物", 0.0), errors="coerce") or 0.0),
                    "micro_notes": str(row.get("備考", ""))
                }
    past_foods = list(past_food_dict.keys())
    if past_foods:
        input_options.append("過去のメニューから選択")

    input_type = st.radio("入力方法を選択してください", input_options, horizontal=True)
    input_content = None

    if input_type == "テキスト入力":
        text_val = st.text_input("食事内容を入力（例: 鮭の塩焼き1切れ、白米200g）")
        if text_val:
            input_content = text_val
    elif input_type == "画像アップロード":
        uploaded_files = st.file_uploader("食事写真をアップロード（複数選択可）", type=["jpg", "jpeg", "png"], accept_multiple_files=True)
        if uploaded_files:
            images = [Image.open(file) for file in uploaded_files]
            cols = st.columns(min(len(images), 4))
            for idx, img in enumerate(images):
                cols[idx % 4].image(img, caption=f"写真 {idx+1}")
            input_content = images
    elif input_type == "カメラで撮影":
        camera_file = st.camera_input("食事を撮影してください")
        if camera_file:
            image = Image.open(camera_file)
            st.image(image, caption="撮影した画像")
            input_content = image
    elif input_type == "過去のメニューから選択":
        selected_past_foods = st.multiselect("過去に記録したメニューを選択（複数選択可）", past_foods, placeholder="メニューを選択してください")
        if selected_past_foods:
            # --- Geminiを介さず、過去データから直接合算して即時反映 ---
            combined_food_name = "、".join(selected_past_foods)
            total_cal = sum(past_food_dict[f]["calories"] for f in selected_past_foods)
            total_p = round(sum(past_food_dict[f]["protein_g"] for f in selected_past_foods), 1)
            total_f = round(sum(past_food_dict[f]["fat_g"] for f in selected_past_foods), 1)
            total_c = round(sum(past_food_dict[f]["carbs_g"] for f in selected_past_foods), 1)
            
            # 備考（栄養メモ）の結合
            notes_list = [f"{f}: {past_food_dict[f]['micro_notes']}" for f in selected_past_foods if past_food_dict[f]["micro_notes"]]
            combined_notes = " / ".join(notes_list)
            
            # 各食材の内訳リスト（画面表示用）
            breakdown_items = [
                {
                    "name": past_food_dict[f]["food_name"],
                    "portion": "過去記録値",
                    "calories": past_food_dict[f]["calories"],
                    "protein_g": past_food_dict[f]["protein_g"],
                    "fat_g": past_food_dict[f]["fat_g"],
                    "carbs_g": past_food_dict[f]["carbs_g"]
                }
                for f in selected_past_foods
            ]
            
            st.session_state["result"] = {
                "food_name": combined_food_name,
                "calories": total_cal,
                "protein_g": total_p,
                "fat_g": total_f,
                "carbs_g": total_c,
                "micro_notes": combined_notes,
                "items": breakdown_items
            }

    # テキスト入力・画像・カメラの時のみGemini解析ボタンを表示
    if input_type != "過去のメニューから選択":
        is_ocr = st.checkbox("🏷️ パッケージ裏の「栄養成分表示」をそのまま読み取る", value=False, help="コンビニ商品やプロテイン等の成分表写真を正確に読み取ります。")

        if st.button("カロリー・PFCを計算する", key="btn_calc_nutrition") and input_content:
            with st.spinner("Geminiが解析中..."):
                try:
                    res = analyze_nutrition(input_content, MY_API_KEY, is_ocr=is_ocr)
                    st.session_state["result"] = res
                except Exception as e:
                    st.error(f"解析エラー: {e}")

    if "result" in st.session_state:
        res = st.session_state["result"]
        st.subheader("解析結果")
        st.write(f"**区分**: {meal_type}")
        st.write(f"**メニュー**: {res.get('food_name', '')}")
        
        # ミクロ栄養素・栄養メモの表示
        if res.get("micro_notes"):
            st.info(f"🌿 **栄養メモ**: {res['micro_notes']}")
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("カロリー", f"{res.get('calories', 0)} kcal")
        col2.metric("タンパク質(P)", f"{res.get('protein_g', 0.0)} g")
        col3.metric("脂質(F)", f"{res.get('fat_g', 0.0)} g")
        col4.metric("炭水化物(C)", f"{res.get('carbs_g', 0.0)} g")
        
        # 食材ごとの内訳（画面確認用アコーディオン）
        if res.get("items"):
            with st.expander("🔍 食材ごとの推定内訳を確認"):
                item_df = pd.DataFrame(res["items"])
                item_df.columns = ["食材・料理名", "推定分量", "カロリー(kcal)", "P(g)", "F(g)", "C(g)"]
                st.dataframe(item_df, use_container_width=True)

        if st.button("スプレッドシートに記録保存"):
            save_to_spreadsheet(res, meal_type)
            st.success("スプレッドシートへ書き込みました！")
            del st.session_state["result"]
            st.rerun()

    st.divider()
    st.subheader("📊 本日の摂取記録")
    if not all_df.empty:
        JST = timezone(timedelta(hours=9))
        today_str = datetime.now(JST).strftime("%Y-%m-%d")
        today_df = all_df[all_df["日付"] == today_str] if "日付" in all_df.columns else pd.DataFrame()
        if not today_df.empty:
            st.dataframe(today_df, use_container_width=True)
            
            c_sum = pd.to_numeric(today_df["カロリー"], errors="coerce").sum()
            p_sum = pd.to_numeric(today_df["タンパク質"], errors="coerce").sum()
            f_sum = pd.to_numeric(today_df["脂質"], errors="coerce").sum()
            carbs_sum = pd.to_numeric(today_df["炭水化物"], errors="coerce").sum()
            # 目標までの残り量を計算
            rem_cal = int(target_cal - c_sum)
            rem_p = round(target_p - p_sum, 1)
            rem_f = round(target_f - f_sum, 1)
            rem_c = round(target_c - carbs_sum, 1)

            st.write("### 本日の合計（目標との差分）")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("合計カロリー", f"{int(c_sum)} kcal", f"残り {rem_cal} kcal", delta_color="normal" if rem_cal >= 0 else "inverse")
            m2.metric("合計 P", f"{round(p_sum, 1)} g", f"残り {rem_p} g", delta_color="normal" if rem_p >= 0 else "inverse")
            m3.metric("合計 F", f"{round(f_sum, 1)} g", f"残り {rem_f} g", delta_color="normal" if rem_f >= 0 else "inverse")
            m4.metric("合計 C", f"{round(carbs_sum, 1)} g", f"残り {rem_c} g", delta_color="normal" if rem_c >= 0 else "inverse")
            
            # PFCエネルギー比率の計算と表示（追加）
            p_cal, f_cal, c_cal = p_sum * 4, f_sum * 9, carbs_sum * 4
            macro_total = p_cal + f_cal + c_cal
            if macro_total > 0:
                p_pct = round((p_cal / macro_total) * 100, 1)
                f_pct = round((f_cal / macro_total) * 100, 1)
                c_pct = round((c_cal / macro_total) * 100, 1)
                st.caption(f"⚖️ **PFCエネルギー比率**: **P** {p_pct}% / **F** {f_pct}% / **C** {c_pct}%")
            # --- 食事区分別の小計表示（追加） ---
            if "区分" in today_df.columns:
                meal_calc_df = today_df.copy()
                for col in ["カロリー", "タンパク質", "脂質", "炭水化物"]:
                    if col in meal_calc_df.columns:
                        meal_calc_df[col] = pd.to_numeric(meal_calc_df[col], errors="coerce").fillna(0)
                
                meal_order = ["朝食", "昼食", "夕食", "間食"]
                meal_summary = meal_calc_df.groupby("区分", as_index=False).agg({
                    "カロリー": "sum",
                    "タンパク質": "sum",
                    "脂質": "sum",
                    "炭水化物": "sum"
                })
                meal_summary["sort_key"] = meal_summary["区分"].map(lambda x: meal_order.index(x) if x in meal_order else 99)
                meal_summary = meal_summary.sort_values("sort_key").drop(columns=["sort_key"])
                
                meal_summary["カロリー"] = meal_summary["カロリー"].astype(int)
                meal_summary["タンパク質"] = meal_summary["タンパク質"].round(1)
                meal_summary["脂質"] = meal_summary["脂質"].round(1)
                meal_summary["炭水化物"] = meal_summary["炭水化物"].round(1)
                
                with st.expander("🍽️ 食事区分別の小計（朝・昼・夕・間食）を確認"):
                    st.dataframe(meal_summary, use_container_width=True)
                # --- 今日の食事AIアドバイス表示（追加） ---
                st.write(" ")
                if st.button("🤖 今日の食事をAIアドバイス・評価する", key="btn_ai_advice"):
                    with st.spinner("Geminiが今日の食事バランスを分析中..."):
                        try:
                            targets = {"cal": target_cal, "p": target_p, "f": target_f, "c": target_c}
                            advice_text = get_daily_advice(today_df, targets, c_sum, p_sum, f_sum, carbs_sum, MY_API_KEY)
                            st.session_state["daily_advice"] = advice_text
                        except Exception as e:
                            st.error(f"アドバイス生成エラー: {e}")
                
                if "daily_advice" in st.session_state and st.session_state["daily_advice"]:
                    st.info(f"💡 **AIアドバイザーからのアドバイス**:\n\n{st.session_state['daily_advice']}")
        else:
            st.info("本日の記録はまだありません。")

        st.divider()
        with st.expander("🛠️ 過去記録の修正・削除"):
            options = []
            for idx, row in all_df.iterrows():
                m_type = row.get('区分', '')
                m_type_str = f"[{m_type}] " if m_type else ""
                label = f"[{row.get('日付', '')} {row.get('時間', '')}] {m_type_str}{row.get('食事内容', '')} ({row.get('カロリー', 0)}kcal)"
                options.append((idx, label))
            
            selected_option = st.selectbox(
                "操作するレコードを選択してください",
                options=options,
                format_func=lambda x: x[1]
            )
            
            if selected_option:
                selected_idx = selected_option[0]
                selected_row = all_df.loc[selected_idx]
                tab1, tab2 = st.tabs(["📝 内容を修正", "🗑️ 記録を削除"])
                
                with tab1:
                    st.write("修正したい項目を変更して「修正内容を保存」を押してください。")
                    # 各入力欄の key に selected_idx を付与して選択内容を確実に反映
                    edit_date = st.text_input("日付", value=str(selected_row.get("日付", "")), key=f"edit_date_{selected_idx}")
                    edit_time = st.text_input("時間", value=str(selected_row.get("時間", "")), key=f"edit_time_{selected_idx}")
                    
                    meal_types = ["朝食", "昼食", "夕食", "間食"]
                    current_type = selected_row.get("区分", "朝食")
                    type_idx = meal_types.index(current_type) if current_type in meal_types else 0
                    edit_type = st.selectbox("区分", meal_types, index=type_idx, key=f"edit_type_{selected_idx}")
                    
                    edit_food = st.text_input("食事内容", value=str(selected_row.get("食事内容", "")), key=f"edit_food_{selected_idx}")
                    edit_cal = st.number_input("カロリー(kcal)", value=int(pd.to_numeric(selected_row.get("カロリー", 0), errors="coerce") or 0), key=f"edit_cal_{selected_idx}")
                    edit_p = st.number_input("タンパク質(P/g)", value=float(pd.to_numeric(selected_row.get("タンパク質", 0.0), errors="coerce") or 0.0), step=0.1, key=f"edit_p_{selected_idx}")
                    edit_f = st.number_input("脂質(F/g)", value=float(pd.to_numeric(selected_row.get("脂質", 0.0), errors="coerce") or 0.0), step=0.1, key=f"edit_f_{selected_idx}")
                    edit_c = st.number_input("炭水化物(C/g)", value=float(pd.to_numeric(selected_row.get("炭水化物", 0.0), errors="coerce") or 0.0), step=0.1, key=f"edit_c_{selected_idx}")
                    edit_notes = st.text_input("備考（栄養メモ）", value=str(selected_row.get("備考", "")), key=f"edit_notes_{selected_idx}")
                    
                    if st.button("修正内容を保存", key=f"btn_save_edit_{selected_idx}"):
                        updated_list = [edit_date, edit_time, edit_type, edit_food, edit_cal, edit_p, edit_f, edit_c, edit_notes]
                        update_spreadsheet_row(selected_idx, updated_list)
                        st.success("データを更新しました！")
                        st.rerun()
                        
                with tab2:
                    st.warning("⚠️ この操作は取り消せません。選択した記録を削除しますか？")
                    if st.button("この記録を削除する", type="primary", key=f"btn_del_meal_{selected_idx}"):
                        delete_spreadsheet_row(selected_idx)
                        st.success("記録を削除しました！")
                        st.rerun()
        st.divider()
        st.subheader("📅 過去の記録・日付別集計")
        if "日付" in all_df.columns:
            calc_df = all_df.copy()
            for col in ["カロリー", "タンパク質", "脂質", "炭水化物"]:
                if col in calc_df.columns:
                    calc_df[col] = pd.to_numeric(calc_df[col], errors="coerce").fillna(0)
            summary_df = calc_df.groupby("日付", as_index=False).agg({
                "カロリー": "sum",
                "タンパク質": "sum",
                "脂質": "sum",
                "炭水化物": "sum"
            })
            
            summary_df["カロリー"] = summary_df["カロリー"].astype(int)
            summary_df["タンパク質"] = summary_df["タンパク質"].round(1)
            summary_df["脂質"] = summary_df["脂質"].round(1)
            summary_df["炭水化物"] = summary_df["炭水化物"].round(1)
            
            st.write("### 📊 日付別 合計一覧")
            st.dataframe(summary_df, use_container_width=True)
            unique_dates = sorted(all_df["日付"].unique().tolist(), reverse=True)
            selected_date = st.selectbox("詳細を表示したい日付を選択してください", unique_dates)
            if selected_date:
                filtered_df = all_df[all_df["日付"] == selected_date]
                st.write(f"#### 📅 {selected_date} の詳細記録")
                st.dataframe(filtered_df, use_container_width=True)
                c_s = pd.to_numeric(filtered_df["カロリー"], errors="coerce").sum()
                p_s = pd.to_numeric(filtered_df["タンパク質"], errors="coerce").sum()
                f_s = pd.to_numeric(filtered_df["脂質"], errors="coerce").sum()
                carbs_s = pd.to_numeric(filtered_df["炭水化物"], errors="coerce").sum()
                st.write(f"**{selected_date} の合計数値**")
                sm1, sm2, sm3, sm4 = st.columns(4)
                sm1.metric("合計カロリー", f"{int(c_s)} kcal")
                sm1.metric("合計 P", f"{round(p_s, 1)} g")
                sm1.metric("合計 F", f"{round(f_s, 1)} g")
                sm1.metric("合計 C", f"{round(carbs_s, 1)} g")
            else:
                st.info("データがまだありません。")

# ==========================================
# タブ2：体重・コンディション記録
# ==========================================
with main_tab2:
    st.subheader("⚖️ 体重・コンディション記録")
    
    JST = timezone(timedelta(hours=9))
    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    
    col_w1, col_w2, col_w3 = st.columns(3)
    w_date = col_w1.text_input("日付", value=today_str, key="input_w_date")
    w_weight = col_w2.number_input("体重 (kg)", min_value=30.0, max_value=200.0, value=70.0, step=0.1, key="input_w_weight")
    w_fat = col_w3.number_input("体脂肪率 (%)", min_value=3.0, max_value=50.0, value=15.0, step=0.1, key="input_w_fat")
    
    col_w4, col_w5 = st.columns(2)
    w_stool = col_w4.selectbox("便の状態", ["選択なし", "快便", "普通", "軟便", "便秘", "出なかった"], key="input_w_stool")
    w_memo = col_w5.text_input("備考・メモ", value="", placeholder="例: 筋トレ脚の日、水分多め", key="input_w_memo")
    
    if st.button("コンディションデータを記録保存", key="btn_save_weight"):
        save_weight_data(w_date, w_weight, w_fat, w_stool, w_memo)
        st.success(f"{w_date} のデータ（体重: {w_weight}kg / 便: {w_stool}）を保存しました！")
        st.rerun()
        
    st.divider()
    st.subheader("📈 体重・体脂肪推移グラフ")
    weight_df = load_weight_data()
    if not weight_df.empty:
        weight_df["体重"] = pd.to_numeric(weight_df["体重"], errors="coerce")
        weight_df["体脂肪率"] = pd.to_numeric(weight_df["体脂肪率"], errors="coerce")
        
        weight_df["日付_dt"] = pd.to_datetime(weight_df["日付"], errors="coerce")
        graph_df = weight_df.dropna(subset=["日付_dt"]).sort_values("日付_dt")
        st.write("#### ⚖️ 体重推移 (kg)")
        st.line_chart(graph_df, x="日付_dt", y="体重")
        st.write("#### 💧 体脂肪率推移 (%)")
        st.line_chart(graph_df, x="日付_dt", y="体脂肪率")
        
        st.divider()
        st.subheader("🛠️ コンディション記録の修正・削除")
        st.dataframe(weight_df, use_container_width=True)
        w_options = []
        for idx, row in weight_df.iterrows():
            w_label = f"[{row.get('日付', '')}] 体重: {row.get('体重', '')}kg / 便: {row.get('便の状態', '-')}"
            if row.get('備考'):
                w_label += f" / メモ: {row.get('備考')}"
            w_options.append((idx, w_label))
            
        w_selected_option = st.selectbox(
            "操作するコンディション記録を選択してください",
            options=w_options,
            format_func=lambda x: x[1]
        )
        
        if w_selected_option:
            w_selected_idx = w_selected_option[0]
            w_selected_row = weight_df.loc[w_selected_idx]
            
            w_tab1, w_tab2 = st.tabs(["📝 内容を修正", "🗑️ 記録を削除"])
            
            with w_tab1:
                st.write("修正したい項目を変更して「修正内容を保存」を押してください。")
                w_edit_date = st.text_input("日付", value=str(w_selected_row.get("日付", "")), key=f"w_edit_date_{w_selected_idx}")
                w_edit_weight = st.number_input("体重 (kg)", value=float(pd.to_numeric(w_selected_row.get("体重", 70.0), errors="coerce") or 70.0), step=0.1, key=f"w_edit_weight_{w_selected_idx}")
                w_edit_fat = st.number_input("体脂肪率 (%)", value=float(pd.to_numeric(w_selected_row.get("体脂肪率", 15.0), errors="coerce") or 15.0), step=0.1, key=f"w_edit_fat_{w_selected_idx}")
                
                stool_list = ["選択なし", "快便", "普通", "軟便", "便秘", "出なかった"]
                current_stool = str(w_selected_row.get("便の状態", "選択なし"))
                stool_idx = stool_list.index(current_stool) if current_stool in stool_list else 0
                w_edit_stool = st.selectbox("便の状態", stool_list, index=stool_idx, key=f"w_edit_stool_{w_selected_idx}")
                
                w_edit_memo = st.text_input("備考・メモ", value=str(w_selected_row.get("備考", "")), key=f"w_edit_memo_{w_selected_idx}")
                
                if st.button("コンディション修正内容を保存", key=f"w_edit_btn_{w_selected_idx}"):
                    updated_w_list = [w_edit_date, w_edit_weight, w_edit_fat, w_edit_stool, w_edit_memo]
                    update_weight_row(w_selected_idx, updated_w_list)
                    st.success("コンディションデータを更新しました！")
                    st.rerun()
                    
            with w_tab2:
                st.warning("⚠️ この操作は取り消せません。選択した記録を削除しますか？")
                if st.button("この記録を削除する", type="primary", key=f"w_del_btn_{w_selected_idx}"):
                    delete_weight_row(w_selected_idx)
                    st.success("記録を削除しました！")
                    st.rerun()
    else:
        st.info("コンディション記録がまだありません。上のフォームから記録を追加してください。")