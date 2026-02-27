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
# 1. 变量解析与环境加载
# ==========================================
raw_companies = os.getenv("TARGET_COMPANIES") or "山东未来机器人有限公司 威海广泰 威海国际经济技术合作股份有限公司"
TARGET_COMPANIES = raw_companies.replace('、', ' ').replace('，', ' ') 

raw_industry = os.getenv("TARGET_INDUSTRY") or "工程承包 橡胶轮胎 医疗器械"
INDUSTRY_LIST = [i for i in raw_industry.replace('、', ' ').replace('，', ' ').split() if i]

SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_REQUEST_DELAY = float(os.getenv("GEMINI_REQUEST_DELAY", "3.0"))

CUSTOM_API_KEY = os.getenv("CUSTOM_API_KEY")
CUSTOM_BASE_URL = os.getenv("CUSTOM_BASE_URL")
CUSTOM_MODEL = os.getenv("CUSTOM_MODEL")

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVERS = os.getenv("EMAIL_RECEIVERS")
SMTP_SERVER = "smtp.qq.com" 

TODAY_STR = datetime.date.today().strftime("%Y年%m月%d日")

# ==========================================
# 2. 增强搜索函数 (提高最大返回条数以满足庞大内容需求)
# ==========================================
def search_info(query, days=7, max_results=15):
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": SEARCH_API_KEY,
        "query": f"{query} (current week {TODAY_STR})",
        "search_depth": "advanced",
        "include_answer": False, 
        "days": days,
        "max_results": max_results
    }
    try:
        response = requests.post(url, json=payload).json()
        results_str = []
        for result in response.get('results', []):
            content = result.get('content', '').replace('\n', ' ')
            source_url = result.get('url', '无来源链接')
            results_str.append(f"【内容】: {content} \n【来源】: {source_url}\n")
        return "\n".join(results_str) if results_str else "暂无直接搜索结果。"
    except Exception as e:
        return f"搜索失败: {e}"

# ==========================================
# 3. 提示词与简报生成 (超级研报排版)
# ==========================================
def generate_briefing(client, model_name, is_gemini, comp_raw, weihai_raw, ind_data_dict, finance_raw, macro_raw, tech_raw):
    ind_context = ""
    for ind, content in ind_data_dict.items():
        ind_context += f"--- 行业: {ind} ---\n{content}\n"

    prompt = f"""
    【角色】
    你是来自顶尖投行研究所的首席经济师，对宏观政策和经济、行业动态、公司发展都有深入的见解。今天是{TODAY_STR}。所有新闻必须是本周最新动态。在所有内容中禁止修辞。

    【极度严厉的排版与格式指令】
    1. 你必须首先生成【目录】，然后再输出正文。
    2. 目录格式：一级标题为板块，二级为序号和新闻标题（例：一、重点企业动态 \\n 1. 山东未来机器人... \\n 2. 威海广泰...）
    3. 正文部分：所有新闻的要素必须【垂直排版，另起一行】。

    【六大板块内容架构（不准找借口缺漏）】
    一、 重点企业动态（必须凑齐15条）：
        包含指定企业，同时深挖指定企业之外的大威海地区（含荣成、文登、乳山）符合“商业模式走得通、海外买家认可的新质生产力”的优质产能企业。
        每条格式：
        序号. **[新闻标题]**
        梗概：[用三句话精确概括核心事件、商业动作及影响]
        关键词：[词1] | [词2]
        来源：[URL地址]

    二、 威海本地政经（含荣成、文登、乳山）（必须凑齐8条）：
        国内焦点（产业升级、招商引资、重大基建、营商环境、民生政策等威海本土政经新闻） 4条 + 国际与出海合作（威海企业海外订单、海外投资、跨境合作、外贸数据、国际展会等对外经贸新闻） 4条。
        每条格式：
        序号. **[新闻标题]**
        梗概：[第一句讲事实，第二句讲影响，第三句讲对威海经济发展和企业的意义]
        关键词：[词1] | [词2]
        来源：[URL地址]

    三、 行业风向（不受固定条数限制）：
        针对以下行业：{list(ind_data_dict.keys())}。每个行业必须提供 1条国内 + 1条国外 新闻。
        每条格式同上。

    四、 金融与银行（至少6条）：
        包含国内外重大金融新闻（美元/日元/欧元兑人民币汇率异动、LPR基准利率、美联储利率等会影响中国企业产能转移、对外投资、对外贸易的指标及价格变化），以及威海市辖区，即威海、荣成、乳山、文登的银行业务与政策。
        每条格式同上。

    五、 宏观与全球重点局势（必须7条）：
        3条国内宏观 + 4条国际重点局势。
        每条格式同上。

    六、 科技前沿与大语言模型（必须9条）：
        分为三部分：
        【大模型焦点】（4条）：
        第1条：**必须是当天的权威普遍认可的大语言模型跑分排行榜（如LMSYS）最新前十名榜单与解读。**
        第2-4条：大语言模型相关重磅新闻。注意时效性，一定要近三天最新。
        【中国科技进展】（2条）：AI/机器人/新能源等。
        【全球科技前沿】（3条）：全球巨头前沿动向。
        每条格式同上。

    【素材池】
    企业A: {comp_raw}
    大威海政经B: {weihai_raw}
    行业C: {ind_context}
    金融与银行D: {finance_raw}
    宏观E: {macro_raw}
    大模型与科技F: {tech_raw}

    【输出框架】：
    # 威海周报

    **报告日期：** {TODAY_STR} | **发件人：** 来自您的智能新闻官🤖
    ---

    ## 目录
    （在此处自动生成带有序号和标题的目录）
    ---

    ## 一、 重点企业动态
    （15条，执行三句话梗概与关键词格式）

    ## 二、 威海本地政经（含荣成、文登、乳山）
    **国内焦点：**
    （4条）
    **国际与出海合作：**
    （4条）

    ## 三、 行业风向
    （按行业分类，每行业1内1外）

    ## 四、 金融与银行
    （国内外重大金融新闻与辖区银行业务）

    ## 五、 宏观与全球重点局势
    **国内宏观：**
    （3条）
    **全球局势：**
    （4条）

    ## 六、 大语言模型与科技前沿
    **大语言模型焦点：**
    （4条，第一条必为权威排行榜前十名）
    **中国科技进展：**
    （2条）
    **全球科技前沿：**
    （3条）

    <p style="text-align: center;"><strong>以上为本周新闻，均为自动收集并由AI生成</strong></p>
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
        return f"生成简报失败: {e}"

# ==========================================
# 4. 邮件发送 (优化目录与段落间距)
# ==========================================
def send_email(subject, markdown_content):
    if not EMAIL_SENDER or not EMAIL_PASSWORD: return
    receivers_list = [EMAIL_SENDER] if not EMAIL_RECEIVERS else [r.strip() for r in EMAIL_RECEIVERS.replace('，', ',').split(',') if r.strip()]

    html_content = markdown.markdown(markdown_content)
    full_html = f"""
    <html>
    <head><style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; line-height: 1.8; color: #333; font-size: 16px; }} 
        h1 {{ color: #1a365d; font-size: 28px; border-bottom: 3px solid #1a365d; padding-bottom: 12px; }}
        h2 {{ color: #2c3e50; font-size: 22px; border-bottom: 1px dashed #ccc; padding-bottom: 8px; margin-top: 40px; }}
        h3 {{ color: #d35400; font-size: 18px; margin-top: 20px; }}
        p {{ margin-bottom: 12px; }}
        a {{ color: #3498db; text-decoration: none; word-break: break-all; }}
        strong {{ color: #c0392b; }}
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
        server = smtplib.SMTP_SSL(SMTP_SERVER, 465, timeout=30)
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, receivers_list, msg.as_string())
        server.quit()
        print("✅ 简报发送成功")
    except Exception as e:
        print(f"❌ 发送失败: {e}")

# ==========================================
# 5. 执行主流程
# ==========================================
if __name__ == "__main__":
    # 恢复了首个工作日的检测逻辑
    if TRIGGER_EVENT == "schedule" and not is_first_workday_of_week():
        print("今日非本周首个工作日，任务跳过。")
        sys.exit(0)

    client = OpenAI(api_key=GEMINI_API_KEY, base_url="https://generativelanguage.googleapis.com/v1beta/openai/") if not CUSTOM_API_KEY else OpenAI(api_key=CUSTOM_API_KEY, base_url=CUSTOM_BASE_URL)
    model = GEMINI_MODEL if not CUSTOM_API_KEY else CUSTOM_MODEL
    is_gem = not bool(CUSTOM_API_KEY)

    print(f"-> 搜集重点与优质产能企业 (最大抓取量以满足15条)...")
    comp_raw = search_info(f"{TARGET_COMPANIES} OR 威海 荣成 文登 乳山 优质产能 新质生产力 出海 重点企业 最新商业新闻", max_results=30)
    
    print("-> 搜集大威海政经...")
    weihai_raw = search_info("威海 荣成 文登 乳山 招商引资 政策 外贸 国际合作 最新动向", max_results=20)
    
    industry_data = {}
    for ind in INDUSTRY_LIST:
        industry_data[ind] = search_info(f"{ind} 行业 中国 国际 最新 突发新闻")
        
    print("-> 搜集金融与银行业务...")
    finance_raw = search_info("跨境结算 美元 日元 欧元 人民币 汇率变动 LPR 联邦基金利率 威海辖区银行 外汇 政策")
    
    print("-> 搜集宏观局势...")
    macro_raw = search_info("中国宏观经济 全球局势 国际贸易 重大新闻")
    
    print("-> 搜集科技与大模型排行榜...")
    tech_raw = search_info("LMSYS Chatbot Arena 大语言模型 跑分排行榜 前十名 最新发布 人工智能 机器人 新能源 全球前沿动向", max_results=25)
    
    print("-> 智能新闻官正在撰写超级周报 (这可能需要近一分钟的时间)...")
    briefing = generate_briefing(client, model, is_gem, comp_raw, weihai_raw, industry_data, finance_raw, macro_raw, tech_raw)
    
    send_email(f"【威海商业情报】{TODAY_STR}", briefing)
