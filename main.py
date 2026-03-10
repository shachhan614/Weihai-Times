import os
import sys
import datetime
import time
import requests
import json
import re
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

BOCHA_API_KEY = os.getenv("BOCHA_API_KEY")
BOCHA_WEB_SEARCH_API_URL = "https://api.bocha.cn/v1/web-search"

# DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat") 
API_REQUEST_DELAY = float(os.getenv("API_REQUEST_DELAY", "3.0"))

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVERS = os.getenv("EMAIL_RECEIVERS")
SMTP_SERVER = "smtp.qq.com" 

TODAY_STR = datetime.date.today().strftime("%Y年%m月%d日")
GLOBAL_SEEN_URLS = set()

# 物理拦截黑名单（极其严格的本地生活、房产与二级市场词汇池）
JUNK_BLACKLIST = [
    # 1. 房产与便民小广告
    "出租", "招租", "日租", "写字楼", "厂房", "商铺", "转让", "物业", 
    
    # 2. 人事与日常采购
    "招聘", "找工作", "办公用品", "政府采购", "信息公示平台", "就业管理",
    
    # 3. 二级市场与炒股
    "涨停", "跌停", "超买", "超卖", "多空", "资金净流入", "资金净流出", 
    "龙虎榜", "证券研报", "上行", "拐点", "持仓", "避险",
    "异常波动", "异动公告", "竞价交易", "减持", "增持", "主力资金", "证券策略", "牛市", "熊市", "个股",
    
    # 4. 基础民生与社会治安
    "天气", "降雨", "气象", "婚宴", "餐饮", "幼儿园", "小学", "中学", "高考",
    "医院", "义诊", "交警", "车祸", "报警", "诈骗", "演唱会", "停水", "停电"
]

# ==========================================
# 2. Bocha Web Search 请求与解析函数
# ==========================================
def search_info(query, max_results=20, include_domains=None):
    global GLOBAL_SEEN_URLS
    
    include_str = "|".join(include_domains) if include_domains else ""

    payload = {
        "query": query,
        "freshness": "oneWeek", # 物理锁死只抓取最近 7 天的新闻
        "summary": True,
        "count": min(max_results, 50) 
    }
    
    if include_str:
        payload["include"] = include_str

    headers = {
        "Authorization": f"Bearer {BOCHA_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            url=BOCHA_WEB_SEARCH_API_URL, 
            headers=headers, 
            json=payload, 
            timeout=15
        )
        response.raise_for_status()
        resp_json = response.json()
        
        webpages = []
        if "data" in resp_json and "webPages" in resp_json["data"]:
            webpages.extend(resp_json["data"]["webPages"].get("value", []))

        results_str = []
        
        for item in webpages:
            snippet = item.get("snippet", "")
            summary = item.get("summary", "")
            raw_content = f"{snippet} {summary}".replace('\n', ' ')
            content = raw_content[:250]
            source_url = item.get("url", "无来源链接")
            name = item.get("name", "无标题")

            # 1. 物理黑名单拦截
            is_junk = False
            for junk_word in JUNK_BLACKLIST:
                if junk_word in name or junk_word in content:
                    is_junk = True
                    break
            if is_junk:
                continue

            # 2. 全局去重
            if source_url in GLOBAL_SEEN_URLS and source_url != '无来源链接':
                continue
            
            GLOBAL_SEEN_URLS.add(source_url)
            results_str.append(f"【标题】: {name} \n【内容】: {content} \n【来源】: {source_url}\n")
        
        short_query = query[:20] + "..." if len(query) > 20 else query
        print(f"    [雷达] 检索: {short_query} -> 抓取到 {len(webpages)} 条，过滤后剩余 {len(results_str)} 条")
        
        return "\n".join(results_str) if results_str else "暂无直接搜索结果。"
    except Exception as e:
        print(f"    [报错] 检索: {query[:20]}... 发生错误: {e}")
        return f"搜索失败: {e}"

# ==========================================
# 3. 提示词与简报生成
# ==========================================
def generate_briefing(client, model_name, comp_raw, weihai_raw, ind_data_dict, finance_raw, macro_raw, tech_raw):
    ind_context = ""
    for ind, content in ind_data_dict.items():
        ind_context += f"--- 行业: {ind} ---\n{content}\n"

    prompt = f"""
    【全局核心设定】
    1. 角色：顶尖投行研究所首席经济师。极端客观，无修辞。今天是{TODAY_STR}。
    2. 【时效性信任前提】：下方素材池均已被系统强制锁死为最近7天内的最新资讯！无条件信任它们的时效性！即使没写明具体日期，也必须采纳，绝对不要因为找不到日期就抛弃！
    3. 【信息来源绝对限制（RAG铁律）】：所有生成内容 100% 仅提取自下方【素材池】！严禁动用记忆捏造！URL 必须一字不差复制。
    4. 【优雅处理真空期】：若某板块毫无符合标准的新闻，请仅输出一句：“本周暂无符合条件的高价值动态。” 严禁写借口。
    5. 【跨板块防串台与查重红线】：
       - 核心铁律：一条新闻只能在一个最合适的板块中出现一次！
       - 绝对禁止在其他板块重复输出，绝对禁止输出诸如“(同某板块某条，此处合并)”此类废话！如果重复，直接彻底删掉！
       - 必须严格核查新闻的核心属性是否属于该板块。例如：海关通关、物流仓库等新闻绝对不能放入“金融与银行”板块！宁可该板块为空，也绝不跨界凑数！

    【极度严厉的排版与格式指令】
    1. 首先生成【目录】，严格使用此 HTML：
       <h3 style="color: #1a365d; font-size: 18px; font-weight: normal; margin-top: 20px; margin-bottom: 10px;">一、 重点企业动态</h3>
       <div style="font-size: 14px; color: #333; line-height: 1.8;">
       1. [新闻标题1]<br>
       2. [新闻标题2]<br>
       </div>
    2. 正文部分格式指令：所有新闻【绝对禁止使用 Markdown 列表(* 或 -)】，必须严格使用以下 HTML 结构：
       <div style="margin-bottom: 20px;">
         <div style="font-size: 14px; font-weight: bold; color: #333;">[序号]. [标题]</div>
         <div style="font-size: 14px; color: #333; line-height: 1.6; margin-top: 4px;">[用三句话精确概括核心事件、商业动作及影响]</div>
         <div style="font-size: 12px; color: #666; margin-top: 4px;">关键词：[词1] | [词2]</div>
         <div style="font-size: 10px; color: #999; margin-top: 4px;">来源：<a href="[URL]" style="color: #3498db; text-decoration: none;">[URL]</a></div>
       </div>

    【六大板块内容架构（基于下方素材池）】
    一、 重点企业动态（最多 15 条）：
        【收录标准】：必须是实体企业。企业必须有涉外属性（国际业务、海外投资、外贸）或重大产能扩建。优先给定目标企业（{TARGET_COMPANIES}）。绝对不允许拿无关内容凑数！
    
    二、 威海本地政经（最多 8 条）：
        1. 核心政经与产业（6-8条）：威海市辖区产业发展、外经外贸、招商引资等。
        2. 民生与消费（最多 2 条）：国内消费市场、文旅等。

    三、 行业风向（每个行业最多 2 条）：
        聚焦行业最新突破、重大利好利空、影响行业的重要事件。

    四、 金融与银行（最多 10 条）：
        【置顶铁律】：本板块的第一条，必须且只能是素材池中日期最新的【美元兑人民币汇率中间价/汇率动态】新闻。如果不包含汇率信息，第一条直接写“本周暂无最新美元汇率数据。”
        【领域严格核查】：后续新闻必须是纯粹的金融宏观数据（LPR、美联储等）或威海本地银行（中行、工行等）的对公跨境业务。绝对禁止将跨境电商园区、海关政策放入此板块！

    五、 宏观与全球重点局势（最多 7 条）：
        国内必须包含最新的国家级产业政策、规划。国际包含地缘政治、贸易战等。

    六、 科技前沿与大语言模型（最多 9 条）：
        大语言模型最新焦点、中国科技进展及全球前沿动向。

    【素材池】
    一/重点企业: {comp_raw}
    二/大威海政经: {weihai_raw}
    三/行业: {ind_context}
    四/金融与银行: {finance_raw}
    五/宏观: {macro_raw}
    六/科技: {tech_raw}

    【输出框架】：
    # 威海营业部超级周报
    **报告日期：** {TODAY_STR} | 来自您的超级智能新闻官🤖
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
    
    time.sleep(API_REQUEST_DELAY)

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
    msg['From'] = formataddr(("Weihai Business Briefing", EMAIL_SENDER)) 
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

    print(f"-> 正在使用 DeepSeek 接口，模型: {DEEPSEEK_MODEL}")
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY, 
        base_url="https://api.deepseek.com",
        timeout=600.0
    )
    model = DEEPSEEK_MODEL

    print(f"-> 搜集重点与优质产能企业...")
    target_or_str = TARGET_COMPANIES.replace(' ', ' OR ')
    comp_raw_target = search_info(f"({target_or_str}) (签约 OR 中标 OR 财报 OR 出海 OR 产能) -股市", max_results=40)
    comp_raw_weihai = search_info("威海 企业 (外贸 OR 出海 OR 跨境电商 OR 国际业务 OR 投资) -旅游 -餐饮", max_results=40)
    comp_raw = f"【指定目标企业】\n{comp_raw_target}\n\n【威海其他出海企业】\n{comp_raw_weihai}"
    
    print("-> 搜集大威海政经...")
    weihai_raw = search_info("威海 (宏观经济 OR 招商引资 OR 产业政策 OR 外经贸 OR 新质生产力 OR 项目) -学校", max_results=35)
    
    industry_data = {}
    for ind in INDUSTRY_LIST:
        industry_data[ind] = search_info(f"{ind}行业 (市场规模 OR 政策 OR 发展趋势 OR 最新动态) -A股", max_results=20)
        
    print("-> 搜集金融与银行业务...")
    # 【新增汇率诱导词】：加入更具体的汇率词，确素材池中有最新的美元汇率
    finance_macro_raw = search_info("美元兑人民币 中间价 汇率 最新 OR LPR OR 美联储利率 OR 大宗商品 OR 关税", max_results=25)
    bank_raw = search_info("威海 银行 (跨境结算 OR 国际业务 OR 对公业务 OR 出口信贷) -零售", max_results=20)
    finance_raw = f"【金融宏观数据】\n{finance_macro_raw}\n\n【威海辖区银行业务】\n{bank_raw}"
    
    print("-> 搜集国内宏观与产业政策...")
    macro_domestic = search_info("国家发改委 OR 工信部 OR 商务部 OR 国务院 (产业政策 OR 宏观经济 OR 进出口数据) 最新", max_results=25)
    
    print("-> 搜集国际地缘与经贸局势...")
    macro_intl = search_info("国际贸易 OR 地缘政治 OR 关税政策 OR 美伊局势 OR 俄乌局势", max_results=25)
    
    macro_raw = f"【国内宏观与产业政策素材池】\n{macro_domestic}\n\n【国际地缘与经贸局势素材池】\n{macro_intl}"
    
    TECH_MEDIA_DOMAINS = [
        "qbitai.com", "jiqizhixin.com", "36kr.com", "leiphone.com", "geekpark.net",
        "techcrunch.com", "venturebeat.com", "theverge.com"
    ]
    
    print("-> 搜集科技前沿 (AI/大模型/机器人/新能源)...")
    tech_raw = search_info("AI OR 大模型 OR 机器人 OR 新能源 最新突破", max_results=25, include_domains=TECH_MEDIA_DOMAINS)
    
    print("-> 智能新闻官正在撰写超级周报...")
    briefing = generate_briefing(client, model, comp_raw, weihai_raw, industry_data, finance_raw, macro_raw, tech_raw)
    
    send_email(f"【威海商业情报】{TODAY_STR}", briefing)
