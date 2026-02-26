import os
import sys
import datetime
import time
import requests
import json
import chinese_calendar as calendar
from openai import OpenAI
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr # 新增：专门用于解决 QQ 邮箱发件人格式验证的库
import markdown

# ==========================================
# 1. 配置区 (直接修改你想关注的企业)
# ==========================================
TARGET_COMPANIES = "威海光威复合材料 威海广泰 迪尚集团 威高集团"

# ==========================================
# 2. 读取环境变量 
# ==========================================
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_MODEL_FALLBACK = os.getenv("GEMINI_MODEL_FALLBACK", "gemini-2.5-flash")
GEMINI_REQUEST_DELAY = float(os.getenv("GEMINI_REQUEST_DELAY", "3.0"))

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVERS = os.getenv("EMAIL_RECEIVERS")
SMTP_SERVER = "smtp.qq.com" 
SMTP_PORT = 465             

TRIGGER_EVENT = os.getenv("TRIGGER_EVENT", "schedule")

# ==========================================
# 3. 核心业务逻辑
# ==========================================
def is_first_workday_of_week():
    today = datetime.date.today()
    if not calendar.is_workday(today):
        return False
    weekday = today.weekday()
    for i in range(weekday):
        prev_day = today - datetime.timedelta(days=weekday - i)
        if calendar.is_workday(prev_day):
            return False
    return True

def search_info(query, days=7):
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": SEARCH_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "include_answer": True,
        "days": days
    }
    try:
        response = requests.post(url, json=payload).json()
        return "\n".join([result.get('content', '') for result in response.get('results', [])])
    except Exception as e:
        print(f"搜索出错 [{query}]: {e}")
        return "暂无相关搜索结果"

def generate_briefing(companies_info, weihai_info, macro_info, global_info):
    client = OpenAI(
        api_key=GEMINI_API_KEY, 
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
    ) 
    
    prompt = f"""
    【你的角色与受众】
    你是一名客观、严谨、务实的商业情报分析师。
    你的报告阅读对象是：中国大陆山东省威海市的常驻居民及一线业务人员。

    【核心工作纪律 - 防幻觉机制（最高优先级）】
    1. 忠于事实：所有的总结、数据、政策名称必须 100% 来源于我下方提供的搜索原文。
    2. 严禁脑补：如果提供的原文中没有相关信息或动态，请直接写“本周暂无相关关键动态”，绝对禁止调用你的内部知识库去编造。
    3. 语言规范：必须使用极其客观、平实、直白的新闻报道体。严禁使用任何比喻、拟人、夸张等修辞手法。不讲废话，直击核心数据与事件。

    【请基于以下四块原始素材，生成本周商业情报参考】
    素材A（关注企业动态）：{companies_info}
    素材B（威海本地政经与外贸）：{weihai_info}
    素材C（中国宏观政策与经济指标）：{macro_info}
    素材D（全球经贸与国际局势）：{global_info}

    【输出格式要求】
    请使用清晰的 Markdown 排版，分四个独立模块（关注企业、威海本地、全国宏观、全球局势）输出。
    每一条简报后，用一句话客观说明该事件对威海本地业务人员在客户沟通或业务开拓上的“参考方向”。
    """
    
    print(f"等待 {GEMINI_REQUEST_DELAY} 秒后发起大模型请求...")
    time.sleep(GEMINI_REQUEST_DELAY)

    try:
        response = client.chat.completions.create(
            model=GEMINI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1 
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"⚠️ 主模型 {GEMINI_MODEL} 请求失败: {e}")
        print(f"🔄 正在尝试使用备用模型 {GEMINI_MODEL_FALLBACK}...")
        try:
            time.sleep(GEMINI_REQUEST_DELAY)
            fallback_response = client.chat.completions.create(
                model=GEMINI_MODEL_FALLBACK,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1 
            )
            return fallback_response.choices[0].message.content
        except Exception as fallback_e:
            print(f"❌ 备用模型也请求失败: {fallback_e}")
            return "生成简报失败，请检查 API Key 或网络状态。"

def send_email(subject, markdown_content):
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        print("未配置发件人邮箱或密码，跳过邮件发送。")
        return

    if not EMAIL_RECEIVERS or EMAIL_RECEIVERS.strip() == "":
        receivers_list = [EMAIL_SENDER]
    else:
        clean_receivers = EMAIL_RECEIVERS.replace('，', ',')
        receivers_list = [r.strip() for r in clean_receivers.split(',') if r.strip()]

    html_content = markdown.markdown(markdown_content)
    full_html = f"""
    <html>
    <head><style>body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }} h2 {{ color: #2c3e50; border-bottom: 1px solid #eee; padding-bottom: 5px; }}</style></head>
    <body>{html_content}</body>
    </html>
    """

    msg = MIMEMultipart()
    
    # --- 修复核心：使用 formataddr 标准化发件人和收件人 ---
    msg['From'] = formataddr(("威海商业情报助手", EMAIL_SENDER))
    msg['To'] = ", ".join(receivers_list)
    msg['Subject'] = Header(subject, 'utf-8')
    msg.attach(MIMEText(full_html, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, receivers_list, msg.as_string())
        server.quit()
        print(f"✅ 邮件已成功发送至: {', '.join(receivers_list)}")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")

# --- 主程序入口 ---
if __name__ == "__main__":
    if TRIGGER_EVENT == "schedule":
        if not is_first_workday_of_week():
            print("今天不是本周首个工作日，任务跳过。")
            sys.exit(0)
            
    print(f"开始执行情报收集，当前配置主模型: {GEMINI_MODEL}")
    
    print("-> 搜索特定企业动态...")
    comp_raw = search_info(f"{TARGET_COMPANIES} 最新公司动态 商业新闻")
    print("-> 搜索威海重点政经...")
    weihai_raw = search_info("威海市 重点舆情 新闻 政策颁布 行业扶持 经济指标 外经外贸 招商引资 最新动态")
    print("-> 搜索中国宏观政策...")
    macro_raw = search_info("中国宏观经济变化 重点政策 十五五规划 两会 中央经济工作会议 重点指标 LPR 关税 最新新闻")
    print("-> 搜索全球宏观局势...")
    global_raw = search_info("Global economic trade financial news international situation latest trends")
    
    print("信息收集完毕，正在呼叫大模型进行严谨提炼...")
    briefing = generate_briefing(comp_raw, weihai_raw, macro_raw, global_raw)
    
    print("简报生成完毕，准备发送邮件...")
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    email_subject = f"【威海业务情报周报】{today_str}"
    
    send_email(email_subject, briefing)
    print("流程全部执行成功！")
