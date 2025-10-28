import os
import time
import sys
import shutil
from urllib.parse import urljoin, urlparse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import requests
import subprocess
import platform
import re
from concurrent.futures import ThreadPoolExecutor

def sanitize_path_component(value):
    if not value:
        return "Unknown"
    sanitized = re.sub(r'[<>:\"/\\|?*]', '_', value)
    sanitized = sanitized.strip().strip('.')
    return sanitized or "Unknown"

def crawl_comic_images():
    os.environ['WDM_LOG_LEVEL'] = '0'
    os.environ['WDM_PRINT_FIRST_LINE'] = 'False'
    
    def episode_sort_key(value):
        if not value:
            return (sys.maxsize,)
        parts = [int(part) for part in re.findall(r'\d+', value)]
        return tuple(parts) if parts else (sys.maxsize,)
    
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--enable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--log-level=3')
    chrome_options.add_argument('--disable-logging')
    chrome_options.add_argument('--disable-in-process-stack-traces')
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    driver_path = ChromeDriverManager().install()
    
    def create_driver():
        service = Service(driver_path)
        if platform.system() == 'Windows':
            service.creation_flags = subprocess.CREATE_NO_WINDOW
        return webdriver.Chrome(service=service, options=chrome_options)
    
    def batched(iterable, size):
        for i in range(0, len(iterable), size):
            yield iterable[i:i + size]
    
    while True:
        print("\n만화 전체 페이지 URL을 입력하세요 (엔터 입력 시 종료):")
        main_page_url = input().strip()
        if not main_page_url:
            print("종료합니다.")
            break
        
        print("\nChrome 브라우저를 시작합니다...")
        try:
            driver = create_driver()
            print("Chrome 브라우저가 성공적으로 시작되었습니다.")
        except Exception as e:
            print(f"Chrome 드라이버 오류: {e}")
            print("Chrome 브라우저가 설치되어 있는지 확인하세요.")
            continue
        
        episode_info_list = []
        comic_title = "Unknown"
        author_name = "Unknown"
        
        try:
            print(f"전체 페이지 로딩 중: {main_page_url}")
            driver.get(main_page_url)
            time.sleep(5)
            
            try:
                comic_title_element = driver.find_element(By.CSS_SELECTOR, "div.dt-left-tt h1")
                comic_title = comic_title_element.text.strip() or "Unknown"
                print(f"만화 제목: {comic_title}")
            except:
                print("만화 제목을 찾을 수 없습니다.")
            
            try:
                author_element = driver.find_element(By.CSS_SELECTOR, "div.detail-title1 a.m-episode-link")
                author_name = author_element.text.strip() or "Unknown"
                print(f"작가: {author_name}")
            except:
                print("작가 정보를 찾을 수 없습니다.")
            
            print("에피소드 리스트 수집 중...")
            seen_episode_titles = set()
            pagination_selectors = [
                ".mPagination button[data-page]",
                ".m-pagination button[data-page]",
                ".mf-Pagination-wrap button[data-page]"
            ]
            
            def collect_current_page_episodes():
                nonlocal episode_info_list
                episode_links = driver.find_elements(By.CSS_SELECTOR, "li a[href*='/detail/']")
                for ep_link in episode_links:
                    try:
                        href = ep_link.get_attribute('href')
                        if not href:
                            continue
                        if not href.startswith('http'):
                            href = urljoin(main_page_url, href)
                        episode_title = ""
                        title_element = None
                        selectors = [
                            "h1.m-episode-list-item-title",
                            "div.dt-le-c h1[title]",
                            "h1[title]"
                        ]
                        for selector in selectors:
                            elements = ep_link.find_elements(By.CSS_SELECTOR, selector)
                            if elements:
                                title_element = elements[0]
                                break
                        if not title_element:
                            print("에피소드 제목 요소를 찾을 수 없습니다.")
                            continue
                        episode_title = (title_element.text or "").strip()
                        if not episode_title:
                            episode_title = (title_element.get_attribute('title') or "").strip()
                        if not episode_title:
                            print("에피소드 제목을 찾을 수 없습니다.")
                            continue
                        print(f"발견: {episode_title}")
                        if episode_title and episode_title not in seen_episode_titles:
                            seen_episode_titles.add(episode_title)
                            episode_info_list.append({
                                'link': href,
                                'comic_title': comic_title,
                                'episode_num': episode_title,
                                'title_text': episode_title
                            })
                    except Exception as e:
                        print(f"에피소드 정보 추출 실패: {str(e)}")
                        continue
            
            def enqueue_pages(queue, visited):
                for selector in pagination_selectors:
                    buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                    for button in buttons:
                        page_value = button.get_attribute('data-page')
                        if page_value and page_value.isdigit() and page_value not in visited and page_value not in queue:
                            queue.append(page_value)
            
            episode_info_list = []
            pages_queue = []
            visited_pages = set()
            enqueue_pages(pages_queue, visited_pages)
            
            if not pages_queue:
                collect_current_page_episodes()
            else:
                while pages_queue:
                    page_value = pages_queue.pop(0)
                    if page_value in visited_pages:
                        continue
                    active_page = None
                    try:
                        active_button = driver.find_element(By.CSS_SELECTOR, ".mPagination button.active, .m-pagination button.active, .mf-Pagination-wrap button.active")
                        active_page = active_button.get_attribute('data-page')
                    except:
                        pass
                    if active_page != page_value:
                        button_xpath = f"//button[@data-page='{page_value}']"
                        reference_elements = driver.find_elements(By.CSS_SELECTOR, "li a[href*='/detail/']")
                        reference_element = reference_elements[0] if reference_elements else None
                        try:
                            target_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, button_xpath)))
                            driver.execute_script("arguments[0].click();", target_button)
                            if reference_element:
                                WebDriverWait(driver, 10).until(EC.staleness_of(reference_element))
                        except Exception as e:
                            print(f"페이지 {page_value} 이동 실패: {str(e)}")
                            continue
                        time.sleep(2)
                    collect_current_page_episodes()
                    visited_pages.add(page_value)
                    enqueue_pages(pages_queue, visited_pages)
            
            print(f"\n총 {len(episode_info_list)}개의 에피소드를 찾았습니다.")
        finally:
            driver.quit()
        
        episode_info_list.sort(key=lambda item: episode_sort_key(item['episode_num']))
        for info in episode_info_list:
            info['sanitized_episode'] = sanitize_path_component(info['episode_num'])
        
        if not episode_info_list:
            print("에피소드 정보를 가져올 수 없습니다.")
            continue
        
        sanitized_author = sanitize_path_component(author_name)
        sanitized_comic_title = sanitize_path_component(comic_title)
        base_dir_name = f"{sanitized_comic_title} by {sanitized_author}" if sanitized_author != "Unknown" else sanitized_comic_title
        base_dir = base_dir_name
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir, ignore_errors=True)
        os.makedirs(base_dir, exist_ok=True)
        processed_dirs = set()
        
        def process_episode(info):
            driver = create_driver()
            link = info['link']
            local_comic_title = info['comic_title']
            episode_num = info['episode_num']
            print(f"\n{local_comic_title} {episode_num} 처리 중: {link}")
            episode_dir = os.path.join(base_dir, info['sanitized_episode'])
            os.makedirs(episode_dir, exist_ok=True)
            try:
                driver.get(link)
                time.sleep(3)
                previous_height = 0
                stable_attempts = 0
                while stable_attempts < 3:
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(2)
                    current_height = driver.execute_script("return document.body.scrollHeight")
                    if current_height == previous_height:
                        stable_attempts += 1
                    else:
                        stable_attempts = 0
                        previous_height = current_height
                try:
                    img_wraps = WebDriverWait(driver, 10).until(
                        EC.presence_of_all_elements_located((By.CLASS_NAME, "lazy-img-wrap"))
                    )
                except:
                    img_wraps = driver.find_elements(By.CSS_SELECTOR, ".lazy-img-wrap")
                print(f"  찾은 이미지 랩: {len(img_wraps)}개")
                img_count = 0
                for wrap in img_wraps:
                    try:
                        img_tag = wrap.find_element(By.TAG_NAME, "img")
                        img_url = (img_tag.get_attribute('data-src') or
                                  img_tag.get_attribute('data-original') or
                                  img_tag.get_attribute('src'))
                        if not img_url:
                            img_url = wrap.get_attribute('data-src') or wrap.get_attribute('data-original')
                        if img_url:
                            img_url = urljoin(link, img_url)
                            parsed_url = urlparse(img_url)
                            path = parsed_url.path
                            ext = os.path.splitext(path)[1].lower()
                            if ext in ['.jpg', '.jpeg']:
                                try:
                                    headers = {
                                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                                    }
                                    img_response = requests.get(img_url, headers=headers, timeout=30)
                                    img_response.raise_for_status()
                                    img_filename = f"img_{img_count:04d}.jpg"
                                    img_path = os.path.join(episode_dir, img_filename)
                                    with open(img_path, 'wb') as f:
                                        f.write(img_response.content)
                                    img_count += 1
                                    print(f"  저장: {img_filename}")
                                    time.sleep(0.5)
                                except Exception as e:
                                    print(f"  이미지 다운로드 실패: {img_url} - {str(e)}")
                    except Exception as e:
                        print(f"  이미지 요소 처리 중 에러: {str(e)}")
                        continue
                print(f"  완료: {img_count}개 이미지 저장")
            except Exception as e:
                print(f"  에러 발생: {str(e)}")
            finally:
                driver.quit()
                time.sleep(1)
        
        for batch in batched(episode_info_list, 3):
            with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                futures = [executor.submit(process_episode, info) for info in batch]
                for future in futures:
                    future.result()
        
        base_dir_path = os.path.abspath(base_dir)
        zip_base = os.path.join(os.path.dirname(base_dir_path), os.path.basename(base_dir_path))
        zip_target = f"{zip_base}.zip"
        if os.path.exists(zip_target):
            os.remove(zip_target)
        shutil.make_archive(zip_base, 'zip', os.path.dirname(base_dir_path), os.path.basename(base_dir_path))
        shutil.rmtree(base_dir_path, ignore_errors=True)
        print(f"\n모든 작업 완료! ZIP 저장 위치: {zip_target}")

if __name__ == "__main__":
    crawl_comic_images()
