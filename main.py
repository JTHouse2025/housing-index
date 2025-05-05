import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import streamlit as st
import os

# 配置
st.set_page_config(page_title="房价指数分析工具", layout="wide")
plt.rcParams['font.family'] = 'SimHei'
plt.rcParams['axes.unicode_minus'] = False

# ===== 参数 =====
SHEET_ID = "1_2_JhjiLFhHPekEmpHQTuWxW6Tre77cq"
SHEET_GID = "1"  # 默认是第一个工作表
SHEET_NAME = 'shanghai'  # 你原来代码里设的，备用
START_DATE = "2020-01-01"
END_DATE = "2025-03-31"
MAX_MOM_DIFF = 0.2

EXCEL_URL = "https://www.dropbox.com/scl/fi/0wuanen2lao6otdk7824b/City-transaction.xlsx?rlkey=efnyazyj7dejx496a3toscho5&dl=1"

# ===== 数据加载与清洗 =====
@st.cache_data
def load_raw_data():
    df = pd.read_excel(EXCEL_URL, sheet_name='shanghai', engine='openpyxl')
    df.columns = df.columns.str.strip()

    # 后续清洗照旧...
    df["成交时间"] = pd.to_datetime(df["成交时间"])
    df = df.query("@START_DATE <= 成交时间 <= @END_DATE")

    df["折价率"] = df["成交价"] / df["挂牌价"]
    df = df[(df["折价率"].between(0.5, 1.5)) | (df["折价率"].isna())]

    df["户型"] = df["房型"].str[:2]
    df["成交年"] = df["成交时间"].dt.year
    df["成交季"] = df["成交时间"].dt.quarter
    df["year_quarter"] = df["成交年"].astype(str) + '-' + df["成交季"].astype(str)

    df["建成年代"] = df["建成年代"].astype(str).str.extract(r'(\d{4})')[0]
    df["建成年代"] = pd.to_datetime(df["建成年代"], format="%Y", errors="coerce")
    current_year = pd.Timestamp.now().year
    df["房龄"] = current_year - df["建成年代"].dt.year
    bins = [0, 10, 15, 20, 25, float("inf")]
    labels = ["0-10年", "10-15年", "15-20年", "20-25年", "25年以上"]
    df["房龄段"] = pd.cut(df["房龄"], bins=bins, labels=labels, right=False)

    return df

@st.cache_data
def get_field_values(df, fields):
    result = {}
    for field in fields:
        result[field] = sorted(df[field].dropna().astype(str).unique().tolist())
    return result

# ===== 加载数据并提取字段选项 =====

df_raw = load_raw_data()
group_fields = ['区县', '环线', '板块', '房龄段']
field_values = get_field_values(df_raw, group_fields)

# ===== 前端字段选择区域 =====

st.title("🏠 上海房价指数分析工具")
st.sidebar.header("字段筛选")

selected_fields = st.sidebar.multiselect("选择分组字段", group_fields, default=['环线'])

filters = {}
for field in selected_fields:
    values = field_values.get(field, [])
    selected = st.sidebar.multiselect(f"选择【{field}】值", values, default=[], key=f"filter_{field}")
    if selected:
        filters[field] = selected

if st.sidebar.button("🔄 重置分析"):
    st.session_state.clear()
    st.experimental_rerun()

# ===== 分析按钮入口 =====

if st.button("开始分析"):
    df = df_raw.copy()
    for field, selected_values in filters.items():
        df = df[df[field].astype(str).isin(selected_values)]

    if df.empty:
        st.warning("⚠️ 筛选后无有效数据")
        st.stop()

    st.success(f"已选样本量：{len(df)} 条")

    # ===== 分析函数区域 =====

    def aggregate_by_quarter(data, group_cols):
        df_agg = data.groupby(group_cols + ['小区', '户型', 'year_quarter'], observed=False).agg(
            成交总额=('成交价', 'sum'),
            成交面积=('面积', 'sum'),
            成交套数=('bk_id', 'count')
        ).reset_index()
        return df_agg[df_agg['成交套数'] > 1]

    def filter_small_groups(df_agg, min_size=2):
        return df_agg.groupby(['小区', '户型']).filter(lambda x: len(x) >= min_size)

    def pivot_and_calc_ratios(df, group_cols):
        df_pivot = df.pivot(index=group_cols + ['小区', '户型'], columns='year_quarter')
        df_pivot.columns = [f"{q}_{col}" for col, q in df_pivot.columns]
        df_pivot = df_pivot.reset_index()

        quarters = sorted(set(c.split('_')[0] for c in df_pivot.columns if '成交总额' in c))
        quarters.sort(key=lambda x: (int(x.split('-')[0]), int(x.split('-')[1])))

        for q in quarters:
            amt_col = f"{q}_成交总额"
            area_col = f"{q}_成交面积"
            avg_col = f"{q}_成交均价"
            if amt_col in df_pivot and area_col in df_pivot:
                df_pivot[avg_col] = df_pivot[amt_col] / df_pivot[area_col]

        for i in range(1, len(quarters)):
            prev, curr = quarters[i - 1], quarters[i]
            p_avg, c_avg = f"{prev}_成交均价", f"{curr}_成交均价"
            mom_col = f"{curr}_成交均价环比"
            if p_avg in df_pivot and c_avg in df_pivot:
                df_pivot[mom_col] = (
                    df_pivot[c_avg] / df_pivot[p_avg] - 1
                ).where((df_pivot[c_avg] / df_pivot[p_avg] - 1).abs() <= MAX_MOM_DIFF)

        return df_pivot, quarters

    def calc_count_ratio(df, quarters):
        df = df.copy()
        for q in quarters:
            mom_col = f"{q}_成交均价环比"
            count_col = f"{q}_成交套数"
            ratio_col = f"{q}_成交套数占比"
            if mom_col in df.columns and count_col in df.columns:
                df.loc[df[mom_col].isna(), count_col] = np.nan
                valid_mask = df[mom_col].notna()
                total = df.loc[valid_mask, count_col].sum()
                df[ratio_col] = np.nan
                if total > 0:
                    df.loc[valid_mask, ratio_col] = df.loc[valid_mask, count_col] / total
        return df

    def calc_weighted_price_index(df, quarters):
        ratios, index_vals = [], [100]
        for q in quarters[1:]:
            mom_col = f"{q}_成交均价环比"
            ratio_col = f"{q}_成交套数占比"
            if mom_col in df.columns and ratio_col in df.columns:
                valid_df = df[~df[mom_col].isna()]
                weighted_sum = (valid_df[mom_col] * valid_df[ratio_col]).sum()
                total_weight = valid_df[ratio_col].sum()
                ratio = weighted_sum / total_weight if total_weight > 0 else np.nan
            else:
                ratio = np.nan
            ratios.append(ratio)
            index_vals.append(index_vals[-1] * (1 + ratio) if not np.isnan(ratio) else np.nan)
        return ratios, index_vals

    def calc_decline_ratios(df, quarters):
        ratios = []
        for q in quarters[1:]:
            mom_col = f"{q}_成交均价环比"
            count_col = f"{q}_成交套数"
            if mom_col in df.columns and count_col in df.columns:
                down_mask = df[mom_col] < 0
                total = df[count_col].sum()
                down_count = df.loc[down_mask, count_col].sum()
                ratios.append(down_count / total if total > 0 else np.nan)
            else:
                ratios.append(np.nan)
        return ratios

    # ===== 分析流程 =====

    df_agg = aggregate_by_quarter(df, selected_fields)
    df_filtered = filter_small_groups(df_agg)
    df_pivot, quarters = pivot_and_calc_ratios(df_filtered, selected_fields)
    df_ratio = calc_count_ratio(df_pivot, quarters)
    mom_ratios, index_vals = calc_weighted_price_index(df_ratio, quarters)
    decline_vals = calc_decline_ratios(df_ratio, quarters)

    # 分组分析
    group_results = {}
    group_values = df[selected_fields].dropna().drop_duplicates()
    for _, row in group_values.iterrows():
        label = ' / '.join(str(row[col]) for col in selected_fields)
        condition = np.logical_and.reduce([df[col] == row[col] for col in selected_fields])
        group_df = df[condition]
        sub_agg = aggregate_by_quarter(group_df, selected_fields)
        sub_filtered = filter_small_groups(sub_agg)
        sub_pivot, _ = pivot_and_calc_ratios(sub_filtered, selected_fields)
        sub_ratio = calc_count_ratio(sub_pivot, quarters)
        sub_index = calc_weighted_price_index(sub_ratio, quarters)[1]
        sub_decline = calc_decline_ratios(sub_ratio, quarters)
        group_results[label] = (sub_index, sub_decline)

    # ===== 图表展示 =====

    st.subheader("📈 图表结果")
    quarters_label = [f"{q.split('-')[0]}Q{q.split('-')[1]}" for q in quarters[1:]]

    fig1, ax1 = plt.subplots(figsize=(10, 5))
    ax1.plot(quarters_label, index_vals[1:], marker='o', linewidth=2, label='全市')
    for label, (idx_vals, _) in group_results.items():
        ax1.plot(quarters_label, idx_vals[1:], linestyle='--', marker='s', label=label)
    ax1.axhline(y=100, linestyle='--', color='gray')
    ax1.set_title("价格指数走势（全市 + 分组）")
    ax1.set_ylabel("价格指数")
    ax1.legend()
    ax1.grid(True)
    st.pyplot(fig1)

    fig2, ax2 = plt.subplots(figsize=(10, 5))
    ax2.plot(quarters_label, decline_vals, marker='x', color='r', label='全市')
    for label, (_, decline_vals_sub) in group_results.items():
        ax2.plot(quarters_label, decline_vals_sub, linestyle='--', marker='o', label=label)
    ax2.axhline(y=0, linestyle='--', color='gray')
    ax2.set_title("环比下跌成交占比走势")
    ax2.set_ylabel("占比")
    ax2.legend()
    ax2.grid(True)
    st.pyplot(fig2)


