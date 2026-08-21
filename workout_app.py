import os
import json
from datetime import datetime, timezone, timedelta
import gspread
import pandas as pd
import streamlit as st

# ==========================================
# 1. 設定 & スプレッドシート認証
# ==========================================
SPREADSHEET_ID = "1RPpypQ_UiiwNkTX923Q_c1suxMlS2DhvxkOvV0I98O8"

def get_spreadsheet():
    """認証情報を利用してスプレッドシートを取得（ローカル・Cloud両対応）"""
    if os.path.exists("credentials.json"):
        gc = gspread.service_account(filename="credentials.json")
        return gc.open_by_key(SPREADSHEET_ID)
    
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            gc = gspread.service_account_from_dict(creds_dict)
        elif "GCP_JSON_TEXT" in st.secrets:
            temp_path = "/tmp/gcp_creds.json"
            if not os.path.exists(temp_path):
                with open(temp_path, "w", encoding="utf-8") as f:
                    f.write(st.secrets["GCP_JSON_TEXT"])
            gc = gspread.service_account(filename=temp_path)
        else:
            gc = gspread.service_account(filename="credentials.json")
        return gc.open_by_key(SPREADSHEET_ID)
    except Exception as e:
        raise e

# ==========================================
# 2. スプレッドシート操作関数（キャッシュ対応）
# ==========================================
@st.cache_data(ttl=600)
def load_exercise_master():
    """種目マスタを取得"""
    sh = get_spreadsheet()
    ws = sh.worksheet("種目マスタ")
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame(columns=["部位", "種目名", "お気に入り"])
    return pd.DataFrame(records)

@st.cache_data(ttl=600)
def load_all_workout_records():
    """筋トレ記録シートから全ログを取得"""
    sh = get_spreadsheet()
    ws = sh.worksheet("筋トレ記録")
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame(columns=["日付", "部位", "種目名", "セット", "重量", "回数", "ボリューム(kg)", "メモ"])
    return pd.DataFrame(records)

def get_last_workout_record(exercise_name: str):
    """指定した種目の直近の過去記録を取得"""
    df = load_all_workout_records()
    if df.empty or "種目名" not in df.columns:
        return None
    
    ex_df = df[df["種目名"] == exercise_name]
    if ex_df.empty or "日付" not in ex_df.columns:
        return None
    
    unique_dates = sorted(ex_df["日付"].unique().tolist(), reverse=True)
    if not unique_dates:
        return None
    latest_date = unique_dates[0]
    
    latest_df = ex_df[ex_df["日付"] == latest_date]
    sets_data = []
    for _, row in latest_df.iterrows():
        raw_w = str(row.get("重量", 0))
        is_bw = (raw_w == "自重")
        weight_val = 0.0 if is_bw else float(pd.to_numeric(raw_w, errors="coerce") or 0.0)
        reps_val = int(pd.to_numeric(row.get("回数", 0), errors="coerce") or 0)
        memo_val = str(row.get("メモ", ""))
        
        sets_data.append({
            "weight": weight_val,
            "is_bodyweight": is_bw,
            "reps": reps_val,
            "memo": memo_val
        })
        
    return {
        "date": latest_date,
        "sets": sets_data
    }

def save_workout_to_spreadsheet(workout_items: list, date_str: str):
    """ワークアウトの全種目・全セットを一括保存"""
    sh = get_spreadsheet()
    ws = sh.worksheet("筋トレ記録")
    
    rows_to_append = []
    for item in workout_items:
        body_part = item["body_part"]
        ex_name = item["exercise"]
        
        for idx, s in enumerate(item["sets"]):
            set_num = idx + 1
            if s["is_bodyweight"]:
                weight_str = "自重"
                vol = 0.0
            else:
                weight_str = str(s["weight"])
                vol = round(float(s["weight"]) * int(s["reps"]), 1)
            
            reps_num = int(s["reps"])
            memo_str = str(s.get("memo", ""))
            
            rows_to_append.append([
                date_str,
                body_part,
                ex_name,
                set_num,
                weight_str,
                reps_num,
                vol,
                memo_str
            ])
            
    if rows_to_append:
        ws.append_rows(rows_to_append)
        st.cache_data.clear()

def add_new_exercise_to_master(body_part: str, exercise_name: str):
    """新しい種目をマスタに追加"""
    sh = get_spreadsheet()
    ws = sh.worksheet("種目マスタ")
    ws.append_row([body_part, exercise_name, "TRUE"])
    st.cache_data.clear()

def delete_workout_row(row_index: int):
    """筋トレ記録から特定の行を削除"""
    sh = get_spreadsheet()
    ws = sh.worksheet("筋トレ記録")
    ws.delete_rows(row_index + 2)
    st.cache_data.clear()

# ==========================================
# 3. セッション状態の初期化
# ==========================================
if "workout_items" not in st.session_state:
    st.session_state.workout_items = []

if "exercise_counter" not in st.session_state:
    st.session_state.exercise_counter = 0

# ==========================================
# 4. 画面構築 & タブ設定
# ==========================================
st.set_page_config(page_title="ワークアウト記録", layout="centered", page_icon="🏋️")

st.title("🏋️ ワークアウト記録")

tab_record, tab_history, tab_master = st.tabs([
    "  🏋️ 今日のワークアウト  ",
    "  📊 過去の履歴・分析  ",
    "  ⚙️ 種目マスタ管理  "
])

# ==========================================
# タブ1：今日のワークアウト
# ==========================================
with tab_record:
    JST = timezone(timedelta(hours=9))
    today_jst = datetime.now(JST).date()
    workout_date = st.date_input("📅 トレーニング日", value=today_jst)
    workout_date_str = workout_date.strftime("%Y-%m-%d")

    st.divider()

    df_master = load_exercise_master()
    all_body_parts = ["胸", "背中", "肩", "腕", "脚", "腹筋"]

    with st.expander("➕ 種目をワークアウトに追加する", expanded=(len(st.session_state.workout_items) == 0)):
        col_part, col_ex = st.columns(2)
        selected_part = col_part.selectbox("部位を選択", all_body_parts, key="sel_part_main")
        
        filtered_df = df_master[df_master["部位"] == selected_part]
        exercise_options = filtered_df["種目名"].tolist() if not filtered_df.empty else []
        
        selected_exercise = col_ex.selectbox("種目を選択", exercise_options if exercise_options else ["登録なし"], key="sel_ex_main")
        
        if st.button("この種目を追加する", type="primary", use_container_width=True):
            if selected_exercise and selected_exercise != "登録なし":
                st.session_state.exercise_counter += 1
                
                last_record = get_last_workout_record(selected_exercise)
                if last_record and last_record["sets"]:
                    initial_sets = [dict(s) for s in last_record["sets"]]
                else:
                    initial_sets = [{"weight": 60.0, "is_bodyweight": False, "reps": 10, "memo": ""}]
                    
                new_item = {
                    "id": f"ex_{st.session_state.exercise_counter}",
                    "body_part": selected_part,
                    "exercise": selected_exercise,
                    "sets": initial_sets
                }
                st.session_state.workout_items.append(new_item)
                st.rerun()

    if not st.session_state.workout_items:
        st.info("👆 上の「➕ 種目をワークアウトに追加する」から種目を追加してください。")
    else:
        for ex_idx, item in enumerate(st.session_state.workout_items):
            ex_id = item["id"]
            ex_name = item["exercise"]
            ex_part = item["body_part"]
            
            st.markdown(f"### 📍 [{ex_part}] {ex_name}")
            
            last_rec = get_last_workout_record(ex_name)
            if last_rec and last_rec["sets"]:
                lines = []
                for i, s in enumerate(last_rec["sets"]):
                    set_num = i + 1
                    reps_cnt = s["reps"]
                    w_str = "自重" if s["is_bodyweight"] else str(s["weight"]) + "kg"
                    lines.append(f"・第{set_num}セット: {w_str} × {reps_cnt}回")
                rec_text = "⏱️ **Last Record (前回記録)**: " + str(last_rec["date"]) + "\n\n" + "  \n".join(lines)
                st.info(rec_text)
                
                col_copy, col_del_ex = st.columns(2)
                if col_copy.button("📋 前回のセット内容をコピー", key=f"btn_copy_{ex_id}"):
                    item["sets"] = [dict(s) for s in last_rec["sets"]]
                    st.success("前回のセット内容を反映しました！")
                    st.rerun()
            else:
                st.caption("ℹ️ この種目の過去記録はまだありません。")
                col_dummy, col_del_ex = st.columns(2)
                
            if col_del_ex.button("🗑️ 種目削除", key=f"btn_del_ex_{ex_id}"):
                st.session_state.workout_items.pop(ex_idx)
                st.rerun()
            
            for set_idx, set_data in enumerate(item["sets"]):
                st.write(f"**第 {set_idx + 1} セット**")
                
                c_bw, c_w, c_r, c_del_set = st.columns(4)
                
                set_data["is_bodyweight"] = c_bw.checkbox("自重", value=set_data["is_bodyweight"], key=f"bw_{ex_id}_{set_idx}")
                
                if set_data["is_bodyweight"]:
                    c_w.text_input("重量", value="自重", disabled=True, key=f"w_dis_{ex_id}_{set_idx}")
                else:
                    set_data["weight"] = c_w.number_input(
                        "重量 (kg)",
                        min_value=0.0,
                        max_value=500.0,
                        value=float(set_data["weight"]),
                        step=0.5,
                        key=f"w_{ex_id}_{set_idx}"
                    )
                    
                set_data["reps"] = c_r.number_input(
                    "回数 (reps)",
                    min_value=1,
                    max_value=100,
                    value=int(set_data["reps"]),
                    step=1,
                    key=f"r_{ex_id}_{set_idx}"
                )
                
                if len(item["sets"]) > 1:
                    if c_del_set.button("✕", key=f"del_set_{ex_id}_{set_idx}", help="このセットを削除"):
                        item["sets"].pop(set_idx)
                        st.rerun()
                
                if not set_data["is_bodyweight"]:
                    b1, b2, b3, b4, b5, b6 = st.columns(6)
                    if b1.button("-5kg", key=f"m5_{ex_id}_{set_idx}"):
                        set_data["weight"] = max(0.0, set_data["weight"] - 5.0)
                        st.rerun()
                    if b2.button("-2.5kg", key=f"m25_{ex_id}_{set_idx}"):
                        set_data["weight"] = max(0.0, set_data["weight"] - 2.5)
                        st.rerun()
                    if b3.button("+2.5kg", key=f"p25_{ex_id}_{set_idx}"):
                        set_data["weight"] = set_data["weight"] + 2.5
                        st.rerun()
                    if b4.button("+5kg", key=f"p5_{ex_id}_{set_idx}"):
                        set_data["weight"] = set_data["weight"] + 5.0
                        st.rerun()
                    if b5.button("-1回", key=f"mr1_{ex_id}_{set_idx}"):
                        set_data["reps"] = max(1, set_data["reps"] - 1)
                        st.rerun()
                    if b6.button("+1回", key=f"pr1_{ex_id}_{set_idx}"):
                        set_data["reps"] = set_data["reps"] + 1
                        st.rerun()
                
                set_data["memo"] = st.text_input("メモ (任意)", value=set_data.get("memo", ""), placeholder="例: フォーム意識、余力あり", key=f"memo_{ex_id}_{set_idx}")
                st.caption("---")
                
            if st.button(f"➕ 第 {len(item['sets']) + 1} セットを追加", key=f"add_set_{ex_id}"):
                last_set = item["sets"][-1]
                item["sets"].append({
                    "weight": last_set["weight"],
                    "is_bodyweight": last_set["is_bodyweight"],
                    "reps": last_set["reps"],
                    "memo": ""
                })
                st.rerun()
                
            st.divider()

        if st.button("💾 このワークアウトをスプレッドシートに記録保存する", type="primary", use_container_width=True):
            with st.spinner("スプレッドシートに書き込み中..."):
                try:
                    save_workout_to_spreadsheet(st.session_state.workout_items, workout_date_str)
                    st.success("🎉 本日のワークアウトをスプレッドシートへ記録保存しました！")
                    st.session_state.workout_items = []
                    st.session_state.exercise_counter = 0
                    st.rerun()
                except Exception as e:
                    st.error("保存エラー: " + str(e))

# ==========================================
# タブ2：過去の履歴・分析
# ==========================================
with tab_history:
    st.subheader("📊 過去のワークアウト履歴・集計")
    all_logs = load_all_workout_records()
    
    if all_logs.empty:
        st.info("過去のトレーニング記録はまだありません。")
    else:
        calc_df = all_logs.copy()
        calc_df["ボリューム(kg)"] = pd.to_numeric(calc_df["ボリューム(kg)"], errors="coerce").fillna(0)
        
        daily_summary = calc_df.groupby("日付", as_index=False).agg({
            "部位": lambda x: "・".join(sorted(list(set(x)))),
            "種目名": "nunique",
            "セット": "count",
            "ボリューム(kg)": "sum"
        })
        daily_summary.columns = ["日付", "鍛えた部位", "種目数", "総セット数", "総ボリューム (kg)"]
        daily_summary["総ボリューム (kg)"] = daily_summary["総ボリューム (kg)"].astype(int)
        
        st.write("#### 📅 日別サマリー一覧")
        st.dataframe(daily_summary.sort_values("日付", ascending=False), use_container_width=True)
        
        st.divider()
        
        st.write("#### 📈 種目別の成長推移 (MAX重量 & ボリューム)")
        all_unique_exercises = sorted(all_logs["種目名"].unique().tolist())
        selected_graph_ex = st.selectbox("分析したい種目を選択", all_unique_exercises)
        
        if selected_graph_ex:
            target_ex_df = all_logs[all_logs["種目名"] == selected_graph_ex].copy()
            target_ex_df["重量_num"] = pd.to_numeric(target_ex_df["重量"], errors="coerce").fillna(0)
            target_ex_df["ボリューム(kg)"] = pd.to_numeric(target_ex_df["ボリューム(kg)"], errors="coerce").fillna(0)
            target_ex_df["日付_dt"] = pd.to_datetime(target_ex_df["日付"], errors="coerce")
            
            ex_growth_df = target_ex_df.groupby("日付_dt", as_index=False).agg({
                "重量_num": "max",
                "ボリューム(kg)": "sum"
            }).sort_values("日付_dt")
            
            ex_growth_df.columns = ["日付", "MAX重量 (kg)", "総ボリューム (kg)"]
            
            st.line_chart(ex_growth_df, x="日付", y="MAX重量 (kg)")
            st.caption(f"▲ {selected_graph_ex} のMAX重量推移 (kg)")

        st.divider()
        # --- 🛠️ 過去記録の削除セクション（format_funcを使わない安全な書き方） ---
        st.subheader("🛠️ 過去記録の削除")
        st.caption("誤って記録したセットを選択して削除できます。")
        
        del_options_map = {}
        for idx, row in all_logs.iterrows():
            w_label = str(row.get("重量", "")) if str(row.get("重量", "")) == "自重" else str(row.get("重量", "")) + "kg"
            memo_str = " / メモ: " + str(row.get("メモ", "")) if row.get("メモ") else ""
            lbl = f"No.{idx+1} [{row.get('日付', '')}] [{row.get('部位', '')}] {row.get('種目名', '')} - 第{row.get('セット', '')}セット ({w_label} × {row.get('回数', '')}回){memo_str}"
            del_options_map[lbl] = idx
            
        if del_options_map:
            selected_label = st.selectbox(
                "削除する記録を選択してください",
                options=list(del_options_map.keys()),
                key="sel_del_workout"
            )
            if selected_label:
                target_del_idx = del_options_map[selected_label]
                st.warning("⚠️ この操作は取り消せません。選択した記録を削除しますか？")
                if st.button("🗑️ この記録を削除する", type="primary", key="btn_exec_del_workout"):
                    delete_workout_row(target_del_idx)
                    st.success("記録を削除しました！")
                    st.rerun()

# ==========================================
# タブ3：種目マスタ管理
# ==========================================
with tab_master:
    st.subheader("⚙️ 種目マスタの追加・管理")
    st.write("新しい種目をライブラリに追加できます。追加した種目は即座にワークアウト記録で選択可能になります。")
    
    with st.form("form_add_exercise"):
        new_part = st.selectbox("部位を選択", ["胸", "背中", "肩", "腕", "脚", "腹筋"], key="new_part_sel")
        new_ex_name = st.text_input("新しい種目名を入力 (例: インクラインダンベルフライ)", placeholder="種目名を入力")
        
        btn_submit = st.form_submit_button("➕ 種目をマスタに追加する", type="primary")
        if btn_submit:
            if new_ex_name.strip():
                try:
                    add_new_exercise_to_master(new_part, new_ex_name.strip())
                    st.success(f"[{new_part}] {new_ex_name.strip()} を種目マスタに追加しました！")
                    st.rerun()
                except Exception as e:
                    st.error("追加エラー: " + str(e))
            else:
                st.warning("種目名を入力してください。")
                
    st.divider()
    st.write("#### 📋 現在登録されている種目一覧")
    current_master = load_exercise_master()
    if not current_master.empty:
        st.dataframe(current_master, use_container_width=True)