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
class NutritionData(BaseModel):
    food_name: str = Field(description="食事の名前やメニュー内容")
    calories: int = Field(description="推定総カロリー(kcal)")
    protein_g: float = Field(description="タンパク質(g)")
    fat_g: float = Field(description="脂質(g)")
    carbs_g: float = Field(description="炭水化物(g)")

# --- 2. Gemini API解析関数 ---
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

# --- 3. スプレッドシート操作関数 ---
def get_spreadsheet():
    # 1. Secrets に GCP_JSON_TEXT が存在する場合
    if hasattr(st, "secrets") and "GCP_JSON_TEXT" in st.secrets and st.secrets["GCP_JSON_TEXT"]:
        try:
            json_text = st.secrets["GCP_JSON_TEXT"]
            # json.loads を介さず直接一時ファイルに書き出して安全に読み込み
            with open("credentials_cloud.json", "w", encoding="utf-8") as f:
                f.write(json_text)
            return gspread.service_account(filename="credentials_cloud.json").open_by_key(SPREADSHEET_ID)
        except Exception as e:
            st.error(f"【Secrets 認証エラー (GCP_JSON_TEXT)】: {e}")
            st.stop()

    # 2. Secrets に gcp_service_account が存在する場合
    if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
        try:
            creds_dict = dict(st.secrets["gcp_service_account"])
            if "private_key" in creds_dict:
                creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
            return gspread.service_account_from_dict(creds_dict).open_by_key(SPREADSHEET_ID)
        except Exception as e:
            st.error(f"【Secrets 認証エラー (gcp_service_account)】: {e}")
            st.stop()

    # 3. Secrets が空で、ローカルの credentials.json も存在しない場合
    if not os.path.exists("credentials.json"):
        st.error("🚨【原因確定】Streamlit Cloud の Secrets（設定）が空です！右下の 'Manage app' > 'Secrets' を開き、鍵の設定を入力して保存（Save）してください。")
        st.stop()

    # 4. ローカルPC環境
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
        ws.append_row(["日付", "体重", "体脂肪率"])
        return ws

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
        data["carbs_g"]
    ]
    ws.append_row(row)

def save_weight_data(date_str: str, weight: float, fat: float):
    ws = get_weight_worksheet()
    ws.append_row([date_str, weight, fat])

def load_all_data():
    ws = get_worksheet()
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)

def load_weight_data():
    ws = get_weight_worksheet()
    records = ws.get_all_records()
    return pd.DataFrame(records) if records else pd.DataFrame()

def delete_spreadsheet_row(row_index: int):
    ws = get_worksheet()
    ws.delete_rows(row_index + 2)

def delete_weight_row(row_index: int):
    ws = get_weight_worksheet()
    ws.delete_rows(row_index + 2)

def update_spreadsheet_row(row_index: int, updated_data: list):
    ws = get_worksheet()
    target_row = row_index + 2
    cell_range = f"A{target_row}:H{target_row}"
    ws.update(cell_range, [updated_data])

# --- 4. Streamlit UI 画面構築 ---
st.set_page_config(page_title="ボディメイク&PFC管理ツール", layout="centered")
st.title("💪 ボディメイク&PFC管理ツール")

# メインタブの作成
main_tab1, main_tab2 = st.tabs(["🥗 食事・PFC管理", "📈 体重・体脂肪トラッキング"])

# ==========================================
# タブ1：食事・PFC管理
# ==========================================
with main_tab1:
    all_df = load_all_data()

    meal_type = st.selectbox("食事区分を選択してください", ["朝食", "昼食", "夕食", "間食"])

    input_options = ["テキスト入力", "画像アップロード", "カメラで撮影"]

    past_foods = []
    if not all_df.empty and "食事内容" in all_df.columns:
        past_foods = [f for f in all_df["食事内容"].unique().tolist() if f]
        if past_foods:
            input_options.append("過去のメニューから選択")

    input_type = st.radio("入力方法を選択してください", input_options, horizontal=True)

    input_content = None

    if input_type == "テキスト入力":
        text_val = st.text_input("食事内容を入力（例: 鮭の塩焼き1切れ、白米200g）")
        if text_val:
            input_content = text_val
    elif input_type == "画像アップロード":
        uploaded_file = st.file_uploader("食事写真をアップロード", type=["jpg", "jpeg", "png"])
        if uploaded_file:
            image = Image.open(uploaded_file)
            st.image(image, caption="アップロード画像")
            input_content = image
    elif input_type == "カメラで撮影":
        camera_file = st.camera_input("食事を撮影してください")
        if camera_file:
            image = Image.open(camera_file)
            st.image(image, caption="撮影した画像")
            input_content = image
    elif input_type == "過去のメニューから選択":
        selected_past_food = st.selectbox("過去に記録したメニューを選択", past_foods)
        if selected_past_food:
            input_content = selected_past_food

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
        st.write(f"**区分**: {meal_type}")
        st.write(f"**メニュー**: {res['food_name']}")
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("カロリー", f"{res['calories']} kcal")
        col2.metric("タンパク質(P)", f"{res['protein_g']} g")
        col3.metric("脂質(F)", f"{res['fat_g']} g")
        col4.metric("炭水化物(C)", f"{res['carbs_g']} g")

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

            st.write("### 本日の合計")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("合計カロリー", f"{int(c_sum)} kcal")
            m2.metric("合計 P", f"{round(p_sum, 1)} g")
            m3.metric("合計 F", f"{round(f_sum, 1)} g")
            m4.metric("合計 C", f"{round(carbs_sum, 1)} g")
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
                edit_date = st.text_input("日付", value=str(selected_row.get("日付", "")))
                edit_time = st.text_input("時間", value=str(selected_row.get("時間", "")))
                edit_type = st.selectbox("区分", ["朝食", "昼食", "夕食", "間食"], index=["朝食", "昼食", "夕食", "間食"].index(selected_row.get("区分", "朝食")) if selected_row.get("区分") in ["朝食", "昼食", "夕食", "間食"] else 0)
                edit_food = st.text_input("食事内容", value=str(selected_row.get("食事内容", "")))
                edit_cal = st.number_input("カロリー(kcal)", value=int(selected_row.get("カロリー", 0)))
                edit_p = st.number_input("タンパク質(P/g)", value=float(selected_row.get("タンパク質", 0.0)))
                edit_f = st.number_input("脂質(F/g)", value=float(selected_row.get("脂質", 0.0)))
                edit_c = st.number_input("炭水化物(C/g)", value=float(selected_row.get("炭水化物", 0.0)))

                if st.button("修正内容を保存"):
                    updated_list = [edit_date, edit_time, edit_type, edit_food, edit_cal, edit_p, edit_f, edit_c]
                    update_spreadsheet_row(selected_idx, updated_list)
                    st.success("データを更新しました！")
                    st.rerun()

            with tab2:
                st.warning("⚠️ この操作は取り消せません。選択した記録を削除しますか？")
                if st.button("この記録を削除する", type="primary"):
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
        summary_df["脂質"] = summary_df["脂質"].round(1) if "脂質" in summary_df.columns else summary_df["脂質"]
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
            sm2.metric("合計 P", f"{round(p_s, 1)} g")
            sm3.metric("合計 F", f"{round(f_s, 1)} g")
            sm4.metric("合計 C", f"{round(carbs_s, 1)} g")
        else:
            st.info("データがまだありません。")

# ==========================================
# タブ2：体重・体脂肪トラッキング（Step 2 で追加）
# ==========================================
with main_tab2:
    st.subheader("⚖️ 体重・体脂肪率の記録")
    
    JST = timezone(timedelta(hours=9))
    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    
    col_w1, col_w2, col_w3 = st.columns(3)
    w_date = col_w1.text_input("日付", value=today_str)
    w_weight = col_w2.number_input("体重 (kg)", min_value=30.0, max_value=200.0, value=70.0, step=0.1)
    w_fat = col_w3.number_input("体脂肪率 (%)", min_value=3.0, max_value=50.0, value=15.0, step=0.1)

    if st.button("体重データを記録保存"):
        save_weight_data(w_date, w_weight, w_fat)
        st.success(f"{w_date} のデータ（体重: {w_weight}kg / 体脂肪率: {w_fat}%）をスプレッドシートへ保存しました！")

st.divider()
st.subheader("📈 体重・体脂肪推移グラフ")

weight_df = load_weight_data()

if not weight_df.empty:
        # 数値型へ変換
        weight_df["体重"] = pd.to_numeric(weight_df["体重"], errors="coerce")
        weight_df["体脂肪率"] = pd.to_numeric(weight_df["体脂肪率"], errors="coerce")
        
        # 日付列を本当の日付型(datetime)に変換（横向き表示になります）
        weight_df["日付_dt"] = pd.to_datetime(weight_df["日付"], errors="coerce")
        graph_df = weight_df.dropna(subset=["日付_dt"]).sort_values("日付_dt")

        st.write("#### ⚖️ 体重推移 (kg)")
        st.line_chart(graph_df, x="日付_dt", y="体重")

        st.write("#### 💧 体脂肪率推移 (%)")
        st.line_chart(graph_df, x="日付_dt", y="体脂肪率")

        st.divider()
        st.subheader("📋 体重記録一覧・削除")
        st.dataframe(weight_df, use_container_width=True)

        w_options = []
        for idx, row in weight_df.iterrows():
            w_label = f"[{row.get('日付', '')}] 体重: {row.get('体重', '')}kg / 体脂肪率: {row.get('体脂肪率', '')}%"
            w_options.append((idx, w_label))

        w_selected = st.selectbox(
            "削除する体重記録を選択してください",
            options=w_options,
            format_func=lambda x: x[1]
        )

        if w_selected and st.button("選択した体重記録を削除"):
            delete_weight_row(w_selected[0])
            st.success("選択した記録を削除しました！")
            st.rerun()
        else:
            st.info("体重・体脂肪の記録がまだありません。上のフォームから記録を追加してください。")