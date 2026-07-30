import streamlit as st
import pandas as pd
import json
import os
from datetime import date, datetime

st.set_page_config(page_title="商品生命週期與銷售儀表板", page_icon="📊", layout="wide")

HERE = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────
# 資料載入（原型階段先讀本地 JSON，之後可以換成讀 Google Sheets）
# ─────────────────────────────────────────────
@st.cache_data
def load_data():
    with open(os.path.join(HERE, "cert_data.json"), encoding="utf-8") as f:
        cert_by_outdoor = json.load(f)
    with open(os.path.join(HERE, "sales_data.json"), encoding="utf-8") as f:
        sales_records = json.load(f)
    return cert_by_outdoor, sales_records

cert_by_outdoor, sales_records = load_data()

sales_df = pd.DataFrame(sales_records)
sales_df["銷售量"] = pd.to_numeric(sales_df["銷售量"], errors="coerce").fillna(0)

# 依型號彙總銷售量（全部期間）
agg = sales_df.groupby("室外機型號", dropna=True)["銷售量"].sum().reset_index()
agg = agg.rename(columns={"銷售量": "總銷售量"})

today = date.today()
rows = []
for _, r in agg.iterrows():
    model = r["室外機型號"]
    if not model:
        continue
    cert = cert_by_outdoor.get(model)
    if cert and cert.get("有效期限"):
        expire_date = datetime.strptime(cert["有效期限"], "%Y-%m-%d").date()
        days_left = (expire_date - today).days
    else:
        expire_date = None
        days_left = None
    rows.append({
        "室外機型號": model,
        "類別": cert.get("類別") if cert else "（無證書資料）",
        "證書編號": cert.get("證書編號") if cert else None,
        "有效期限": expire_date,
        "剩餘天數": days_left,
        "總銷售量": int(r["總銷售量"]),
    })

df = pd.DataFrame(rows)

# ─────────────────────────────────────────────
# 標題與說明
# ─────────────────────────────────────────────
st.markdown("##### 📊 商品生命週期與銷售數量儀表板")
st.caption("串接「商品驗證登錄證書效期」與「節能標章銷售申報」兩份資料，用室外機型號比對。"
           "目前是用你上傳的檔案做的原型，之後可以改成讀取 Google Sheets 即時資料。")

has_cert = df["有效期限"].notna().sum()
st.caption(f"共 {len(df)} 個有銷售紀錄的型號，其中 {has_cert} 個能對應到證書效期資料"
           f"（{len(df) - has_cert} 個型號在證書資料裡找不到，可能是舊型號或尚未登錄）。")

st.divider()

# ─────────────────────────────────────────────
# 篩選控制項
# ─────────────────────────────────────────────
f1, f2, f3 = st.columns([1, 1, 2])
with f1:
    year_options = sorted(sales_df["年度"].unique())
    selected_years = st.multiselect("年度", year_options, default=year_options)
with f2:
    quarter_options = sorted(sales_df["季"].unique())
    selected_quarters = st.multiselect("季別", quarter_options, default=quarter_options)
with f3:
    expiry_filter = st.select_slider(
        "只看效期在此範圍內的型號（可調整年/月門檻）",
        options=["不限", "3個月內", "半年內", "1年內", "1年3個月內", "2年內"],
        value="不限",
    )

days_map = {"3個月內": 90, "半年內": 182, "1年內": 365, "1年3個月內": 456, "2年內": 730}

# 依篩選重新彙總銷售量
filtered_sales = sales_df[
    sales_df["年度"].isin(selected_years) & sales_df["季"].isin(selected_quarters)
]
filtered_agg = filtered_sales.groupby("室外機型號")["銷售量"].sum().reset_index()
filtered_agg_map = dict(zip(filtered_agg["室外機型號"], filtered_agg["銷售量"]))

df["篩選期間銷售量"] = df["室外機型號"].map(filtered_agg_map).fillna(0).astype(int)

view_df = df.copy()
if expiry_filter != "不限":
    max_days = days_map[expiry_filter]
    view_df = view_df[view_df["剩餘天數"].notna() & (view_df["剩餘天數"] <= max_days) & (view_df["剩餘天數"] >= 0)]

view_df = view_df.sort_values("篩選期間銷售量", ascending=False)

# ─────────────────────────────────────────────
# 摘要卡片
# ─────────────────────────────────────────────
m1, m2, m3 = st.columns(3)
m1.metric("符合條件型號數", len(view_df))
m2.metric("篩選期間總銷售量", f"{int(view_df['篩選期間銷售量'].sum()):,}")
urgent_count = ((view_df["剩餘天數"].notna()) & (view_df["剩餘天數"] <= 180)).sum()
m3.metric("半年內到期型號數", urgent_count)

st.divider()

# ─────────────────────────────────────────────
# 泡泡圖：X=剩餘天數、Y=銷售量，快到期又賣得好的最值得優先處理
# ─────────────────────────────────────────────
st.markdown("**優先度泡泡圖**（左上角＝快到期又賣得好，最需要優先處理續證）")
chart_df = view_df[view_df["剩餘天數"].notna()].copy()
if not chart_df.empty:
    st.scatter_chart(
        chart_df,
        x="剩餘天數",
        y="篩選期間銷售量",
        color="類別",
        size="篩選期間銷售量",
        use_container_width=True,
    )
else:
    st.info("目前篩選條件下沒有可繪圖的資料（可能都缺證書效期資料）。")

st.divider()

# ─────────────────────────────────────────────
# 明細表
# ─────────────────────────────────────────────
st.markdown("**明細清單**")
display_df = view_df[["室外機型號", "類別", "證書編號", "有效期限", "剩餘天數", "篩選期間銷售量", "總銷售量"]]
st.dataframe(display_df, use_container_width=True, hide_index=True)

st.download_button(
    "⬇ 下載這份清單（CSV）",
    data=display_df.to_csv(index=False).encode("utf-8-sig"),
    file_name="商品生命週期與銷售數量.csv",
    mime="text/csv",
)

st.divider()

# ─────────────────────────────────────────────
# 單一型號的季度銷售趨勢
# ─────────────────────────────────────────────
st.markdown("**單一型號的季度銷售趨勢**")
model_options = view_df["室外機型號"].tolist()
if model_options:
    picked_model = st.selectbox("選擇型號", model_options)
    trend = sales_df[sales_df["室外機型號"] == picked_model].copy()
    trend["期間"] = trend["年度"].astype(str) + " Q" + trend["季"].astype(str)
    trend = trend.groupby("期間")["銷售量"].sum().reset_index().sort_values("期間")
    st.bar_chart(trend, x="期間", y="銷售量", use_container_width=True)
else:
    st.info("目前篩選條件下沒有型號可選。")
