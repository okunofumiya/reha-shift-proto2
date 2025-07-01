import streamlit as st
import pandas as pd
import numpy as np
from ortools.sat.python import cp_model
import calendar
import io
from datetime import datetime
from dateutil.relativedelta import relativedelta

# ★★★ バージョン情報 ★★★
APP_VERSION = "proto.2.6.1" # パフォーマンス調整機能反映
APP_CREDIT = "Okuno with 🤖 Gemini and Claude"

# --- ヘルパー関数: サマリー作成 ---
def _create_summary(schedule_df, staff_info_dict, year, month, event_units):
    num_days = calendar.monthrange(year, month)[1]; days = list(range(1, num_days + 1)); daily_summary = []
    for d in days:
        day_info = {}; 
        work_staff_ids = schedule_df[(schedule_df[d] == '') | (schedule_df[d] == '○') | (schedule_df[d] == '出') | (schedule_df[d] == 'AM休') | (schedule_df[d] == 'PM休')]['職員番号']
        half_day_staff_ids = [s for s, dates in st.session_state.get('requests_half', {}).items() if d in dates]
        am_off_staff_ids = [s for s, dates in st.session_state.get('requests_am_off', {}).items() if d in dates]
        pm_off_staff_ids = [s for s, dates in st.session_state.get('requests_pm_off', {}).items() if d in dates]
        total_workers = 0
        for sid in work_staff_ids:
            if sid in half_day_staff_ids or sid in am_off_staff_ids or sid in pm_off_staff_ids:
                total_workers += 0.5
            else:
                total_workers += 1
        day_info['日'] = d; day_info['曜日'] = ['月','火','水','木','金','土','日'][calendar.weekday(year, month, d)]
        day_info['出勤者総数'] = total_workers
        day_info['PT'] = sum(0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 for sid in work_staff_ids if staff_info_dict.get(sid, {}).get('職種') == '理学療法士')
        day_info['OT'] = sum(0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 for sid in work_staff_ids if staff_info_dict.get(sid, {}).get('職種') == '作業療法士')
        day_info['ST'] = sum(0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 for sid in work_staff_ids if staff_info_dict.get(sid, {}).get('職種') == '言語聴覚士')
        day_info['役職者'] = sum(0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 for sid in work_staff_ids if pd.notna(staff_info_dict.get(sid, {}).get('役職')))
        day_info['回復期'] = sum(0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 for sid in work_staff_ids if staff_info_dict.get(sid, {}).get('役割1') == '回復期専従')
        day_info['地域包括'] = sum(0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 for sid in work_staff_ids if staff_info_dict.get(sid, {}).get('役割1') == '地域包括専従')
        day_info['外来'] = sum(0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1 for sid in work_staff_ids if staff_info_dict.get(sid, {}).get('役割1') == '外来PT')
        if calendar.weekday(year, month, d) != 6:
            pt_units = sum(int(staff_info_dict.get(sid, {}).get('1日の単位数', 0)) * (0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1) for sid in work_staff_ids if staff_info_dict.get(sid, {}).get('職種') == '理学療法士')
            ot_units = sum(int(staff_info_dict.get(sid, {}).get('1日の単位数', 0)) * (0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1) for sid in work_staff_ids if staff_info_dict.get(sid, {}).get('職種') == '作業療法士')
            st_units = sum(int(staff_info_dict.get(sid, {}).get('1日の単位数', 0)) * (0.5 if sid in (half_day_staff_ids + am_off_staff_ids + pm_off_staff_ids) else 1) for sid in work_staff_ids if staff_info_dict.get(sid, {}).get('職種') == '言語聴覚士')
            day_info['PT単位数'] = pt_units; day_info['OT単位数'] = ot_units; day_info['ST単位数'] = st_units
            day_info['PT+OT単位数'] = pt_units + ot_units; day_info['特別業務単位数'] = event_units.get(d, 0)
        else:
            day_info['PT単位数'] = '-'; day_info['OT単位数'] = '-'; day_info['ST単位数'] = '-';
            day_info['PT+OT単位数'] = '-'; day_info['特別業務単位数'] = '-'
        daily_summary.append(day_info)
    return pd.DataFrame(daily_summary)

def _create_schedule_df(shifts_values, staff, days, staff_df, requests_x, requests_tri, requests_paid, requests_special, requests_summer, requests_am_off, requests_pm_off, requests_must_work):
    schedule_data = {}
    for s in staff:
        row = []; s_requests_x = requests_x.get(s, []); s_requests_tri = requests_tri.get(s, []); s_requests_paid = requests_paid.get(s, []); s_requests_special = requests_special.get(s, []); s_requests_summer = requests_summer.get(s, []); s_requests_am_off = requests_am_off.get(s, []); s_requests_pm_off = requests_pm_off.get(s, []); s_requests_must = requests_must_work.get(s, [])
        for d in days:
            if shifts_values.get((s, d), 0) == 0:
                if d in s_requests_x: row.append('×')
                elif d in s_requests_tri: row.append('△')
                elif d in s_requests_paid: row.append('有')
                elif d in s_requests_special: row.append('特')
                elif d in s_requests_summer: row.append('夏')
                else: row.append('-')
            else:
                if d in s_requests_must: row.append('○')
                elif d in s_requests_tri: row.append('出')
                elif d in s_requests_am_off: row.append('AM休')
                elif d in s_requests_pm_off: row.append('PM休')
                else: row.append('')
        schedule_data[s] = row
    schedule_df = pd.DataFrame.from_dict(schedule_data, orient='index', columns=days)
    schedule_df = schedule_df.reset_index().rename(columns={'index': '職員番号'})
    staff_map = staff_df.set_index('職員番号')
    schedule_df.insert(1, '職種', schedule_df['職員番号'].map(staff_map['職種']))
    return schedule_df

def _calculate_penalty_breakdown(shifts_values, params):
    return {} # Placeholder, as this function is complex and might need debugging later.

def solve_three_patterns(staff_df, requests_df, year, month, 
                         target_pt, target_ot, target_st, tolerance,
                         event_units, tri_penalty_weight, min_distance_N,
                         unit_penalty_multiplier, suppress_consecutive_diff):
    num_days = calendar.monthrange(year, month)[1]; days = list(range(1, num_days + 1)); staff = staff_df['職員番号'].tolist()
    staff_info = staff_df.set_index('職員番号').to_dict('index')
    sundays = [d for d in days if calendar.weekday(year, month, d) == 6]; weekdays = [d for d in days if d not in sundays]
    managers = [s for s in staff if pd.notna(staff_info.get(s, {}).get('役職'))]; pt_staff = [s for s in staff if staff_info.get(s, {}).get('職種') == '理学療法士']
    ot_staff = [s for s in staff if staff_info.get(s, {}).get('職種') == '作業療法士']; st_staff = [s for s in staff if staff_info.get(s, {}).get('職種') == '言語聴覚士']
    kaifukuki_staff = [s for s in staff if staff_info.get(s, {}).get('役割1') == '回復期専従']; kaifukuki_pt = [s for s in kaifukuki_staff if staff_info.get(s, {}).get('職種') == '理学療法士']
    kaifukuki_ot = [s for s in kaifukuki_staff if staff_info.get(s, {}).get('職種') == '作業療法士']; gairai_staff = [s for s in staff if staff_info.get(s, {}).get('役割1') == '外来PT']
    chiiki_staff = [s for s in staff if staff_info.get(s, {}).get('役割1') == '地域包括専従']; sunday_off_staff = gairai_staff + chiiki_staff
    requests_x = {}; requests_tri = {}; requests_must_work = {};
    requests_paid = {}; requests_special = {}; st.session_state.requests_half = {};
    requests_summer = {}; st.session_state.requests_am_off = {}; st.session_state.requests_pm_off = {};
    for index, row in requests_df.iterrows():
        staff_id = row['職員番号']
        if staff_id not in staff: continue
        requests_x[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '×']; requests_tri[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '△']
        requests_must_work[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '○']; requests_paid[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '有']
        requests_special[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '特']; requests_summer[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == '夏']
        st.session_state.requests_half[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) in ['AM有', 'PM有']]; st.session_state.requests_am_off[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == 'AM休']; st.session_state.requests_pm_off[staff_id] = [d for d in days if str(d) in row and row.get(str(d)) == 'PM休']
    
    def build_model(add_distance_constraint=False, base_solution=None, high_flat_penalty=False):
        model = cp_model.CpModel(); shifts = {(s, d): model.NewBoolVar(f'shift_{s}_{d}') for s in staff for d in days}
        for s in staff:
            num_paid = len(requests_paid.get(s, [])); num_special = len(requests_special.get(s, [])); num_summer = len(requests_summer.get(s,[])); num_am_off = len(st.session_state.requests_am_off.get(s,[])); num_pm_off = len(st.session_state.requests_pm_off.get(s,[]))
            model.Add(sum(1 - shifts[(s, d)] for d in days) == int(9 + num_paid + num_special + num_summer + 0.5 * (num_am_off + num_pm_off)))
        for s, dates in requests_must_work.items():
            for d in dates: model.Add(shifts[(s, d)] == 1)
        for s, dates in st.session_state.requests_half.items():
            for d in dates: model.Add(shifts[(s, d)] == 1)
        for s, dates in st.session_state.requests_am_off.items():
            for d in dates: model.Add(shifts[(s, d)] == 1)
        for s, dates in st.session_state.requests_pm_off.items():
            for d in dates: model.Add(shifts[(s, d)] == 1)
        for s, dates in requests_x.items():
            for d in dates: model.Add(shifts[(s, d)] == 0)
        for s, dates in requests_paid.items():
            for d in dates: model.Add(shifts[(s, d)] == 0)
        for s, dates in requests_special.items():
            for d in dates: model.Add(shifts[(s, d)] == 0)
        for s, dates in requests_summer.items():
            for d in dates: model.Add(shifts[(s, d)] == 0)
        for d in days: model.Add(sum(shifts[(s, d)] for s in managers) >= 1)
        for s in staff: model.Add(sum(shifts[(s, d)] for d in sundays) <= 2)
        penalties = []
        for s, req_days in requests_tri.items():
            if s in staff:
                for d in req_days: penalties.append(tri_penalty_weight * shifts[(s, d)])
        weeks_in_month = []; current_week = []
        for d in days:
            current_week.append(d)
            if calendar.weekday(year, month, d) == 5 or d == num_days: weeks_in_month.append(current_week); current_week = []
        for s_idx, s in enumerate(staff):
            all_requests = requests_x.get(s, []) + requests_tri.get(s, []) + requests_paid.get(s, []) + requests_special.get(s, []) + requests_summer.get(s,[])
            for w_idx, week in enumerate(weeks_in_month):
                if sum(1 for d in week if d in all_requests) >= 3: continue
                num_holidays_in_week = sum(1 - shifts[(s, d)] for d in week)
                if len(week) == 7:
                    violation = model.NewBoolVar(f'f_w_v_s{s_idx}_w{w_idx}'); model.Add(num_holidays_in_week < 2).OnlyEnforceIf(violation); model.Add(num_holidays_in_week >= 2).OnlyEnforceIf(violation.Not()); penalties.append(200 * violation)
                else:
                    violation = model.NewBoolVar(f'p_w_v_s{s_idx}_w{w_idx}'); model.Add(num_holidays_in_week < 1).OnlyEnforceIf(violation); model.Add(num_holidays_in_week >= 1).OnlyEnforceIf(violation.Not()); penalties.append(25 * violation)
        for d in sundays:
            pt_on_sunday = sum(shifts[(s, d)] for s in pt_staff); ot_on_sunday = sum(shifts[(s, d)] for s in ot_staff); st_on_sunday = sum(shifts[(s, d)] for s in st_staff)
            total_pt_ot = pt_on_sunday + ot_on_sunday; total_diff = model.NewIntVar(-50, 50, f't_d_{d}'); model.Add(total_diff == total_pt_ot - (target_pt + target_ot)); abs_total_diff = model.NewIntVar(0, 50, f'a_t_d_{d}'); model.AddAbsEquality(abs_total_diff, total_diff); penalties.append(50 * abs_total_diff)
            pt_diff = model.NewIntVar(-30, 30, f'p_d_{d}'); model.Add(pt_diff == pt_on_sunday - target_pt); pt_penalty = model.NewIntVar(0, 30, f'p_p_{d}'); model.Add(pt_penalty >= pt_diff - tolerance); model.Add(pt_penalty >= -pt_diff - tolerance); penalties.append(40 * pt_penalty)
            ot_diff = model.NewIntVar(-30, 30, f'o_d_{d}'); model.Add(ot_diff == ot_on_sunday - target_ot); ot_penalty = model.NewIntVar(0, 30, f'o_p_{d}'); model.Add(ot_penalty >= ot_diff - tolerance); model.Add(ot_penalty >= -ot_diff - tolerance); penalties.append(40 * ot_penalty)
            st_diff = model.NewIntVar(-10, 10, f's_d_{d}'); model.Add(st_diff == st_on_sunday - target_st); abs_st_diff = model.NewIntVar(0, 10, f'a_s_d_{d}'); model.AddAbsEquality(abs_st_diff, st_diff); penalties.append(60 * abs_st_diff)
        for d in days:
            num_gairai_off = sum(1 - shifts[(s, d)] for s in gairai_staff); penalty = model.NewIntVar(0, len(gairai_staff), f'g_p_{d}'); model.Add(penalty >= num_gairai_off - 1); penalties.append(10 * penalty)
            model.Add(sum(shifts[(s, d)] for s in kaifukuki_staff) >= 1); pt_present = model.NewBoolVar(f'k_p_p_{d}'); ot_present = model.NewBoolVar(f'k_o_p_{d}'); model.Add(sum(shifts[(s, d)] for s in kaifukuki_pt) >= 1).OnlyEnforceIf(pt_present); model.Add(sum(shifts[(s, d)] for s in kaifukuki_pt) == 0).OnlyEnforceIf(pt_present.Not()); model.Add(sum(shifts[(s, d)] for s in kaifukuki_ot) >= 1).OnlyEnforceIf(ot_present); model.Add(sum(shifts[(s, d)] for s in kaifukuki_ot) == 0).OnlyEnforceIf(ot_present.Not()); penalties.append(5 * (1 - pt_present)); penalties.append(5 * (1 - ot_present))
        
        unit_penalty_weight = round(2 * unit_penalty_multiplier) if not high_flat_penalty else round(4 * unit_penalty_multiplier)
        staff_penalty_weight = round(1 * unit_penalty_multiplier) if not high_flat_penalty else round(3 * unit_penalty_multiplier)

        job_types = {'PT': pt_staff, 'OT': ot_staff, 'ST': st_staff};
        for job, members in job_types.items():
            if not members: continue
            expected_work_days = sum(num_days - 9 - len(requests_paid.get(s,[])) - len(requests_special.get(s,[])) for s in members)
            target_per_weekday = expected_work_days / len(weekdays) if weekdays else 0
            for d in weekdays:
                actual = sum(shifts[(s, d)] for s in members); diff = model.NewIntVar(-len(members), len(members), f'd_{job}_{d}'); model.Add(diff == actual - round(target_per_weekday)); abs_diff = model.NewIntVar(0, len(members), f'a_d_{job}_{d}'); model.AddAbsEquality(abs_diff, diff); penalties.append(staff_penalty_weight * abs_diff)
        
        total_expected_units = sum(int(staff_info.get(s,{}).get('1日の単位数',0)) * (len(weekdays) / (len(weekdays)+len(sundays))) * (len(days) - 9 - len(requests_paid.get(s,[])) - len(requests_special.get(s,[])) - 0.5 * (len(st.session_state.requests_am_off.get(s,[]))+len(st.session_state.requests_pm_off.get(s,[])) ) ) for s in staff)
        total_event_units = sum(event_units.values()); avg_residual_units = (total_expected_units - total_event_units) / len(weekdays) if weekdays else 0
        for d in weekdays:
            provided_units = sum(shifts[(s, d)] * int(staff_info.get(s,{}).get('1日の単位数',0)) * (0.5 if s in st.session_state.requests_half.get(s, []) and d in st.session_state.requests_half[s] else 1.0) for s in staff); event_unit = event_units.get(d, 0); residual_units = model.NewIntVar(-2000, 2000, f'r_{d}'); model.Add(residual_units == provided_units - event_unit); diff = model.NewIntVar(-2000, 2000, f'u_d_{d}'); model.Add(diff == residual_units - round(avg_residual_units)); abs_diff = model.NewIntVar(0, 2000, f'a_u_d_{d}'); model.AddAbsEquality(abs_diff, diff); penalties.append(unit_penalty_weight * abs_diff)
        
        if suppress_consecutive_diff:
            for i in range(len(weekdays) - 1):
                d1, d2 = weekdays[i], weekdays[i + 1]
                units_d1 = sum(shifts[(s, d1)] * int(staff_info.get(s,{}).get('1日の単位数',0)) * (0.5 if s in st.session_state.requests_half.get(s, []) and d1 in st.session_state.requests_half.get(s,[]) else 1.0) for s in staff)
                units_d2 = sum(shifts[(s, d2)] * int(staff_info.get(s,{}).get('1日の単位数',0)) * (0.5 if s in st.session_state.requests_half.get(s, []) and d2 in st.session_state.requests_half.get(s,[]) else 1.0) for s in staff)
                consecutive_diff = model.NewIntVar(-2000, 2000, f'cd_{d1}_{d2}'); model.Add(consecutive_diff == units_d1 - units_d2)
                abs_consecutive_diff = model.NewIntVar(0, 2000, f'acd_{d1}_{d2}'); model.AddAbsEquality(abs_consecutive_diff, consecutive_diff)
                penalties.append(10 * abs_consecutive_diff)

        if add_distance_constraint and base_solution:
            diff_vars = []
            for s in staff:
                for d in days:
                    diff_var = model.NewBoolVar(f'diff_{s}_{d}'); model.Add(shifts[(s, d)] != base_solution.get((s, d), 0)).OnlyEnforceIf(diff_var); model.Add(shifts[(s, d)] == base_solution.get((s, d), 0)).OnlyEnforceIf(diff_var.Not()); diff_vars.append(diff_var)
            model.Add(sum(diff_vars) >= min_distance_N)
        
        model.Minimize(sum(penalties))
        return model, shifts
    
    params = locals()
    params['job_types'] = {'PT': pt_staff, 'OT': ot_staff, 'ST': st_staff}
    params['weeks_in_month'] = []
    current_week = [];
    for d in days:
        current_week.append(d)
        if calendar.weekday(year, month, d) == 5 or d == num_days: params['weeks_in_month'].append(current_week); current_week = []
    total_expected_units_for_avg = sum(int(staff_info.get(s,{}).get('1日の単位数',0)) * (len(weekdays) / (len(weekdays)+len(sundays))) * (len(days) - 9 - len(requests_paid.get(s,[])) - len(requests_special.get(s,[])) - 0.5 * (len(st.session_state.requests_am_off.get(s,[]))+len(st.session_state.requests_pm_off.get(s,[])) ) ) for s in staff)
    params['avg_residual_units'] = (total_expected_units_for_avg - sum(event_units.values())) / len(weekdays) if weekdays else 0
    params['target_staff_weekday'] = {job: sum(len(days) - 9 - len(requests_paid.get(s,[])) - len(requests_special.get(s,[])) - 0.5 * (len(st.session_state.requests_am_off.get(s,[]))+len(st.session_state.requests_pm_off.get(s,[]))) for s in members) / len(weekdays) if weekdays else 0 for job, members in params['job_types'].items() if members}
    params['requests_x'] = requests_x; params['requests_tri'] = requests_tri; params['requests_paid'] = requests_paid; params['requests_special'] = requests_special; params['requests_summer'] = requests_summer
    params['requests_half'] = st.session_state.requests_half; params['requests_am_off'] = st.session_state.requests_am_off; params['requests_pm_off'] = st.session_state.requests_pm_off; params['requests_must_work'] = requests_must_work
    
    results = []
    base_solution_values = None
    with st.spinner("パターン1 (最適解) を探索中..."):
        model1, shifts1 = build_model()
        solver1 = cp_model.CpSolver(); solver1.parameters.max_time_in_seconds = 20.0; status1 = solver1.Solve(model1)
    if status1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return False, [], "致命的エラー: 勤務表を作成できませんでした。ハード制約が矛盾している可能性があります。", None
    base_solution_values = {(s, d): solver1.Value(shifts1[(s, d)]) for s in staff for d in days}
    result1 = {"title": "勤務表パターン1", "status": solver1.StatusName(status1), "penalty": round(solver1.ObjectiveValue())}
    result1["schedule_df"] = _create_schedule_df(base_solution_values, staff, days, staff_df, requests_x, requests_tri, requests_paid, requests_special, requests_summer, st.session_state.requests_am_off, st.session_state.requests_pm_off, requests_must_work)
    params1 = params.copy()
    params1['unit_penalty_weight'] = round(2 * unit_penalty_multiplier); params1['staff_penalty_weight'] = round(1 * unit_penalty_multiplier)
    result1["breakdown"] = _calculate_penalty_breakdown(base_solution_values, params1)
    results.append(result1)
    
    with st.spinner(f"パターン2 (パターン1と{min_distance_N}マス以上違う解) を探索中..."):
        model2, shifts2 = build_model(add_distance_constraint=True, base_solution=base_solution_values)
        solver2 = cp_model.CpSolver(); solver2.parameters.max_time_in_seconds = 20.0; status2 = solver2.Solve(model2)
    if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        solution2_values = {(s, d): solver2.Value(shifts2[(s, d)]) for s in staff for d in days}
        result2 = {"title": "勤務表パターン2", "status": solver2.StatusName(status2), "penalty": round(solver2.ObjectiveValue())}
        result2["schedule_df"] = _create_schedule_df(solution2_values, staff, days, staff_df, requests_x, requests_tri, requests_paid, requests_special, requests_summer, st.session_state.requests_am_off, st.session_state.requests_pm_off, requests_must_work)
        result2["breakdown"] = _calculate_penalty_breakdown(solution2_values, params1)
        results.append(result2)

    with st.spinner("パターン3 (平準化重視) を探索中..."):
        model3, shifts3 = build_model(high_flat_penalty=True)
        solver3 = cp_model.CpSolver(); solver3.parameters.max_time_in_seconds = 20.0; status3 = solver3.Solve(model3)
    if status3 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        solution3_values = {(s, d): solver3.Value(shifts3[(s, d)]) for s in staff for d in days}
        result3 = {"title": "パターン3 (平準化重視)", "status": solver3.StatusName(status3), "penalty": round(solver3.ObjectiveValue())}
        result3["schedule_df"] = _create_schedule_df(solution3_values, staff, days, staff_df, requests_x, requests_tri, requests_paid, requests_special, requests_summer, st.session_state.requests_am_off, st.session_state.requests_pm_off, requests_must_work)
        params3 = params.copy()
        params3['unit_penalty_weight'] = round(4 * unit_penalty_multiplier); params3['staff_penalty_weight'] = round(3 * unit_penalty_multiplier)
        result3["breakdown"] = _calculate_penalty_breakdown(solution3_values, params3)
        results.append(result3)
    
    return True, results, f"{len(results)}パターンの探索が完了しました。", requests_must_work

def display_result(result_data, staff_info, event_units, year, month):
    st.header(result_data['title'])
    st.info(f"求解ステータス: **{result_data['status']}** | ペナルティ合計: **{result_data['penalty']}**")
    if "breakdown" in result_data:
        with st.expander("ペナルティの内訳を表示"):
            breakdown_df = pd.DataFrame(result_data['breakdown'].items(), columns=['ルール', 'ペナルティ点'])
            st.dataframe(breakdown_df, hide_index=True)
    schedule_df = result_data["schedule_df"]
    temp_work_df = schedule_df.copy()
    for col in temp_work_df.columns:
        if col not in ['職員番号', '職種']:
            temp_work_df[col] = temp_work_df[col].apply(lambda x: '出' if x in ['', '○', '出', 'AM休', 'PM休'] else '休')
    summary_df = _create_summary(schedule_df, staff_info, year, month, event_units)
    num_days = calendar.monthrange(year, month)[1]
    summary_T = summary_df.drop(columns=['日', '曜日']).T
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
    st.download_button(label=f"📥 {result_data['title']} をExcelでダウンロード", data=excel_data, file_name=f"schedule_{result_data['title']}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=result_data['title'])
    st.dataframe(style_table(final_df_for_display))

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
        min_distance = st.number_input("パターン2の最低相違マス数(N)", min_value=1, value=50, step=10, help="パターン1と最低でもこれだけ違うマスを持つパターン2を探します。")
    st.markdown("---")
    st.subheader("平準化の強度と詳細設定")
    c4, c5 = st.columns(2)
    with c4:
        unit_penalty_multiplier = st.slider("業務負荷・人数平滑化の強度", min_value=1.0, max_value=10.0, value=2.0, step=0.5, help="値を大きくすると、日々の業務量や人数の差が小さくなります。")
    with c5:
        suppress_consecutive_diff = st.checkbox("【検証用】隣接日の変動を強く抑制する", value=False, help="ONにすると計算時間が長くなりますが、日々の業務量の変動がより滑らかになります。")
    
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

if create_button:
    if staff_file is not None and requests_file is not None:
        try:
            staff_df = pd.read_csv(staff_file); requests_df = pd.read_csv(requests_file)
            if '職員名' not in staff_df.columns:
                st.info("職員名列は使用しません。職員番号で個人を識別します。")
            
            is_feasible, results, message, requests_must_work = solve_three_patterns(
                staff_df, requests_df, year, month,
                target_pt, target_ot, target_st, tolerance,
                event_units_input, tri_penalty_weight, min_distance,
                unit_penalty_multiplier, suppress_consecutive_diff
            )
            
            st.success(message)
            if is_feasible:
                staff_info = staff_df.set_index('職員番号').to_dict('index')
                num_results = len(results)
                if num_results > 0:
                    cols = st.columns(num_results)
                    for i, res in enumerate(results):
                        with cols[i]:
                            display_result(res, staff_info, event_units_input, year, month)
        except Exception as e:
            st.error(f'予期せぬエラーが発生しました: {e}')
            st.exception(e)
    else:
        st.warning('職員一覧と希望休一覧の両方のファイルをアップロードしてください。')
st.markdown("---")
st.markdown(f"<div style='text-align: right; color: grey;'>{APP_CREDIT} | Version: {APP_VERSION}</div>", unsafe_allow_html=True)