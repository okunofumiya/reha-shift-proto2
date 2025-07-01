import streamlit as st
import pandas as pd
import numpy as np
from ortools.sat.python import cp_model
import calendar
import io
from datetime import datetime
from dateutil.relativedelta import relativedelta

# ★★★ バージョン情報 ★★★
APP_VERSION = "proto.2.6" # 設定項目を日曜出勤エリアに正しく配置
APP_CREDIT = "Okuno with 🤖 Gemini and Claude"

# --- ヘルパー関数: サマリー作成 ---
def _create_summary(schedule_df, staff_info_dict, year, month, event_units):
    num_days = calendar.monthrange(year, month)[1]
    days = list(range(1, num_days + 1))
    daily_summary = []
    
    for d in days:
        day_info = {}
        
        # 出勤者の計算（○、出、空欄は出勤、AM有・PM有・AM休・PM休は0.5人扱い）
        work_staff_ids = schedule_df[(schedule_df[d] == '') | (schedule_df[d] == '○') | (schedule_df[d] == '出')]['職員番号']
        half_day_staff_ids = [s for s, dates in st.session_state.get('requests_half', {}).items() if d in dates]
        am_off_staff_ids = [s for s, dates in st.session_state.get('requests_am_off', {}).items() if d in dates]
        pm_off_staff_ids = [s for s, dates in st.session_state.get('requests_pm_off', {}).items() if d in dates]
        
        # 出勤者数の計算（AM有・PM有・AM休・PM休は全て0.5人扱い）
        total_workers = 0
        for sid in work_staff_ids:
            if sid in half_day_staff_ids or sid in am_off_staff_ids or sid in pm_off_staff_ids:
                total_workers += 0.5
            else:
                total_workers += 1
        
        day_info['日'] = d
        day_info['曜日'] = ['月','火','水','木','金','土','日'][calendar.weekday(year, month, d)]
        day_info['出勤者総数'] = total_workers
        
        # 職種別出勤者数の計算（AM有・PM有・AM休・PM休は全て0.5人扱い）
        pt_workers = sum(
            0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 
            for sid in work_staff_ids if staff_info_dict[sid]['職種'] == '理学療法士'
        )
        ot_workers = sum(
            0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 
            for sid in work_staff_ids if staff_info_dict[sid]['職種'] == '作業療法士'
        )
        st_workers = sum(
            0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 
            for sid in work_staff_ids if staff_info_dict[sid]['職種'] == '言語聴覚士'
        )
        
        day_info['PT'] = pt_workers
        day_info['OT'] = ot_workers
        day_info['ST'] = st_workers
        
        # 役職者・役割別の計算（AM有・PM有・AM休・PM休は全て0.5人扱い）
        day_info['役職者'] = sum(
            0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 
            for sid in work_staff_ids if pd.notna(staff_info_dict[sid].get('役職'))
        )
        day_info['回復期'] = sum(
            0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 
            for sid in work_staff_ids if staff_info_dict[sid].get('役割1') == '回復期専従'
        )
        day_info['地域包括'] = sum(
            0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 
            for sid in work_staff_ids if staff_info_dict[sid].get('役割1') == '地域包括専従'
        )
        day_info['外来'] = sum(
            0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 
            for sid in work_staff_ids if staff_info_dict[sid].get('役割1') == '外来PT'
        )
        
        # 単位数の計算（日曜日以外）
        if calendar.weekday(year, month, d) != 6:
            # PT単位数（AM休・PM休は半分の単位数、AM有・PM有も半分）
            pt_units = 0
            for sid in work_staff_ids:
                if staff_info_dict[sid]['職種'] == '理学療法士':
                    if sid in am_off_staff_ids or sid in pm_off_staff_ids:
                        pt_units += int(staff_info_dict[sid].get('1日の単位数', 0)) * 0.5  # AM休・PM休は半分の単位数
                    elif sid in half_day_staff_ids:
                        pt_units += int(staff_info_dict[sid].get('1日の単位数', 0)) * 0.5  # AM有・PM有は半分
                    else:
                        pt_units += int(staff_info_dict[sid].get('1日の単位数', 0))  # 通常勤務
            
            # OT単位数
            ot_units = 0
            for sid in work_staff_ids:
                if staff_info_dict[sid]['職種'] == '作業療法士':
                    if sid in am_off_staff_ids or sid in pm_off_staff_ids:
                        ot_units += int(staff_info_dict[sid].get('1日の単位数', 0)) * 0.5  # AM休・PM休は半分の単位数
                    elif sid in half_day_staff_ids:
                        ot_units += int(staff_info_dict[sid].get('1日の単位数', 0)) * 0.5
                    else:
                        ot_units += int(staff_info_dict[sid].get('1日の単位数', 0))
            
            # ST単位数
            st_units = 0
            for sid in work_staff_ids:
                if staff_info_dict[sid]['職種'] == '言語聴覚士':
                    if sid in am_off_staff_ids or sid in pm_off_staff_ids:
                        st_units += int(staff_info_dict[sid].get('1日の単位数', 0)) * 0.5  # AM休・PM休は半分の単位数
                    elif sid in half_day_staff_ids:
                        st_units += int(staff_info_dict[sid].get('1日の単位数', 0)) * 0.5
                    else:
                        st_units += int(staff_info_dict[sid].get('1日の単位数', 0))
            
            day_info['PT単位数'] = pt_units
            day_info['OT単位数'] = ot_units
            day_info['ST単位数'] = st_units
            day_info['PT+OT単位数'] = pt_units + ot_units
            day_info['特別業務単位数'] = event_units.get(d, 0)
        else:
            day_info['PT単位数'] = '-'
            day_info['OT単位数'] = '-'
            day_info['ST単位数'] = '-'
            day_info['PT+OT単位数'] = '-'
            day_info['特別業務単位数'] = '-'
        
        daily_summary.append(day_info)
    
    return pd.DataFrame(daily_summary)

def _create_schedule_df(shifts_values, staff, days, staff_df, requests_x, requests_tri, requests_paid, requests_special, requests_summer, requests_am_off, requests_pm_off, requests_must_work):
    schedule_data = {}
    
    for s in staff:
        row = []
        s_requests_x = requests_x.get(s, [])
        s_requests_tri = requests_tri.get(s, [])
        s_requests_paid = requests_paid.get(s, [])
        s_requests_special = requests_special.get(s, [])
        s_requests_summer = requests_summer.get(s, [])
        s_requests_am_off = requests_am_off.get(s, [])
        s_requests_pm_off = requests_pm_off.get(s, [])
        s_requests_must = requests_must_work.get(s, [])
        
        for d in days:
            if shifts_values.get((s, d), 0) == 0:
                # 休日の場合の表示
                if d in s_requests_x:
                    row.append('×')
                elif d in s_requests_tri:
                    row.append('△')
                elif d in s_requests_paid:
                    row.append('有')
                elif d in s_requests_special:
                    row.append('特')
                elif d in s_requests_summer:
                    row.append('夏')
                else:
                    row.append('-')
            else:
                # 出勤日の場合の表示
                if d in s_requests_must:
                    row.append('○')
                elif d in s_requests_tri:
                    row.append('出')
                elif d in s_requests_am_off:
                    row.append('AM休')
                elif d in s_requests_pm_off:
                    row.append('PM休')
                else:
                    row.append('')
        
        schedule_data[s] = row
    
    schedule_df = pd.DataFrame.from_dict(schedule_data, orient='index', columns=days)
    schedule_df = schedule_df.reset_index().rename(columns={'index': '職員番号'})
    
    # 職種情報を追加
    staff_map = staff_df.set_index('職員番号')
    schedule_df.insert(1, '職種', schedule_df['職員番号'].map(staff_map['職種']))
    
    return schedule_df

# --- メインのソルバー関数 ---
def solve_shift_model(params):
    year, month = params['year'], params['month']
    num_days = calendar.monthrange(year, month)[1]
    days = list(range(1, num_days + 1))
    staff = params['staff_df']['職員番号'].tolist()
    staff_info = params['staff_df'].set_index('職員番号').to_dict('index')
    
    sundays = [d for d in days if calendar.weekday(year, month, d) == 6]
    weekdays = [d for d in days if d not in sundays]
    
    # 職員グループの定義
    managers = [s for s in staff if pd.notna(staff_info.get(s, {}).get('役職'))]
    pt_staff = [s for s in staff if staff_info[s]['職種'] == '理学療法士']
    ot_staff = [s for s in staff if staff_info[s]['職種'] == '作業療法士']
    st_staff = [s for s in staff if staff_info[s]['職種'] == '言語聴覚士']
    
    kaifukuki_staff = [s for s in staff if staff_info.get(s, {}).get('役割1') == '回復期専従']
    kaifukuki_pt = [s for s in kaifukuki_staff if staff_info[s]['職種'] == '理学療法士']
    kaifukuki_ot = [s for s in kaifukuki_staff if staff_info[s]['職種'] == '作業療法士']
    gairai_staff = [s for s in staff if staff_info.get(s, {}).get('役割1') == '外来PT']
    chiiki_staff = [s for s in staff if staff_info.get(s, {}).get('役割1') == '地域包括専従']
    
    # 希望休の分類
    requests_x = {}
    requests_tri = {}
    requests_must_work = {}
    requests_paid = {}
    requests_special = {}
    requests_summer = {}  # 新規追加
    requests_am_off = {}  # 新規追加
    requests_pm_off = {} # 新規追加
    st.session_state.requests_half = {}
    st.session_state.requests_am_off = {}  # サマリー用
    st.session_state.requests_pm_off = {}  # サマリー用
    
    for index, row in params['requests_df'].iterrows():
        staff_id = row['職員番号']
        if staff_id not in staff:
            continue
        
        requests_x[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '×']
        requests_tri[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '△']
        requests_must_work[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '○']
        requests_paid[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '有']
        requests_special[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '特']
        requests_summer[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '夏']  # 新規追加
        requests_am_off[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == 'AM休']  # 新規追加
        requests_pm_off[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == 'PM休']  # 新規追加
        st.session_state.requests_half[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) in ['AM有', 'PM有']]
        st.session_state.requests_am_off[staff_id] = requests_am_off[staff_id]  # サマリー用
        st.session_state.requests_pm_off[staff_id] = requests_pm_off[staff_id]  # サマリー用
    
    # CP-SATモデルの作成
    model = cp_model.CpModel()
    shifts = {}
    
    # 変数の定義
    for s in staff:
        for d in days:
            shifts[(s, d)] = model.NewBoolVar(f'shift_{s}_{d}')
    
    # ハード制約
    if params['h1_on']:
        # H1: 月間休日数制約（夏期休暇、AM休、PM休も考慮）
        for s in staff:
            num_paid_leave = len(requests_paid.get(s, []))
            num_special_leave = len(requests_special.get(s, []))
            num_summer_leave = len(requests_summer.get(s, []))  # 新規追加
            num_am_off = len(requests_am_off.get(s, []))  # 新規追加
            num_pm_off = len(requests_pm_off.get(s, []))  # 新規追加
            
            # AM休・PM休は0.5日の休み扱いとして計算
            total_off_days = 9 + num_paid_leave + num_special_leave + num_summer_leave + (num_am_off + num_pm_off) * 0.5
            model.Add(sum(1 - shifts[(s, d)] for d in days) == int(total_off_days))
    
    if params['h2_on']:
        # H2: 希望休/有休制約
        for s, dates in requests_must_work.items():
            for d in dates:
                model.Add(shifts[(s, d)] == 1)
        
        for s, dates in st.session_state.requests_half.items():
            for d in dates:
                model.Add(shifts[(s, d)] == 1)
        
        for s, dates in requests_am_off.items():
            for d in dates:
                model.Add(shifts[(s, d)] == 1)  # AM休は出勤扱い
        
        for s, dates in requests_pm_off.items():
            for d in dates:
                model.Add(shifts[(s, d)] == 1)  # PM休は出勤扱い
        
        for s, dates in requests_x.items():
            for d in dates:
                model.Add(shifts[(s, d)] == 0)
        
        for s, dates in requests_paid.items():
            for d in dates:
                model.Add(shifts[(s, d)] == 0)
        
        for s, dates in requests_special.items():
            for d in dates:
                model.Add(shifts[(s, d)] == 0)
        
        for s, dates in requests_summer.items():  # 新規追加
            for d in dates:
                model.Add(shifts[(s, d)] == 0)
    
    if params['h3_on']:
        # H3: 役職者配置制約
        for d in days:
            model.Add(sum(shifts[(s, d)] for s in managers) >= 1)
    
    if params['h5_on']:
        # H5: 個人別日曜上限制約
        for s in staff:
            limit = int(staff_info.get(s, {}).get('日曜上限', 2))
            model.Add(sum(shifts[(s, d)] for d in sundays) <= limit)
    
    # ソフト制約（ペナルティの追加）
    penalties = []
    
    # S0: 完全週の週休2日制約
    if params['s0_on']:
        for s in staff:
            for week_start in range(1, num_days + 1, 7):
                week_end = min(week_start + 6, num_days)
                if week_start == 1 and week_end == 7:  # 完全週
                    week_days = list(range(week_start, week_end + 1))
                    violation = model.NewBoolVar(f's0_violation_{s}_{week_start}')
                    model.Add(sum(shifts[(s, d)] for d in week_days) >= 6).OnlyEnforceIf(violation.Not())
                    model.Add(sum(shifts[(s, d)] for d in week_days) <= 5).OnlyEnforceIf(violation)
                    penalties.append(violation * params['s0_penalty'])
    
    # S1: 日曜人数目標制約
    if params['s1a_on']:
        # S1-a: PT+OT合計目標
        for d in sundays:
            pt_ot_count = sum(shifts[(s, d)] for s in pt_staff + ot_staff)
            target_total = params['target_pt'] + params['target_ot']
            deviation_pos = model.NewIntVar(0, len(pt_staff + ot_staff), f's1a_dev_pos_{d}')
            deviation_neg = model.NewIntVar(0, len(pt_staff + ot_staff), f's1a_dev_neg_{d}')
            model.Add(pt_ot_count == target_total + deviation_pos - deviation_neg)
            penalties.append((deviation_pos + deviation_neg) * params['s1a_penalty'])
    
    if params['s1b_on']:
        # S1-b: PT/OT個別目標（許容誤差考慮）
        for d in sundays:
            # PT目標
            pt_count = sum(shifts[(s, d)] for s in pt_staff)
            if params['tolerance'] > 0:
                pt_dev_pos = model.NewIntVar(0, len(pt_staff), f's1b_pt_dev_pos_{d}')
                pt_dev_neg = model.NewIntVar(0, len(pt_staff), f's1b_pt_dev_neg_{d}')
                model.Add(pt_count == params['target_pt'] + pt_dev_pos - pt_dev_neg)
                pt_penalty_pos = model.NewIntVar(0, len(pt_staff) * params['s1b_penalty'], f's1b_pt_penalty_pos_{d}')
                pt_penalty_neg = model.NewIntVar(0, len(pt_staff) * params['s1b_penalty'], f's1b_pt_penalty_neg_{d}')
                model.AddMaxEquality(pt_penalty_pos, [0, (pt_dev_pos - params['tolerance']) * params['s1b_penalty']])
                model.AddMaxEquality(pt_penalty_neg, [0, (pt_dev_neg - params['tolerance']) * params['s1b_penalty']])
                penalties.append(pt_penalty_pos + pt_penalty_neg)
            
            # OT目標
            ot_count = sum(shifts[(s, d)] for s in ot_staff)
            if params['tolerance'] > 0:
                ot_dev_pos = model.NewIntVar(0, len(ot_staff), f's1b_ot_dev_pos_{d}')
                ot_dev_neg = model.NewIntVar(0, len(ot_staff), f's1b_ot_dev_neg_{d}')
                model.Add(ot_count == params['target_ot'] + ot_dev_pos - ot_dev_neg)
                ot_penalty_pos = model.NewIntVar(0, len(ot_staff) * params['s1b_penalty'], f's1b_ot_penalty_pos_{d}')
                ot_penalty_neg = model.NewIntVar(0, len(ot_staff) * params['s1b_penalty'], f's1b_ot_penalty_neg_{d}')
                model.AddMaxEquality(ot_penalty_pos, [0, (ot_dev_pos - params['tolerance']) * params['s1b_penalty']])
                model.AddMaxEquality(ot_penalty_neg, [0, (ot_dev_neg - params['tolerance']) * params['s1b_penalty']])
                penalties.append(ot_penalty_pos + ot_penalty_neg)
    
    if params['s1c_on']:
        # S1-c: ST目標
        for d in sundays:
            st_count = sum(shifts[(s, d)] for s in st_staff)
            st_dev_pos = model.NewIntVar(0, len(st_staff), f's1c_dev_pos_{d}')
            st_dev_neg = model.NewIntVar(0, len(st_staff), f's1c_dev_neg_{d}')
            model.Add(st_count == params['target_st'] + st_dev_pos - st_dev_neg)
            penalties.append((st_dev_pos + st_dev_neg) * params['s1c_penalty'])
    
    # S2: 不完全週の週休1日制約
    if params['s2_on']:
        for s in staff:
            for week_start in range(1, num_days + 1, 7):
                week_end = min(week_start + 6, num_days)
                if not (week_start == 1 and week_end == 7):  # 不完全週
                    week_days = list(range(week_start, week_end + 1))
                    if len(week_days) >= 2:
                        violation = model.NewBoolVar(f's2_violation_{s}_{week_start}')
                        model.Add(sum(shifts[(s, d)] for d in week_days) <= len(week_days) - 1).OnlyEnforceIf(violation.Not())
                        model.Add(sum(shifts[(s, d)] for d in week_days) >= len(week_days)).OnlyEnforceIf(violation)
                        penalties.append(violation * params['s2_penalty'])
    
    # S3: 外来同時休制約
    if params['s3_on']:
        for d in weekdays:
            if len(gairai_staff) >= 2:
                working_gairai = sum(shifts[(s, d)] for s in gairai_staff)
                violation = model.NewBoolVar(f's3_violation_{d}')
                model.Add(working_gairai >= 1).OnlyEnforceIf(violation.Not())
                model.Add(working_gairai == 0).OnlyEnforceIf(violation)
                penalties.append(violation * params['s3_penalty'])
    
    # S4: 準希望休(△)尊重制約
    if params['s4_on']:
        for s, dates in requests_tri.items():
            for d in dates:
                penalties.append(shifts[(s, d)] * params['s4_penalty'])
    
    # S5: 回復期配置制約
    if params['s5_on']:
        for d in weekdays:
            kaifukuki_pt_working = sum(shifts[(s, d)] for s in kaifukuki_pt)
            kaifukuki_ot_working = sum(shifts[(s, d)] for s in kaifukuki_ot)
            
            pt_shortage = model.NewIntVar(0, len(kaifukuki_pt), f's5_pt_shortage_{d}')
            ot_shortage = model.NewIntVar(0, len(kaifukuki_ot), f's5_ot_shortage_{d}')
            
            model.AddMaxEquality(pt_shortage, [0, 4 - kaifukuki_pt_working])
            model.AddMaxEquality(ot_shortage, [0, 2 - kaifukuki_ot_working])
            
            penalties.append((pt_shortage + ot_shortage) * params['s5_penalty'])
    
    # S6: 業務負荷平準化制約
    if params['s6_on']:
        for d in weekdays:
            total_units = 0
            for s in staff:
                if staff_info[s]['職種'] in ['理学療法士', '作業療法士']:
                    daily_units = int(staff_info.get(s, {}).get('1日の単位数', 0))
                    # AM休・PM休・AM有・PM有の場合は半分の単位数
                    if d in requests_am_off.get(s, []) or d in requests_pm_off.get(s, []) or d in st.session_state.requests_half.get(s, []):
                        total_units += shifts[(s, d)] * daily_units * 0.5
                    else:
                        total_units += shifts[(s, d)] * daily_units
            
            total_units += params['event_units'].get(d, 0)
            
            target_units = 400
            deviation_pos = model.NewIntVar(0, 1000, f's6_dev_pos_{d}')
            deviation_neg = model.NewIntVar(0, 1000, f's6_dev_neg_{d}')
            model.Add(total_units == target_units + deviation_pos - deviation_neg)
            
            heavy_penalty_pos = model.NewIntVar(0, 1000 * params['s6_penalty_heavy'], f's6_heavy_pos_{d}')
            heavy_penalty_neg = model.NewIntVar(0, 1000 * params['s6_penalty_heavy'], f's6_heavy_neg_{d}')
            model.AddMaxEquality(heavy_penalty_pos, [0, (deviation_pos - 50) * params['s6_penalty_heavy']])
            model.AddMaxEquality(heavy_penalty_neg, [0, (deviation_neg - 50) * params['s6_penalty_heavy']])
            
            penalties.append(deviation_pos * params['s6_penalty'] + deviation_neg * params['s6_penalty'])
            penalties.append(heavy_penalty_pos + heavy_penalty_neg)
    
    # S7: 人数平準化制約
    if params['s7_on']:
        target_workers = 18
        for d in weekdays:
            total_workers = sum(shifts[(s, d)] for s in staff)
            deviation_pos = model.NewIntVar(0, len(staff), f's7_dev_pos_{d}')
            deviation_neg = model.NewIntVar(0, len(staff), f's7_dev_neg_{d}')
            model.Add(total_workers == target_workers + deviation_pos - deviation_neg)
            
            heavy_penalty_pos = model.NewIntVar(0, len(staff) * params['s7_penalty_heavy'], f's7_heavy_pos_{d}')
            heavy_penalty_neg = model.NewIntVar(0, len(staff) * params['s7_penalty_heavy'], f's7_heavy_neg_{d}')
            model.AddMaxEquality(heavy_penalty_pos, [0, (deviation_pos - 3) * params['s7_penalty_heavy']])
            model.AddMaxEquality(heavy_penalty_neg, [0, (deviation_neg - 3) * params['s7_penalty_heavy']])
            
            penalties.append(deviation_pos * params['s7_penalty'] + deviation_neg * params['s7_penalty'])
            penalties.append(heavy_penalty_pos + heavy_penalty_neg)
    
    # S8: 2回超日曜出勤制約
    if params['s8_on']:
        for s in staff:
            sunday_count = sum(shifts[(s, d)] for d in sundays)
            excess_sunday = model.NewIntVar(0, len(sundays), f's8_excess_{s}')
            model.AddMaxEquality(excess_sunday, [0, sunday_count - 2])
            penalties.append(excess_sunday * params['s8_penalty'])
    
    # 目的関数の設定
    model.Minimize(sum(penalties))
    
    # ソルバーの実行
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60.0
    status = solver.Solve(model)
    
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        shifts_values = {(s, d): solver.Value(shifts[(s, d)]) for s in staff for d in days}
        
        schedule_df = _create_schedule_df(
            shifts_values, staff, days, params['staff_df'], 
            requests_x, requests_tri, requests_paid, requests_special, requests_summer,
            requests_am_off, requests_pm_off, requests_must_work
        )
        
        # サマリー用の作業用DataFrame作成
        temp_work_df = schedule_df.copy()
        for col in temp_work_df.columns:
            if col not in ['職員番号', '職種']:
                temp_work_df[col] = temp_work_df[col].apply(
                    lambda x: '出' if x in ['', '○', '出', 'AM休', 'PM休'] else '休'
                )
        
        summary_df = _create_summary(temp_work_df, staff_info, year, month, params['event_units'])
        
        message = f"求解ステータス: **{solver.StatusName(status)}** (ペナルティ合計: **{round(solver.ObjectiveValue())}**)"
        return True, schedule_df, summary_df, message
    else:
        message = f"致命的なエラー: ハード制約が矛盾しているため、勤務表を作成できませんでした。({solver.StatusName(status)})"
        return False, pd.DataFrame(), pd.DataFrame(), message

# --- Streamlit UI ---
st.set_page_config(layout="wide")
st.title('リハビリテーション科 勤務表作成アプリ')

# デフォルト年月の設定
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
        with c2_1:
            target_pt = st.number_input("PT目標", min_value=0, value=10, step=1)
        with c2_2:
            target_ot = st.number_input("OT目標", min_value=0, value=5, step=1)
        with c2_3:
            target_st = st.number_input("ST目標", min_value=0, value=3, step=1)
        
        st.markdown("---")
        tolerance = st.number_input(
            "PT/OT許容誤差(±)", 
            min_value=0, max_value=5, value=1, 
            help="PT/OTの合計人数が目標通りなら、それぞれの人数がこの値までずれてもペナルティを課しません。"
        )
        tri_penalty_weight = st.slider(
            "準希望休(△)の優先度", 
            min_value=0, max_value=20, value=8, 
            help="値が大きいほど△希望が尊重されます。"
        )
    
    with c3:
        st.subheader("希望休の種類")
        st.markdown("**×**: 完全希望休　**△**: 準希望休　**○**: 必須出勤")
        st.markdown("**有**: 有給休暇　**特**: 特別休暇　**夏**: 夏期休暇")
        st.markdown("**AM有/PM有**: 半日有給（0.5人、単位数1/2）")
        st.markdown("**AM休/PM休**: 半日休み（0.5人、単位数1/2）")
    
    st.markdown("---")
    st.subheader(f"{year}年{month}月のイベント設定（各日の特別業務単位数を入力）")
    
    event_units_input = {}
    num_days_in_month = calendar.monthrange(year, month)[1]
    first_day_weekday = calendar.weekday(year, month, 1)
    
    # カレンダー形式で表示
    cal_cols = st.columns(7)
    weekdays_jp = ['月', '火', '水', '木', '金', '土', '日']
    for i, day_name in enumerate(weekdays_jp):
        cal_cols[i].markdown(f"<p style='text-align: center;'><b>{day_name}</b></p>", unsafe_allow_html=True)
    
    day_counter = 1
    for week in range(6):
        cols = st.columns(7)
        for day_of_week in range(7):
            if (week == 0 and day_of_week < first_day_weekday) or day_counter > num_days_in_month:
                continue
            
            with cols[day_of_week]:
                is_sunday = calendar.weekday(year, month, day_counter) == 6
                event_units_input[day_counter] = st.number_input(
                    label=f"{day_counter}日", 
                    value=0, 
                    step=10, 
                    disabled=is_sunday, 
                    key=f"event_{year}_{month}_{day_counter}"
                )
            day_counter += 1
        
        if day_counter > num_days_in_month:
            break
    
    st.markdown("---")
    create_button = st.button('勤務表を作成', type="primary", use_container_width=True)

# ルール検証モード
with st.expander("▼ ルール検証モード（上級者向け）"):
    st.warning("注意: 各ルールのON/OFFやペナルティ値を変更することで、意図しない結果や、解が見つからない状況が発生する可能性があります。")
    st.markdown("---")
    
    st.subheader("ハード制約のON/OFF")
    h_cols = st.columns(5)
    params = {}
    
    with h_cols[0]:
        params['h1_on'] = st.toggle('H1: 月間休日数', value=True, key='h1')
    with h_cols[1]:
        params['h2_on'] = st.toggle('H2: 希望休/有休', value=True, key='h2')
    with h_cols[2]:
        params['h3_on'] = st.toggle('H3: 役職者配置', value=True, key='h3')
    with h_cols[3]:
        params['h5_on'] = st.toggle('H5: 個人別日曜上限', value=True, key='h5')
    
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

# メイン処理
if create_button:
    if staff_file is not None and requests_file is not None:
        try:
            # パラメータの設定
            params['staff_df'] = pd.read_csv(staff_file)
            params['requests_df'] = pd.read_csv(requests_file)
            params['year'] = year
            params['month'] = month
            params['target_pt'] = target_pt
            params['target_ot'] = target_ot
            params['target_st'] = target_st
            params['tolerance'] = tolerance
            params['event_units'] = event_units_input
            
            # ソルバーの実行
            is_feasible, schedule_df, summary_df, message = solve_shift_model(params)
            
            st.info(message)
            
            if is_feasible:
                st.header("勤務表")
                
                # 表示用データの準備
                num_days = calendar.monthrange(year, month)[1]
                
                # サマリー情報を勤務表の下に追加するための処理
                temp_work_df = schedule_df.copy()
                for col in temp_work_df.columns:
                    if col not in ['職員番号', '職種']:
                        temp_work_df[col] = temp_work_df[col].apply(
                            lambda x: '出' if x in ['', '○', '出', 'AM休', 'PM休'] else '休'
                        )
                
                summary_T = _create_summary(
                    temp_work_df, 
                    params['staff_df'].set_index('職員番号').to_dict('index'), 
                    year, month, event_units_input
                ).drop(columns=['日', '曜日']).T
                
                summary_T.columns = list(range(1, num_days + 1))
                summary_processed = summary_T.reset_index().rename(columns={'index': '職員名'})
                summary_processed['職員番号'] = summary_processed['職員名'].apply(lambda x: f"_{x}")
                summary_processed['職種'] = "サマリー"
                summary_processed = summary_processed[['職員番号', '職種'] + list(range(1, num_days + 1))]
                
                # 勤務表とサマリーを結合
                final_df_for_display = pd.concat([schedule_df, summary_processed], ignore_index=True)
                
                # マルチインデックスヘッダーの作成
                days_header = list(range(1, num_days + 1))
                weekdays_header = [
                    ['月','火','水','木','金','土','日'][calendar.weekday(year, month, d)] 
                    for d in days_header
                ]
                final_df_for_display.columns = pd.MultiIndex.from_tuples(
                    [('職員情報', '職員番号'), ('職員情報', '職種')] + 
                    list(zip(days_header, weekdays_header))
                )
                
                # スタイリング関数
                def style_table(df):
                    sunday_cols = [col for col in df.columns if col[1] == '日']
                    styler = df.style.set_properties(**{'text-align': 'center'})
                    for col in sunday_cols:
                        styler = styler.set_properties(subset=[col], **{'background-color': '#fff0f0'})
                    return styler
                
                # Excelファイルの作成とダウンロードボタン
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    schedule_df.to_excel(writer, sheet_name='勤務表', index=False)
                    summary_df.to_excel(writer, sheet_name='日別サマリー', index=False)
                excel_data = output.getvalue()
                
                st.download_button(
                    label="📥 Excelでダウンロード",
                    data=excel_data,
                    file_name=f"schedule_{year}{month:02d}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
                # 勤務表の表示
                st.dataframe(style_table(final_df_for_display))
        
        except Exception as e:
            st.error(f'予期せぬエラーが発生しました: {e}')
            st.exception(e)
    else:
        st.warning('職員一覧と希望休一覧の両方のファイルをアップロードしてください。')

# フッター
st.markdown("---")
st.markdown(f"<div style='text-align: right; color: grey;'>{APP_CREDIT} | Version: {APP_VERSION}</div>", unsafe_allow_html=True)