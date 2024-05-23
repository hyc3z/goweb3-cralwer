import os
import sys
import time
import random
import json
from time import sleep
from pathlib import Path
from urllib.parse import urlparse
import traceback
import concurrent.futures

import psutil
from loguru import logger
from rich.prompt import Confirm
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

from .browser import get_browser, get_multiple_browsers
from .pool import ThreadPool
from .util.config import config, work_directory, set_work_directory



def get_executor(count: int | None = None):
    return concurrent.futures.ThreadPoolExecutor(max_workers=count)


def cleanup(driver, work_list, tweet_executor):
    print("Quit main driver...")
    driver.quit()
    print("Quit main driver finished.")
    for i in work_list:
        print("Quit worker driver..")
        i.quit()
        print("Quit worker driver finished.")
    print("Quit threadpool...")
    tweet_executor.shutdown(wait=False)
    print("Quit threadpool finished.")
    print("Start kill all chrome processes")
    if config['kill_chrome_process']:
        kill_chrome_processes()
    print("Kill all chrome processes finished.")


def read_config() -> list[dict]:
    with open(work_directory / 'cookie.json', 'r') as f:
        return json.load(f)

def write_config(data: list[dict]):
    with open(work_directory / 'cookie.json', 'w') as f:
        json.dump(data, f)

def set_cookie(browser: WebDriver, cookie):
    for i in cookie:
        browser.add_cookie(i)

def get_items_need_handle(driver, selector):
    return driver.find_elements(*selector)

def human_typing(element, text, min_delay=0.1, max_delay=0.3):
    """
    Simulate human typing by sending keys one by one with random delays.

    Args:
    - element: The web element to send keys to.
    - text: The text to type.
    - min_delay: Minimum delay between keystrokes.
    - max_delay: Maximum delay between keystrokes.
    """
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(min_delay, max_delay))

def calculate_remaining_time(expiry, current_time):
    remaining_seconds = expiry - current_time
    remaining_minutes = remaining_seconds // 60
    remaining_hours = remaining_minutes // 60
    remaining_minutes = remaining_minutes % 60
    return remaining_hours, remaining_minutes

def check_cookie_expiry(cookies):
    
    current_time = int(time.time())
    expired = False
    # 检查每个 cookie 是否已过期
    for cookie in cookies:
        expiry = cookie.get('expiry')
        if expiry:
            if current_time > expiry:
                print(f"Cookie {cookie['name']} has expired.")
                expired = True
            else:
                remaining_hours, remaining_minutes = calculate_remaining_time(expiry, current_time)
                print(f"Cookie {cookie['name']} is valid for {remaining_hours} hours and {remaining_minutes} minutes.")
                if remaining_hours < 2:
                    print("Cookie about to expire.")
                    expired = True
        else:
            print(f"Cookie {cookie['name']} does not have an expiry time.")

    return expired

def auto_login(driver, include_click_login=True):
    login_pwd = os.getenv('LOGIN_PWD')
    if login_pwd is None:
        print("Environment variable LOGIN_PWD is not set.")
    else:
        print("Environment variable LOGIN_PWD is set.")
    if include_click_login:
        login_button_selector = driver.find_element(By.XPATH, '//a[@href="/login" and @role="link"]')
        login_button_selector.click()
    wait = WebDriverWait(driver, 20)
    input_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[autocapitalize="sentences"][autocomplete="username"][type="text"]')))
    print("Start type username.")
    human_typing(input_element, config['login_username'])
    driver.implicitly_wait(1)
    input_element.send_keys(Keys.ENTER)
    driver.implicitly_wait(2)
    # button_element = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[role="button"]')))
    # button_element.click()
    input_pwd_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="password"]')))
    print("Start type password.")
    human_typing(input_pwd_element, login_pwd)
    driver.implicitly_wait(1)
    input_pwd_element.send_keys(Keys.ENTER)

def kill_chrome_processes():
    # 遍历所有进程
    for proc in psutil.process_iter(attrs=['pid', 'name']):
        try:
            # 检查进程名是否包含 "chrome"
            if 'chrome' in proc.info['name'].lower():
                print(f"Killing process {proc.info['name']} with PID {proc.info['pid']}")
                proc.terminate()  # 尝试优雅地终止进程
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass


    try:
        # 等待所有进程被终止
        gone, still_alive = psutil.wait_procs(psutil.process_iter(attrs=['pid', 'name']), timeout=3)
        
        for p in still_alive:
            try:
                print(f"Force killing process {p.info['name']} with PID {p.info['pid']}")
                p.kill()
            except Exception:
                pass
    except Exception:
        pass

def login_and_get_cookies(main_driver, worker_driver):
    main_driver.get('https://twitter.com/404')
    cookie_file_path = os.path.join(work_directory,'cookie.json')
    if not Path(work_directory / 'cookie.json').exists():
        print("Cookie not exist, try auto login to fetch.")
        auto_login(main_driver)
        write_config(main_driver.get_cookies())
    else:
        cookie_expired = False
        with open(cookie_file_path, 'r') as file:
            cookies = json.load(file)
            cookie_expired = check_cookie_expiry(cookies)
        if cookie_expired:
            print("Cookies expired, try auto login.")
            auto_login(main_driver)
            write_config(main_driver.get_cookies())
        else:
            print("Cookies exist and not expired, reuse them.")
            set_cookie(main_driver, cookie=cookies)
    main_driver.implicitly_wait(5)
    cookie = main_driver.get_cookies()
    for drivers in worker_driver:
        set_cookie(drivers, cookie=cookie)

def check_login(driver):
    try:
        retryCount = 0
        while retryCount < 3:
            wait = WebDriverWait(driver, 20)
            input_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[autocomplete="username"]')))
            print("Input redirect showing again.")
            auto_login(driver=driver, include_click_login=False)
            retryCount += 1
    except Exception as ex:
        print("Input redirect not showing again.")

def main():
    from .tweet import Tweet
    cookie: list[dict]
    work_list: list[WebDriver]
    driver: WebDriver
    wait_list = []
    tweet_executor = get_executor(config['max_threads'])

    if config['kill_chrome_process']:
        print("Start kill chrome processes")
        kill_chrome_processes()
        print("Kill chrome processes finished.")

    selector = (By.XPATH, '//*/div[2]/div/div[3]/a[@role="link"]')
    (Path(config.save) / 'res').mkdir(exist_ok=True, parents=True)

    driver = get_browser(headless=True)

    work_list = get_multiple_browsers(config['max_threads'], headless=False)
    if config['headed']:
        work_list.extend(get_multiple_browsers(config['headed'], headless=True))
    for i in work_list:
        wait_list.append(tweet_executor.submit(i.get, 'https://twitter.com/404'))

    print("Finish get_browser")

    try:
        for ii in wait_list:
            ii.result()
        login_and_get_cookies(main_driver=driver, worker_driver=work_list)
        driver.get("https://twitter.com/" + config.user)

        data_dict = {}
        pool = ThreadPool(work_list, tweet_executor)

        while True:
            # Looping drop-down scroll bar
            driver.execute_script("window.scrollBy(0, 300)")
            sleep(1)
            try:
                check_login(driver=driver)
                links = get_items_need_handle(driver=driver, selector=selector)
                print("Found {} links.".format(len(links)))
                for i in links:
                    full_url = i.get_attribute("href")
                    tweet_id = urlparse(full_url).path.split('/')[-1]
                    if tweet_id not in data_dict and not (Path(config.save) / f'{tweet_id}.md').exists():
                        data_dict[tweet_id] = Tweet(full_url)
                        pool.jobs.append(data_dict[tweet_id].load_data)
                        logger.info(full_url)
                pool.check_and_work()
            except:
                pass
    except KeyboardInterrupt:
        print("Execution interrupted by user.")
    except Exception as e:
        print("Oops, exception", e)
        traceback.print_exc() 
    finally:
        cleanup(driver, work_list, tweet_executor)
        sys.exit(0)

if __name__ == "__main__":
    set_work_directory(Path(__file__).absolute().parent)
    logger.add(work_directory / "log/{time:YYYY-MM-DD}.log", rotation="00:00",
               level="ERROR",
               encoding="utf-8", format="{time} | {level} | {message}", enqueue=True)
    (Path(__file__).absolute().parent / 'output/res').mkdir(parents=True, exist_ok=True)
    config.load("config.yaml")
    main()
