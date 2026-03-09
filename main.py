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

# 替换为 DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat") 
API_REQUEST_DELAY = float(os.getenv("API_REQUEST_DELAY", "3.0"))

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVERS = os.getenv("EMAIL_RECEIVERS")
SMTP_SERVER = "smtp.qq.com" 

TODAY_STR = datetime.date.today().strftime("%Y年%m月%d日")
CURRENT_YEAR = datetime.date.today().year
GLOBAL_SEEN_URLS = set()

# 拦截旧闻正则
OUTDATED_YEAR_PATTERN = re.compile(r'(201\d|202[0-5])')

# 物理拦截黑名单（极其严格的本地生活与无关词汇池）
JUNK_BLACKLIST = [
    "天气", "降雨", "大风", "暴雪", "冰雹", "气象", # 天气类
    "婚宴", "餐饮", "美食", "饭店", "小吃", "外卖", # 餐饮类
    "幼儿园", "小学", "中学", "中考", "高考", "开学", # 教育类
    "物业", "停水", "停电", "供暖", "公交", "绕行", # 社区民生类
    "医院", "义诊", "诈骗", "交警", "车祸", "报警", # 社会治安类
    "招聘会", "找工作", "相亲", "广场舞", "演唱会"  # 本地活动类
]

# ==========================================
# 2. Bocha Web Search 请求与解析函数
# ==========================================
def search_info(query, days=7, max_results=20, include_domains=None):
    global GLOBAL_SEEN_URLS
    
    freshness = "oneWeek" if days <= 7 else "noLimit"
    include_str = "|".join(include_domains) if include_domains else ""

    payload = {
        "query": query,
        "freshness": freshness,
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
        data_block = resp_json.get("data", resp_json)
        webpages = data_block.get("webPages", {}).get("value", [])
        
        results_str = []
        
        for item in webpages:  # 确保这里前面是标准的 8 个空格
            snippet = item.get("snippet", "")
            summary = item.get("summary", "")
            raw_content = f"{snippet} {summary}".replace('\n', ' ')
            content = raw_content[:250]
            source_url = item.get("url", "无来源链接")
            name = item.get("name", "无标题")

            # --- 新增的物理黑名单拦截逻辑 ---
            is_junk = False
            for junk_word in JUNK_BLACKLIST:
                if junk_word in name or junk_word in content:
                    is_junk = True
                    break
            if is_junk:
                continue # 只要触碰黑名单，直接抛弃，不喂给大模型
            # -------------------------------

            if source_url in GLOBAL_SEEN_URLS and source_url != '无来源链接':
                continue
            if OUTDATED_YEAR_PATTERN.search(source_url) or OUTDATED_YEAR_PATTERN.search(content):
                continue
            
            GLOBAL_SEEN_URLS.add(source_url)
            results_str.append(f"【标题】: {name} \n【内容】: {content} \n【来源】: {source_url}\n")
            
        return "\n".join(results_str) if results_str else "暂无直接搜索结果。"
    except Exception as e:
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
    1. 角色：顶尖投行研究所首席经济师，专精于外经外贸、宏观政治、海外投资等业务，会洞察宏微观趋势。生成的内容不要修辞，不要客套，极端客观。今天是{TODAY_STR}。
    2. 辖区绝对定义：下文中所有提到“大威海地区”、“威海市辖区”、“威海本地”的概念，均【严格且仅包含】威海、荣成、文登、乳山四个区域。
    3. 严格审查每条素材的时间与真实性:
       - 如果内容事件发生时间涉及{TODAY_STR}之前一周以上的旧闻，绝对不予采纳！
       - 严禁拿旧闻（{CURRENT_YEAR - 1}年及以前的内容）凑数，或伪造虚假URL。
    4. 【反摆烂绝对红线】：严禁在正文中输出任何诸如“受限于素材密度”、“未搜索到相关信息”等借口或声明性文字。必须竭尽全力从下方庞大的素材池中挖掘信息，严格满足各板块要求的数量！
    5. 一个来源链接（URL）最多只能对应生成一条新闻，严格禁止一个素材反复使用。
    
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
    一、 重点企业动态（强制生成 15 条）：
        【收录标准】：必须且只能是具体的实体企业。企业必须有明确的涉外属性（国际业务、海外投资、出口订单、外贸潜力）或重大的实体产能扩建。优先包含给定目标企业（{TARGET_COMPANIES}）的业务动态，不要采信复工复产、招聘厂房出租等事务性新闻信息。
        严格排除以下5点内容，若素材属于此类，直接抛弃，宁可从素材池深挖也禁止用其凑数：
          1. 绝对禁止包含任何银行、金融机构（此类应归入第四部分）。
          2. 绝对禁止包含区域宏观经济、政府招商会议、工作动员大会（此类应归入第二部分）。
          3. 绝对禁止包含任何形式的“股价动态”、“股票价格”、“涨跌幅”新闻。
          4. 绝对禁止包含纯国内本地生活服务类企业（如新闻内容包含国内客运、饭店、国内文旅、学校、本地商店等关键词，不予采信）。
          5. 绝对禁止同一条素材用于生成两条及以上的新闻。
        注意，企业必须严格限制在威海辖区内，绝对禁止纳入非威海的全国性科技公司！必须凑够15条，严禁写借口。
    
    二、 威海本地政经（强制生成 8 条）：
        【收录与配比标准】：必须严格凑够 8 条，且内部结构必须执行以下硬性配比，绝对不许越界：
        1. 核心政经与产业（6至8条）：必须聚焦威海市辖区的产业发展、外经外贸、重大招商引资、新质生产力、地方政府债务、重大会议、工作部署、重要政府领导访问调研等政治经济领域新闻。
        2. 民生与消费（最高限额 2 条）：国内消费市场、文旅活动、人才招聘等社会民生新闻【严格限制在 2 条以内】。
        若民生消费类素材不足，剩余名额全部由“核心政经与产业”补齐。绝对排斥社会奇闻、恶性事件。严禁写借口。

    三、 行业风向（强制生成每个行业 2 条）：
        禁止聚焦单一企业公关稿。每个行业配齐一内一外共两条新闻。新闻内容须为行业内最新突破、重大利好或利空、可能影响行业的重要事件及其影响，限制采用研报摘要。

    四、 金融与银行（强制生成 8 条）：
        该板块要求如下：
        1. 金融宏观（5条）：LPR、存款准备金率、美联储利率、汇率等发生重大变化或其他有出海需求的中国大陆企业应当关注的其他新闻。
        2. 本地银行（3条）：新闻主体严格限制在威海市分行及其下属支行，关于跨境结算、对公业务、出口信贷等方面出台新优惠政策或其他领域的新闻，不包括银行股价变动等出口企业进行海外业务不需要的信息。
        3. 汇率新闻仅限生成一条，且日期应为素材池内日期最新的那条。

    五、 宏观与全球重点局势（强制生成 7 条）：
        国内与国际政治经济、贸易局势、突发事件重大新闻。其中国内6条，国际4条。
        新闻内容要限制在会影响产业发展、大宗商品价格、经贸局势的新闻。

    六、 科技前沿与大语言模型（强制生成 9 条）：
        全面汇总4条大语言模型最新焦点、2条中国科技进展（AI或者机器人或者新能源）及3条全球前沿动向。所用发布时间须为{TODAY_STR}的三日内，消息内事件的发生时间也须为{TODAY_STR}的三日内，严格审核。

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
    comp_raw_target = search_info(f"({target_or_str}) (签约 OR 中标 OR 财报 OR 出海 OR 布局 OR 产能) -股价 -涨停 -跌停 -股市", max_results=55)
    comp_raw_weihai = search_info("(威海 OR 荣成 OR 文登 OR 乳山) 企业 (外贸 OR 出海 OR 跨境电商 OR 国际业务 OR 海外订单 OR 投资) -旅游 -文娱 -餐饮 -客运 -银行 -股价 -动员大会 -招聘 -房产", max_results=65)
    comp_raw = f"【指定目标企业】\n{comp_raw_target}\n\n【威海其他出海企业】\n{comp_raw_weihai}"
    
    print("-> 搜集大威海政经...")
    weihai_raw = search_info("(威海 OR 荣成 OR 文登 OR 乳山) (宏观经济 OR 招商引资 OR 产业政策 OR 外经贸 OR 新质生产力 OR 政府债务 OR 工作部署 OR 消费数据 OR 走访 OR 调研 OR 市长) -奇闻 -事故 -学校", max_results=25)
    
    industry_data = {}
    for ind in INDUSTRY_LIST:
        industry_data[ind] = search_info(f"{ind}行业 (市场规模 OR 最新政策 OR 发展趋势 OR 全球宏观 OR 最新动态)", max_results=25)
        
    print("-> 搜集金融与银行业务...")
    # 增加 -A股 -炒股 -超买 -技术分析 等排除词
    finance_macro_raw = search_info("(LPR OR 存款准备金率 OR 美联储利率 OR 汇率变动 OR 跨境人民币 OR 大宗 OR 美元 OR 石油 OR 黄金) -A股 -炒股 -股市 -超买 -超卖 -技术分析", max_results=20)
    bank_raw = search_info("(威海 OR 荣成 OR 文登 OR 乳山) 银行 (跨境结算 OR 国际业务 OR 外汇便利化 OR 对公业务 OR 银企对接 OR 出口信贷) -零售 -个人 -股价 -涨停", max_results=20)
    finance_raw = f"【金融宏观数据】\n{finance_macro_raw}\n\n【威海辖区银行业务】\n{bank_raw}"
    
    print("-> 搜集宏观局势...")
    # 同样加上二级市场排除词
    macro_raw = search_info("(中国宏观经济 OR 全球局势 OR 国际贸易 OR 出海政策 OR 突发事件) 最新新闻 -股市 -A股 -大盘", max_results=35)
    
    TECH_MEDIA_DOMAINS = [
        "qbitai.com", "jiqizhixin.com", "36kr.com", "leiphone.com", "geekpark.net",
        "techcrunch.com", "venturebeat.com", "theverge.com"
    ]
    
    print("-> 搜集科技前沿 (AI/大模型/机器人/新能源)...")
    tech_raw = search_info("(人工智能 OR 大语言模型 OR 机器人 OR 新能源) (前沿动向 OR 最新突破)", max_results=25, include_domains=TECH_MEDIA_DOMAINS)
    
    print("-> 智能新闻官正在撰写超级周报...")
    briefing = generate_briefing(client, model, comp_raw, weihai_raw, industry_data, finance_raw, macro_raw, tech_raw)
    
    send_email(f"【威海商业情报】{TODAY_STR}", briefing)
