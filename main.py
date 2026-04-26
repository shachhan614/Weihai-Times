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

raw_giants = os.getenv("INDUSTRY_GIANTS") or "巴林石油 巴林国家石油公司 Bapco 沙特阿美 Aramco 丹格特 Dangote 马士基 Maersk"
GIANTS_LIST = [i for i in raw_giants.replace('、', ' ').replace('，', ' ').split() if i]

# 搜索用 Bocha
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY")
BOCHA_WEB_SEARCH_API_URL = "https://api.bocha.cn/v1/web-search"

# 生成简报用 DeepSeek 官方 API
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro") 
API_REQUEST_DELAY = float(os.getenv("API_REQUEST_DELAY", "3.0"))

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVERS = os.getenv("EMAIL_RECEIVERS")
SMTP_SERVER = "smtp.qq.com" 

TODAY_STR = datetime.date.today().strftime("%Y年%m月%d日")
GLOBAL_SEEN_URLS = set()

# 物理拦截黑名单（保持原样）
JUNK_BLACKLIST = ["出租", "招租", "招聘", "招标", "涨停", "跌停", "理财", "旅游", "美食"] 

# ==========================================
# 2. Bocha Web Search 请求与解析函数
# ==========================================
def search_info(query, max_results=20, include_domains=None):
    global GLOBAL_SEEN_URLS
    payload = {
        "query": query,
        "freshness": "oneWeek",
        "summary": True,
        "count": min(max_results, 50) 
    }
    if include_domains: payload["include"] = "|".join(include_domains)

    headers = {
        "Authorization": f"Bearer {BOCHA_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url=BOCHA_WEB_SEARCH_API_URL, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        resp_json = response.json()
        
        webpages = resp_json.get("data", {}).get("webPages", {}).get("value", [])
        results_str = []
        for item in webpages:
            content = f"{item.get('snippet', '')} {item.get('summary', '')}"[:250]
            source_url = item.get("url", "无来源链接")
            name = item.get("name", "无标题")

            if any(jw in name or jw in content for jw in JUNK_BLACKLIST): continue
            if source_url in GLOBAL_SEEN_URLS: continue
            
            GLOBAL_SEEN_URLS.add(source_url)
            results_str.append(f"【标题】: {name} \n【内容】: {content} \n【来源】: {source_url}\n")
        return "\n".join(results_str) if results_str else "暂无直接搜索结果。"
    except Exception as e:
        return f"搜索失败: {e}"

# ==========================================
# 3. 提示词与简报生成
# ==========================================
def generate_briefing(client, model_name, comp_raw, weihai_raw, ind_data_dict, giants_raw, finance_raw, macro_raw, tech_raw):
    ind_context = ""
    for ind, content in ind_data_dict.items():
        ind_context += f"--- 行业泛资讯: {ind} ---\n{content}\n"
    
    # 提示词保持原有的 HTML 结构和业务逻辑
    prompt = f"（...此处省略你原代码中冗长的 Prompt 逻辑以节省空间，实际运行时请保留完整 Prompt...）"
    
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
    # 此处保持你原有的 send_email 逻辑不变
    pass

# ==========================================
# 5. 执行主流程
# ==========================================
if __name__ == "__main__":
    print(f"-> 启动报告生成器，当前日期: {TODAY_STR} ...")

    # 客户端回归官方 DeepSeek 渠道
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY, 
        base_url="https://api.deepseek.com",
        timeout=600.0
    )
    model = DEEPSEEK_MODEL

    # 下方搜索逻辑保持不变，依然使用 Bocha 获取数据
    print(f"-> 开始搜集数据...")
    # ... (省略重复的搜索流程代码) ...
    
    # 最后生成并发送
    # briefing = generate_briefing(...)
    # send_email(...)
