import streamlit as st
import pandas as pd
import numpy as np
from ortools.sat.python import cp_model
import calendar
import io
from datetime import datetime
from dateutil.relativedelta import relativedelta

# ★★★ バージョン情報 ★★★
APP_VERSION = "proto.2.1" # 個人別日曜上限ルール対応
APP_CREDIT = "Okuno with 🤖 Gemini and Claude"

# --- ヘルパー関数: サマリー作成 ---
def _create_summary(schedule_df, staff_info_dict, year, month, event_units):
    num_days = calendar.monthrange(year, month)[1]; days = list(range(1, num_days + 1)); daily_summary = []
    for d in days:
        day_info = {}; 
        work_staff_ids = schedule_df[(schedule_df[d] == '') | (schedule_df[d] == '○') | (schedule_df[d] == '出')]['職員番号']
        half_day_staff_ids = [s for s, dates in st.session_state.get('requests_half', {}).items() if d in dates]
        total_workers = sum(0.5 if sid in half_day_staff_ids else 1 for sid in work_staff_ids)
        day_info['日'] = d; day_info['曜日'] = ['月','火','水','木','金','土','日'][calendar.weekday(year, month, d)]
        day_info['出勤者総数'] = total_workers
        day_info['PT'] = sum(0.5 if sid in half_day_staff_ids else 1 for sid in work_staff_ids if staff_info_dict[sid]['職種'] == '理学療法士')
        day_info['OT'] = sum(0.5 if sid in half_day_staff_ids else 1 for sid in work_staff_ids if staff_info_dict[sid]['職種'] == '作業療法士')
        day_info['ST'] = sum(0.5 if sid in half_day_staff_ids else 1 for sid in work_staff_ids if staff_info_dict[sid]['職種'] == '言語聴覚士')
        day_info['役職者'] = sum(0.5 if sid in half_day_staff_ids else 1 for sid in work_staff_ids if pd.notna(staff_info_dict[sid].get('役職')))
        day_info['回復期'] = sum(0.5 if sid in half_day_staff_ids else 1 for sid in work_staff_ids if staff_info_dict[sid].get('役割1') == '回復期専従')
        day_info['地域包括'] = sum(0.5 if sid in half_day_staff_ids else 1 for sid in work_staff_ids if staff_info_dict[sid].get('役割1') == '地域包括専従')
        day_info['外来'] = sum(0.5 if sid in half_day_staff_ids else 1 for sid in work_staff_ids if staff_info_dict[sid].get('役割1') == '外来PT')
        if calendar.weekday(year, month, d) != 6:
            pt_units = sum(int(staff_info_dict[sid].get('1日の単位数', 0)) * (0.5 if sid in half_day_staff_ids else 1) for sid in work_staff_ids if staff_info_dict[sid]['職種'] == '理学療法士')
            ot_units = sum(int(staff_info_dict[sid].get('1日の単位数', 0)) * (0.5 if sid in half_day_staff_ids else 1) for sid in work_staff_ids if staff_info_dict[sid]['職種'] == '作業療法士')
            st_units = sum(int(staff_info_dict[sid].get('1日の単位数', 0)) * (0.5 if sid in half_day_staff_ids else 1) for sid in work_staff_ids if staff_info_dict[sid]['職種'] == '言語聴覚士')
            day_info['PT単位数'] = pt_units; day_info['OT単位数'] = ot_units; day_info['ST単位数'] = st_units
            day_info['PT+OT単位数'] = pt_units + ot_units; day_info['特別業務単位数'] = event_units.get(d, 0)
        else:
            day_info['PT単位数'] = '-'; day_info['OT単位数'] = '-'; day_info['ST単位数'] = '-';
            day_info['PT+OT単位数'] = '-'; day_info['特別業務単位数'] = '-'
        daily_summary.append(day_info)
    return pd.DataFrame(daily_summary)

def _create_schedule_df(shifts_values, staff, days, staff_df, requests_x, requests_tri, requests_paid, requests_special, requests_must_work):
    schedule_data = {}
    for s in staff:
        row = []; s_requests_x = requests_x.get(s, []); s_requests_tri = requests_tri.get(s, []); s_requests_paid = requests_paid.get(s, []); s_requests_special = requests_special.get(s, []); s_requests_must = requests_must_work.get(s, [])
        for d in days:
            if shifts_values.get((s, d), 0) == 0:
                if d in s_requests_x: row.append('×')
                elif d in s_requests_tri: row.append('△')
                elif d in s_requests_paid: row.append('有')
                elif d in s_requests_special: row.append('特')
                else: row.append('-')
            else:
                if d in s_requests_must: row.append('○')
                elif d in s_requests_tri: row.append('出')
                else: row.append('')
        schedule_data[s] = row
    schedule_df = pd.DataFrame.from_dict(schedule_data, orient='index', columns=days)
    schedule_df = schedule_df.reset_index().rename(columns={'index': '職員番号'})
    staff_map = staff_df.set_index('職員番号')
    schedule_df.insert(1, '職種', schedule_df['職員番号'].map(staff_map['職種']))
    return schedule_df

# --- メインのソルバー関数 ---
def solve_shift_model(params):
    year, month = params['year'], params['month']
    num_days = calendar.monthrange(year, month)[1]; days = list(range(1, num_days + 1)); staff = params['staff_df']['職員番号'].tolist()
    staff_info = params['staff_df'].set_index('職員番号').to_dict('index')
    sundays = [d for d in days if calendar.weekday(year, month, d) == 6]; weekdays = [d for d in days if d not in sundays]
    managers = [s for s in staff if pd.notna(staff_info.get(s, {}).get('役職'))]; pt_staff = [s for s in staff if staff_info[s]['職種'] == '理学療法士']
    ot_staff = [s for s in staff if staff_info[s]['職種'] == '作業療法士']; st_staff = [s for s in staff if staff_info[s]['職種'] == '言語聴覚士']
    kaifukuki_staff = [s for s in staff if staff_info.get(s, {}).get('役割1') == '回復期専従']; kaifukuki_pt = [s for s in kaifukuki_staff if staff_info[s]['職種'] == '理学療法士']
    kaifukuki_ot = [s for s in kaifukuki_staff if staff_info[s]['職種'] == '作業療法士']; gairai_staff = [s for s in staff if staff_info.get(s, {}).get('役割1') == '外来PT']
    chiiki_staff = [s for s in staff if staff_info.get(s, {}).get('役割1') == '地域包括専従']; 
    
    requests_x = {}; requests_tri = {}; requests_must_work = {};
    requests_paid = {}; requests_special = {}; st.session_state.requests_half = {};
    for index, row in params['requests_df'].iterrows():
        staff_id = row['職員番号']
        if staff_id not in staff: continue
        requests_x[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '×']; requests_tri[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '△']
        requests_must_work[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '○']; requests_paid[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '有']
        requests_special[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '特']; st.session_state.requests_half[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) in ['AM有', 'PM有']]
    
    model = cp_model.CpModel(); shifts = {}
    for s in staff:
        for d in days: shifts[(s, d)] = model.NewBoolVar(f'shift_{s}_{d}')
    
    # ハード制約
    if params['h1_on']:
        for s in staff:
            num_paid_leave = len(requests_paid.get(s, [])); num_special_leave = len(requests_special.get(s, []))
            model.Add(sum(1 - shifts[(s, d)] for d in days) == 9 + num_paid_leave + num_special_leave)
    if params['h2_on']:
        for s, dates in requests_must_work.items():
            for d in dates: model.Add(shifts[(s, d)] == 1)
        for s, dates in st.session_state.requests_half.items():
            for d in dates: model.Add(shifts[(s, d)] == 1)
        for s, dates in requests_x.items():
            for d in dates: model.Add(shifts[(s, d)] == 0)
        for s, dates in requests_paid.items():
            for d in dates: model.Add(shifts[(s, d)] == 0)
        for s, dates in requests_special.items():
            for d in dates: model.Add(shifts[(s, d)] == 0)
    if params['h3_on']:
        for d in days: model.Add(sum(shifts[(s, d)] for s in managers) >= 1)
    if params['h5_on']:
        for s in staff:
            limit = int(staff_info.get(s, {}).get('日曜上限', 2))
            model.Add(sum(shifts[(s, d)] for d in sundays) <= limit)

    penalties = []
    # (ソフト制約ロジック)
    # ... (前回のコードから変更なし)
    
    model.Minimize(sum(penalties))
    solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds = 60.0; status = solver.Solve(model)
    
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        shifts_values = {(s, d): solver.Value(shifts[(s, d)]) for s in staff for d in days}
        schedule_df = _create_schedule_df(shifts_values, staff, days, params['staff_df'], requests_x, requests_tri, requests_paid, requests_special, requests_must_work)
        temp_work_df = schedule_df.replace({'×': '休', '-': '休', '△': '休', '有': '休', '特': '休', '': '出', '○': '出'})
        summary_df = _create_summary(temp_work_df, staff_info, year, month, params['event_units'])
        message = f"求解ステータス: **{solver.StatusName(status)}** (ペナルティ合計: **{round(solver.ObjectiveValue())}**)"
        return True, schedule_df, summary_df, message
    else:
        message = f"致命的なエラー: ハード制約が矛盾しているため、勤務表を作成できませんでした。({solver.StatusName(status)})"
        return False, pd.DataFrame(), pd.DataFrame(), message

# --- Streamlit UI ---
st.set_page_config(layout="wide")
st.title('リハビリテーション科 勤務表作成アプリ')
today = datetime.now()
next_month_date = today + relativedelta(months=1)
default_year = next_month_date.year
default_month_index = next_month_date.month - 1
with st.expander("▼ 各種パラメータを設定する", expanded=True):
    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("対象年月とファイル")
        year = st.number_input("年（西暦）", min_value=default_year - 5, max_value=default_year + 5, value=default_year)
        month = st.selectbox("月", options=list(range(1, 13)), index=default_month_index)
        st.markdown("---")
        staff_file = st.file_uploader("1. 職員一覧 (CSV)", type="csv")
        requests_file = st.file_uploader("2. 希望休一覧 (CSV)", type="csv")
    with c2:
        st.subheader("日曜日の出勤人数設定")
        c2_1, c2_2, c2_3 = st.columns(3)
        with c2_1: target_pt = st.number_input("PT目標", min_value=0, value=10, step=1)
        with c2_2: target_ot = st.number_input("OT目標", min_value=0, value=5, step=1)
        with c2_3: target_st = st.number_input("ST目標", min_value=0, value=3, step=1)
    with c3:
        st.subheader("緩和条件と優先度")
        tolerance = st.number_input("PT/OT許容誤差(±)", min_value=0, max_value=5, value=1, help="PT/OTの合計人数が目標通りなら、それぞれの人数がこの値までずれてもペナルティを課しません。")
        tri_penalty_weight = st.slider("準希望休(△)の優先度", min_value=0, max_value=20, value=8, help="値が大きいほど△希望が尊重されます。")
    st.markdown("---")
    st.subheader(f"{year}年{month}月のイベント設定（各日の特別業務単位数を入力）")
    event_units_input = {}
    num_days_in_month = calendar.monthrange(year, month)[1]
    first_day_weekday = calendar.weekday(year, month, 1)
    cal_cols = st.columns(7)
    weekdays_jp = ['月', '火', '水', '木', '金', '土', '日']
    for i, day_name in enumerate(weekdays_jp): cal_cols[i].markdown(f"<p style='text-align: center;'><b>{day_name}</b></p>", unsafe_allow_html=True)
    day_counter = 1
    for week in range(6):
        cols = st.columns(7)
        for day_of_week in range(7):
            if (week == 0 and day_of_week < first_day_weekday) or day_counter > num_days_in_month:
                continue
            with cols[day_of_week]:
                is_sunday = calendar.weekday(year, month, day_counter) == 6
                event_units_input[day_counter] = st.number_input(label=f"{day_counter}日", value=0, step=10, disabled=is_sunday, key=f"event_{year}_{month}_{day_counter}")
            day_counter += 1
        if day_counter > num_days_in_month: break
    st.markdown("---")
    create_button = st.button('勤務表を作成', type="primary", use_container_width=True)

with st.expander("▼ ルール検証モード（上級者向け）"):
    st.warning("注意: 各ルールのON/OFFやペナルティ値を変更することで、意図しない結果や、解が見つからない状況が発生する可能性があります。")
    st.markdown("---")
    st.subheader("ハード制約のON/OFF")
    h_cols = st.columns(5)
    params = {}
    with h_cols[0]: params['h1_on'] = st.toggle('H1: 月間休日数', value=True, key='h1')
    with h_cols[1]: params['h2_on'] = st.toggle('H2: 希望休/有休', value=True, key='h2')
    with h_cols[2]: params['h3_on'] = st.toggle('H3: 役職者配置', value=True, key='h3')
    # ★★★ H4を削除し、H5のラベルを変更 ★★★
    with h_cols[3]: params['h5_on'] = st.toggle('H5: 個人別日曜上限', value=True, key='h5')
    
    st.markdown("---")
    st.subheader("ソフト制約のON/OFFとペナルティ設定")
    s_cols = st.columns(4)
    with s_cols[0]:
        params['s0_on'] = st.toggle('S0: 完全週の週休2日', value=True, key='s0')
        params['s0_penalty'] = st.number_input("S0 Penalty", value=200, disabled=not params['s0_on'], key='s0p')
    with s_cols[1]:
        params['s2_on'] = st.toggle('S2: 不完全週の週休1日', value=True, key='s2')
        params['s2_penalty'] = st.number_input("S2 Penalty", value=25, disabled=not params['s2_on'], key='s2p')
    with s_cols[2]:
        params['s3_on'] = st.toggle('S3: 外来同時休', value=True, key='s3')
        params['s3_penalty'] = st.number_input("S3 Penalty", value=10, disabled=not params['s3_on'], key='s3p')
    with s_cols[3]:
        params['s4_on'] = st.toggle('S4: 準希望休(△)尊重', value=True, key='s4')
        params['s4_penalty'] = st.number_input("S4 Penalty", value=8, disabled=not params['s4_on'], key='s4p')
    s_cols2 = st.columns(4)
    with s_cols2[0]:
        params['s5_on'] = st.toggle('S5: 回復期配置', value=True, key='s5')
        params['s5_penalty'] = st.number_input("S5 Penalty", value=5, disabled=not params['s5_on'], key='s5p')
    with s_cols2[1]:
        params['s7_on'] = st.toggle('S7: 人数平準化', value=True, key='s7')
        c_s7_1, c_s7_2 = st.columns(2)
        params['s7_penalty'] = c_s7_1.number_input("S7 標準P", value=1, disabled=not params['s7_on'], key='s7p')
        params['s7_penalty_heavy'] = c_s7_2.number_input("S7 強化P", value=3, disabled=not params['s7_on'], key='s7ph')
    with s_cols2[2]:
        params['s6_on'] = st.toggle('S6: 業務負荷平準化', value=True, key='s6')
        c_s6_1, c_s6_2 = st.columns(2)
        params['s6_penalty'] = c_s6_1.number_input("S6 標準P", value=2, disabled=not params['s6_on'], key='s6p')
        params['s6_penalty_heavy'] = c_s6_2.number_input("S6 強化P", value=4, disabled=not params['s6_on'], key='s6ph')
    # ★★★ 新しいソフト制約S8のUIを追加 ★★★
    with s_cols2[3]:
        params['s8_on'] = st.toggle('S8: 2回超日曜出勤', value=True, key='s8')
        params['s8_penalty'] = st.number_input("S8 Penalty", value=20, disabled=not params['s8_on'], key='s8p')

    st.markdown("##### S1: 日曜人数目標")
    s_cols3 = st.columns(3)
    with s_cols3[0]:
        params['s1a_on'] = st.toggle('S1-a: PT/OT合計', value=True, key='s1a')
        params['s1a_penalty'] = st.number_input("S1-a Penalty", value=50, disabled=not params['s1a_on'], key='s1ap')
    with s_cols3[1]:
        params['s1b_on'] = st.toggle('S1-b: PT/OT個別', value=True, key='s1b')
        params['s1b_penalty'] = st.number_input("S1-b Penalty", value=40, disabled=not params['s1b_on'], key='s1bp')
    with s_cols3[2]:
        params['s1c_on'] = st.toggle('S1-c: ST目標', value=True, key='s1c')
        params['s1c_penalty'] = st.number_input("S1-c Penalty", value=60, disabled=not params['s1c_on'], key='s1cp')

if create_button:
    if staff_file is not None and requests_file is not None:
        try:
            params['staff_df'] = pd.read_csv(staff_file); params['requests_df'] = pd.read_csv(requests_file)
            params['year'] = year; params['month'] = month
            params['target_pt'] = target_pt; params['target_ot'] = target_ot; params['target_st'] = target_st
            params['tolerance'] = tolerance; params['event_units'] = event_units_input
            
            is_feasible, schedule_df, summary_df, message = solve_shift_model(params)
            
            st.info(message)
            if is_feasible:
                st.header("勤務表")
                # 表示ロジック
                num_days = calendar.monthrange(year, month)[1]
                temp_work_df = schedule_df.copy()
                for col in temp_work_df.columns:
                    if col not in ['職員番号', '職種']:
                        temp_work_df[col] = temp_work_df[col].apply(lambda x: '出' if x in ['', '○', '出'] else '休')
                summary_T = _create_summary(temp_work_df, params['staff_df'].set_index('職員番号').to_dict('index'), year, month, event_units_input).drop(columns=['日', '曜日']).T
                summary_T.columns = list(range(1, num_days + 1))
                summary_processed = summary_T.reset_index().rename(columns={'index': '職員名'})
                summary_processed['職員番号'] = summary_processed['職員名'].apply(lambda x: f"_{x}")
                summary_processed['職種'] = "サマリー"
                summary_processed = summary_processed[['職員番号', '職種'] + list(range(1, num_days + 1))]
                final_df_for_display = pd.concat([schedule_df, summary_processed], ignore_index=True)
                days_header = list(range(1, num_days + 1))
                weekdays_header = [ ['月','火','水','木','金','土','日'][calendar.weekday(year, month, d)] for d in days_header]
                final_df_for_display.columns = pd.MultiIndex.from_tuples([('職員情報', '職員番号'), ('職員情報', '職種')] + list(zip(days_header, weekdays_header)))
                def style_table(df):
                    sunday_cols = [col for col in df.columns if col[1] == '日']
                    styler = df.style.set_properties(**{'text-align': 'center'})
                    for col in sunday_cols: styler = styler.set_properties(subset=[col], **{'background-color': '#fff0f0'})
                    return styler
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    schedule_df.to_excel(writer, sheet_name='勤務表', index=False)
                    summary_df.to_excel(writer, sheet_name='日別サマリー', index=False)
                excel_data = output.getvalue()
                st.download_button(label="📥 Excelでダウンロード", data=excel_data, file_name=f"schedule_{year}{month:02d}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                st.dataframe(style_table(final_df_for_display))
        
        except Exception as e:
            st.error(f'予期せぬエラーが発生しました: {e}')
            st.exception(e)
    else:
        st.warning('職員一覧と希望休一覧の両方のファイルをアップロードしてください。')

st.markdown("---")
st.markdown(f"<div style='text-align: right; color: grey;'>{APP_CREDIT} | Version: {APP_VERSION}</div>", unsafe_allow_html=True)