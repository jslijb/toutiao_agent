"""今日头条发布器 - 支持微头条发布

核心规则：找不到元素就是代码逻辑 bug，立即中断并输出详细诊断信息，
不搞回退选择器猜测，不留容错继续执行。

页面选择器（基于微头条发布页实际 DOM 结构）：
- 编辑器: div.syl-editor div.ProseMirror[contenteditable="true"]
- AI 声明: label.byte-checkbox:has-text("引用AI") 内的 input[type="checkbox"]
- 发布按钮: button.publish-content
- 草稿按钮: button.save-draft
"""
from __future__ import annotations

import os
import time
import base64
from pathlib import Path
from typing import Optional, List

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from config.settings import settings
from utils.logger import logger
from publisher.publisher_base import PublisherBase, PublishResult
from utils.cookie_manager import CookieManager

# 全局变量
_COOKIE_DIR = Path(__file__).parent / "data" / "publisher"
_COOKIE_DIR.mkdir(parents=True, exist_ok=True)

# 微头条发布页 URL (PC端)
_MICRO_PUBLISH_URL = "https://mp.toutiao.com/profile_v4/weitoutiao/publish"

# 编辑器选择器（基于实际页面 DOM：div.syl-editor > div.ProseMirror）
_MICRO_EDITOR = 'div.syl-editor div.ProseMirror[contenteditable="true"]'

# 用户代理
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class ElementNotFoundError(Exception):
    """元素未找到异常 - 附带详细诊断信息"""

    def __init__(self, name: str, selector: str, page_url: str, diag_path: str):
        self.name = name
        self.selector = selector
        self.page_url = page_url
        self.diag_path = diag_path
        super().__init__(
            f"未找到 [{name}]，选择器: {selector}，"
            f"当前 URL: {page_url}，诊断信息已保存到: {diag_path}"
        )


class ToutiaoPublisher(PublisherBase):
    """今日头条发布器"""

    platform: str = "toutiao"

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.page: Optional[Page] = None
        from config.settings import PROJECT_ROOT
        self.cookie_manager = CookieManager(PROJECT_ROOT / "data" / "cookies")

    # ── 诊断工具 ──────────────────────────────────────

    def _diagnose_element_not_found(self, page: Page, name: str, selector: str):
        """元素未找到时，保存完整诊断信息并抛出异常

        诊断内容包括：
        1. 页面截图
        2. 页面完整 HTML
        3. 当前 URL
        4. 页面上所有相关元素列表（帮助定位问题）
        """
        diag_prefix = f"missing_{name}"
        diag_path = str(_COOKIE_DIR / diag_prefix)

        # 保存截图
        page.screenshot(path=f"{diag_path}.png")

        # 保存 HTML
        try:
            html = page.content()
            with open(f"{diag_path}.html", "w", encoding="utf-8") as f:
                f.write(html)
        except Exception as e:
            logger.error(f"[诊断] 保存 HTML 失败: {e}")

        # 保存 URL
        current_url = page.url
        try:
            with open(f"{diag_path}.url", "w", encoding="utf-8") as f:
                f.write(current_url)
        except Exception:
            pass

        # 输出页面上相关元素列表
        try:
            all_info = page.evaluate("""(selector) => {
                const result = {
                    url: location.href,
                    title: document.title,
                    // 查找所有可能相关的元素
                    allCheckboxes: Array.from(document.querySelectorAll('label.byte-checkbox, input[type="checkbox"]')).map(el => ({
                        tag: el.tagName,
                        class: el.className,
                        text: el.textContent?.trim().slice(0, 50),
                        value: el.value || '',
                        type: el.type || '',
                    })),
                    allButtons: Array.from(document.querySelectorAll('button')).map(el => ({
                        tag: el.tagName,
                        class: el.className,
                        text: el.textContent?.trim().slice(0, 50),
                    })),
                    allContentEditable: Array.from(document.querySelectorAll('[contenteditable="true"]')).map(el => ({
                        tag: el.tagName,
                        class: el.className,
                        text: el.textContent?.trim().slice(0, 50),
                    })),
                    allInputs: Array.from(document.querySelectorAll('input, textarea')).map(el => ({
                        tag: el.tagName,
                        class: el.className,
                        placeholder: el.placeholder || '',
                        name: el.name || '',
                        type: el.type || '',
                    })),
                };
                return result;
            }""", selector)

            logger.error(f"[诊断] 页面 URL: {all_info.get('url')}")
            logger.error(f"[诊断] 页面标题: {all_info.get('title')}")
            logger.error(f"[诊断] 页面上所有 checkbox: {all_info.get('allCheckboxes')}")
            logger.error(f"[诊断] 页面上所有 button: {all_info.get('allButtons')}")
            logger.error(f"[诊断] 页面上所有 contenteditable: {all_info.get('allContentEditable')}")
            logger.error(f"[诊断] 页面上所有 input/textarea: {all_info.get('allInputs')}")

        except Exception as e:
            logger.error(f"[诊断] 获取页面元素列表失败: {e}")

        raise ElementNotFoundError(name, selector, current_url, diag_path)

    # ── 登录管理 ──────────────────────────────────────

    def login(self) -> bool:
        """登录今日头条 - 先加载 Cookie 自动登录，失败才弹扫码窗口"""
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(user_agent=_USER_AGENT)
                self.page = context.new_page()

                # 先加载已保存的 Cookie
                cookies = self.cookie_manager.load_cookies("toutiao")
                if cookies:
                    logger.info(f"[Publisher] 加载了 {len(cookies)} 个已保存的 Cookie")
                    context.add_cookies(cookies)
                else:
                    logger.info("[Publisher] 无已保存的 Cookie，需要扫码登录")

                # 访问创作者后台
                self.page.goto("https://mp.toutiao.com")
                time.sleep(3)

                # 检查是否已登录（Cookie 有效则自动登录成功）
                if self._check_logged_in_on_page():
                    logger.info("[Publisher] Cookie 有效，自动登录成功！")
                    # 刷新保存最新 Cookie（延长有效期）
                    self._save_cookies()
                    return True

                # Cookie 无效或不存在，需要扫码登录
                logger.info("[Publisher] Cookie 已过期或不存在，请扫描二维码登录...")
                time.sleep(2)
                self.page.screenshot(path=str(_COOKIE_DIR / "qrcode.png"))

                # 等待用户扫码登录
                for i in range(60):
                    if self._check_logged_in_on_page():
                        logger.info("[Publisher] 扫码登录成功！")
                        self._save_cookies()
                        return True
                    time.sleep(1)
                    if i % 10 == 9:
                        logger.info(f"[Publisher] 等待扫码登录中... ({i + 1}s)")

                logger.error("[Publisher] 登录超时，请重试")
                return False

        except Exception as e:
            logger.error(f"[Publisher] 登录失败: {e}")
            return False

    def _check_logged_in_on_page(self) -> bool:
        """检查是否已登录"""
        if not self.page:
            return False
        try:
            # 检查用户菜单（登录后出现）
            if self.page.query_selector('.user-panel, .user-menu-wrapper'):
                return True
            # 检查 URL 是否在创作者后台
            if "mp.toutiao.com/profile_v4" in self.page.url:
                return True
            return False
        except Exception:
            return False

    def _save_cookies(self):
        """保存当前浏览器的所有 Cookie"""
        try:
            cookies = self.page.context.cookies()
            logger.info(f"[Publisher] 获取到 {len(cookies)} 个 cookies")
            domains = {}
            for c in cookies:
                domain = c.get('domain', 'unknown')
                domains[domain] = domains.get(domain, 0) + 1
            logger.info(f"[Publisher] Cookie 域名分布：{domains}")
            self.cookie_manager.save_cookies("toutiao", cookies)
            logger.info(f"[Publisher] Cookie 已保存到：{self.cookie_manager.cookie_dir}")
        except Exception as e:
            logger.error(f"[Publisher] 保存 Cookie 失败: {e}")

    def is_logged_in(self) -> bool:
        """检查是否已登录"""
        return self._check_logged_in_on_page()

    def verify_login(self) -> bool:
        """验证登录状态（深度验证）"""
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=_USER_AGENT)
                page = context.new_page()

                cookies = self.cookie_manager.load_cookies("toutiao")
                if not cookies:
                    logger.error("[Publisher] 未找到 Cookie 文件")
                    return False

                logger.info(f"[Publisher] 加载了 {len(cookies)} 个 cookies")
                page.context.add_cookies(cookies)

                page.goto("https://mp.toutiao.com")
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

                time.sleep(3)
                page.screenshot(path=str(_COOKIE_DIR / "verify_login_page.png"))
                logger.info(f"[Publisher] 当前 URL: {page.url}")

                if page.query_selector('.user-panel, .user-menu-wrapper'):
                    return True
                if "mp.toutiao.com/profile_v4" in page.url:
                    return True

                logger.warning("[Publisher] Cookie 已过期，需要重新登录")
                return False

        except Exception as e:
            logger.error(f"[Publisher] 验证登录失败：{e}")
            return False

    # ── 发布 ──────────────────────────────────────────

    def publish_article(
        self,
        title: str,
        content: str,
        image_paths: Optional[List[str]] = None,
        location: str = "",
        category: str = "",
        topics: Optional[List[str]] = None,
        **kwargs,
    ) -> PublishResult:
        """发布文章"""
        result = PublishResult(platform=self.platform)
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(user_agent=_USER_AGENT)
                self.page = context.new_page()

                # 加载 Cookie
                self._load_cookies_to_page()

                # 直接导航到微头条发布页
                self.page.goto(_MICRO_PUBLISH_URL)

                # 等待 SPA 完全渲染（编辑器出现 = 页面加载完成）
                try:
                    self.page.wait_for_selector(
                        _MICRO_EDITOR, timeout=20000,
                        state="visible"
                    )
                    logger.info("[Publisher] 页面加载完成，编辑器已渲染")
                except PlaywrightTimeout:
                    # 编辑器超时未出现 → 可能是 SPA 加载慢或页面异常
                    logger.warning("[ Publisher] 等待编辑器渲染超时(20s)，保存诊断信息...")
                    self._diagnose_element_not_found(
                        self.page, "编辑器(SPA加载)", _MICRO_EDITOR
                    )

                # 检查登录状态
                if not self._check_logged_in_on_page():
                    result.error = "未登录，请先登录头条号"
                    return result

                # 关闭弹窗 → 填内容 → 上传图 → 声明 → 发布
                self._close_assistant_drawer()
                self._fill_content(title, content, topics)
                if image_paths:
                    self._upload_images(self.page, image_paths)
                if location:
                    self._add_location(self.page, location)
                if category:
                    self._select_category(self.page, category)
                self._add_ai_declaration(self.page)
                self._publish(self.page)

                result.success = True
                result.message = "发布成功"

        except Exception as e:
            logger.error(f"[Publisher] 发布失败: {e}")
            result.error = str(e)
        return result

    def publish_micro_toutiao(
        self,
        content: str,
        image_paths: Optional[List[str]] = None,
        topics: Optional[List[str]] = None,
        location: str = "",
        **kwargs,
    ) -> PublishResult:
        """发布微头条"""
        result = PublishResult(platform=self.platform)
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(user_agent=_USER_AGENT)
                self.page = context.new_page()

                self._load_cookies_to_page()
                self.page.goto(_MICRO_PUBLISH_URL)

                # 等待 SPA 完全渲染（编辑器出现 = 页面加载完成）
                try:
                    self.page.wait_for_selector(
                        _MICRO_EDITOR, timeout=20000,
                        state="visible"
                    )
                    logger.info("[Publisher] 页面加载完成，编辑器已渲染")
                except PlaywrightTimeout:
                    logger.warning("[Publisher] 等待编辑器渲染超时(20s)，保存诊断信息...")
                    self._diagnose_element_not_found(
                        self.page, "编辑器(SPA加载)", _MICRO_EDITOR
                    )

                if not self._check_logged_in_on_page():
                    result.error = "未登录，请先登录头条号"
                    return result

                self._close_assistant_drawer()
                self._fill_content("", content, topics)
                if image_paths:
                    self._upload_images(self.page, image_paths)
                if location:
                    self._add_location(self.page, location)
                self._add_ai_declaration(self.page)
                self._publish(self.page)

                result.success = True
                result.message = "发布成功"

        except Exception as e:
            logger.error(f"[Publisher] 发布失败：{e}")
            result.error = str(e)
        return result

    def _load_cookies_to_page(self):
        """将已保存的 Cookie 加载到当前页面上下文"""
        cookies = self.cookie_manager.load_cookies("toutiao")
        if cookies:
            logger.info(f"[Publisher] 加载了 {len(cookies)} 个已保存的 Cookie")
            self.page.context.add_cookies(cookies)
        else:
            logger.warning("[Publisher] 无已保存的 Cookie，可能需要登录")

    # ── 弹窗关闭 ──────────────────────────────────────

    def _close_assistant_drawer(self):
        """关闭发布助手抽屉弹窗

        发布助手会弹出 byte-drawer 遮罩层覆盖整个页面，
        必须彻底关闭才能操作编辑器。
        """
        try:
            for attempt in range(5):  # 多次尝试确保关闭
                mask = self.page.query_selector('div.byte-drawer-mask')
                if not mask:
                    logger.info(f"[Publisher] 无发布助手弹窗 (第{attempt+1}次检查)")
                    break

                # 方法1: 按 ESC 关闭
                self.page.keyboard.press('Escape')
                time.sleep(1)

            # 最终确认
            mask = self.page.query_selector('div.byte-drawer-mask')
            if mask:
                # ESC 关不掉 → 用 JS 强制移除遮罩层 DOM
                logger.warning("[Publisher] ESC 无法关闭发布助手弹窗，尝试强制移除遮罩层")
                removed = self.page.evaluate("""() => {
                    const mask = document.querySelector('div.byte-drawer-mask');
                    if (mask) {
                        // 尝试找到关闭按钮点击
                        const closeBtn = document.querySelector(
                            '.byte-drawer-close-icon, .byte-drawer-header .close, [class*="close-icon"]'
                        );
                        if (closeBtn) { closeBtn.click(); return 'clicked_close_btn'; }
                        // 强制移除遮罩
                        const wrapper = document.querySelector('.publish-assistant-old-drawer');
                        if (wrapper) { wrapper.style.display = 'none'; return 'hidden_wrapper'; }
                    }
                    return 'none';
                }""")
                logger.info(f"[Publisher] 强制处理结果: {removed}")
                time.sleep(0.5)
            else:
                logger.info("[Publisher] ✅ 发布助手弹窗已关闭")

        except Exception as e:
            logger.error(f"[Publisher] 关闭发布助手弹窗失败: {e}")
            raise

    # ── 内容填写 ──────────────────────────────────────

    def _fill_content(self, title: str, content: str, topics: Optional[List[str]] = None):
        """填写内容到编辑器"""
        # 填写标题（微头条没有标题输入框，只有文章类型有）
        if title:
            title_input = self.page.query_selector(
                'textarea[placeholder*="标题"], input[placeholder*="标题"]'
            )
            if title_input:
                title_input.fill(title)
                logger.info(f"[Publisher] 已填写标题: {title}")

        # 组装并输入正文
        full_content = self._build_micro_content(content, topics)
        self._type_content(self.page, full_content)

    def _type_content(self, page: Page, content: str):
        """将内容输入到 ProseMirror 编辑器

        优先使用 JS execCommand 方式（快速且可靠），
        失败则使用键盘逐字输入。
        """
        # 查找编辑器 - 找不到立即中断
        editor = page.query_selector(_MICRO_EDITOR)
        if not editor:
            self._diagnose_element_not_found(page, "编辑器", _MICRO_EDITOR)

        logger.info("[Publisher] 找到编辑器：div.syl-editor div.ProseMirror")

        # 方法1：通过 document.execCommand('insertHTML') 插入
        html_content = content.replace('\n', '<br>')
        try:
            success = page.evaluate("""(args) => {
                const editor = document.querySelector('div.syl-editor div.ProseMirror[contenteditable="true"]');
                if (!editor) return false;
                editor.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('insertHTML', false, args.html);
                return true;
            }""", {"html": html_content})
            if success:
                logger.info("[Publisher] 通过 JS insertHTML 输入内容成功")
                return
        except Exception as e:
            logger.warning(f"[Publisher] JS insertHTML 失败: {e}，尝试键盘输入")

        # 方法2：键盘逐段输入
        editor.focus()
        time.sleep(0.3)
        page.keyboard.press("Control+A")
        time.sleep(0.2)
        page.keyboard.press("Backspace")
        time.sleep(0.2)

        paragraphs = content.split('\n')
        for i, para in enumerate(paragraphs):
            page.keyboard.type(para, delay=20)
            time.sleep(0.05)
            if i < len(paragraphs) - 1:
                page.keyboard.press('Enter')
                time.sleep(0.05)

        logger.info("[Publisher] 通过键盘输入内容完成")

    # ── 图片上传 ──────────────────────────────────────

    def _upload_images(self, page: Page, image_paths: List[str]):
        """上传配图（通过剪贴板粘贴方式）"""
        valid_paths = []
        for p in image_paths:
            path = Path(p)
            if path.exists() and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                valid_paths.append(str(path.resolve()))

        if not valid_paths:
            logger.warning("[Publisher] 没有有效的配图可上传")
            return

        logger.info(f"[Publisher] 开始粘贴上传 {len(valid_paths)} 张配图...")

        # 确保编辑器获得焦点，光标移到末尾 - 找不到编辑器立即中断
        editor = page.query_selector(_MICRO_EDITOR)
        if not editor:
            self._diagnose_element_not_found(page, "编辑器(上传图片)", _MICRO_EDITOR)

        editor.click()
        time.sleep(0.3)
        page.keyboard.press("Control+End")
        time.sleep(0.2)
        page.keyboard.press("Enter")
        time.sleep(0.2)

        for idx, img_path in enumerate(valid_paths):
            logger.info(f"[Publisher] 配图 {idx + 1}/{len(valid_paths)}: {Path(img_path).name}")

            img_base64 = self._read_image_as_base64(img_path)
            if not img_base64:
                raise Exception(f"无法读取图片: {img_path}")

            ext = Path(img_path).suffix.lower()
            mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
            mime_type = mime_map.get(ext, "image/png")

            paste_success = page.evaluate("""async ({base64, mimeType}) => {
                const editor = document.querySelector('div.syl-editor div.ProseMirror[contenteditable="true"]');
                if (!editor) return false;

                const byteStr = atob(base64);
                const ab = new ArrayBuffer(byteStr.length);
                const ia = new Uint8Array(ab);
                for (let i = 0; i < byteStr.length; i++) {
                    ia[i] = byteStr.charCodeAt(i);
                }
                const blob = new Blob([ab], { type: mimeType });
                const file = new File([blob], 'image.png', { type: mimeType });

                const dt = new DataTransfer();
                dt.items.add(file);

                const pasteEvent = new ClipboardEvent('paste', {
                    bubbles: true,
                    cancelable: true,
                    clipboardData: dt,
                });

                editor.focus();
                editor.dispatchEvent(pasteEvent);
                return true;
            }""", {"base64": img_base64, "mimeType": mime_type})

            if not paste_success:
                raise Exception(f"JS 粘贴事件触发失败: {img_path}")

            time.sleep(2)
            logger.info(f"[Publisher] ✅ 配图 {idx + 1}/{len(valid_paths)} 粘贴成功")

        logger.info(f"[Publisher] 配图上传完成: {len(valid_paths)} 张成功")

    @staticmethod
    def _read_image_as_base64(img_path: str) -> str:
        """读取图片文件为 base64 字符串"""
        try:
            with open(img_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"[Publisher] 读取图片失败: {e}")
            return ""

    # ── 内容组装 ──────────────────────────────────────

    def _build_micro_content(self, content: str, topics: Optional[List[str]] = None) -> str:
        """组装微头条完整内容：正文 + 话题"""
        fixed_topics = ["职场", "副业搞钱", "个人成长"]
        dynamic_topics = topics or []
        all_topics = fixed_topics + dynamic_topics[:2]

        parts = [content]
        if all_topics:
            topic_str = " ".join(f"#{topic}#" for topic in all_topics)
            parts.append(topic_str)

        full = "\n".join(parts)
        logger.info(f"[Publisher] 内容组装完成: {len(content)}字正文 + {len(all_topics)}个话题")
        return full

    # ── 位置 / 声明 / 分类 ────────────────────────────

    def _add_location(self, page: Page, location: str):
        """添加位置信息 - 找不到立即中断

        实际 DOM 结构：位置是一个 position-select 组件（非标准 input）
        <div class="item-component">
          <div tabindex="0" class="position-select byte-select ...">
            <div class="byte-select-view-placeholder">标记位置，让更多用户看到</div>
            <span class="byte-select-view-search">
              <input value="">
            </span>
          </div>
        </div>

        操作方式：点击组件 → 在内部 input 中输入 → 从下拉列表选择
        """
        logger.info(f"[Publisher] 开始添加位置: {location}")

        # 查找位置选择组件
        location_select = page.query_selector('div.position-select')
        if not location_select:
            self._diagnose_element_not_found(
                page, "位置选择组件", 'div.position-select'
            )

        # 点击激活组件
        location_select.click()
        time.sleep(1)

        # 在搜索 input 中输入位置
        search_input = page.query_selector('div.position-select input')
        if not search_input:
            self._diagnose_element_not_found(
                page, "位置搜索input", 'div.position-select input'
            )

        search_input.fill(location)
        time.sleep(2)

        # 等待下拉选项出现并点击匹配项
        option = page.query_selector(f'div[class*="select-option"]:has-text("{location}")')
        if option:
            option.click()
            time.sleep(0.5)
        else:
            # 如果没有下拉选项，按 Enter 确认
            logger.info(f"[Publisher] 无下拉选项，尝试回车确认")
            search_input.press("Enter")
            time.sleep(0.5)

        logger.info(f"[Publisher] ✅ 已添加位置: {location}")

    def _add_ai_declaration(self, page: Page):
        """勾选 AI 作者声明「引用AI」

        实际页面 DOM 结构：
        <label class="byte-checkbox checkbot-item checkbox-with-tip">
          <input type="checkbox" value="3">
          <span class="byte-checkbox-wrapper">
            <div class="byte-checkbox-mask"></div>
            <span class="byte-checkbox-inner-text">引用AI</span>
          </span>
        </label>

        找不到 = 页面结构变了或页面未正确加载，必须中断。
        """
        logger.info("[Publisher] 开始勾选「引用AI」声明...")

        checkbox_label = page.query_selector(
            'label.byte-checkbox:has-text("引用AI")'
        )
        if not checkbox_label:
            self._diagnose_element_not_found(
                page, "引用AI声明", 'label.byte-checkbox:has-text("引用AI")'
            )

        # 检查是否已经勾选
        is_checked = page.evaluate("""(label) => {
            const input = label.querySelector('input[type="checkbox"]');
            return input ? input.checked : false;
        }""", checkbox_label)

        if not is_checked:
            checkbox_label.click()
            time.sleep(0.5)
            logger.info("[Publisher] ✅ 已勾选「引用AI」声明")
        else:
            logger.info("[Publisher] 「引用AI」声明已勾选，跳过")

    def _select_category(self, page: Page, category: str):
        """选择文章分类 - 找不到立即中断"""
        logger.info(f"[Publisher] 开始选择分类: {category}")

        cat_selector = page.query_selector(
            '[class*="category"], [class*="classify"], '
            '[placeholder*="分类"], [placeholder*="领域"]'
        )
        if not cat_selector:
            self._diagnose_element_not_found(
                page, "分类选择器", '[class*="category"], [placeholder*="分类"]'
            )

        cat_selector.click()
        time.sleep(0.5)

        option = page.query_selector(f'text="{category}"')
        if not option:
            self._diagnose_element_not_found(
                page, f"分类选项({category})", f'text="{category}"'
            )

        option.click()
        logger.info(f"[Publisher] ✅ 已选择分类: {category}")

    # ── 发布 ──────────────────────────────────────────

    def _publish(self, page: Page):
        """点击发布按钮并等待发布结果

        实际页面 DOM 结构：
        <div class="footer garr-footer-publish-content">
          <button class="byte-btn byte-btn-default save-draft">存草稿</button>
          <button class="byte-btn byte-btn-primary publish-content">发布</button>
        </div>

        点击后需要等待发布完成（成功提示或页面跳转）。
        """
        logger.info("[Publisher] 开始发布...")

        publish_btn = page.query_selector('button.publish-content')
        if not publish_btn:
            self._diagnose_element_not_found(page, "发布按钮", 'button.publish-content')

        # 记录点击前的 URL
        url_before = page.url

        # 滚动到底部确保按钮可见
        publish_btn.scroll_into_view_if_needed()
        time.sleep(0.5)
        
        publish_btn.click()
        logger.info("[Publisher] 已点击发布按钮，等待发布结果...")

        # 等待发布完成（以下任一条件即视为发布处理完毕）:
        # 1. 出现成功/失败提示弹窗
        # 2. 页面 URL 发生变化（跳转）
        # 3. 发布按钮变为 disabled/loading 状态
        try:
            page.wait_for_function(
                """(urlBefore) => {
                    if (window.location.href !== urlBefore) return true;
                    const modals = document.querySelectorAll(
                        '.byte-modal, .byte-toast, .garr-toast, [class*="publish-success"], [class*="publish-result"]'
                    );
                    for (const m of modals) {
                        if (m.offsetParent !== null || getComputedStyle(m).display !== 'none') return true;
                    }
                    const btn = document.querySelector('button.publish-content');
                    if (btn && btn.disabled) return true;
                    return false;
                }""",
                arg=url_before,
                timeout=15000,
            )
            logger.info(f"[Publisher] 发布响应已返回，当前URL: {page.url}")
        except PlaywrightTimeout:
            # 超时但可能是网络慢，记录但不中断
            current_url = page.url
            logger.warning(
                f"[Publisher] 发布等待超时(15s)，"
                f"点击前URL: {url_before}, 当前URL: {current_url}"
            )
            # 截图保存当前状态用于排查
            page.screenshot(path=str(_COOKIE_DIR / "publish_timeout.png"))

        time.sleep(2)

        # 最终截图确认
        final_screenshot = str(_COOKIE_DIR / "publish_result.png")
        page.screenshot(path=final_screenshot)
        logger.info(f"[Publisher] ✅ 发布流程结束，结果截图: {final_screenshot}")

    def close(self):
        """关闭浏览器/释放资源"""
        if self.page:
            try:
                self.page.close()
            except Exception:
                pass
            self.page = None
        logger.info("[Publisher] 发布器已关闭")


# ── 全局单例管理 ──────────────────────────────────────

_publisher_instance: Optional[ToutiaoPublisher] = None


def get_toutiao_publisher() -> ToutiaoPublisher:
    """获取头条发布器单例"""
    global _publisher_instance
    if _publisher_instance is None:
        headless = getattr(settings, "publisher", None)
        headless = getattr(headless, "headless", True) if headless else True
        _publisher_instance = ToutiaoPublisher(headless=headless)
    return _publisher_instance


def close_publisher():
    """关闭发布器"""
    global _publisher_instance
    if _publisher_instance:
        _publisher_instance.close()
        _publisher_instance = None
