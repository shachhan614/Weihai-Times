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
from email.utils import formataddr
import markdown

# ==========================================
# 1. 读取环境变量
# ==========================================
raw_companies = os.getenv("TARGET_COMPANIES") or "山东未来机器人有限公司 威海广泰 威海国际经济技术合作股份有限公司"
TARGET_COMPANIES = raw_companies.replace('、', ' ').replace('，', ' ') 

TARGET_INDUSTRY = os.getenv("TARGET_INDUSTRY") or "工程承包 橡胶轮胎 医疗器械"
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_MODEL_FALLBACK = os.getenv("GEMINI_MODEL_FALLBACK", "gemini-2.5-flash")
GEMINI_REQUEST_DELAY = float(os.getenv("GEMINI_REQUEST_DELAY", "3.0"))

CUSTOM_API_KEY = os.getenv("CUSTOM_API_KEY")
CUSTOM_BASE_URL = os.getenv("CUSTOM_BASE_URL")
CUSTOM_MODEL = os.getenv("CUSTOM_MODEL")

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVERS = os.getenv("EMAIL_RECEIVERS")
SMTP_SERVER = "smtp.qq.com" 

TRIGGER_EVENT = os.getenv("TRIGGER_EVENT", "schedule")
TODAY_STR = datetime.date.today().strftime("%Y年%m月%d日")

# ==========================================
# 2. 核心逻辑
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
        "include_answer": False, 
        "days": days
    }
    try:
        response = requests.post(url, json=payload).json()
        results_str = []
        for result in response.get('results', []):
            content = result.get('content', '').replace('\n', ' ')
            source_url = result.get('url', '无来源链接')
            results_str.append(f"【内容】: {content} \n【来源】: {source_url}\n")
        return "\n".join(results_str) if results_str else "暂无直接结果。"
    except Exception as e:
        print(f"搜索出错: {e}")
        return "搜索请求失败。"

def get_llm_client():
    if CUSTOM_API_KEY:
        base_url = CUSTOM_BASE_URL or "https://api.deepseek.com"
        model = CUSTOM_MODEL or "deepseek-chat"
        return OpenAI(api_key=CUSTOM_API_KEY, base_url=base_url), model, False
    else:
        client = OpenAI(
            api_key=GEMINI_API_KEY, 
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        return client, GEMINI_MODEL, True

def generate_briefing(client, model_name, is_gemini, target_comp_info, alt_comp_info, weihai_info, ind_info, bank_info, macro_global_info, tech_info):
    prompt = f"""
    【角色要求】
    你是“来自您的智能新闻官🤖”。系统时间：{TODAY_STR}。

    【排版极其重要的规则】
    1. 每个版块必须恰好 4 条内容（2条国内/本地 + 2条国际/出海）。
    2. 可读性优先：每一条新闻的输出格式必须严格如下，且每一项都必须【另起一行】：
       序号. 标题概述
       业务参考方向/视野拓展：具体建议内容
       来源：[来源地址]
    
    【防拒答逻辑】
    第二至五部分绝对禁止说无新闻。

    【素材】
    指定企业A1: {target_comp_info} | 备用企业A2: {alt_comp_info} | 威海政经B: {weihai_info} 
    行业C: {ind_info} | 银行D: {bank_info} | 宏观E: {macro_global_info} | 科技F: {tech_info}

    【强制模板】（请直接生成内容，不要有开头语）：

    # 商业情报周报

    **报告日期：** {TODAY_STR} | **发件人：** 来自您的智能新闻官🤖
    ---

    ## 一、 重点企业动态
    （逻辑：首先尝试A1。若无则输出“**关注企业过去一周没有新闻。以下为您整理威海市辖区内其他优质产能与出海重点企业动态：**”并使用A2。严格按照 2026/2/26 的 2+2 结构输出，每项内容和来源必须【另起一行】）

    ## 二、 威海本地政经
    **国内焦点：**
    序号. [内容]
    业务参考方向：[内容]
    来源：[URL]
    （重复完成2条）
    **国际与出海合作：**
    序号. [内容]
    业务参考方向：[内容]
    来源：[URL]
    （重复完成2条）

    ## 三、 行业风向与银行动态
    （同上格式，2条国内风向+2条国际/跨境银行动态。每项必须【另起一行】）

    ## 四、 宏观与全球重点局势
    （同上格式，2条国内宏观+2条全球局势。每项必须【另起一行】）

    ## 五、 科技前沿杂谈（AI/机器人/新能源）
    （同上格式，2条中国突破+2条全球前沿。业务参考改为“视野拓展”，每项必须【另起一行】）

    <p style="text-align: center;"><strong>以上为本周新闻，均为自动收集并由AI生成。</strong></p>
    <p style="text-align: center;">🤖我们下周再见🤖</p>
    """
    
    if is_gemini: time.sleep(GEMINI_REQUEST_DELAY)

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1 
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"请求失败: {e}")
        return "生成简报失败。"

def send_email(subject, markdown_content):
    if not EMAIL_SENDER or not EMAIL_PASSWORD: return
    receivers_list = [EMAIL_SENDER] if not EMAIL_RECEIVERS else [r.strip() for r in EMAIL_RECEIVERS.replace('，', ',').split(',') if r.strip()]

    html_content = markdown.markdown(markdown_content)
    # 升级 CSS：整体字号变大，增加行间距，确保居中落款生效
    full_html = f"""
    <html>
    <head><style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; line-height: 1.8; color: #333; font-size: 16px; }} 
        h1 {{ color: #1a365d; font-size: 28px; border-bottom: 3px solid #1a365d; padding-bottom: 12px; }}
        h2 {{ color: #2c3e50; font-size: 22px; border-bottom: 1px dashed #ccc; padding-bottom: 8px; margin-top: 35px; }}
        p, li {{ font-size: 16px; margin-bottom: 10px; }}
        a {{ color: #3498db; text-decoration: none; word-break: break-all; }}
        .footer {{ text-align: center; margin-top: 50px; padding-top: 20px; border-top: 1px solid #eee; }}
    </style></head>
    <body>{html_content}</body>
    </html>
    """

    msg = MIMEMultipart()
    msg['From'] = formataddr(("来自您的智能新闻官🤖", EMAIL_SENDER))
    msg['To'] = ", ".join(receivers_list)
    msg['Subject'] = Header(subject, 'utf-8')
    msg.attach(MIMEText(full_html, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, 465, timeout=15)
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, receivers_list, msg.as_string())
        server.quit()
        print("✅ 邮件发送成功")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")

if __name__ == "__main__":
    if TRIGGER_EVENT == "schedule" and not is_first_workday_of_week(): sys.exit(0)
    llm_client, model_name, is_gemini = get_llm_client()
    
    target_comp_raw = search_info(f"{TARGET_COMPANIES} 威海 中国 国际 出海 最新商业新闻")
    alt_comp_raw = search_info("威海市 重点企业 外贸 出口 海外投资 优质产能 最新重大商业新闻")
    weihai_raw = search_info("威海市 重点舆情 招商引资 政策 外贸 国际合作 新闻")
    ind_raw = search_info(f"{TARGET_INDUSTRY} 中国 国际 行业最新 突发 重大变革 新闻")
    bank_raw = search_info("银行 国内政策 国际业务 跨境金融 外汇 威海分行 政策 最新新闻")
    macro_global_raw = search_info("中国宏观经济 重点政策落地 全球经济 国际贸易 重大事件 新闻")
    tech_raw = search_info("前沿科技 人工智能 AI 机器人 新能源 中国突破 全球巨头动向")
    
    briefing = generate_briefing(llm_client, model_name, is_gemini, target_comp_raw, alt_comp_raw, weihai_raw, ind_raw, bank_raw, macro_global_raw, tech_raw)
    email_subject = f"【威海商业情报】{TODAY_STR}"
    send_email(email_subject, briefing)
