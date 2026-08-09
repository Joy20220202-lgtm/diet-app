import json
from datetime import datetime
import gspread
import pandas as pd
from PIL import Image
import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# --- 1. Secrets/環境設定 ---
if "GEMINI_API_KEY" in st.secrets:
    MY_API_KEY = st.secrets["GEMINI_API_KEY"]
else:
    MY_API_KEY = ""

if "SPREADSHEET_ID" in st.secrets:
    SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]
else:
    SPREADSHEET_ID = "1RPpypQ_UiiwNkTX923Q_c1suxMlS2DhvxkOvV0I98O8"

# --- 2. データ構造定義 ---
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

def load_all_data():
    ws = get_worksheet()
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)

# 削除処理
def delete_spreadsheet_row(row_index: int):
    ws = get_worksheet()
    # 1行目はヘッダーのため、データ行は row_index + 2
    ws.delete_rows(row_index + 2)

# 修正更新処理
def update_spreadsheet_row(row_index: int, updated_data: list):
    ws = get_worksheet()
    target_row = row_index + 2
    cell_range = f"A{target_row}:G{target_row}"
    ws.update(cell_range, [updated_data])

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

# --- 本日の摂取記録 & 合計表示 ---
st.subheader("📊 本日の摂取記録")
all_df = load_all_data()

if not all_df.empty:
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_df = all_df[all_df["日付"] == today_str] if "日付" in all_df.columns else pd.DataFrame()

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

    st.divider()

    # --- 🛠️ データの修正・削除機能 ---
    with st.expander("🛠️ 過去記録の修正・削除"):
        # 選択用のリストを作成
        options = []
        for idx, row in all_df.iterrows():
            label = f"[{row.get('日付', '')} {row.get('時間', '')}] {row.get('食事内容', '')} ({row.get('カロリー', 0)}kcal)"
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

            # 修正タブ
            with tab1:
                st.write("修正したい項目を変更して「修正内容を保存」を押してください。")
                edit_date = st.text_input("日付", value=str(selected_row.get("日付", "")))
                edit_time = st.text_input("時間", value=str(selected_row.get("時間", "")))
                edit_food = st.text_input("食事内容", value=str(selected_row.get("食事内容", "")))
                edit_cal = st.number_input("カロリー(kcal)", value=int(selected_row.get("カロリー", 0)))
                edit_p = st.number_input("タンパク質(P/g)", value=float(selected_row.get("タンパク質", 0.0)))
                edit_f = st.number_input("脂質(F/g)", value=float(selected_row.get("脂質", 0.0)))
                edit_c = st.number_input("炭水化物(C/g)", value=float(selected_row.get("炭水化物", 0.0)))

                if st.button("修正内容を保存"):
                    updated_list = [edit_date, edit_time, edit_food, edit_cal, edit_p, edit_f, edit_c]
                    update_spreadsheet_row(selected_idx, updated_list)
                    st.success("データを更新しました！")
                    st.rerun()

            # 削除タブ
            with tab2:
                st.warning("⚠️ この操作は取り消せません。選択した記録を削除しますか？")
                if st.button("この記録を削除する", type="primary"):
                    delete_spreadsheet_row(selected_idx)
                    st.success("記録を削除しました！")
                    st.rerun()
else:
    st.info("データがまだありません。")

#日付ごとの合計表示機能↓
# --- ここから一番最後に追加 ---
st.divider()

# --- 📅 過去の記録・日付別集計 ---
st.subheader("📅 過去の記録・日付別集計")

if not all_df.empty and "日付" in all_df.columns:
    # 1. 日付ごとの合計一覧テーブルを作成
    # 数値列を数値型に変換して合計
    calc_df = all_df.copy()
    for col in ["カロリー", "タンパク質", "脂質", "炭水化物"]:
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

    # 2. 過去の日付を選択して詳細と合計を表示する機能
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
        sm2.metric("合計 P", f"{round(p_s, 1)} g")
        sm3.metric("合計 F", f"{round(f_s, 1)} g")
        sm4.metric("合計 C", f"{round(carbs_s, 1)} g")
# --- ここまで追加 ---