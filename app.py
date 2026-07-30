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
        })
    return pd.DataFrame(rows)

cert_df = build_cert_rows()

def build_email_body(edited_df, threshold_label):
    to_renew = edited_df[edited_df["要展延"]]
    not_renew = edited_df[edited_df["不展延"]]
    undecided = edited_df[~edited_df["要展延"] & ~edited_df["不展延"]]

    lines = [f"【證書展延決策通知】{today.strftime('%Y/%m/%d')}", ""]
    lines.append(f"門檻：{threshold_label}內到期　總筆數：{len(edited_df)}")
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
    return "\n".join(lines)

def send_mail(recipients, subject, body):
    gmail_user = st.secrets["GMAIL_ADDRESS"]
    gmail_pass = st.secrets["GMAIL_APP_PASSWORD"]
    msg = MIMEMultipart()
    msg["From"] = gmail_user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, recipients, msg.as_string())

# ─────────────────────────────────────────────
# 標題
# ─────────────────────────────────────────────
st.markdown("##### 📊 商品生命週期與銷售數量儀表板")
st.caption("串接「商品驗證登錄證書效期」與「節能標章銷售申報」，用室外機型號比對。"
           "目前是用上傳的檔案做的原型，之後可以改成讀取 Google Sheets 即時資料。")

if "renewal_decisions" not in st.session_state:
    st.session_state["renewal_decisions"] = {}
if "search_threshold_days" not in st.session_state:
    st.session_state["search_threshold_days"] = 365
    st.session_state["search_threshold_label"] = "1年"

# ─────────────────────────────────────────────
# 到期清單（搜尋列：年/月 + 搜尋鈕，按下才更新）
# ─────────────────────────────────────────────
st.markdown("#### 📅 到期清單")

s1, s2, s3 = st.columns([0.7, 0.7, 1])
with s1:
    year_part = st.selectbox("年", list(range(0, 6)), index=1, key="expiry_year")
with s2:
    month_part = st.selectbox("月", list(range(0, 12)), index=0, key="expiry_month")
with s3:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    search_clicked = st.button("🔍 搜尋", use_container_width=True)

if search_clicked:
    st.session_state["search_threshold_days"] = year_part * 365 + month_part * 30
    st.session_state["search_threshold_label"] = "、".join(
        filter(None, [f"{year_part}年" if year_part else "", f"{month_part}個月" if month_part else ""])
    ) or "0天"

threshold_days = st.session_state["search_threshold_days"]
threshold_label = st.session_state["search_threshold_label"]

expiry_view = cert_df[(cert_df["剩餘天數"] >= 0) & (cert_df["剩餘天數"] <= threshold_days)].copy()
expiry_view = expiry_view.sort_values("剩餘天數")
st.caption(f"目前顯示：{threshold_label}內到期（約 {threshold_days} 天），共 {len(expiry_view)} 筆")

decisions = st.session_state["renewal_decisions"]
expiry_view["要展延"] = expiry_view["室外機型號"].map(lambda m: decisions.get(m, {}).get("要展延", False))
expiry_view["不展延"] = expiry_view["室外機型號"].map(lambda m: decisions.get(m, {}).get("不展延", False))

edited_expiry = st.data_editor(
    expiry_view[["室外機型號", "類別", "證書編號", "有效期限", "剩餘天數", "要展延", "不展延"]],
    use_container_width=True,
    hide_index=True,
    disabled=["室外機型號", "類別", "證書編號", "有效期限", "剩餘天數"],
    column_config={
        "要展延": st.column_config.CheckboxColumn("要展延"),
        "不展延": st.column_config.CheckboxColumn("不展延"),
    },
    key="expiry_editor",
)

for _, row in edited_expiry.iterrows():
    st.session_state["renewal_decisions"][row["室外機型號"]] = {
        "要展延": bool(row["要展延"]),
        "不展延": bool(row["不展延"]),
    }

st.divider()

# ─────────────────────────────────────────────
# 手動寄送（立即寄出目前清單）
# ─────────────────────────────────────────────
st.markdown("**手動寄送展延決策通知**")
mail_col1, mail_col2 = st.columns([2, 1])
with mail_col1:
    recipient_input = st.text_input("收件信箱（多個用逗號分隔）", placeholder="example1@company.com, example2@company.com")
with mail_col2:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    send_clicked = st.button("📨 立即寄送", use_container_width=True)

if send_clicked:
    recipients = [r.strip() for r in recipient_input.split(",") if r.strip()]
    if not recipients:
        st.error("請輸入至少一個收件信箱")
    else:
        body = build_email_body(edited_expiry, threshold_label)
        try:
            send_mail(recipients, f"【證書展延決策通知】{today.strftime('%Y/%m/%d')}", body)
            st.success(f"已寄出通知信給 {len(recipients)} 位收件人")
        except KeyError:
            st.error("⚠️ 尚未設定寄信帳號，請在 Streamlit Cloud 的 Secrets 加入 GMAIL_ADDRESS 與 GMAIL_APP_PASSWORD")
        except Exception as e:
            st.error(f"寄信失敗：{e}")

st.divider()

# ─────────────────────────────────────────────
# 定時寄信設定（兩列，日期／到期範圍都可調整；
# 這裡只是「設定」介面，實際自動觸發需要另外做 Lambda + EventBridge 排程）
# ─────────────────────────────────────────────
st.markdown("**⏰ 定時寄信設定**")
st.caption("這裡只負責設定「什麼時候寄、寄多久內到期的清單」，實際「自動在那天寄出」需要另外做一個 "
           "Lambda 排程（跟你們原本 cert-expiry-notifier 同一種做法），設定確認後我再協助串接。")

if "schedule_rows" not in st.session_state:
    st.session_state["schedule_rows"] = [
        {"月": 1, "日": 10, "年門檻": 1, "月門檻": 3, "收件信箱": ""},
        {"月": 7, "日": 10, "年門檻": 1, "月門檻": 3, "收件信箱": ""},
    ]

schedule_df = pd.DataFrame(st.session_state["schedule_rows"])
edited_schedule = st.data_editor(
    schedule_df,
    use_container_width=True,
    hide_index=True,
    num_rows="fixed",
    column_config={
        "月": st.column_config.SelectboxColumn("觸發月", options=list(range(1, 13)), required=True),
        "日": st.column_config.SelectboxColumn("觸發日", options=list(range(1, 29)), required=True),
        "年門檻": st.column_config.SelectboxColumn("到期範圍－年", options=list(range(0, 6)), required=True),
        "月門檻": st.column_config.SelectboxColumn("到期範圍－月", options=list(range(0, 12)), required=True),
        "收件信箱": st.column_config.TextColumn("收件信箱（逗號分隔）"),
    },
    key="schedule_editor",
)
st.session_state["schedule_rows"] = edited_schedule.to_dict("records")

if st.button("💾 儲存排程設定"):
    st.success("排程設定已暫存（目前僅存在這次瀏覽的畫面狀態；確定內容沒問題後，"
               "我會協助把這份設定寫進 Lambda，讓它每年準時自動執行）。")
