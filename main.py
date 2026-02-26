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
# 解析出行业列表，用于后续动态生成
INDUSTRY_LIST = [i for i in raw_industry.replace('、', ' ').replace('，', ' ').split() if i]

SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_REQUEST_DELAY = float(os.getenv("GEMINI_REQUEST_DELAY", "3.0"))

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVERS = os.getenv("EMAIL_RECEIVERS")
SMTP_SERVER = "smtp.qq.com" 

TODAY_STR = datetime.date.today().strftime("%Y年%m月%d日")

# ==========================================
# 2. 增强搜索函数
# ==========================================
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
        return "\n".join(results_str) if results_str else "暂无直接搜索结果。"
    except Exception as e:
        return f"搜索失败: {e}"

# ==========================================
# 3. 提示词与简报生成 (核心逻辑更新)
# ==========================================
def generate_briefing(target_comp_info, alt_comp_info, weihai_info, ind_data_dict, bank_info, macro_global_info, tech_info):
    client = OpenAI(api_key=GEMINI_API_KEY, base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    
    # 构造行业素材字符串
    ind_context = ""
    for ind, content in ind_data_dict.items():
        ind_context += f"--- 行业名称: {ind} ---\n{content}\n"

    prompt = f"""
    【角色】
    你是来自顶尖投行研究所的首席经济师，对宏观政策和经济、行业动态、公司发展都有深入的见解。系统时间：{TODAY_STR}。

    【排版极其严格规则 - 参考 image_c1e7f1.png】
    1. 垂直分布：每一条新闻的输出必须严格按照以下格式，【每一项内容必须独占一行，禁止连在一起】：
       数字序号. 标题概述（加粗）
       业务参考方向：具体建议内容
       来源：[URL地址]
    2. 禁止堆砌：每一条新闻之间要有一个空行。

    【内容分布逻辑】
    - 第一部分：2+2。若指定企业无，声明后使用备用企业。
    - 第二、四、五部分：严格 2国内+2国际。
    - 第三部分（行业风向与银行动态）：
        - 请针对以下行业列表逐一输出：{list(ind_data_dict.keys())}。
        - 【每个行业】必须提供：1条国内动态 + 1条国际动态。
        - 【银行板块】：最后固定输出 3 条威海辖区银行国际业务政策。
        - 此板块不受“4条限制”，条数 = 行业数*2 + 3。

    【素材池】
    指定企业A1: {target_comp_info} | 备用A2: {alt_comp_info} | 威海政经B: {weihai_info} 
    全行业素材C: {ind_context} | 银行素材D: {bank_info} | 宏观E: {macro_global_info} | 科技F: {tech_info}

    【强制模板】（不要输出开头语）：

    # 商业情报周报

    **报告日期：** {TODAY_STR} | **发件人：** 来自您的智能新闻官🤖
    ---

    ## 一、 重点企业动态
    （2国内+2国际。若无则输出“**关注企业过去一周没有新闻。以下为您整理威海市辖区内其他优质产能与出海重点企业动态：**”。垂直排版，业务参考与来源必须换行。）

    ## 二、 威海本地政经
    **国内焦点：**
    （2条，垂直排版）
    **国际与出海合作：**
    （2条，垂直排版）

    ## 三、 行业风向与银行动态
    （请按行业顺序输出，每个行业 1内1外。最后附带 3 条银行动态。每个条目必须垂直换行。）

    ## 四、 宏观与全球重点局局势
    （2国内宏观+2全球局势。垂直排版。）

    ## 五、 科技前沿杂谈（AI/机器人/新能源）
    （2中国突破+2全球前沿。垂直排版。）

    <p style="text-align: center;"><strong>以上为本周新闻，均为自动收集并由AI生成。</strong></p>
    <p style="text-align: center;">🤖我们下周再见🤖</p>
    """
    
    time.sleep(GEMINI_REQUEST_DELAY)
    response = client.chat.completions.create(
        model=GEMINI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1 
    )
    return response.choices[0].message.content

# ==========================================
# 4. 邮件发送 (样式升级)
# ==========================================
def send_email(subject, markdown_content):
    if not EMAIL_SENDER or not EMAIL_PASSWORD: return
    receivers_list = [EMAIL_SENDER] if not EMAIL_RECEIVERS else [r.strip() for r in EMAIL_RECEIVERS.replace('，', ',').split(',') if r.strip()]

    html_content = markdown.markdown(markdown_content)
    full_html = f"""
    <html>
    <head><style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; line-height: 1.8; color: #333; font-size: 18px; }} 
        h1 {{ color: #1a365d; font-size: 32px; border-bottom: 3px solid #1a365d; padding-bottom: 12px; }}
        h2 {{ color: #2c3e50; font-size: 26px; border-bottom: 1px dashed #ccc; padding-bottom: 8px; margin-top: 40px; }}
        p {{ margin-bottom: 15px; }}
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
        server = smtplib.SMTP_SSL(SMTP_SERVER, 465, timeout=20)
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
    # 1. 搜集各维度素材
    print(f"-> 搜集企业动态: {TARGET_COMPANIES}")
    target_comp_raw = search_info(f"{TARGET_COMPANIES} 最新 商业新闻 国际动态")
    alt_comp_raw = search_info("威海市 重点企业 外贸 出口 海外投资 优质产能 最新新闻")
    
    print("-> 搜集威海政经...")
    weihai_raw = search_info("威海市 招商引资 政策 外贸 国际合作 最新动向")
    
    # 动态搜集每个行业的素材
    industry_data = {}
    for ind in INDUSTRY_LIST:
        print(f"-> 搜集行业素材: {ind}...")
        industry_data[ind] = search_info(f"{ind} 行业 中国 国际 最新 突发新闻")
        
    print("-> 搜集银行动态...")
    bank_raw = search_info("威海 银行 国际业务 跨境金融 结售汇 政策 最新动态")
    
    macro_global_raw = search_info("中国宏观经济 重点政策 全球局势 国际贸易 重大新闻")
    tech_raw = search_info("人工智能 AI 机器人 新能源 中国突破 全球前沿动向")
    
    # 2. 调用大模型生成
    print("-> 正在由智能新闻官🤖提炼简报内容...")
    briefing = generate_briefing(target_comp_raw, alt_comp_raw, weihai_raw, industry_data, bank_raw, macro_global_raw, tech_raw)
    
    # 3. 发送邮件
    send_email(f"【威海周报～信保特供】{TODAY_STR}", briefing)
