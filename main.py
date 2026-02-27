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
raw_companies = os.getenv("TARGET_COMPANIES") or "山东未来机器人有限公司 威海广泰 威海国际经济技术合作股份有限公司 双丰物探 威尔海姆 迪尚集团"
TARGET_COMPANIES = raw_companies.replace('、', ' ').replace('，', ' ') 

raw_industry = os.getenv("TARGET_INDUSTRY") or "工程承包 橡胶轮胎 医疗器械 油气装备 机器人"
INDUSTRY_LIST = [i for i in raw_industry.replace('、', ' ').replace('，', ' ').split() if i]

SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash") # 修复了上一版可能存在的模型名称错误
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
# 2. 增强搜索函数 (移除干扰词)
# ==========================================
def search_info(query, days=7, max_results=15, include_domains=None):
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": SEARCH_API_KEY,
        "query": query, # 移除了强拼凑的中文日期，防止语义污染
        "search_depth": "advanced",
        "include_answer": False, 
        "days": days,
        "max_results": max_results
    }
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
    【全局核心设定】
    1. 角色：顶尖投行研究所首席经济师。无修辞，无客套，极端客观。今天是{TODAY_STR}。
    2. 辖区绝对定义：下文中所有提到“大威海地区”、“威海市辖区”、“威海本地”的概念，均【严格且仅包含】威海、荣成、文登、乳山四个区域。
    3. 严禁旧闻：仔细核查URL和内容年份，非近期内容一律舍弃。特例：LMSYS榜单无更新时输出特定话术。

    【极度严厉的排版与格式指令】
    1. 必须首先生成【目录】，严格照抄以下 HTML 格式：
       <h3 style="color: #1a365d; font-size: 18px; font-weight: normal; margin-top: 20px; margin-bottom: 10px;">一、 重点企业动态</h3>
       <div style="font-size: 14px; color: #333; line-height: 1.8;">
       1. [新闻标题1]<br>
       2. [新闻标题2]<br>
       </div>
    2. 正文部分格式指令：
       正文所有板块的每一条新闻，【绝对禁止使用 Markdown 列表(* 或 -)】，必须严格使用以下 HTML 结构框定，以确保字号精确递减：
       <div style="margin-bottom: 20px;">
         <div style="font-size: 14px; font-weight: bold; color: #333;">[序号]. [标题]</div>
         <div style="font-size: 14px; color: #333; line-height: 1.6; margin-top: 4px;">[用三句话精确概括核心事件、商业动作及影响]</div>
         <div style="font-size: 12px; color: #666; margin-top: 4px;">关键词：[词1] | [词2]</div>
         <div style="font-size: 10px; color: #999; margin-top: 4px;">来源：<a href="[URL]" style="color: #3498db; text-decoration: none;">[URL]</a></div>
       </div>

    【六大板块内容架构（基于下方素材池）】
    一、 重点企业动态（15条）：
        必须优先包含给定目标企业（{TARGET_COMPANIES}）的最新商业动态。其次补充威海市辖区内其他产品受海外认可、商业模式可行、符合新质生产力的优质产能企业。
    
    二、 威海本地政经（8条）：
        绝对排斥文化、旅游、社会奇闻。必须且只能聚焦：威海市辖区的宏观经济、重大招商引资、外经外贸政策、国际产能合作。

    三、 行业风向（每个行业2条）：
        针对素材池中的行业。禁止聚焦单一企业公关稿，必须提炼为券商研报视角的“行业级”发展、政策或宏观趋势。
        标题强制格式：[XX行业国内动态] 和 [XX行业国际动态]。每个行业必须配齐一内一外。

    四、 金融与银行（8条）：
        分两部分严格筛选：
        1. 金融宏观（5条）：外贸及出海企业高度关注的硬指标（LPR、法定存款准备金率、美联储联邦基金利率，以及USD、EUR、JPY、GBP兑人民币汇率的重大变化）。
        2. 本地银行（3条）：威海市辖区内开展业务的银行，关于跨境结算、国际业务便利化、对公出海信贷的政策新闻（禁止收录个人压岁钱、零售理财等无关新闻）。

    五、 宏观与全球重点局势（7条）：
        国内政治经济与国际政治经济重大新闻。国内3条，国际4条。

    六、 科技前沿与大语言模型（9条）：
        第1条必为权威跑分排行榜（如LMSYS）最新榜单（无变动和大语言模型焦点部分一起输出大模型新闻）。随后为大语言模型焦点、中国科技进展（AI/机器人/新能源）、全球前沿动向。该部分要严格审核，保障发布时间和内容均为三日内。

    【素材池】
    一/重点企业: {comp_raw}
    二/大威海政经: {weihai_raw}
    三/行业: {ind_context}
    四/金融与银行: {finance_raw}
    五/宏观: {macro_raw}
    六/科技: {tech_raw}

    【输出框架】：
    # 威海营业部超级周报
    **报告日期：** {TODAY_STR} | ** 来自您的超级智能新闻官🤖
    ---
    ## 目录
    （目录 HTML 代码）
    ---
    ## 一、 重点企业动态
    （正文 HTML 代码）
    ## 二、 威海本地政经
    （正文 HTML 代码）
    ## 三、 行业风向
    （正文 HTML 代码）
    ## 四、 金融与银行
    （正文 HTML 代码）
    ## 五、 宏观与全球重点局势
    （正文 HTML 代码）
    ## 六、 科技前沿与大语言模型
    （正文 HTML 代码）
    ---
    <p style="text-align: center;"><strong>以上为本周新闻，均为自动收集并由AI生成</strong></p >
    <p style="text-align: center;">🤖我们下周再见🤖</p >
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

    # 替换 Markdown 代码块标记，防止 LLM 自作主张输出 ```html
    markdown_content = markdown_content.replace("```html", "").replace("```", "")
    html_content = markdown.markdown(markdown_content)
    
    full_html = f"""
    <html>
    <head><style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; line-height: 1.8; color: #333; font-size: 14px; }} 
        h1 {{ color: #1a365d; font-size: 24px; border-bottom: 2px solid #1a365d; padding-bottom: 10px; }}
        h2 {{ color: #2c3e50; font-size: 20px; border-bottom: 1px dashed #ccc; padding-bottom: 8px; margin-top: 30px; }}
        a {{ text-decoration: none; word-break: break-all; }}
    </style></head>
    <body>{html_content}</body>
    </html>
    """

    msg = MIMEMultipart()
    msg['From'] = formataddr(("Weihai Business Briefing", EMAIL_SENDER)) # 移除了Emoji防退信
    msg['To'] = ", ".join(receivers_list)
    msg['Subject'] = Header(subject, 'utf-8')
    msg.attach(MIMEText(full_html, 'html', 'utf-8'))

    try:
        print("尝试使用 SSL (端口 465) 发送邮件...")
        server = smtplib.SMTP_SSL(SMTP_SERVER, 465, timeout=30)
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, receivers_list, msg.as_string())
        server.quit()
        print("✅ 简报发送成功 (465端口)")
    except Exception as e1:
        print(f"⚠️ 465 端口失败 ({e1})，尝试备用 STARTTLS (端口 587)...")
        try:
            time.sleep(3) 
            server = smtplib.SMTP(SMTP_SERVER, 587, timeout=30)
            server.starttls() 
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, receivers_list, msg.as_string())
            server.quit()
            print("✅ 简报发送成功 (587端口)")
        except Exception as e2:
            print(f"❌ 邮件发送最终失败: {e2}")

# ==========================================
# 5. 执行主流程
# ==========================================
if __name__ == "__main__":
    print(f"-> 启动报告生成器，当前日期: {TODAY_STR} ...")

    # 【关键修改】：去掉了多余的括号，并加入了 timeout=120.0 防止大模型写长文时超时断连
    if not CUSTOM_API_KEY:
        client = OpenAI(
            api_key=GEMINI_API_KEY, 
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            timeout=120.0
        )
    else:
        client = OpenAI(
            api_key=CUSTOM_API_KEY, 
            base_url=CUSTOM_BASE_URL,
            timeout=120.0
        )
        
    model = GEMINI_MODEL if not CUSTOM_API_KEY else CUSTOM_MODEL
    is_gem = not bool(CUSTOM_API_KEY)

    print(f"-> 搜集重点与优质产能企业...")
    # 拆分搜索：确保指定企业不被淹没
    comp_raw_target = search_info(f"{TARGET_COMPANIES} 签约 中标 财报 出海 布局 产能 最新动态", max_results=15)
    comp_raw_weihai = search_info("威海 OR 荣成 OR 文登 OR 乳山 制造业 优质产能 外贸 新质生产力 企业 出海 -旅游 -文娱", max_results=15)
    comp_raw = f"【指定目标企业】\n{comp_raw_target}\n\n【威海其他优质企业】\n{comp_raw_weihai}"
    
    print("-> 搜集大威海政经...")
    weihai_raw = search_info("威海 OR 荣成 OR 文登 OR 乳山 宏观经济 招商引资 政策 外经贸 国际产能合作 专精特新 产业集群 -旅游 -消费 -文化 -娱乐", max_results=20)
    
    industry_data = {}
    for ind in INDUSTRY_LIST:
        industry_data[ind] = search_info(f"{ind} 行业 市场规模 政策 发展趋势 全球 宏观 研报", max_results=10)
        
    print("-> 搜集金融与银行业务...")
    # 拆分搜索：宏观指标与本地对公银行分离
    finance_macro_raw = search_info("LPR 存款准备金率 美联储联邦基金利率 USD EUR JPY GBP 兑人民币 汇率 变动", max_results=10)
    bank_raw = search_info("威海 OR 荣成 OR 文登 OR 乳山 银行 跨境结算 国际业务 外汇便利化 对公业务 -零售金融 -个人理财", max_results=10)
    finance_raw = f"【金融宏观数据】\n{finance_macro_raw}\n\n【威海辖区银行业务】\n{bank_raw}"
    
    print("-> 搜集宏观局势...")
    macro_raw = search_info("中国宏观经济 全球局势 国际贸易 重大新闻")
    
    LMSYS_DOMAIN = ["lmsys.org"]
    TECH_MEDIA_DOMAINS = [
        "qbitai.com", "jiqizhixin.com", "36kr.com", "leiphone.com", "geekpark.net",
        "techcrunch.com", "venturebeat.com", "theverge.com"
    ]
    
    print("-> 搜集权威大语言模型排行榜...")
    llm_leaderboard_raw = search_info("LLM Leaderboard Chatbot Arena Model Ranking updates", max_results=5, include_domains=LMSYS_DOMAIN)
    
    print("-> 搜集其他科技前沿 (AI/机器人/新能源)...")
    tech_general_raw = search_info("人工智能 AI大模型 机器人 新能源 全球前沿动向 最新突破", max_results=20, include_domains=TECH_MEDIA_DOMAINS)
    
    tech_raw = f"【权威大模型榜单专区（来自lmsys.org）】\n{llm_leaderboard_raw}\n\n【其他科技进展】\n{tech_general_raw}"
    
    print("-> 智能新闻官正在撰写超级周报...")
    briefing = generate_briefing(client, model, is_gem, comp_raw, weihai_raw, industry_data, finance_raw, macro_raw, tech_raw)
    
    send_email(f"【威海商业情报】{TODAY_STR}", briefing)
