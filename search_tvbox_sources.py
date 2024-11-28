# 搜索TVBox源地址
import requests
from bs4 import BeautifulSoup
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import threading
import random
import signal
import concurrent.futures
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import ssl
from urllib3 import poolmanager
import urllib3
import backoff
import urllib.parse
from requests.exceptions import ProxyError
from collections import defaultdict
import itertools
import os
import logging

# 在文件开头添加这行
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 全局变量用于控制搜索是否应该停止
stop_search = False

# 定义提前结束搜索的地址数量阈值
# 当找到的有效地址数量达到此值时，搜索将提前结束
# 您可以根据需要修改此值
MAX_URLS = 300

# 定义搜索超时时间（单位：秒）
# 如果在此时间内未找到足够的地址，搜索将自动结束
# 您可以根据需要修改此值
SEARCH_TIMEOUT = 600  # 10分钟

# 添加一些代理服务器，格式如下：
# {'http': 'http://10.10.1.10:3128', 'https': 'http://10.10.1.10:1080'},
# {'http': 'http://10.10.1.11:3128', 'https': 'http://10.10.1.11:1080'},
PROXIES = []

# 添加这个 User-Agent 列表
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.101 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1"
]

# 添加一个域名访问间隔（秒）
DOMAIN_COOLDOWN = 60
domain_last_access = defaultdict(float)

# 在全局变量部分添加
MIN_REQUEST_INTERVAL = 3  # 最小请求间隔（秒）
last_request_time = 0

# 在全局变量部分添加
MAX_REQUESTS_PER_MINUTE = 20
request_times = []

# 在全局变量部分添加
urls = set()
urls_lock = threading.Lock()

# 在文件开头的全局变量部分添加
OUTPUT_FILE = 'tvbox-out.txt'  # 输出文件名
URL_FILE = 'tvbox-url.txt'     # URL文件名


class TLSAdapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False):
        """Create and initialize the urllib3 PoolManager."""
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.poolmanager = poolmanager.PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_version=ssl.PROTOCOL_TLSv1_2,
            ssl_context=ctx)

# 添加一个装饰器用于重试


@backoff.on_exception(backoff.expo,
                      (requests.exceptions.Timeout,
                       requests.exceptions.ConnectionError,
                       ProxyError),
                      max_tries=5)
def make_request(session, url, timeout):
    global last_request_time
    global request_times
    current_time = time.time()

    # 确保请求间隔不小于 MIN_REQUEST_INTERVAL
    if current_time - last_request_time < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - (current_time - last_request_time))

    # 移除一分钟之前的请求记录
    request_times = [t for t in request_times if current_time - t < 60]

    # 如果在过去一分钟内的请求数达到上限，则等待
    if len(request_times) >= MAX_REQUESTS_PER_MINUTE:
        sleep_time = 60 - (current_time - request_times[0])
        time.sleep(sleep_time)

    domain = urllib.parse.urlparse(url).netloc

    # 查询域名的上次访问时间
    if current_time - domain_last_access[domain] < DOMAIN_COOLDOWN:
        sleep_time = DOMAIN_COOLDOWN - \
            (current_time - domain_last_access[domain])
        time.sleep(sleep_time)

    headers = {
        'User-Agent': random.choice(USER_AGENTS),  # 使用随机选择的 User-Agent
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0',
    }

    if PROXIES:
        proxy = random.choice(PROXIES)
    else:
        proxy = None

    response = session.get(url, headers=headers,
                           timeout=timeout, verify=False, proxies=proxy)

    # 更新域名的最后访问时间
    domain_last_access[domain] = time.time()

    last_request_time = time.time()
    request_times.append(time.time())
    return response


def clean_url(url):
    # 移除百度搜索的中转链接
    if url.startswith('http://www.baidu.com/link?') or url.startswith('https://www.baidu.com/link?'):
        return None

    # 移除 URL 中的 HTML 属性
    url = re.sub(r'\s+rel="nofollow".*', '', url)
    # 移除 URL 末尾的引号
    url = url.rstrip('"')
    # 解码 URL
    url = urllib.parse.unquote(url)
    return url


def is_valid_tvbox_url(url):
    # 清理 URL
    url = clean_url(url)

    # 排除 GitHub 搜索页面、仓库主页和其他明显不是 TVBox 配置的 URL
    if re.search(r'(github\.com/search|github\.com/[^/]+/[^/]+$|search\?q=|\.com/s\?|\.com/web\?)', url, re.I):
        return False

    # 检查 URL 是否符合基本模式
    if not re.search(r'(tvbox|tv\.json|live\.json|epg\.json|source\.json|raw\.githubusercontent\.com.*\.json|gitee\.com.*\.json|pastebin\.com|gist\.github\.com)', url, re.I):
        return False

    try:
        response = make_request(requests.Session(), url, timeout=10)
        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', '').lower()
            if 'json' in content_type:
                try:
                    json_data = response.json()
                    if isinstance(json_data, dict) and ('lives' in json_data or 'sites' in json_data or 'spider' in json_data):
                        return True
                except json.JSONDecodeError:
                    pass
            elif 'text' in content_type:
                text_content = response.text.lower()
                # 检查是否包含 TVBox 配置的特定结构
                if re.search(r'("lives":\s*\[.*\]|"sites":\s*\[.*\]|"spider":\s*".*")', text_content, re.DOTALL):
                    return True
    except requests.RequestException as e:
        print(f"验证 URL 时出错: {url} - {e}")
    except Exception as e:
        print(f"验证 URL 时发生未知错误: {url} - {e}")
    return False


def signal_handler(signum, frame):
    global stop_search
    print("\n接收到中断信号，正在停止搜索...")
    stop_search = True

# 添加深度爬虫函数


def deep_crawl(url, depth=2):
    if depth == 0:
        return

    try:
        response = make_request(requests.Session(), url, 10)
        soup = BeautifulSoup(response.text, 'html.parser')
        for link in soup.find_all('a'):
            new_url = link.get('href')
            if new_url and re.match(r'https?://', new_url):
                process_url(new_url)
                deep_crawl(new_url, depth - 1)
    except Exception as e:
        print(f"深度爬虫出错: {url} - {e}")

# 扩展搜索关键词


def expand_keywords(keywords):
    synonyms = {
        'tvbox': ['tv box', 'television box', '电视盒子', 'IPTV', '网络电视'],
        'source': ['源', 'sources', '来源', '地址', 'address', 'url'],
        'live': ['直播', 'streaming', '在线', 'online'],
        'json': ['配置', 'configuration', 'setup', 'config'],
        'vod': ['点播', 'on-demand', '视频'],
        'epg': ['节目单', 'program guide', '电子节目指南'],
        'm3u': ['播放列表', 'playlist'],
        'api': ['接口', 'interface', '数据源']
    }
    expanded = []
    for keyword in keywords:
        parts = keyword.split()
        combinations = [synonyms.get(part, [part]) for part in parts]
        expanded.extend([' '.join(combo)
                        for combo in itertools.product(*combinations)])
    return expanded

# 文件托管网站搜索


def search_file_hosting_sites(keyword):
    sites = [
        f"https://github.com/search?q={keyword}",
        f"https://gitee.com/search?q={keyword}",
        f"https://gitlab.com/search?search={keyword}",
        f"https://pastebin.com/search?q={keyword}",
        f"https://gist.github.com/search?q={keyword}",
        f"https://sourceforge.net/directory/?q={keyword}",
        f"https://bitbucket.org/repo/all?name={keyword}",
        f"https://www.jsdelivr.com/github?q={keyword}",
        f"https://www.npmjs.com/search?q={keyword}",
        f"https://pypi.org/search/?q={keyword}",
        f"https://search.maven.org/search?q={keyword}",
        f"https://packagist.org/?query={keyword}",
    ]
    for site in sites:
        process_url(site)

# 多语言搜索


def multilingual_search(keyword):
    translations = {
        'tvbox': ['tvbox', '电视盒子', 'テレビボックス', '티비박스'],
        'source': ['source', '源', 'ソース', '소스'],
        'live': ['live', '直播', 'ライブ', '라이브'],
        'json': ['json', '配置', '設定', '설정']
    }
    for word in keyword.split():
        if word in translations:
            yield from translations[word]
        else:
            yield word


def process_url(url):
    global urls
    # 清理 URL
    cleaned_url = clean_url(url)
    if cleaned_url is None:  # 如果是百度中转链接，直接返回
        return

    url = cleaned_url

    # 首先检查 URL 是否符合基本模式，并排除明显无效的 URL
    if re.search(r'(github\.com/search|github\.com/[^/]+/[^/]+$|search\?q=|\.com/s\?|\.com/web\?)', url, re.I):
        return

    if not re.search(r'(tvbox|tv\.json|live\.json|epg\.json|source\.json|raw\.githubusercontent\.com.*\.json|gitee\.com.*\.json|pastebin\.com|gist\.github\.com)', url, re.I):
        return

    if url not in urls:
        try:
            # 设置重试策略
            session = requests.Session()
            retries = Retry(total=5, backoff_factor=0.1,
                            status_forcelist=[500, 502, 503, 504])
            session.mount('https://', TLSAdapter(max_retries=retries))
            session.mount('http://', TLSAdapter(max_retries=retries))

            # 对特定域名使用更长的超时时间
            timeout = 30 if 'kgithub.com' in url else 15

            if is_valid_tvbox_url(url):
                with urls_lock:
                    urls.add(url)
                    print(f"当前已获取 {len(urls)} 个有效地址", end='\r')
                    sys.stdout.flush()
                    # 立即保存到文件
                    save_url_to_file(url)
            else:
                # 处理可能包含间接链接的页面
                response = make_request(session, url, timeout)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    for link in soup.find_all('a'):
                        indirect_url = link.get('href')
                        if indirect_url and re.match(r'https?://', indirect_url):
                            indirect_url = clean_url(indirect_url)
                            try:
                                if is_valid_tvbox_url(indirect_url):
                                    with urls_lock:
                                        urls.add(indirect_url)
                                        print(
                                            f"当前已获取 {len(urls)} 个有效地址", end='\r')
                                        sys.stdout.flush()
                                        # 立即保存到文件
                                        save_url_to_file(indirect_url)
                            except Exception as e:
                                print(f"处理间接链接时出错: {indirect_url} - {e}")
        except requests.RequestException as e:
            print(f"处理 URL 时出错: {url} - {e}")
        except Exception as e:
            print(f"处理 URL 时发生未知错误: {url} - {e}")


def save_url_to_file(url):
    try:
        with open(URL_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{url}\n")
    except IOError as e:
        print(f"保存 URL 到文件时发生错误: {e}")


# 设置日志记录
logger = logging.getLogger(__name__)


def search_google(query, num_results=10):
    url = f"https://www.google.com/search?q={query}&num={num_results}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    search_results = soup.find_all('div', class_='yuRUbf')
    return [result.find('a')['href'] for result in search_results if result.find('a')]


def search_bing(query, num_results=10):
    url = f"https://www.bing.com/search?q={query}&count={num_results}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    search_results = soup.find_all('li', class_='b_algo')
    return [result.find('a')['href'] for result in search_results if result.find('a')]


def search_baidu(query, num_results=10):
    url = f"https://www.baidu.com/s?wd={query}&rn={num_results}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        search_results = []

        # 查找所有搜索结果
        for result in soup.find_all('h3', class_='t'):
            link = result.find('a')
            if link:
                href = link.get('href', '')
                # 跳过百度中转链接
                if not href.startswith(('http://www.baidu.com/link?', 'https://www.baidu.com/link?')):
                    search_results.append(href)

        return search_results
    except Exception as e:
        logger.error(f"百度搜索出错: {str(e)}")
        return []


def search_tvbox_sources(timeout=SEARCH_TIMEOUT):
    global stop_search
    start_time = time.time()
    found_urls = set()

    search_engines = [
        ("Google", search_google),
        ("Bing", search_bing),
        ("Baidu", search_baidu)
    ]

    search_queries = [
        "TVBox源 site:github.com",
        "TVBox配置 site:github.com",
        "TVBox接口 site:github.com",
        "TVBox源 site:gitee.com",
        "TVBox配置 site:gitee.com",
        "TVBox接口 site:gitee.com"
    ]

    for query in search_queries:
        if stop_search or time.time() - start_time > timeout or len(found_urls) >= MAX_URLS:
            break

        for engine_name, search_function in search_engines:
            if stop_search or time.time() - start_time > timeout or len(found_urls) >= MAX_URLS:
                break

            try:
                logger.info(f"使用 {engine_name} 搜索: {query}")
                results = search_function(query)
                for url in results:
                    if stop_search or time.time() - start_time > timeout or len(found_urls) >= MAX_URLS:
                        break
                    if url not in found_urls:
                        found_urls.add(url)
                        logger.debug(f"找到新的URL: {url}")
                time.sleep(random.uniform(1, 3))  # 随机延迟，避免被封禁
            except Exception as e:
                logger.error(f"{engine_name} 搜索出错: {str(e)}")

    # 添加时间信息并写入文件
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open(URL_FILE, "w", encoding='utf-8') as f:
        for url in found_urls:
            f.write(f"[{current_time}] {url}\n")

    logger.info(f"搜索完成，共找到 {len(found_urls)} 个URL")
    return len(found_urls)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    search_tvbox_sources()
