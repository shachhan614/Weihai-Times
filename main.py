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
        return "\n".join(results_str) if results_str else "搜索无直接结果，请基于常识或临近素材提炼。"
    except Exception as e:
        print(f"搜索出错 [{query}]: {e}")
        return "搜索请求失败。"

def get_llm_client():
    if CUSTOM_API_KEY:
        print("检测到备用模型，使用备用通道...")
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
    你是“威海营业部首席新闻官”。系统当前时间为：{TODAY_STR}。严格基于此时间点，总结最近一周的最新动态。

    【最严厉的强制规则（违背此规则将被判定为任务失败）】
    1. 真实溯源：报告中写的每一条新闻必须在结尾附上【来源】URL链接。
    2. 剥夺拒答权：除了第一部分在特定情况下可以声明“过去一周没有新闻”外，**第二、三、四、五部分绝对不允许找借口说没有新闻！绝对禁止出现“本周暂无相关动态”等字眼。** 你必须尽最大努力从提供的素材中提炼出相关内容，哪怕是略微宽泛的行业或宏观背景！
    3. 结构极其死板：**每一个大板块（一、二、三、四、五）必须恰好包含 4 条内容，且必须严格按照“2条国内/本地 + 2条国际/出海”的结构输出！少一条或多一条都不行！**

    【信息素材池】
    素材A1（指定企业）：{target_comp_info}
    素材A2（威海辖区其他出海/外贸/优质产能重点企业）：{alt_comp_info}
    素材B（威海政经与外贸）：{weihai_info}
    素材C（关注行业动态 - {TARGET_INDUSTRY}）：{ind_info}
    素材D（威海辖区银行业务与跨境金融）：{bank_info}
    素材E（宏观与全球局势）：{macro_global_info}
    素材F（前沿科技 AI/机器人/新能源）：{tech_info}

    【强制输出格式模板】（请严格按照此模板生成，直接粘贴内容，不要加任何废话）：

    # 商业情报周报

    **报告日期：** {TODAY_STR} | **发件人：** 威海营业部首席新闻官
    ---

    ## 一、 重点企业动态
    （逻辑：首先检查素材A1是否有关于指定企业的新闻。如果有，基于A1输出 2条国内市场动态 + 2条出海/国际动态。
    如果A1中没有明确新闻，你**必须**首先输出加粗的这句话：“**关注企业过去一周没有新闻。以下为您整理威海市辖区内其他优质产能与出海重点企业动态：**”，然后基于素材A2输出：2条威海其他重点企业的国内动态 + 2条威海其他重点企业的出海/外贸动态。格式：核心概述 + 业务参考方向 + [来源地址]）

    ## 二、 威海本地政经
    （绝对禁止说无新闻！必须基于素材B提取。结构要求：
    **国内焦点：**
    1. [提取第1条威海本地政经/招商政策] + 业务参考 + [来源地址]
    2. [提取第2条威海本地政经/基础设施] + 业务参考 + [来源地址]
    **国际与出海合作：**
    3. [提取第1条威海外贸/中韩贸易/国际合作] + 业务参考 + [来源地址]
    4. [提取第2条威海辖区涉外经济相关动态] + 业务参考 + [来源地址]）

    ## 三、 行业风向与银行动态
    （绝对禁止说无新闻！基于素材C和D提取。结构要求：
    **国内风向：**
    1. [素材C：提取1条国内行业风向] + 业务参考 + [来源地址]
    2. [素材D：提取1条国内银行业务/政策] + 业务参考 + [来源地址]
    **国际与跨境：**
    3. [素材C：提取1条全球/国际行业风向] + 业务参考 + [来源地址]
    4. [素材D：提取1条银行跨境金融/国际业务政策] + 业务参考 + [来源地址]）

    ## 四、 宏观与全球重点局势
    （绝对禁止说无新闻！基于素材E提取。结构要求：
    **国内宏观：**
    1. [提取第1条中国宏观政策/经济事件] + 业务参考 + [来源地址]
    2. [提取第2条中国重点经济指标变化] + 业务参考 + [来源地址]
    **全球局势：**
    3. [提取第1条全球重大经济/贸易事件] + 业务参考 + [来源地址]
    4. [提取第2条国际重要政治或金融局势] + 业务参考 + [来源地址]）

    ## 五、 科技前沿杂谈（AI/机器人/新能源）
    （绝对禁止说无新闻！基于素材F提取。结构要求：
    **中国科技突破：**
    1. [提取第1条中国前沿科技/新能源突破] + 视野拓展 + [来源地址]
    2. [提取第2条中国科技巨头/AI动向] + 视野拓展 + [来源地址]
    **全球科技前沿：**
    3. [提取第1条国际顶级科技突破/大模型进展] + 视野拓展 + [来源地址]
    4. [提取第2条全球科技巨头商业动向] + 视野拓展 + [来源地址]）
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
            print(f"🔄 尝试备用模型...")
            try:
                time.sleep(GEMINI_REQUEST_DELAY)
                fallback_response = client.chat.completions.create(
                    model=GEMINI_MODEL_FALLBACK,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1 
                )
                return fallback_response.choices[0].message.content
            except Exception as fallback_e:
                print(f"❌ 备用模型失败: {fallback_e}")
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
        strong {{ color: #d35400; }}
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
    
    # 扩大搜索词的广度，确保AI有足够的“国内”和“国际”双重素材
    print(f"-> [搜索] 目标重点企业: {TARGET_COMPANIES}")
    target_comp_raw = search_info(f"{TARGET_COMPANIES} 威海 中国 国内 国际 出海 最新商业新闻")
    
    print("-> [搜索] 威海其他出海/优质产能企业...")
    alt_comp_raw = search_info("威海市 重点企业 外贸 出口 海外投资 国际市场 优质产能 最新重大商业新闻")
    
    print("-> [搜索] 威海政经与外贸合作...")
    weihai_raw = search_info("威海市 重点舆情 招商引资 政策 外贸 出海 韩国 日本 国际合作 新闻")
    
    print(f"-> [搜索] 行业风向 ({TARGET_INDUSTRY})...")
    ind_raw = search_info(f"{TARGET_INDUSTRY} 中国 国际 全球 行业最新 突发 重大变革 新闻")
    
    print("-> [搜索] 威海辖区银行业务与跨境政策...")
    bank_raw = search_info("银行 国内政策 国际业务 跨境金融 外汇 威海分行 政策 最新新闻")
    
    print("-> [搜索] 宏观与全球局势...")
    macro_global_raw = search_info("中国宏观经济 重点政策落地 全球经济 国际贸易 重大事件 新闻")
    
    print("-> [搜索] 科技杂谈...")
    tech_raw = search_info("前沿科技 人工智能 AI 机器人 新能源 中国突破 全球巨头动向")
    
    print("所有信息收集完毕，正在强力约束大模型生成...")
    briefing = generate_briefing(llm_client, model_name, is_gemini, target_comp_raw, alt_comp_raw, weihai_raw, ind_raw, bank_raw, macro_global_raw, tech_raw)
    
    email_subject = f"【威海商业情报】{TODAY_STR}"
    send_email(email_subject, briefing)
    print("流程执行成功！")
