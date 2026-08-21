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

def save_single_exercise(body_part: str, exercise_name: str, sets: list, date_str: str):
    """1種目分の全セットをスプレッドシートに保存"""
    sh = get_spreadsheet()
    ws = sh.worksheet("筋トレ記録")
    
    rows_to_append = []
    for idx, s in enumerate(sets):
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
            exercise_name,
            set_num,
            weight_str,
            reps_num,
            vol,
            memo_str
        ])
        
    if rows_to_append:
        ws.append_rows(rows_to_append)
        st.cache_data.clear()

def delete_workout_row(row_index: int):
    """筋トレ記録から特定の1行（1セット）を削除"""
    sh = get_spreadsheet()
    ws = sh.worksheet("筋トレ記録")
    ws.delete_rows(row_index + 2)
    st.cache_data.clear()

def delete_exercise_all_sets_for_day(date_str: str, exercise_name: str):
    """その日の特定の種目をまとめて全行削除"""
    sh = get_spreadsheet()
    ws = sh.worksheet("筋トレ記録")
    all_values = ws.get_all_values()
    if len(all_values) <= 1:
        return
    
    header = all_values[0]
    date_col_idx = header.index("日付") if "日付" in header else 0
    ex_col_idx = header.index("種目名") if "種目名" in header else 2
    
    rows_to_delete = []
    for i in range(1, len(all_values)):
        row = all_values[i]
        if len(row) > max(date_col_idx, ex_col_idx):
            if row[date_col_idx] == date_str and row[ex_col_idx] == exercise_name:
                rows_to_delete.append(i + 1)
                
    for row_num in reversed(rows_to_delete):
        ws.delete_rows(row_num)
        
    st.cache_data.clear()

def add_new_exercise_to_master(body_part: str, exercise_name: str):
    """新しい種目をマスタに追加"""
    sh = get_spreadsheet()
    ws = sh.worksheet("種目マスタ")
    ws.append_row([body_part, exercise_name, "TRUE"])
    st.cache_data.clear()

# ==========================================
# 3. セッション状態の初期化
# ==========================================
if "current_exercise" not in st.session_state:
    st.session_state.current_exercise = None

# ==========================================
# 4. 画面構築 & タブ設定
# ==========================================
st.set_page_config(page_title="ワークアウト記録", layout="centered", page_icon="🏋️")

st.title("🏋️ ワークアウト記録")

tab_record, tab_history, tab_master = st.tabs([
    "  🏋️ ワークアウト  ",
    "  📊 履歴・分析  ",
    "  ⚙️ 種目マスタ  "
])

# ==========================================
# タブ1：ワークアウト
# ==========================================
with tab_record:
    JST = timezone(timedelta(hours=9))
    today_jst = datetime.now(JST).date()
    workout_date = st.date_input("📅 日付", value=today_jst, key="main_workout_date")
    workout_date_str = workout_date.strftime("%Y-%m-%d")

    st.divider()

    df_master = load_exercise_master()
    all_body_parts = ["胸", "背中", "肩", "腕", "脚", "腹筋"]

    # --- 種目選択エリア ---
    st.subheader("➕ 種目を選択")
    col_part, col_ex = st.columns(2)
    selected_part = col_part.selectbox("部位", all_body_parts, key="sel_part_record")
    
    filtered_df = df_master[df_master["部位"] == selected_part]
    exercise_options = filtered_df["種目名"].tolist() if not filtered_df.empty else []
    
    selected_exercise = col_ex.selectbox("種目", exercise_options if exercise_options else ["登録なし"], key="sel_ex_record")
    
    if st.button("➕ 種目を追加", type="primary", use_container_width=True):
        if selected_exercise and selected_exercise != "登録なし":
            last_record = get_last_workout_record(selected_exercise)
            if last_record and last_record["sets"]:
                initial_sets = [dict(s) for s in last_record["sets"]]
            else:
                initial_sets = [{"weight": 60.0, "is_bodyweight": False, "reps": 10, "memo": ""}]
                
            st.session_state.current_exercise = {
                "body_part": selected_part,
                "exercise": selected_exercise,
                "sets": initial_sets
            }
            st.rerun()

    # --- 入力中フォーム ---
    if st.session_state.current_exercise:
        cur_ex = st.session_state.current_exercise
        ex_name = cur_ex["exercise"]
        ex_part = cur_ex["body_part"]
        
        st.write(" ")
        st.markdown(f"### 📝 [{ex_part}] {ex_name}")
        
        # 前回記録の表示
        last_rec = get_last_workout_record(ex_name)
        if last_rec and last_rec["sets"]:
            lines = []
            for i, s in enumerate(last_rec["sets"]):
                set_num = i + 1
                reps_cnt = s["reps"]
                w_str = "自重" if s["is_bodyweight"] else str(s["weight"]) + "kg"
                lines.append(f"{set_num}: {w_str} × {reps_cnt}回")
            rec_text = "⏱️ **Last Record**: " + str(last_rec["date"]) + "\n\n" + "  \n".join(lines)
            st.info(rec_text)
            
            col_cp, col_cancel = st.columns(2)
            if col_cp.button("📋 前回をコピー", key="btn_copy_current"):
                cur_ex["sets"] = [dict(s) for s in last_rec["sets"]]
                st.success("前回内容を反映しました！")
                st.rerun()
        else:
            st.caption("ℹ️ 過去記録なし")
            col_dummy, col_cancel = st.columns(2)
            
        if col_cancel.button("✕ キャンセル", key="btn_cancel_current"):
            st.session_state.current_exercise = None
            st.rerun()

        # セットごとの入力リスト
        for set_idx, set_data in enumerate(cur_ex["sets"]):
            st.write(f"**セット {set_idx + 1}**")
            
            c_bw, c_w, c_r, c_del_set = st.columns(4)
            
            set_data["is_bodyweight"] = c_bw.checkbox("自重", value=set_data["is_bodyweight"], key=f"cur_bw_{set_idx}")
            
            if set_data["is_bodyweight"]:
                c_w.text_input("重量", value="自重", disabled=True, key=f"cur_w_dis_{set_idx}")
            else:
                set_data["weight"] = c_w.number_input(
                    "重量 (kg)",
                    min_value=0.0,
                    max_value=500.0,
                    value=float(set_data["weight"]),
                    step=0.5,
                    key=f"cur_w_{set_idx}"
                )
                
            set_data["reps"] = c_r.number_input(
                "回数",
                min_value=1,
                max_value=100,
                value=int(set_data["reps"]),
                step=1,
                key=f"cur_r_{set_idx}"
            )
            
            if len(cur_ex["sets"]) > 1:
                if c_del_set.button("✕", key=f"cur_del_set_{set_idx}", help="セット削除"):
                    cur_ex["sets"].pop(set_idx)
                    st.rerun()
            
            set_data["memo"] = st.text_input("メモ", value=set_data.get("memo", ""), placeholder="メモ（任意）", key=f"cur_memo_{set_idx}")
            st.caption("---")

        # アクションボタン
        col_add_s, col_save_ex = st.columns(2)
        if col_add_s.button("➕ セット追加", key="btn_add_set_current"):
            last_s = cur_ex["sets"][-1]
            cur_ex["sets"].append({
                "weight": last_s["weight"],
                "is_bodyweight": last_s["is_bodyweight"],
                "reps": last_s["reps"],
                "memo": ""
            })
            st.rerun()
            
        if col_save_ex.button("💾 保存する", type="primary", use_container_width=True, key="btn_save_current_ex"):
            with st.spinner("保存中..."):
                try:
                    save_single_exercise(ex_part, ex_name, cur_ex["sets"], workout_date_str)
                    st.success(f"{ex_name} を保存しました！")
                    st.session_state.current_exercise = None
                    st.rerun()
                except Exception as e:
                    st.error("保存エラー: " + str(e))

    st.divider()

    # ==========================================
    # 本日のトレーニング実績（サマリー ＆ 一覧）
    # ==========================================
    st.subheader(f"📊 本日の実績 ({workout_date_str})")
    all_logs = load_all_workout_records()
    today_logs = all_logs[all_logs["日付"] == workout_date_str] if not all_logs.empty and "日付" in all_logs.columns else pd.DataFrame()
    
    if today_logs.empty:
        st.info("本日の記録はまだありません。")
    else:
        # サマリー集計
        t_reps = pd.to_numeric(today_logs["回数"], errors="coerce").fillna(0).sum()
        t_vol = pd.to_numeric(today_logs["ボリューム(kg)"], errors="coerce").fillna(0).sum()
        t_sets = len(today_logs)
        t_ex_cnt = today_logs["種目名"].nunique()
        
        sum1, sum2, sum3, sum4 = st.columns(4)
        sum1.metric("種目数", f"{t_ex_cnt}")
        sum2.metric("セット数", f"{t_sets}")
        sum3.metric("総レップ数", f"{int(t_reps)}")
        sum4.metric("総負荷量", f"{int(t_vol)} kg")
        
        st.write(" ")
        
        # 種目ごとのカード一覧表示
        unique_today_ex = today_logs["種目名"].unique().tolist()
        for t_ex in unique_today_ex:
            ex_group_df = today_logs[today_logs["種目名"] == t_ex]
            part_name = ex_group_df["部位"].iloc[0] if "部位" in ex_group_df.columns else ""
            
            with st.expander(f"📌 [{part_name}] {t_ex} ({len(ex_group_df)} sets)", expanded=True):
                if st.button("🗑️ 種目を削除", key=f"del_all_{t_ex}_{workout_date_str}"):
                    delete_exercise_all_sets_for_day(workout_date_str, t_ex)
                    st.success(f"{t_ex} を削除しました！")
                    st.rerun()
                
                st.write("---")
                
                for set_idx, row in ex_group_df.iterrows():
                    s_num = row.get("セット", "")
                    s_w = str(row.get("重量", ""))
                    w_disp = s_w if s_w == "自重" else s_w + "kg"
                    s_r = str(row.get("回数", ""))
                    s_memo = str(row.get("メモ", ""))
                    
                    row_c1, row_c2, row_c3 = st.columns(3)
                    row_c1.write(f"**{s_num}**: **{w_disp}** × **{s_r}回**")
                    if s_memo:
                        row_c2.caption(f"📝 {s_memo}")
                    else:
                        row_c2.write("")
                        
                    if row_c3.button("🗑️ 削除", key=f"del_single_set_{set_idx}", help="この1セットのみ削除"):
                        delete_workout_row(set_idx)
                        st.success("セットを削除しました！")
                        st.rerun()

# ==========================================
# タブ2：履歴・分析
# ==========================================
with tab_history:
    st.subheader("📊 履歴・集計")
    all_logs = load_all_workout_records()
    
    if all_logs.empty:
        st.info("過去の記録はまだありません。")
    else:
        calc_df = all_logs.copy()
        calc_df["ボリューム(kg)"] = pd.to_numeric(calc_df["ボリューム(kg)"], errors="coerce").fillna(0)
        
        daily_summary = calc_df.groupby("日付", as_index=False).agg({
            "部位": lambda x: "・".join(sorted(list(set(x)))),
            "種目名": "nunique",
            "セット": "count",
            "ボリューム(kg)": "sum"
        })
        daily_summary.columns = ["日付", "部位", "種目数", "セット数", "総ボリューム (kg)"]
        daily_summary["総ボリューム (kg)"] = daily_summary["総ボリューム (kg)"].astype(int)
        
        st.write("#### 📅 日別サマリー")
        st.dataframe(daily_summary.sort_values("日付", ascending=False), use_container_width=True)
        
        st.divider()
        
        st.write("#### 📈 種目別推移")
        all_unique_exercises = sorted(all_logs["種目名"].unique().tolist())
        selected_graph_ex = st.selectbox("種目を選択", all_unique_exercises, key="sel_graph_ex_tab2")
        
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

# ==========================================
# タブ3：種目マスタ
# ==========================================
with tab_master:
    st.subheader("⚙️ 種目マスタ")
    
    with st.form("form_add_exercise"):
        new_part = st.selectbox("部位", ["胸", "背中", "肩", "腕", "脚", "腹筋"], key="new_part_sel")
        new_ex_name = st.text_input("種目名", placeholder="例: インクラインダンベルフライ")
        
        btn_submit = st.form_submit_button("➕ 追加", type="primary")
        if btn_submit:
            if new_ex_name.strip():
                try:
                    add_new_exercise_to_master(new_part, new_ex_name.strip())
                    st.success(f"[{new_part}] {new_ex_name.strip()} を追加しました！")
                    st.rerun()
                except Exception as e:
                    st.error("追加エラー: " + str(e))
            else:
                st.warning("種目名を入力してください。")
                
    st.divider()
    st.write("#### 📋 登録済み種目一覧")
    current_master = load_exercise_master()
    if not current_master.empty:
        st.dataframe(current_master, use_container_width=True)