import os
import sys
import datetime
import time
import requests
import json
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
# 2. 增强搜索函数 (加入防污染白名单机制 include_domains)
# ==========================================
def search_info(query, days=7, max_results=15, include_domains=None):
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": SEARCH_API_KEY,
        "query": f"{query} (current week {TODAY_STR})",
        "search_depth": "advanced",
        "include_answer": False, 
        "days": days,
        "max_results": max_results
    }
    # 如果传入了白名单，则限制只在这些域名内搜索
    if include_domains:
        payload["include_domains"] = include_domains

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
# 3. 提示词与简报生成
# ==========================================
def generate_briefing(client, model_name, is_gemini, comp_raw, weihai_raw, ind_data_dict, finance_raw, macro_raw, tech_raw):
    ind_context = ""
    for ind, content in ind_data_dict.items():
        ind_context += f"--- 行业: {ind} ---\n{content}\n"

    prompt = f"""
    【角色】
    你是来自顶尖投行研究所的首席经济师。今天是{TODAY_STR}。所有新闻必须是本周最新动态。禁止修辞。

    【极度严厉的排版与格式指令】
    1. 必须首先生成【目录】，然后输出正文。
    2. 【目录排版要求】：
       绝对禁止把目录连成一段！为了精确控制字号（标题18px不加粗，正文14px），请在生成【目录】部分时，放弃 Markdown，严格照抄以下 HTML 格式：

       <h3 style="color: #1a365d; font-size: 18px; font-weight: normal; margin-top: 20px; margin-bottom: 10px;">一、 重点企业动态</h3>
       <div style="font-size: 14px; color: #333; line-height: 1.8;">
       1. [新闻标题1]<br>
       2. [新闻标题2]<br>
       ...
       </div>

       <h3 style="color: #1a365d; font-size: 18px; font-weight: normal; margin-top: 20px; margin-bottom: 10px;">二、 威海本地政经</h3>
       <div style="font-size: 14px; color: #333; line-height: 1.8;">
       1. [新闻标题1]<br>
       ...
       </div>
       （其余板块以此类推，必须严格使用 <h3> 和 <div><br> 结构！）

    3. 正文部分：恢复使用 Markdown。所有新闻的要素必须【垂直排版，另起一行】。

    【绝对时效性与 URL 年份查杀机制（防旧闻生死红线）】
    1. 你必须同步核查“文章发布时间”与“事件真实发生时间”。
    2. URL 查杀：你必须仔细检查我提供的每一个【来源】URL。如果网址中包含 "2024"、"2023" 或不属于本月的日期路径（例如 /2024/11/221717.html），说明搜索引擎抓取了严重的过期废料，【绝对禁止使用该条素材】！
    3. 特例容错：如果在限定的 lmsys.org 素材中找不到最近几天发布的新榜单，请不要强行编造，直接在第六部分第1条输出：“1. **LMSYS 官方排行榜本周无显著变动**\\n梗概：LMSYS 官方本周暂未发布新的大模型综合跑分变动，当前格局保持稳定。\\n关键词：LMSYS | 榜单稳定\\n来源：https://lmsys.org”

    【六大板块内容架构（不准缺漏）】
    一、 重点企业动态（必须15条）：
        包含指定企业，同时深挖大威海地区符合新质生产力的优质产能企业。
        每条格式：
        序号. **[新闻标题]**
        梗概：[用三句话精确概括核心事件、商业动作及影响]
        关键词：[词1] | [词2]
        来源：[URL地址]

    二、 威海本地政经（必须8条）：
        国内焦点 4条 + 国际与出海合作 4条。每条格式同上。

    三、 行业风向（不受固定条数限制）：
        针对以下行业：{list(ind_data_dict.keys())}。每个行业必须提供 1条国内 + 1条国外 新闻。每条格式同上。

    四、 金融与银行（至少6条）：
        包含国内外重大金融新闻及威海市辖区银行业务与政策。每条格式同上。

    五、 宏观与全球重点局势（必须7条）：
        3条国内宏观 + 4条国际重点局势。每条格式同上。

    六、 科技前沿与大语言模型（必须9条，严格执行 URL 年份查杀）：
        分为三部分：
        【大模型焦点】（4条）：第1条必为当天的权威跑分排行榜（如LMSYS）最新榜单与解读（如无更新按特例容错输出）。第2-4条必为本周刚发生的重磅新闻。
        【中国科技进展】（2条）：AI/机器人/新能源等本周真实突破。
        【全球科技前沿】（3条）：全球巨头本周最新前沿动向。
        每条格式同上。

    【素材池】
    企业A: {comp_raw}
    大威海政经B: {weihai_raw}
    行业C: {ind_context}
    金融与银行D: {finance_raw}
    宏观E: {macro_raw}
    大模型与科技F: {tech_raw}

    【输出框架】：
    # 超级威海周报

    **报告日期：** {TODAY_STR} | **发件人：** 您的超级智能新闻官🤖
    ---

    ## 目录
    （严格照抄 HTML 代码生成目录）
    ---

    ## 一、 重点企业动态
    ...
    
    ## 二、 威海本地政经
    ...
    
    （其余正文板块正常输出）

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
# 4. 邮件发送
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
        p {{ margin-bottom: 12px; }}
        a {{ color: #3498db; text-decoration: none; word-break: break-all; }}
        strong {{ color: #c0392b; }}
    </style></head>
    <body>{html_content}</body>
    </html>
    """

    msg = MIMEMultipart()
    msg['From'] = formataddr(("来自您的超级智能新闻官🤖", EMAIL_SENDER))
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
    if TRIGGER_EVENT == "schedule" and not is_first_workday_of_week():
        print("今日非本周首个工作日，任务跳过。")
        sys.exit(0)

    client = OpenAI(api_key=GEMINI_API_KEY, base_url="https://generativelanguage.googleapis.com/v1beta/openai/") if not CUSTOM_API_KEY else OpenAI(api_key=CUSTOM_API_KEY, base_url=CUSTOM_BASE_URL)
    model = GEMINI_MODEL if not CUSTOM_API_KEY else CUSTOM_MODEL
    is_gem = not bool(CUSTOM_API_KEY)

    print(f"-> 搜集重点与优质产能企业...")
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
    
    # ---------------------------------------------------------
    # 彻底拦截：榜单官方化，媒体精细化
    # ---------------------------------------------------------
    # 1. 唯一且绝对权威的大模型排名官网
    LMSYS_DOMAIN = ["lmsys.org"]
    
    # 2. 其他科技进展使用的顶尖优质媒体（过滤掉了 CSDN、百家号等内容农场）
    TECH_MEDIA_DOMAINS = [
        "qbitai.com", "jiqizhixin.com", "36kr.com", "leiphone.com", "geekpark.net",
        "techcrunch.com", "venturebeat.com", "theverge.com"
    ]
    
    # 注意这里必须用英文搜索，因为 lmsys.org 是纯英文网站，用中文搜返回是空的
    print("-> 搜集权威大语言模型排行榜 (严苛限制仅在 lmsys.org 内搜索)...")
    llm_leaderboard_raw = search_info("LLM Leaderboard Chatbot Arena Model Ranking updates", max_results=5, include_domains=LMSYS_DOMAIN)
    
    print("-> 搜集其他科技前沿 (AI/机器人/新能源)...")
    tech_general_raw = search_info("人工智能 AI大模型 机器人 新能源 全球前沿动向 最新突破", max_results=20, include_domains=TECH_MEDIA_DOMAINS)
    
    # 组合为科技总素材
    tech_raw = f"【权威大模型榜单专区（来自lmsys.org）】\n{llm_leaderboard_raw}\n\n【其他科技进展】\n{tech_general_raw}"
    
    print("-> 智能新闻官正在撰写超级周报...")
    briefing = generate_briefing(client, model, is_gem, comp_raw, weihai_raw, industry_data, finance_raw, macro_raw, tech_raw)
    
    send_email(f"【威海商业情报】{TODAY_STR}", briefing)
