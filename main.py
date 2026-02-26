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
# 1. 读取环境变量并清洗
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
# 2. 核心业务逻辑
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
        return "\n".join(results_str) if results_str else "暂无相关搜索结果"
    except Exception as e:
        print(f"搜索出错 [{query}]: {e}")
        return "暂无相关搜索结果"

def get_llm_client():
    if CUSTOM_API_KEY:
        print("检测到备用模型 (CUSTOM_API_KEY)，使用备用通道...")
        base_url = CUSTOM_BASE_URL or "https://api.deepseek.com"
        model = CUSTOM_MODEL or "deepseek-chat"
        return OpenAI(api_key=CUSTOM_API_KEY, base_url=base_url), model, False
    else:
        print("使用默认 Gemini 通道...")
        client = OpenAI(
            api_key=GEMINI_API_KEY, 
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        return client, GEMINI_MODEL, True

def generate_briefing(client, model_name, is_gemini, target_comp_info, alt_comp_info, weihai_info, ind_info, bank_info, macro_global_info, tech_info):
    prompt = f"""
    【角色与纪律要求】
    你是“威海营业部首席新闻官”，负责为团队提供高度聚焦、客观、真实的商业简报。
    系统当前时间为：{TODAY_STR}。严格基于此时间点，只总结最近一周的最新动态。

    【防幻觉与物理隔离强硬规则（最高优先级）】
    1. 真实溯源：你在报告中写的每一条新闻，必须在结尾附上我提供的对应【来源】URL链接。
    2. 严格物理隔离：各板块素材绝对严禁跨区借用！没有新闻就按要求声明，绝不凑数。
    3. 拒绝宏大叙事：宏观板块必须写具体的事件，严禁空谈。禁止使用修辞手法。

    【信息素材池】
    素材A1（用户指定的关注企业）：{target_comp_info}
    素材A2（威海辖区其他出海/外贸/优质产能重点企业）：{alt_comp_info}
    素材B（威海政经与招商）：{weihai_info}
    素材C（关注行业动态 - {TARGET_INDUSTRY}）：{ind_info}
    素材D（威海辖区银行业国际业务/跨境金融政策）：{bank_info}
    素材E（宏观与全球重点事件）：{macro_global_info}
    素材F（前沿科技杂谈 AI/机器人/新能源）：{tech_info}

    【强制输出格式模板】（直接复制以下结构并填入内容）：

    # 商业情报周报

    **报告日期：** {TODAY_STR} | **发件人：** 威海营业部首席新闻官
    ---

    ## 一、 重点企业动态
    （【生成逻辑-非常重要】：首先检查素材A1中有没有这几家企业的具体新闻。如果没有，你**必须**输出这句话：“**关注企业过去一周没有新闻。以下为您整理威海市辖区内可能感兴趣的其他重点外贸与出海企业动态：**”。然后再基于【素材A2】提取1-3条企业动态。如果有，则正常基于素材A1提取。格式要求：一句话事件核心概述 + 业务参考方向 + [来源地址]）

    ## 二、 威海本地政经
    （基于素材B提取1-3条。格式要求：一句话事件核心概述 + 业务参考方向 + [来源地址]）

    ## 三、 【{TARGET_INDUSTRY}】行业风向与辖区银行业务
    （【生成逻辑】：本部分包含两块内容。首先基于素材C提取1-2条行业风向。然后，**必须**基于【素材D】提取1-2条威海辖区内银行最新发布的国际业务、跨境金融或外汇政策信息，并说明业务人员该如何利用此信息与银行开展合作。格式要求：一句话事件核心概述 + 业务参考方向 + [来源地址]）

    ## 四、 宏观与全球重点局势
    （基于素材E提取1-3个具体的宏观经济/全球贸易大事件。格式要求：一句话事件核心概述 + 外贸/宏观参考方向 + [来源地址]）

    ## 五、 科技前沿杂谈（AI/机器人/新能源）
    （基于素材F提取1-3条科技突破或巨头动向，作为业务拓展的视野谈资。格式要求：一句话事件核心概述 + 视野参考 + [来源地址]）
    """
    
    if is_gemini:
        time.sleep(GEMINI_REQUEST_DELAY)

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1 
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"⚠️ 主模型 {model_name} 请求失败: {e}")
        if is_gemini:
            print(f"🔄 尝试使用备用模型 {GEMINI_MODEL_FALLBACK}...")
            try:
                time.sleep(GEMINI_REQUEST_DELAY)
                fallback_response = client.chat.completions.create(
                    model=GEMINI_MODEL_FALLBACK,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1 
                )
                return fallback_response.choices[0].message.content
            except Exception as fallback_e:
                print(f"❌ 备用模型也失败: {fallback_e}")
        return "生成简报失败，请检查 API Key 或网络状态。"

def send_email(subject, markdown_content):
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        return

    receivers_list = [EMAIL_SENDER] if not EMAIL_RECEIVERS else [r.strip() for r in EMAIL_RECEIVERS.replace('，', ',').split(',') if r.strip()]

    html_content = markdown.markdown(markdown_content)
    full_html = f"""
    <html>
    <head><style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; line-height: 1.6; color: #333; }} 
        h1 {{ color: #1a365d; font-size: 24px; border-bottom: 2px solid #1a365d; padding-bottom: 10px; }}
        h2 {{ color: #2c3e50; font-size: 18px; border-bottom: 1px dashed #ccc; padding-bottom: 5px; margin-top: 25px; }}
        a {{ color: #3498db; text-decoration: none; word-break: break-all; }}
    </style></head>
    <body>{html_content}</body>
    </html>
    """

    msg = MIMEMultipart()
    msg['From'] = formataddr(("威海营业部首席新闻官", EMAIL_SENDER))
    msg['To'] = ", ".join(receivers_list)
    msg['Subject'] = Header(subject, 'utf-8')
    msg.attach(MIMEText(full_html, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, 465, timeout=15)
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, receivers_list, msg.as_string())
        server.quit()
        print(f"✅ 邮件已成功发送")
    except Exception as e1:
        print(f"⚠️ 465 失败 ({e1})，尝试 587...")
        try:
            time.sleep(3) 
            server = smtplib.SMTP(SMTP_SERVER, 587, timeout=15)
            server.starttls() 
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, receivers_list, msg.as_string())
            server.quit()
            print(f"✅ 邮件已成功发送")
        except Exception as e2:
            print(f"❌ 邮件发送最终失败: {e2}")

if __name__ == "__main__":
    if TRIGGER_EVENT == "schedule" and not is_first_workday_of_week():
        sys.exit(0)
            
    llm_client, model_name, is_gemini = get_llm_client()
    
    # 增加细分的搜索策略
    print(f"-> [搜索] 目标重点企业: {TARGET_COMPANIES}")
    target_comp_raw = search_info(f"{TARGET_COMPANIES} 威海 最新 商业新闻 业务进展")
    
    print("-> [搜索] 威海其他出海/外贸/优质产能企业...")
    alt_comp_raw = search_info("威海市 外贸 出口 海外投资 出海 优质产能 高新技术 企业 最新重大商业新闻")
    
    print("-> [搜索] 威海政经与招商...")
    weihai_raw = search_info("威海市 最新 突发 重点舆情 招商引资 政策落地 新闻")
    
    print(f"-> [搜索] 行业风向 ({TARGET_INDUSTRY})...")
    ind_raw = search_info(f"{TARGET_INDUSTRY} 行业最新 突发 重大变革 新闻")
    
    print("-> [搜索] 威海辖区银行业务与国际政策...")
    bank_raw = search_info("威海 银行 国际业务 跨境金融 跨境人民币 外汇便利化 政策 最新新闻")
    
    print("-> [搜索] 宏观与全球局势...")
    macro_global_raw = search_info("中国宏观经济 重点政策落地 OR Global international major events breaking news")
    
    print("-> [搜索] 科技杂谈...")
    tech_raw = search_info("前沿科技 人工智能 AI 机器人 新能源 最新技术突破 巨头动向")
    
    print("所有信息维度收集完毕，正在呼叫大模型分析...")
    briefing = generate_briefing(llm_client, model_name, is_gemini, target_comp_raw, alt_comp_raw, weihai_raw, ind_raw, bank_raw, macro_global_raw, tech_raw)
    
    email_subject = f"【威海商业情报】{TODAY_STR}"
    send_email(email_subject, briefing)
    print("流程执行成功！")
