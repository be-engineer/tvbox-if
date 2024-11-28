# 搜索或者批量测试TVBox地址是否有效，把有效地址保存到文件，程序运行后5秒不选择菜单则自动执行网络搜索功能
# 用pyinstaller --onefile --name tvbox-search  --log-level=DEBUG --windowed main.py打包成可执行文件
# 使用方法：
# 安装venv模块（如果尚未安装）
# sudo apt install python3-venv
# 创建一个新的虚拟环境
# python3 -m venv .venv
# # 激活虚拟环境
# source .venv/bin/activate
# # 现在您可以使用pip安装包了
# pip install requests
# 1. 安装依赖：pip install -r requirements.txt
# 2. 运行：python main.py
import sys
import os
import requests
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from search_tvbox_sources import search_tvbox_sources, stop_search, SEARCH_TIMEOUT, MAX_URLS
import time
import threading
import signal
import select
import logging
from logging.handlers import RotatingFileHandler
import configparser
import datetime
import traceback

# 全局变量用于控制程序是否应该退出
should_exit = False
main_thread = None

# 在文件开头添加全局变量
OUTPUT_FILE = 'tvbox-source.txt'  # 输出文件名source
URL_FILE = 'tvbox-url.txt'     # URL文件名

# 设置日志


def setup_logging():
    current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(os.getcwd(), 'log')

    # 如果 log 目录不存在，创建它
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, f'tvbox_search_{current_time}.log')
    log_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')

    file_handler = RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.DEBUG)

    app_log = logging.getLogger('root')
    app_log.setLevel(logging.DEBUG)

    # 移除所有已存在的处理器
    for handler in app_log.handlers[:]:
        app_log.removeHandler(handler)

    app_log.addHandler(file_handler)
    return app_log


logger = setup_logging()


def signal_handler(signum, frame):
    global should_exit, stop_search
    should_exit = True
    stop_search = True
    logger.warning("接收到中断信号，正在退出程序...")
    print("\n正在退出程序，请稍候...")
    if main_thread and main_thread != threading.current_thread():
        main_thread.join(timeout=5)  # 等待主线程最多5秒
    sys.exit(0)


# 注册信号处理函数
signal.signal(signal.SIGINT, signal_handler)


def test_url(url):
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            try:
                json_data = response.json()
                if isinstance(json_data, dict) and ('lives' in json_data or 'sites' in json_data or 'spider' in json_data):
                    logger.debug(f"有效的 TVBox 地址: {url}")
                    return url
            except json.JSONDecodeError:
                # 如果不是 JSON，检查是否包含常见 TVBox 配置关键字
                text_content = response.text.lower()
                if any(keyword in text_content for keyword in ['tvbox', 'live', 'vod', 'epg', 'source']):
                    logger.debug(f"可能的 TVBox 地址: {url}")
                    return url
        else:
            logger.debug(f"无效的地址 (状态码 {response.status_code}): {url}")
    except requests.RequestException as e:
        logger.error(f"测试 URL 时出错: {url} - {e}")
    return None


def test_main(input_file):
    with open(input_file, 'r') as f:
        urls = [line.strip().split('] ')[-1] if ']' in line else line.strip()
                for line in f if line.strip()]

    valid_urls = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_url = {executor.submit(test_url, url): url for url in urls}
        for future in as_completed(future_to_url):
            result = future.result()
            if result:
                valid_urls.append(result)

    # 读取现有的输出文件内容
    existing_urls = set()
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, 'r') as f:
            existing_urls = set(line.strip().split('] ')[-1] if ']' in line else line.strip()
                                for line in f if line.strip())

    # 追加新的有效地址到输出文件
    new_urls_count = 0
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open(OUTPUT_FILE, 'a') as f:
        for url in valid_urls:
            if url not in existing_urls:
                f.write(f"[{current_time}] {url}\n")
                new_urls_count += 1
                existing_urls.add(url)

    logger.warning(f"测试完成。共测试 {len(urls)} 个地址，有效地址 {len(valid_urls)} 个。")
    logger.warning(f"其中 {new_urls_count} 个新地址已追加到 {OUTPUT_FILE} 文件中。")
    logger.warning(f"{OUTPUT_FILE} 文件现共包含 {len(existing_urls)} 个有效地址。")
    print(f"测试完成。共测试 {len(urls)} 个地址，有效地址 {len(valid_urls)} 个。")
    print(f"其中 {new_urls_count} 个新地址已追加到 {OUTPUT_FILE} 文件中。")
    print(f"{OUTPUT_FILE} 文件现共包含 {len(existing_urls)} 个有效地址。")


def search_and_test(timeout=None):
    # 如果没有指定 timeout，就使用默认值
    search_timeout = timeout if timeout is not None else SEARCH_TIMEOUT
    global should_exit, stop_search
    logger.warning("开始搜索TVBox源地址...")
    print("正在搜索TVBox源地址...")
    start_time = time.time()

    search_thread = threading.Thread(
        target=search_tvbox_sources, args=(search_timeout,))
    search_thread.start()

    while search_thread.is_alive() and time.time() - start_time < search_timeout and not should_exit:
        remaining_time = search_timeout - (time.time() - start_time)
        print(f"\r搜索中，剩余时间约 {int(remaining_time)} 秒...", end="")
        sys.stdout.flush()
        time.sleep(1)  # 每秒更新一次

    if search_thread.is_alive():
        if should_exit:
            logger.warning("搜索被用户中断。")
            print("\n搜索被用户中断。")
        else:
            logger.warning("搜索超时，超过设定时间。")
            print("\n搜索超时，超过设定时间。")
            search_timed_out = True
        stop_search = True
        search_thread.join(10)  # 给线程10秒时间来清理和退出

    elapsed_time = time.time() - start_time
    logger.warning(f"搜索完成或已中断，耗时 {elapsed_time:.2f} 秒。")
    print(f"\n搜索完成或已中断，耗时 {elapsed_time:.2f} 秒。")

    input_file = "tvbox-url.txt"
    input_file_path = os.path.join(os.getcwd(), input_file)

    if not os.path.exists(input_file_path):
        logger.error(f"错误：文件 '{input_file_path}' 不存在。搜索可能未找到任何结果。")
        print(f"错误：文件 '{input_file_path}' 不存在。搜索可能未找到任何结果。")
        return False
    elif os.path.getsize(input_file_path) == 0:
        logger.warning(f"警告：文件 '{input_file_path}' 为空。搜索未找到任何结果。")
        print(f"警告：文件 '{input_file_path}' 为空。搜索未找到任何结果。")
        return False
    else:
        logger.info(f"文件 '{input_file_path}' 存在且不为空。开始测试已找到的地址...")
        print(f"文件 '{input_file_path}' 存在且不为空。开始测试已找到的地址...")
        test_main(input_file_path)

    if should_exit or search_timed_out:
        return False

    return True  # 如果搜索和测试都成功完成，返回 True


def test_local_file():
    while True:
        input_file = input("请输入包含TVBox源地址的本地文件名 (输入 'q' 返回主菜单): ").strip()
        if input_file.lower() == 'q':
            logger.info("用户选择返回主菜单")
            print("返回主菜单...")
            return False  # 返回 False 表示不退出程序，而是返回主菜单

        if not os.path.exists(input_file):
            logger.error(f"错误：文件 '{input_file}' 不存在。")
            print(f"错误：文件 '{input_file}' 不存在。请重新输入。")
            continue

        logger.warning(f"开始测试本地文件: {input_file}")
        print(f"开始测试本地文件: {input_file}")

        with open(input_file, 'r') as f:
            urls = [line.strip().split('] ')[-1] if ']' in line else line.strip()
                    for line in f if line.strip()]

        valid_urls = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_url = {executor.submit(
                test_url, url): url for url in urls}
            for future in as_completed(future_to_url):
                result = future.result()
                if result:
                    valid_urls.append(result)

        # 读取现有的输出文件内容
        existing_urls = set()
        if os.path.exists(OUTPUT_FILE):
            with open(OUTPUT_FILE, 'r') as f:
                existing_urls = set(line.strip().split('] ')[-1] if ']' in line else line.strip()
                                    for line in f if line.strip())

        # 追加新的有效地址到输出文件
        new_urls_count = 0
        current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(OUTPUT_FILE, 'a') as f:
            for url in valid_urls:
                if url not in existing_urls:
                    f.write(f"[{current_time}] {url}\n")
                    new_urls_count += 1
                    existing_urls.add(url)

        logger.warning(f"测试完成。共测试 {len(urls)} 个地址，有效地址 {len(valid_urls)} 个。")
        logger.warning(f"其中 {new_urls_count} 个新地址已追加到 {OUTPUT_FILE} 文件中。")
        logger.warning(f"{OUTPUT_FILE} 文件现共包含 {len(existing_urls)} 个有效地址。")
        print(f"测试完成。共测试 {len(urls)} 个地址，有效地址 {len(valid_urls)} 个")
        print(f"其中 {new_urls_count} 个新地址已追加到 {OUTPUT_FILE} 文件中。")
        print(f"{OUTPUT_FILE} 文件现共包含 {len(existing_urls)} 个有效地址。")
        return True  # 返回 True 表示测试完成，可以退出程序


def load_config():
    config = configparser.ConfigParser()
    if os.path.exists('config.ini'):
        config.read('config.ini')
    if 'Settings' not in config:
        config['Settings'] = {}
    if 'SEARCH_TIMEOUT' not in config['Settings']:
        config['Settings']['SEARCH_TIMEOUT'] = '600'  # 10分钟
    if 'MAX_URLS' not in config['Settings']:
        config['Settings']['MAX_URLS'] = '300'

    # 保存默认配置
    with open('config.ini', 'w') as configfile:
        config.write(configfile)

    return config


def save_config(config):
    with open('config.ini', 'w') as configfile:
        config.write(configfile)


def set_search_parameters():
    global SEARCH_TIMEOUT, MAX_URLS
    config = load_config()

    while True:
        print("\n=== 设置搜索参数 ===")
        print(f"1. 设置超时时间 (当前: {config['Settings']['SEARCH_TIMEOUT']} 秒)")
        print(f"2. 设置最大搜索地址数量 (当前: {config['Settings']['MAX_URLS']})")
        print("3. 返回主菜单")

        choice = input("请选择要设置的参数 (1-3): ")

        if choice == '1':
            new_timeout = input(
                f"请输入新的超时时间（当前为 {config['Settings']['SEARCH_TIMEOUT']} 秒，直接回车保留当前值）: ")
            if new_timeout:
                config['Settings']['SEARCH_TIMEOUT'] = new_timeout
                SEARCH_TIMEOUT = int(new_timeout)
                logger.warning(f"超时时间已更新：{SEARCH_TIMEOUT}秒")
                print(f"超时时间已更新：{SEARCH_TIMEOUT}秒")
        elif choice == '2':
            new_max_urls = input(
                f"请输入新的最大URL数量（当前为 {config['Settings']['MAX_URLS']}，直接回车保留当前值）: ")
            if new_max_urls:
                config['Settings']['MAX_URLS'] = new_max_urls
                MAX_URLS = int(new_max_urls)
                logger.warning(f"最大URL数量已更新：{MAX_URLS}")
                print(f"最大URL数量已更新：{MAX_URLS}")
        elif choice == '3':
            break
        else:
            print("无效的选择，请重试。")

    save_config(config)
    logger.warning("搜索参数已保存到配置文件")
    print("搜索参数已保存到配置文件")


def ensure_files_exist():
    files_to_check = [URL_FILE, OUTPUT_FILE, 'config.ini']
    for file in files_to_check:
        if not os.path.exists(file):
            try:
                with open(file, 'w') as f:
                    pass  # 创建一个空文件
                logger.info(f"创建了文件: {file}")
                print(f"创建了文件: {file}")
            except IOError as e:
                logger.error(f"无法创建文件 {file}: {e}")
                print(f"无法创建文件 {file}: {e}")


def auto_run():
    """自动运行模式，用于 GitHub Actions"""
    global main_thread, should_exit, SEARCH_TIMEOUT, MAX_URLS
    main_thread = threading.current_thread()

    logger.info("程序启动 - 自动模式")

    # 确保必要的文件存在
    ensure_files_exist()

    # 加载配置
    config = load_config()
    SEARCH_TIMEOUT = int(config['Settings']['SEARCH_TIMEOUT'])
    MAX_URLS = int(config['Settings']['MAX_URLS'])

    try:
        # 直接执行搜索
        if not search_and_test():
            logger.warning("搜索超时或被中断")
            return False
        else:
            logger.warning("搜索和测试完成")
            return True
    except Exception as e:
        logger.error(f"程序发生异常: {str(e)}")
        logger.error(f"异常详情:\n{traceback.format_exc()}")
        return False


if __name__ == "__main__":
    # 检查是否在 GitHub Actions 环境中运行
    if os.environ.get('GITHUB_ACTIONS'):
        auto_run()
    else:
        main()  # 原来的交互式主函数


def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

# 使用示例
# config_path = resource_path('.env')
# search_tvbox_sources_path = resource_path('search_tvbox_sources.py')
