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

raw_industry = os.getenv("TARGET_INDUSTRY") or "工程承包 橡胶 轮胎 纺织 医疗器械 油气装备 机器人"
INDUSTRY_LIST = [i for i in raw_industry.replace('、', ' ').replace('，', ' ').split() if i]

# 巨头名单（建议在此处或环境变量中加入“巴林国家石油公司”以防媒体用全称）
raw_giants = os.getenv("INDUSTRY_GIANTS") or "巴林石油 巴林国家石油公司 Bapco 沙特阿美 Aramco 丹格特 Dangote 马士基 Maersk"
GIANTS_LIST = [i for i in raw_giants.replace('、', ' ').replace('，', ' ').split() if i]

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

# 物理拦截黑名单（史诗级加强：彻底封杀文旅、股市黑话、杂鱼招标）
JUNK_BLACKLIST = [
    # 房产与杂务
    "出租", "招租", "日租", "写字楼", "厂房", "商铺", "转让", "物业", 
    "招聘", "找工作", "办公用品", "政府采购", "信息公示平台", "就业管理", "印刷项目", "安保服务",
    # 二级市场、券商研报与股市黑话
    "涨停", "跌停", "超买", "超卖", "多空", "资金净流入", "资金净流出", 
    "龙虎榜", "证券研报", "上行", "拐点", "持仓", "避险", "指数", "收盘报", 
    "ETF", "评级", "港交所", "联交所", "港股", "收盘价", "看好评级", "买入评级",
    "异常波动", "异动公告", "竞价交易", "减持", "增持", "主力资金", "证券策略", "牛市", "熊市", "个股",
    "走高", "走低", "大幅波动", "报收", "领涨", "领跌", "盘中", "A股板块",
    # C端民生、补贴、旅游美食、零售理财
    "天气", "降雨", "气象", "婚宴", "餐饮", "幼儿园", "小学", "中学", "高考",
    "医院", "义诊", "交警", "车祸", "报警", "诈骗", "演唱会", "停水", "停电",
    "育儿", "工伤", "医保", "社保", "大走访", "理财产品", "日添利", "天天利", "零售客户", "储户",
    "旅游", "文旅", "美食", "游玩", "打卡", "景区", "攻略", "热度持续"
]

# ==========================================
# 2. Bocha Web Search 请求与解析函数
# ==========================================
def search_info(query, max_results=20, include_domains=None):
    global GLOBAL_SEEN_URLS
    
    include_str = "|".join(include_domains) if include_domains else ""

    payload = {
        "query": query,
        "freshness": "oneWeek", # 物理锁死只抓取最近 7 天
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

            # 黑名单物理拦截
            is_junk = False
            for junk_word in JUNK_BLACKLIST:
                if junk_word in name or junk_word in content:
                    is_junk = True
                    break
            if is_junk:
                continue

            # 去重
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
def generate_briefing(client, model_name, comp_raw, weihai_raw, ind_data_dict, giants_raw, finance_raw, macro_raw, tech_raw):
    ind_context = ""
    for ind, content in ind_data_dict.items():
        ind_context += f"--- 行业泛资讯: {ind} ---\n{content}\n"

    # 重大事件检测扩容逻辑
    major_events_keywords = ["两会", "中东冲突", "俄乌", "美伊", "人大", "政协", "政府工作报告", "不可抗力"]
    is_major_event_active = any(kw in macro_raw for kw in major_events_keywords)
    
    if is_major_event_active:
        print("    [系统通知] 检测到近期有重大宏观会议/冲突，启动第五板块扩容（最大12条）...")
        macro_rule = "宏观与全球重点局势（本周处于重大事件期，最多生成 12 条）：\n        国内核心政策必须占 8 条，国际地缘局势占 4 条。"
    else:
        macro_rule = "宏观与全球重点局势（最多生成 7 条）：\n        国内政策必须占 4 条，国际局势占 3 条。"

    prompt = f"""
    【全局核心设定】
    1. 角色与受众：你是顶尖投行研究所的B2B首席经济师。你的读者是【高级B2B业务拓展人员】，他们只关心：企业出海订单、重磅政经外贸政策、硬核行业风向、宏观金融大局。
    2. 【绝对排除原则（生死线）】：读者绝不需要炒股指南，不需要个人理财，不需要旅游攻略！所有带有“指数”、“ETF”、“看好评级”、“理财产品”、“旅游”、“美食”的素材，一律直接抛弃，绝不纳入正文！
    3. 【时效性信任前提】：下方素材池均已锁定为最近7天内资讯！无条件信任它们的时效性！
    4. 【跨板块防串台（极度严格）】：
       - 核心铁律：一条新闻只能在一个最合适的板块中出现一次！严禁写“(同第一条)”等废话。
       - 属性严格核对：如“海关前置仓”、“电商物流”属于【政经】或【重点企业】，绝对不能塞进“金融与银行”板块！

    【极度严厉的排版与格式指令】
    1. 首先生成【目录】，严格使用此 HTML：
       <h3 style="color: #1a365d; font-size: 18px; font-weight: normal; margin-top: 20px; margin-bottom: 10px;">一、 重点企业动态</h3>
       <div style="font-size: 14px; color: #333; line-height: 1.8;">
       1. [新闻标题1]<br>
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
        【实体鉴别红线】：新闻主体必须是特定且具名的单一实体企业。优先报道核心出海业务、产能扩建、海外中标。绝对禁止放入“威海印发政策”等政府新闻，绝对剔除印刷评标等杂碎采购！
    
    二、 威海本地政经（最多 8 条）：
        【收录标准】：威海辖区重大的外经外贸政策、海关监管创新（如跨境前置仓）、大型招商引资、重大会议部署。
        【彻底封杀】：绝对剔除所有文旅、旅游、美食、交通安全课等社会民生废料！

    三、 行业风向（必须生成！最多 8 条）：
        【收录法则】：完全不受地域限制！综合提取【行业泛资讯】与【行业巨头定向情报】中的内容：包含全球/全国产业政策、技术突破、龙头巨头动态（如停产、遭袭、不可抗力等突发事件）。
        【死命令】：只要池子里有相关行业的宏观趋势、巨头突发或政策，就必须提炼出来，绝不允许交白卷留空！绝对禁止放入股指涨跌！

    四、 金融与银行（最多 10 条）：
        1. 【置顶铁律】：第一条必须且只能是素材池中日期【最新】的【美元兑人民币汇率中间价】新闻。
        2. 【国家金融宏观】：纯宏观的货币政策（LPR、降准、央行、美联储）。
        3. 【本地银行对公业务】：威海辖区内银行的银企对接、外汇对公业务。
        4. 【防串台判死刑】：本板块的主体必须且只能是“银行”、“央行”、“美联储”等纯金融机构！“跨境电商监管仓”、“海关通关”、“物流”等新闻【绝对禁止】出现在这里，必须划归第二板块！

    五、 {macro_rule}
        国内必须包含最新的国家级产业政策、规划。国际包含地缘政治、重大突发冲突等。

    六、 科技前沿与大语言模型（最多 9 条）：
        大语言模型最新焦点、中国硬科技进展及全球前沿动向。

    【素材池】
    一/重点企业: {comp_raw}
    二/大威海政经: {weihai_raw}
    三/行业泛资讯: {ind_context}
    三/行业巨头定向情报: {giants_raw}
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
    comp_raw_target = ""
    for comp in TARGET_COMPANIES.split():
        if not comp: continue
        res = search_info(f"{comp} -股票 -收盘 -涨停 -跌停 -招标公告 -采购", max_results=30)
        comp_raw_target += f"【{comp} 相关动态】\n{res}\n"
        
    comp_raw_weihai = search_info("威海 (企业 OR 集团) (外贸 OR 出海 OR 跨境 OR 投资 OR 出口 OR 产能) -旅游", max_results=30)
    comp_raw = f"{comp_raw_target}\n\n【威海其他出海企业】\n{comp_raw_weihai}"
    
    print("-> 搜集大威海政经...")
    weihai_raw = search_info("威海 (宏观经济 OR 招商引资 OR 产业政策 OR 外经贸 OR 新质生产力 OR 项目 OR 会议) -学校 -交通事故 -天气 -旅游 -文旅 -美食", max_results=35)
    
    print("-> 搜集行业风向与巨头情报...")
    industry_data = {}
    for ind in INDUSTRY_LIST:
        industry_data[ind] = search_info(f"{ind} (技术突破 OR 产业政策 OR 发展趋势 OR 全球市场) -A股 -指数 -板块 -收盘", max_results=25)
    
    giants_raw = ""
    if GIANTS_LIST:
        giants_str = " OR ".join(GIANTS_LIST)
        # 【致胜修改】：彻底去掉巨头的限制词，无论不可抗力还是财报，全部抓走！
        giants_raw = search_info(f"({giants_str}) -股票 -大盘 -板块 -收盘 -涨停 -跌停", max_results=40)
        
    print("-> 搜集金融与银行业务...")
    exchange_rate_raw = search_info("美元兑人民币 中间价 汇率 最新报道", max_results=15)
    finance_macro_raw = search_info("(中国人民银行 OR 央行 OR 国家外汇管理局 OR 财政部 OR 美联储) (货币政策 OR LPR OR 降准 OR 降息 OR 外汇) -A股 -股市", max_results=20)
    bank_raw = search_info("威海 (银行 OR 分行) (跨境结算 OR 国际业务 OR 对公业务 OR 出口信贷) -理财 -零售 -港股", max_results=15)
    finance_raw = f"【汇率强制置顶数据】\n{exchange_rate_raw}\n\n【国家金融宏观数据】\n{finance_macro_raw}\n\n【威海辖区银行业务】\n{bank_raw}"
    
    print("-> 搜集国内宏观与产业政策...")
    macro_domestic = search_info("国家发改委 OR 工信部 OR 商务部 OR 外交部 OR 国务院 (产业政策 OR 宏观经济 OR 进出口数据) 最新", max_results=30)
    
    print("-> 搜集国际地缘与经贸局势...")
    macro_intl = search_info("国际贸易 OR 地缘政治 OR 关税政策 OR 美伊局势 OR 俄乌局势", max_results=30)
    
    macro_raw = f"【国内宏观与产业政策素材池】\n{macro_domestic}\n\n【国际地缘与经贸局势素材池】\n{macro_intl}"
    
    TECH_MEDIA_DOMAINS = [
        "qbitai.com", "jiqizhixin.com", "36kr.com", "leiphone.com", "geekpark.net",
        "techcrunch.com", "venturebeat.com", "theverge.com"
    ]
    
    print("-> 搜集科技前沿 (AI/大模型/机器人/新能源)...")
    tech_raw = search_info("AI OR 大模型 OR 机器人 OR 新能源 最新突破", max_results=25, include_domains=TECH_MEDIA_DOMAINS)
    
    print("-> 智能新闻官正在撰写超级周报...")
    briefing = generate_briefing(client, model, comp_raw, weihai_raw, industry_data, giants_raw, finance_raw, macro_raw, tech_raw)
    
    send_email(f"【威海商业情报】{TODAY_STR}", briefing)
