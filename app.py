import streamlit as st
import pandas as pd
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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
sales_df["期間"] = sales_df["年度"].astype(str) + "Q" + sales_df["季"].astype(str)

total_sales_by_model = sales_df.groupby("室外機型號")["銷售量"].sum().to_dict()

today = date.today()

def build_cert_rows():
    rows = []
    for model, cert in cert_by_outdoor.items():
        if not cert.get("有效期限"):
            continue
        expire_date = datetime.strptime(cert["有效期限"], "%Y-%m-%d").date()
        days_left = (expire_date - today).days
        rows.append({
            "室外機型號": model,
            "類別": cert.get("類別"),
            "證書編號": cert.get("證書編號"),
            "有效期限": expire_date,
            "剩餘天數": days_left,
            "總銷售量": int(total_sales_by_model.get(model, 0)),
        })
    return pd.DataFrame(rows)

cert_df = build_cert_rows()

# ─────────────────────────────────────────────
# 標題
# ─────────────────────────────────────────────
st.markdown("##### 📊 商品生命週期與銷售數量儀表板")
st.caption("串接「商品驗證登錄證書效期」與「節能標章銷售申報」，用室外機型號比對。"
           "目前是用上傳的檔案做的原型，之後可以改成讀取 Google Sheets 即時資料。")

if "renewal_decisions" not in st.session_state:
    st.session_state["renewal_decisions"] = {}

# ─────────────────────────────────────────────
# 1. 到期清單（可調整門檻 + 展延決策 + 寄信）
# ─────────────────────────────────────────────
with st.expander("📅 到期清單", expanded=True):
    c1, c2 = st.columns([1, 1])
    with c1:
        year_part = st.selectbox("年", list(range(0, 6)), index=1, key="expiry_year")
    with c2:
        month_part = st.selectbox("月", list(range(0, 12)), index=0, key="expiry_month")

    threshold_days = year_part * 365 + month_part * 30

    expiry_view = cert_df[(cert_df["剩餘天數"] >= 0) & (cert_df["剩餘天數"] <= threshold_days)].copy()
    expiry_view = expiry_view.sort_values("剩餘天數")
    threshold_label = "、".join(filter(None, [f"{year_part}年" if year_part else "", f"{month_part}個月" if month_part else ""])) or "0天"
    st.caption(f"目前門檻：{threshold_label}內到期（約 {threshold_days} 天），共 {len(expiry_view)} 筆")

    # 帶入之前的展延決策（如果有），沒有的話預設兩欄都是 False
    decisions = st.session_state["renewal_decisions"]
    expiry_view["要展延"] = expiry_view["室外機型號"].map(lambda m: decisions.get(m, {}).get("要展延", False))
    expiry_view["不展延"] = expiry_view["室外機型號"].map(lambda m: decisions.get(m, {}).get("不展延", False))

    edited_expiry = st.data_editor(
        expiry_view[["室外機型號", "類別", "證書編號", "有效期限", "剩餘天數", "總銷售量", "要展延", "不展延"]],
        use_container_width=True,
        hide_index=True,
        disabled=["室外機型號", "類別", "證書編號", "有效期限", "剩餘天數", "總銷售量"],
        column_config={
            "要展延": st.column_config.CheckboxColumn("要展延"),
            "不展延": st.column_config.CheckboxColumn("不展延"),
        },
        key="expiry_editor",
    )

    # 存回決策狀態
    for _, row in edited_expiry.iterrows():
        st.session_state["renewal_decisions"][row["室外機型號"]] = {
            "要展延": bool(row["要展延"]),
            "不展延": bool(row["不展延"]),
        }

    st.divider()
    st.markdown("**寄送展延決策通知**")
    mail_col1, mail_col2 = st.columns([2, 1])
    with mail_col1:
        recipient_input = st.text_input("收件信箱（多個用逗號分隔）", placeholder="example1@company.com, example2@company.com")
    with mail_col2:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        send_clicked = st.button("📨 寄送通知", use_container_width=True)

    if send_clicked:
        recipients = [r.strip() for r in recipient_input.split(",") if r.strip()]
        if not recipients:
            st.error("請輸入至少一個收件信箱")
        else:
            to_renew = edited_expiry[edited_expiry["要展延"]]
            not_renew = edited_expiry[edited_expiry["不展延"]]
            undecided = edited_expiry[~edited_expiry["要展延"] & ~edited_expiry["不展延"]]

            lines = [f"【證書展延決策通知】{today.strftime('%Y/%m/%d')}", ""]
            lines.append(f"門檻：{threshold_label}內到期　總筆數：{len(edited_expiry)}")
            lines.append("")
            lines.append(f"✅ 要展延（{len(to_renew)} 筆）：")
            for _, r in to_renew.iterrows():
                lines.append(f"  {r['室外機型號']}（{r['類別']}）　到期：{r['有效期限']}　剩餘 {r['剩餘天數']} 天")
            lines.append("")
            lines.append(f"❌ 不展延（{len(not_renew)} 筆）：")
            for _, r in not_renew.iterrows():
                lines.append(f"  {r['室外機型號']}（{r['類別']}）　到期：{r['有效期限']}")
            lines.append("")
            lines.append(f"⚠️ 尚未決定（{len(undecided)} 筆）：")
            for _, r in undecided.iterrows():
                lines.append(f"  {r['室外機型號']}（{r['類別']}）　到期：{r['有效期限']}")
            lines.append("")
            lines.append("此為系統自動發送，請勿回覆。")
            body = "\n".join(lines)

            try:
                gmail_user = st.secrets["GMAIL_ADDRESS"]
                gmail_pass = st.secrets["GMAIL_APP_PASSWORD"]
                msg = MIMEMultipart()
                msg["From"] = gmail_user
                msg["To"] = ", ".join(recipients)
                msg["Subject"] = f"【證書展延決策通知】{today.strftime('%Y/%m/%d')}"
                msg.attach(MIMEText(body, "plain", "utf-8"))
                with smtplib.SMTP("smtp.gmail.com", 587) as server:
                    server.starttls()
                    server.login(gmail_user, gmail_pass)
                    server.sendmail(gmail_user, recipients, msg.as_string())
                st.success(f"已寄出通知信給 {len(recipients)} 位收件人")
            except KeyError:
                st.error("⚠️ 尚未設定寄信帳號，請在 Streamlit Cloud 的 Secrets 加入 GMAIL_ADDRESS 與 GMAIL_APP_PASSWORD")
            except Exception as e:
                st.error(f"寄信失敗：{e}")

# ─────────────────────────────────────────────
# 2. 歷史銷量（類型／型號／年季銷量／總銷量）
# ─────────────────────────────────────────────
with st.expander("📈 歷史銷量", expanded=False):
    pivot = sales_df.pivot_table(
        index="室外機型號", columns="期間", values="銷售量", aggfunc="sum", fill_value=0
    )
    period_cols = sorted(pivot.columns)
    pivot = pivot[period_cols]
    pivot["總銷量"] = pivot.sum(axis=1)
    pivot = pivot.reset_index()
    pivot["類型"] = pivot["室外機型號"].map(lambda m: cert_by_outdoor.get(m, {}).get("類別", "（無證書資料）"))

    cat_options = ["全部"] + sorted(pivot["類型"].dropna().unique().tolist())
    picked_cat = st.selectbox("篩選類型", cat_options, key="sales_cat_filter")
    view_pivot = pivot if picked_cat == "全部" else pivot[pivot["類型"] == picked_cat]
    view_pivot = view_pivot.sort_values("總銷量", ascending=False)

    display_cols = ["類型", "室外機型號"] + period_cols + ["總銷量"]
    st.dataframe(view_pivot[display_cols], use_container_width=True, hide_index=True)

    st.download_button(
        "⬇ 下載歷史銷量（CSV）",
        data=view_pivot[display_cols].to_csv(index=False).encode("utf-8-sig"),
        file_name="歷史銷量.csv",
        mime="text/csv",
    )
