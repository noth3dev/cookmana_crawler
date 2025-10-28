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
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QProgressBar, QTextEdit, QMessageBox, QFrame, QSizePolicy
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont, QPalette, QColor, QIcon

def sanitize_path_component(value):
    if not value:
        return "Unknown"
    sanitized = re.sub(r'[<>:\"/\\|?*]', '_', value)
    sanitized = sanitized.strip().strip('.')
    return sanitized or "Unknown"

class CrawlerThread(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int)
    title_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, url, gui):
        super().__init__()
        self.url = url
        self.gui = gui
        self.running = True

    def run(self):
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

        episode_info_list = []
        comic_title = "Unknown"
        author_name = "Unknown"

        try:
            self.log_signal.emit("Starting Chrome browser...")
            driver = create_driver()
            self.log_signal.emit("Chrome browser started successfully.")

            self.log_signal.emit(f"Loading main page: {self.url}")
            driver.get(self.url)
            time.sleep(5)

            try:
                comic_title_element = driver.find_element(By.CSS_SELECTOR, "div.dt-left-tt h1")
                comic_title = comic_title_element.text.strip() or "Unknown"
                self.log_signal.emit(f"Comic Title: {comic_title}")
                self.title_signal.emit(comic_title)
            except:
                self.log_signal.emit("Could not find comic title.")
                self.title_signal.emit("Unknown")

            try:
                author_element = driver.find_element(By.CSS_SELECTOR, "div.detail-title1 a.m-episode-link")
                author_name = author_element.text.strip() or "Unknown"
                self.log_signal.emit(f"Author: {author_name}")
            except:
                self.log_signal.emit("Could not find author information.")

            self.log_signal.emit("Collecting episode list...")
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
                    if not self.running:
                        return
                    try:
                        href = ep_link.get_attribute('href')
                        if not href:
                            continue
                        if not href.startswith('http'):
                            href = urljoin(self.url, href)
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
                            self.log_signal.emit("Could not find episode title element.")
                            continue
                        episode_title = (title_element.text or "").strip()
                        if not episode_title:
                            episode_title = (title_element.get_attribute('title') or "").strip()
                        if not episode_title:
                            self.log_signal.emit("Could not find episode title.")
                            continue
                        self.log_signal.emit(f"Found: {episode_title}")
                        if episode_title and episode_title not in seen_episode_titles:
                            seen_episode_titles.add(episode_title)
                            episode_info_list.append({
                                'link': href,
                                'comic_title': comic_title,
                                'episode_num': episode_title,
                                'title_text': episode_title
                            })
                    except Exception as e:
                        self.log_signal.emit(f"Failed to extract episode info: {str(e)}")
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
                while pages_queue and self.running:
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
                            self.log_signal.emit(f"Failed to navigate to page {page_value}: {str(e)}")
                            continue
                        time.sleep(2)
                    collect_current_page_episodes()
                    visited_pages.add(page_value)
                    enqueue_pages(pages_queue, visited_pages)

            self.log_signal.emit(f"\nFound {len(episode_info_list)} episodes.")
        except Exception as e:
            self.log_signal.emit(f"Error: {str(e)}")
        finally:
            if 'driver' in locals():
                driver.quit()

        if not self.running:
            self.finished_signal.emit()
            return

        episode_info_list.sort(key=lambda item: episode_sort_key(item['episode_num']))
        for info in episode_info_list:
            info['sanitized_episode'] = sanitize_path_component(info['episode_num'])

        if not episode_info_list:
            self.log_signal.emit("No episodes found.")
            self.finished_signal.emit()
            return

        sanitized_author = sanitize_path_component(author_name)
        sanitized_comic_title = sanitize_path_component(comic_title)
        base_dir_name = f"{sanitized_comic_title} by {sanitized_author}" if sanitized_author != "Unknown" else sanitized_comic_title
        base_dir = base_dir_name
        existing_episode_dirs = []
        for info in episode_info_list:
            episode_dir = os.path.join(base_dir, info['sanitized_episode'])
            if os.path.isdir(episode_dir):
                existing_episode_dirs.append(episode_dir)
        for episode_dir in existing_episode_dirs[-3:]:
            try:
                shutil.rmtree(episode_dir)
            except Exception as e:
                self.log_signal.emit(f"Failed to remove existing episode dir {episode_dir}: {str(e)}")
        os.makedirs(base_dir, exist_ok=True)
        episodes_to_process = []
        for info in episode_info_list:
            episode_dir = os.path.join(base_dir, info['sanitized_episode'])
            if os.path.isdir(episode_dir) and os.listdir(episode_dir):
                continue
            episodes_to_process.append(info)

        total_episodes = len(episodes_to_process)
        completed_episodes = 0
        self.progress_signal.emit(0, total_episodes)

        def process_episode(info):
            if not self.running:
                return
            driver = create_driver()
            link = info['link']
            local_comic_title = info['comic_title']
            episode_num = info['episode_num']
            self.log_signal.emit(f"\nProcessing {local_comic_title} {episode_num}: {link}")
            episode_dir = os.path.join(base_dir, info['sanitized_episode'])
            os.makedirs(episode_dir, exist_ok=True)
            try:
                driver.get(link)
                time.sleep(3)
                previous_height = 0
                stable_attempts = 0
                while stable_attempts < 3 and self.running:
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
                self.log_signal.emit(f"  Found {len(img_wraps)} image wraps.")
                img_count = 0
                total_imgs = len(img_wraps)
                for wrap in img_wraps:
                    if not self.running:
                        break
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
                            allowed_extensions = {'.jpg', '.jpeg', '.jpe', '.png', '.gif', '.bmp', '.webp', '.avif'}
                            if ext not in allowed_extensions:
                                ext = ''
                            try:
                                headers = {
                                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                                }
                                img_response = requests.get(img_url, headers=headers, timeout=30)
                                img_response.raise_for_status()
                                if not ext:
                                    content_type = img_response.headers.get('Content-Type', '')
                                    if content_type.startswith('image/'):
                                        guessed_ext = mimetypes.guess_extension(content_type.split(';')[0].strip())
                                        if guessed_ext:
                                            if guessed_ext == '.jpe':
                                                guessed_ext = '.jpg'
                                            ext = guessed_ext
                                if not ext or ext not in allowed_extensions:
                                    ext = '.jpg'
                                img_filename = f"img_{img_count:04d}{ext}"
                                img_path = os.path.join(episode_dir, img_filename)
                                with open(img_path, 'wb') as f:
                                    f.write(img_response.content)
                                img_count += 1
                                self.log_signal.emit(f"  Saved: {img_filename}")
                                time.sleep(0.5)
                            except Exception as e:
                                self.log_signal.emit(f"  Failed to download image: {img_url} - {str(e)}")
                    except Exception as e:
                        self.log_signal.emit(f"  Error processing image element: {str(e)}")
                        continue
                self.log_signal.emit(f"  Completed: Saved {img_count} images.")
            except Exception as e:
                self.log_signal.emit(f"  Error occurred: {str(e)}")
            finally:
                driver.quit()
                time.sleep(1)

        for batch in batched(episodes_to_process, 3):
            if not self.running:
                break
            with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                futures = [executor.submit(process_episode, info) for info in batch]
                for future in futures:
                    future.result()
                    completed_episodes += 1
                    self.progress_signal.emit(completed_episodes, total_episodes)

        if self.running:
            base_dir_path = os.path.abspath(base_dir)
            zip_base = os.path.join(os.path.dirname(base_dir_path), os.path.basename(base_dir_path))
            zip_target = f"{zip_base}.zip"
            if os.path.exists(zip_target):
                try:
                    os.remove(zip_target)
                    self.log_signal.emit(f"Removed existing ZIP: {zip_target}")
                except Exception as e:
                    self.log_signal.emit(f"Failed to remove existing ZIP {zip_target}: {str(e)}")
            try:
                shutil.make_archive(zip_base, 'zip', base_dir_path)
                self.log_signal.emit(f"Created ZIP: {zip_target}")
            except Exception as e:
                self.log_signal.emit(f"Failed to create ZIP: {str(e)}")
            try:
                shutil.rmtree(base_dir_path)
                self.log_signal.emit(f"Deleted original directory: {base_dir_path}")
            except Exception as e:
                self.log_signal.emit(f"Failed to delete original directory {base_dir_path}: {str(e)}")
            self.log_signal.emit(f"\nAll tasks completed! ZIP saved at: {zip_target}")

        self.finished_signal.emit()

class ComicCrawlerGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Comic Crawler")
        self.resize(800, 600)
        self.setMinimumSize(600, 400)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # Header Frame
        header_frame = QFrame()
        header_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(5)

        app_title = QLabel("Comic Crawler")
        app_title.setFont(QFont("Segoe UI", 18, QFont.Bold))
        app_title.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(app_title)

        self.title_label = QLabel("Enter URL to start")
        self.title_label.setFont(QFont("Segoe UI", 14, QFont.Normal))
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        header_layout.addWidget(self.title_label)

        main_layout.addWidget(header_frame)

        # Input Frame
        input_frame = QFrame()
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(10)

        url_label = QLabel("URL:")
        url_label.setFixedWidth(40)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Enter comic main page URL...")
        input_layout.addWidget(url_label)
        input_layout.addWidget(self.url_edit)

        main_layout.addWidget(input_frame)

        # Buttons Frame
        buttons_frame = QFrame()
        buttons_layout = QHBoxLayout(buttons_frame)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(10)

        self.start_btn = QPushButton("Start")
        self.start_btn.setIcon(QIcon.fromTheme("media-playback-start"))  # Assuming theme icons available
        self.start_btn.clicked.connect(self.start_crawling)
        self.start_btn.setToolTip("Start crawling the comic")

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setIcon(QIcon.fromTheme("media-playback-stop"))
        self.stop_btn.clicked.connect(self.stop_crawling)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setToolTip("Stop the crawling process")

        buttons_layout.addWidget(self.start_btn)
        buttons_layout.addWidget(self.stop_btn)
        buttons_layout.addStretch()

        main_layout.addWidget(buttons_frame)

        # Progress Frame
        progress_frame = QFrame()
        progress_layout = QVBoxLayout(progress_frame)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(5)

        overall_label = QLabel("Progress:")
        progress_layout.addWidget(overall_label)

        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setTextVisible(True)
        self.overall_progress.setFixedHeight(25)
        self.overall_progress.setFormat("%p% (%v/%m)")
        progress_layout.addWidget(self.overall_progress)

        main_layout.addWidget(progress_frame)

        # Log Frame
        log_frame = QFrame()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(5)

        log_label = QLabel("Log:")
        log_layout.addWidget(log_label)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        self.log_text.setLineWrapMode(QTextEdit.NoWrap)
        log_layout.addWidget(self.log_text)

        main_layout.addWidget(log_frame, stretch=1)

    def start_crawling(self):
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Error", "Please enter a URL.")
            return
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.log_text.clear()
        self.overall_progress.setValue(0)
        self.title_label.setText("Loading...")
        self.thread = CrawlerThread(url, self)
        self.thread.log_signal.connect(self.log)
        self.thread.progress_signal.connect(self.update_progress)
        self.thread.title_signal.connect(self.set_title)
        self.thread.finished_signal.connect(self.finish_crawling)
        self.thread.start()

    def stop_crawling(self):
        if hasattr(self, 'thread'):
            self.thread.running = False
            self.log("Stopping crawling...")

    def log(self, message):
        self.log_text.append(message)
        self.log_text.ensureCursorVisible()

    def update_progress(self, completed, total):
        self.overall_progress.setMaximum(total)
        self.overall_progress.setValue(completed)

    def set_title(self, title):
        self.title_label.setText(title)

    def finish_crawling(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        QMessageBox.information(self, "Info", "Crawling finished.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet("""
        QWidget {
            background-color: #f0f4f8;
            color: #333333;
            font-family: 'Segoe UI', sans-serif;
            font-size: 13px;
        }
        QMainWindow {
            background-color: #ffffff;
        }
        QLabel {
            color: #333333;
        }
        QLineEdit {
            background-color: #ffffff;
            border: 1px solid #d1d5db;
            border-radius: 6px;
            padding: 8px 12px;
            selection-background-color: #3b82f6;
            color: #333333;
        }
        QLineEdit:focus {
            border: 1px solid #3b82f6;
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
        }
        QPushButton {
            background-color: #3b82f6;
            color: #ffffff;
            border: none;
            border-radius: 6px;
            padding: 10px 16px;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: #2563eb;
        }
        QPushButton:disabled {
            background-color: #9ca3af;
            color: #d1d5db;
        }
        QProgressBar {
            background-color: #e5e7eb;
            border-radius: 6px;
            text-align: center;
            color: #333333;
            height: 25px;
            border: 1px solid #d1d5db;
        }
        QProgressBar::chunk {
            background-color: #3b82f6;
            border-radius: 6px;
        }
        QTextEdit {
            background-color: #f9fafb;
            border: 1px solid #d1d5db;
            border-radius: 6px;
            padding: 8px;
            color: #333333;
        }
        QFrame {
            background-color: transparent;
        }
    """)
    window = ComicCrawlerGUI()
    window.show()
    sys.exit(app.exec_())