import json
from datetime import datetime
import gspread
import pandas as pd
from PIL import Image
import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# --- 1. Secrets（安全な設定機能）からの読み込み設定 ---
# ローカル実行時とクラウド実行時の両方に対応
if "GEMINI_API_KEY" in st.secrets:
    MY_API_KEY = st.secrets["GEMINI_API_KEY"]
else:
    # 修正後
    MY_API_KEY = ""
    SPREADSHEET_ID = "1RPpypQ_UiiwNkTX923Q_c1suxMlS2DhvxkOvV0I98O8"

if "SPREADSHEET_ID" in st.secrets:
    SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]
else:
    SPREADSHEET_ID = "1RPpypQ_UiiwNkTX923Q_c1suxMlS2DhvxkOvV0I98O8"

# --- 2. データ構造の定義 ---
class NutritionData(BaseModel):
    food_name: str = Field(description="食事の名前やメニュー内容")
    calories: int = Field(description="推定総カロリー(kcal)")
    protein_g: float = Field(description="タンパク質(g)")
    fat_g: float = Field(description="脂質(g)")
    carbs_g: float = Field(description="炭水化物(g)")

# --- 3. Gemini API解析関数 ---
def analyze_nutrition(input_data, api_key: str) -> dict:
    client = genai.Client(api_key=api_key)
    prompt = "提供された食事内容（テキストまたは画像）から、推定される総カロリー(kcal)とマクロ栄養素（タンパク質・脂質・炭水化物(g)）を計算してください。"
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

# --- 4. スプレッドシート操作関数 ---
def get_worksheet():
    # クラウド環境ではSecretsからGoogle認証情報を読み込み、ローカルでは credentials.json を使用
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(creds_dict)
    else:
        gc = gspread.service_account(filename="credentials.json")
        
    sh = gc.open_by_key(SPREADSHEET_ID)
    return sh.get_worksheet(0)

def save_to_spreadsheet(data: dict):
    ws = get_worksheet()
    now = datetime.now()
    row = [
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M"),
        data["food_name"],
        data["calories"],
        data["protein_g"],
        data["fat_g"],
        data["carbs_g"]
    ]
    ws.append_row(row)

def load_today_data():
    ws = get_worksheet()
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    today_str = datetime.now().strftime("%Y-%m-%d")
    if "日付" in df.columns:
        df = df[df["日付"] == today_str]
    return df

# --- 5. Streamlit UI 画面構築 ---
st.set_page_config(page_title="PFC食事管理ツール", layout="centered")
st.title("🥗 食事・PFC管理ツール")

input_type = st.radio("入力方法を選択してください", ["テキスト入力", "画像アップロード"], horizontal=True)

input_content = None

if input_type == "テキスト入力":
    text_val = st.text_input("食事内容を入力（例: 鮭の塩焼き1切れ、白米200g）")
    if text_val:
        input_content = text_val
else:
    uploaded_file = st.file_uploader("食事写真をアップロード", type=["jpg", "jpeg", "png"])
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="アップロード画像", use_column_width=True)
        input_content = image

if st.button("カロリー・PFCを計算する") and input_content:
    with st.spinner("Geminiが解析中..."):
        try:
            res = analyze_nutrition(input_content, MY_API_KEY)
            st.session_state["result"] = res
        except Exception as e:
            st.error(f"解析エラー: {e}")

if "result" in st.session_state:
    res = st.session_state["result"]
    st.subheader("解析結果")
    st.write(f"**メニュー**: {res['food_name']}")
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("カロリー", f"{res['calories']} kcal")
    col2.metric("タンパク質(P)", f"{res['protein_g']} g")
    col3.metric("脂質(F)", f"{res['fat_g']} g")
    col4.metric("炭水化物(C)", f"{res['carbs_g']} g")

    if st.button("スプレッドシートに記録保存"):
        save_to_spreadsheet(res)
        st.success("スプレッドシートへ書き込みました！")
        del st.session_state["result"]
        st.rerun()

st.divider()

st.subheader("📊 本日の摂取記録")
today_df = load_today_data()

if not today_df.empty:
    st.dataframe(today_df, use_container_width=True)
    
    c_sum = pd.to_numeric(today_df["カロリー"], errors="coerce").sum()
    p_sum = pd.to_numeric(today_df["タンパク質"], errors="coerce").sum()
    f_sum = pd.to_numeric(today_df["脂質"], errors="coerce").sum()
    carbs_sum = pd.to_numeric(today_df["炭水化物"], errors="coerce").sum()

    st.write("### 本日の合計")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("合計カロリー", f"{int(c_sum)} kcal")
    m2.metric("合計 P", f"{round(p_sum, 1)} g")
    m3.metric("合計 F", f"{round(f_sum, 1)} g")
    m4.metric("合計 C", f"{round(carbs_sum, 1)} g")
else:
    st.info("本日の記録はまだありません。")