import asyncio
import hashlib
import logging
import re
from typing import Dict, List, Optional, Set

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeoutError,
)

import config

logger = logging.getLogger(__name__)

LOGIN_SELECTORS = {
    'username': [
        '#username',
        'input[name="username"]',
        'input[type="text"][id*="user"]',
        'input[autocomplete="username"]',
        '.pf-c-form-control[name="username"]',
    ],
    'password': [
        '#password',
        'input[name="password"]',
        'input[type="password"]',
        '.pf-c-form-control[name="password"]',
    ],
    'submit': [
        '#kc-login',
        'input[type="submit"]',
        'button[type="submit"]',
        '.pf-c-button[type="submit"]',
    ],
}


def _notif_hash(client_id: str, subject: str, date: str, notif_type: str) -> str:
    raw = f"{client_id}|{(subject or '').strip()}|{(date or '').strip()}|{(notif_type or '').strip()}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


async def _safe_text(page: Page, selector: str) -> str:
    try:
        el = await page.query_selector(selector)
        if el:
            return (await el.inner_text()).strip()
    except Exception:
        pass
    return ''


async def _wait_for_ui5(page: Page, timeout: int = 30000) -> None:
    try:
        await page.wait_for_selector(
            '.sapMBusyIndicator, .sapUiBusy, .sapBlockLayerTapThrough',
            state='hidden', timeout=timeout,
        )
    except PWTimeoutError:
        pass
    try:
        await page.wait_for_load_state('networkidle', timeout=timeout)
    except PWTimeoutError:
        pass
    await asyncio.sleep(2)


async def _screenshot(page: Page, client_name: str, step: str) -> None:
    if not config.SAVE_SCREENSHOTS:
        return
    try:
        raw  = f"{client_name}_{step}"
        safe = re.sub(r'[^\w\-]', '_', raw)[:80]   # max 80 chars to avoid OS path-length limits
        ts   = int(asyncio.get_event_loop().time())
        path = config.SCREENSHOTS_DIR / f"err_{safe}_{ts}.png"
        await page.screenshot(path=str(path), full_page=True)
    except Exception:
        pass


def cleanup_old_screenshots() -> None:
    """مسح ملفات الـ screenshots الأقدم من LOG_RETENTION_DAYS من فولدر logs/screenshots."""
    import time
    cutoff  = time.time() - (config.LOG_RETENTION_DAYS * 86400)
    deleted = 0
    for f in config.SCREENSHOTS_DIR.glob('*.png'):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                deleted += 1
        except Exception:
            pass
    if deleted:
        logger.info(f"Deleted {deleted} old screenshots (>{config.LOG_RETENTION_DAYS} days)")


class ETAScraper:

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        args = dict(
            headless=config.HEADLESS,
            args=['--disable-dev-shm-usage', '--ignore-certificate-errors', '--lang=ar'],
        )
        for channel in (None, 'chrome', 'msedge'):
            try:
                if channel:
                    self._browser = await self._playwright.chromium.launch(channel=channel, **args)
                else:
                    self._browser = await self._playwright.chromium.launch(**args)
                logger.info(f"Browser ready ({channel or 'playwright-chromium'})")
                return
            except Exception as e:
                logger.warning(f"Browser launch failed ({channel}): {e}")
        raise RuntimeError("No usable browser found — install Chrome or Edge")

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _new_context(self, accept_downloads: bool = False) -> BrowserContext:
        return await self._browser.new_context(
            ignore_https_errors=True,
            locale='ar-EG',
            timezone_id='Africa/Cairo',
            viewport={'width': 1280, 'height': 900},
            accept_downloads=accept_downloads,
        )

    async def login(self, page: Page, username: str, password: str, client_name: str) -> bool:
        try:
            await page.goto(config.ETA_AUTH_URL, timeout=config.PAGE_TIMEOUT, wait_until='domcontentloaded')
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"[{client_name}] login page load failed: {e}")
            await _screenshot(page, client_name, 'goto_login')
            return False

        for field, selectors in [('username', LOGIN_SELECTORS['username']), ('password', LOGIN_SELECTORS['password'])]:
            filled = False
            value  = username if field == 'username' else password
            for sel in selectors:
                try:
                    await page.fill(sel, value, timeout=5000)
                    filled = True
                    break
                except Exception:
                    continue
            if not filled:
                logger.error(f"[{client_name}] {field} field not found")
                await _screenshot(page, client_name, f'{field}_field')
                return False

        clicked = False
        for sel in LOGIN_SELECTORS['submit']:
            try:
                await page.click(sel, timeout=5000)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            try:
                await page.keyboard.press('Enter')
                clicked = True
            except Exception:
                pass
        if not clicked:
            logger.error(f"[{client_name}] submit button not found")
            await _screenshot(page, client_name, 'submit')
            return False

        try:
            await page.wait_for_url(
                re.compile(r'workspace\.eta\.gov\.eg|fpascs\.eta\.gov\.eg'),
                timeout=config.PAGE_TIMEOUT,
            )
        except PWTimeoutError:
            err = await _safe_text(page, '#input-error, .pf-c-alert__description, [class*="error"]')
            logger.error(f"[{client_name}] login timeout — {err or page.url}")
            await _screenshot(page, client_name, 'login_timeout')
            return False

        return await self._navigate_to_app(page, client_name)

    async def _navigate_to_app(self, page: Page, client_name: str) -> bool:
        try:
            await page.goto(config.ETA_HOME_URL, timeout=config.PAGE_TIMEOUT)
            await _wait_for_ui5(page, timeout=60000)
        except Exception as e:
            logger.error(f"[{client_name}] SAP app load failed: {e}")
            await _screenshot(page, client_name, 'app_load')
            return False

        if 'auth.eta.gov.eg' in page.url or 'login' in page.url.lower():
            logger.error(f"[{client_name}] redirected back to login — bad credentials")
            return False

        return True

    async def get_home_counts(self, page: Page, client_name: str) -> Dict[str, int]:
        counts = {'notifications': 0, 'obligations': 0, 'forms': 0, 'documents': 0}
        try:
            if '#/home' not in page.url:
                await page.goto(config.ETA_HOME_URL, timeout=config.PAGE_TIMEOUT)
                await _wait_for_ui5(page)
            counts = await self._extract_card_counts(page, client_name)
        except Exception as e:
            logger.warning(f"[{client_name}] card counts failed: {e}")
        return counts

    async def _extract_card_counts(self, page: Page, client_name: str) -> Dict[str, int]:
        result = {'notifications': 0, 'obligations': 0, 'forms': 0, 'documents': 0}

        tile_data = await page.evaluate("""
        () => {
            const sels = [
                '.sapMGT', '.sapMST', '[role="option"]',
                '[role="button"][class*="tile"]',
                '[class*="Tile"]:not([class*="TileContent"])',
            ];
            let cards = [];
            for (const s of sels) {
                cards = Array.from(document.querySelectorAll(s));
                if (cards.length > 1) break;
            }
            if (!cards.length) {
                const kw = ['التنبيهات','التزامات التقديم','النماذج','مستنداتي'];
                cards = Array.from(document.querySelectorAll('div, section, article'))
                    .filter(el => {
                        const t = el.innerText || '';
                        return kw.some(k => t.includes(k)) && t.length < 300 && el.children.length > 0;
                    });
            }
            return cards.map(card => {
                const text = (card.innerText || '').trim();
                const nums = text.match(/(?<![\\w٠-٩])[0-9٠-٩]+(?![\\w٠-٩])/g) || [];
                return { text, nums };
            }).filter(c => c.text.length > 0);
        }
        """)

        tr = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')
        for item in tile_data:
            text = item.get('text', '')
            nums = item.get('nums', [])
            val  = 0
            for n in reversed(nums):
                try:
                    val = int(n.translate(tr))
                    break
                except ValueError:
                    continue

            if any(k in text for k in config.CARD_LABELS['notifications']):
                result['notifications'] = val
            elif any(k in text for k in config.CARD_LABELS['obligations']):
                result['obligations'] = val
            elif any(k in text for k in config.CARD_LABELS['forms']):
                result['forms'] = val
            elif any(k in text for k in config.CARD_LABELS['documents']):
                result['documents'] = val

        logger.info(
            f"[{client_name}] counts — notifs={result['notifications']} "
            f"obligations={result['obligations']} forms={result['forms']} docs={result['documents']}"
        )
        return result

    async def get_notifications(self, page: Page, client_name: str,
                                client_id: str = '', known_hashes: Set[str] = None) -> List[Dict]:
        notifications = []
        try:
            if '#/home' not in page.url:
                await page.goto(config.ETA_HOME_URL, timeout=config.PAGE_TIMEOUT)
                await _wait_for_ui5(page)

            if not await self._click_notifications_card(page, client_name):
                logger.warning(f"[{client_name}] notifications card not found")
                return []

            await _wait_for_ui5(page, timeout=20000)
            await asyncio.sleep(2)

            if await self._is_session_expired(page):
                logger.warning(f"[{client_name}] session expired")
                return []

            known = known_hashes or set()
            for page_num in range(50):
                items = await self._extract_notifications_from_page(page, client_name, client_id, known)
                if not items:
                    break

                notifications.extend(items)

                if len(notifications) >= config.MAX_NOTIFICATIONS:
                    logger.warning(f"[{client_name}] hit MAX_NOTIFICATIONS limit")
                    break

                if not await self._go_to_next_page(page):
                    break

                await _wait_for_ui5(page, timeout=15000)

        except Exception as e:
            logger.error(f"[{client_name}] notification scrape error: {e}")
            await _screenshot(page, client_name, 'notifications')

        logger.info(f"[{client_name}] scraped {len(notifications)} notifications")
        return notifications

    async def _extract_notifications_from_page(self, page: Page, client_name: str,
                                                client_id: str = '', known_hashes: Set[str] = None) -> List[Dict]:
        items = await page.evaluate("""
        () => {
            const rows = document.querySelectorAll('.sapMLIB');
            if (!rows.length) return [];
            return Array.from(rows).map(row => {
                const walker = document.createTreeWalker(
                    row,
                    NodeFilter.SHOW_TEXT,
                    { acceptNode: n => n.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT }
                );
                const texts = [];
                let node;
                while ((node = walker.nextNode())) {
                    const t = node.textContent.trim();
                    if (t && !texts.includes(t)) texts.push(t);
                }
                return { texts };
            });
        }
        """)

        known    = known_hashes or set()
        new_notifs      = []  # need detail enrichment (click)
        existing_notifs = []  # already in DB, skip click

        for item in items:
            notif = self._parse_notification_row(item.get('texts', []))
            if not notif:
                continue
            h = _notif_hash(client_id, notif.get('subject', ''),
                            notif.get('date', ''), notif.get('notif_type', ''))
            notif['_hash'] = h
            if h in known:
                existing_notifs.append(notif)
            else:
                new_notifs.append(notif)

        if existing_notifs:
            logger.debug(f"[{client_name}] skipping {len(existing_notifs)} already-fetched notifications")

        enriched = await self._enrich_with_details(page, new_notifs, client_name)
        return enriched + existing_notifs

    def _parse_notification_row(self, texts: List[str]) -> Optional[Dict]:
        if not texts:
            return None

        READ_STATUS_VALS = {'تم الاستلام', 'تم القراءة', 'غير مقروء'}
        SKIP = {'تم إصدار تحذير'}
        SEVERITY_VALS = {'معلومات', 'تحذير', 'خطأ', 'عاجل', 'طارئ', 'Information', 'Warning', 'Error'}
        TYPE_VALS     = {'تنبيه', 'إشعار', 'رسالة', 'Alert', 'Notification'}
        date_pat      = re.compile(
            r'\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}|\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2}'
        )

        result = {'severity': '', 'notif_type': '', 'subject': '', 'message_body': '', 'date': '', 'read_status': ''}
        remaining = []

        for t in texts:
            clean = t.replace('‏', '').replace('‎', '').strip()
            if date_pat.search(clean):
                result['date'] = clean
            elif t in READ_STATUS_VALS:
                result['read_status'] = t
            elif t in SKIP:
                pass
            elif t in SEVERITY_VALS:
                result['severity'] = t
            elif t in TYPE_VALS:
                result['notif_type'] = t
            else:
                remaining.append(t)

        if not remaining and not result['notif_type'] and not result['severity']:
            return None

        if remaining:
            result['subject'] = remaining[0]
            if len(remaining) > 1:
                result['message_body'] = ' '.join(remaining[1:])

        return result

    async def _enrich_with_details(
        self, page: Page, notifications: List[Dict], client_name: str
    ) -> List[Dict]:
        MAX_FETCH = 100
        enriched  = []

        for i, notif in enumerate(notifications):
            if i < MAX_FETCH:
                try:
                    row = None
                    for sel in [
                        f'.sapMListItems > .sapMLIB:nth-child({i+1})',
                        f'table tbody tr:nth-child({i+1})',
                        f'[role="row"]:nth-child({i+1})',
                    ]:
                        row = await page.query_selector(sel)
                        if row:
                            break

                    if row:
                        await row.click()
                        await asyncio.sleep(1)
                        await _wait_for_ui5(page, timeout=10000)

                        detail = await page.evaluate("""
                        () => {
                            const r = { severity:'', type:'', subject:'', message:'' };
                            const ta = document.querySelector('textarea.sapMTextAreaInner, textarea[aria-readonly="true"]');
                            if (ta) r.message = (ta.value || '').trim();
                            const inputs = Array.from(document.querySelectorAll('input.sapMInputBaseInner[disabled]'));
                            if (inputs[0]) r.severity = (inputs[0].value || '').trim();
                            if (inputs[1]) r.type     = (inputs[1].value || '').trim();
                            if (inputs[2]) r.subject  = (inputs[2].value || '').trim();
                            return r;
                        }
                        """)

                        if detail:
                            if detail.get('message'):
                                notif['message_body'] = detail['message']
                            if detail.get('severity') and not notif['severity']:
                                notif['severity'] = detail['severity']
                            if detail.get('type') and not notif['notif_type']:
                                notif['notif_type'] = detail['type']
                            if detail.get('subject') and not notif['subject']:
                                notif['subject'] = detail['subject']

                except Exception as e:
                    logger.debug(f"[{client_name}] detail fetch {i} failed: {e}")

            enriched.append(notif)

        return enriched

    async def _click_notifications_card(self, page: Page, client_name: str) -> bool:
        for label in config.CARD_LABELS['notifications']:
            for strategy in [
                f'text="{label}"',
                f'[role="option"]:has-text("{label}")',
                f'.sapMGT:has-text("{label}")',
                f'div:has-text("{label}"):not(:has(div:has-text("{label}")))',
            ]:
                try:
                    el = await page.wait_for_selector(strategy, timeout=4000, state='visible')
                    if el:
                        url_before = page.url
                        await el.click()
                        await asyncio.sleep(2)
                        await _wait_for_ui5(page, timeout=5000)
                        return True
                except Exception:
                    continue

        # fallback: set hash directly via JS
        try:
            await page.evaluate("hash => { window.location.hash = hash; }", "/messages/AL/%201")
            await asyncio.sleep(3)
            await _wait_for_ui5(page, timeout=15000)
            return True
        except Exception as e:
            logger.warning(f"[{client_name}] hash navigation failed: {e}")

        return False

    async def _go_to_next_page(self, page: Page) -> bool:
        for sel in [
            'button[aria-label*="التالي"]', 'button[aria-label*="Next"]',
            '.sapMPaginatorNext', 'button:has-text("التالي")', 'button:has-text("Next")',
        ]:
            try:
                btn = await page.query_selector(sel)
                if btn and not await btn.get_attribute('disabled'):
                    await btn.click()
                    return True
            except Exception:
                continue
        return False

    async def _is_session_expired(self, page: Page) -> bool:
        if 'auth.eta.gov.eg' in page.url or 'login' in page.url.lower():
            return True
        try:
            text = (await page.evaluate("() => document.body.innerText")).lower()
            return any(t in text for t in ['session expired', 'انتهت الجلسة', 'انتهاء الجلسة'])
        except Exception:
            return False

    async def _click_card(self, page: Page, labels: list, client_name: str) -> bool:
        """Generic tile/card click helper."""
        for label in labels:
            for strategy in [
                f'text="{label}"',
                f'[role="option"]:has-text("{label}")',
                f'.sapMGT:has-text("{label}")',
                f'div:has-text("{label}"):not(:has(div:has-text("{label}")))',
            ]:
                try:
                    el = await page.wait_for_selector(strategy, timeout=4000, state='visible')
                    if el:
                        await el.click()
                        await asyncio.sleep(2)
                        await _wait_for_ui5(page, timeout=10000)
                        return True
                except Exception:
                    continue
        return False

    async def get_client_documents(
        self,
        page: Page,
        client_id: str,
        client_name: str,
        docs_dir,
        known_files: set = None,
    ) -> list:
        """Navigate to مستنداتي tile and download all available documents."""
        import re as _re, shutil
        from pathlib import Path
        known = known_files or set()

        await page.goto(config.ETA_HOME_URL, timeout=config.PAGE_TIMEOUT)
        await _wait_for_ui5(page, timeout=20000)
        await asyncio.sleep(2)

        if not await self._click_card(page, config.CARD_LABELS['documents'], client_name):
            logger.warning(f"[{client_name}] مستنداتي tile not found")
            return []

        await _wait_for_ui5(page, timeout=20000)
        await asyncio.sleep(2)

        try:
            await page.wait_for_selector('.sapMLIB, [role="row"], .sapMListItems li', timeout=15000)
        except Exception:
            logger.warning(f"[{client_name}] مستنداتي: no document rows loaded")
            return []

        client_dir = docs_dir / str(client_id)
        client_dir.mkdir(parents=True, exist_ok=True)

        async def _save_dl(dl, fallback_name: str) -> dict | None:
            try:
                tmp  = await dl.path()
                if not tmp:
                    return None
                name = dl.suggested_filename or fallback_name or 'document'
                safe = _re.sub(r'[^\w؀-ۿ.\-]', '_', name)[:200] or 'document'
                if safe in known:
                    logger.debug(f"[{client_name}] skip existing: {safe}")
                    return None
                dest = client_dir / safe
                n = 1
                while dest.exists():
                    dest = client_dir / f"{Path(safe).stem}_{n}{Path(safe).suffix}"
                    n += 1
                shutil.copy2(tmp, dest)
                rel = f"{client_id}/{dest.name}"
                known.add(safe)       # block re-download by original name
                known.add(dest.name)  # also track saved name
                logger.info(f"[{client_name}] saved: {dest.name} ({dest.stat().st_size:,} bytes)")
                return {'name': dest.name, 'path': rel, 'size': dest.stat().st_size}
            except Exception as e:
                logger.debug(f"[{client_name}] save failed: {e}")
                return None

        saved = []

        _date_pat = re.compile(
            r'(?<!\d)(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}|\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2})(?!\d)'
        )
        _ar_tr = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')

        for page_num in range(20):
            rows = await page.query_selector_all('.sapMLIB')
            if not rows:
                break

            new_this_page = 0
            for idx in range(len(rows)):
                try:
                    current_rows = await page.query_selector_all('.sapMLIB')
                    if idx >= len(current_rows):
                        break
                    row = current_rows[idx]

                    # استخراج كل نصوص الصف بشكل منظم
                    row_texts = await page.evaluate("""
                    (row) => {
                        const walker = document.createTreeWalker(
                            row, NodeFilter.SHOW_TEXT,
                            { acceptNode: n => n.textContent.trim()
                                ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT }
                        );
                        const texts = [];
                        let node;
                        while ((node = walker.nextNode())) {
                            const t = node.textContent.trim();
                            if (t && !texts.includes(t)) texts.push(t);
                        }
                        return texts;
                    }
                    """, row)

                    row_text = ' | '.join(row_texts)
                    logger.info(f"[{client_name}] مستنداتي row[{idx}]: {row_text}")

                    # استخراج التواريخ (أرقام عربية + غربية + RTL marks)
                    dates = []
                    for t in row_texts:
                        clean = t.translate(_ar_tr).replace('‏', '').replace('‎', '').replace('​', '')
                        if any(c.isdigit() for c in clean):
                            logger.info(f"[{client_name}] date_check: {repr(clean)}")
                        for m in _date_pat.finditer(clean):
                            d = m.group()
                            if d not in dates:
                                dates.append(d)

                    doc_date    = dates[0] if len(dates) > 0 else None
                    expiry_date = dates[1] if len(dates) > 1 else None

                    if dates:
                        logger.info(f"[{client_name}] row[{idx}] dates: {dates}")

                    fallback_name = row_texts[0][:80] if row_texts else ''

                    nav = (await row.query_selector('.sapMLIBImgNav') or
                           await row.query_selector('.sapMLIBType') or
                           await row.query_selector('.sapUiIcon'))
                    if not nav:
                        continue

                    try:
                        async with page.expect_download(timeout=20000) as dl_info:
                            await nav.click()
                        dl = await dl_info.value
                        r  = await _save_dl(dl, fallback_name)
                        if r:
                            r['doc_date']    = doc_date
                            r['expiry_date'] = expiry_date
                            saved.append(r)
                            new_this_page += 1
                    except Exception as e:
                        logger.debug(f"[{client_name}] row {idx} no download: {e}")

                except Exception as e:
                    logger.debug(f"[{client_name}] row {idx} error: {e}")

            # no new files this page, or no next page → done
            if new_this_page == 0 or not await self._go_to_next_page(page):
                break
            await asyncio.sleep(1)

        logger.info(f"[{client_name}] مستنداتي: {len(saved)} documents saved")
        return saved

    async def get_notification_attachments(
        self,
        page: Page,
        client_name: str,
        subject: str,
        date_str: str,
    ) -> list:
        """
        Navigate to a specific notification (matched by subject+date) and
        download any file attachments found in the detail panel.
        Returns list of {name, content (bytes)}.
        """
        # make sure we're on notifications page
        if 'messages' not in page.url and '#/home' not in page.url:
            await page.goto(config.ETA_HOME_URL, timeout=config.PAGE_TIMEOUT)
            await _wait_for_ui5(page)

        if not await self._click_notifications_card(page, client_name):
            return []

        await _wait_for_ui5(page, timeout=20000)

        # find and click the matching row
        clicked = await page.evaluate("""
        ([subj, date]) => {
            const rows = document.querySelectorAll('.sapMLIB');
            for (const row of rows) {
                const text = row.innerText || '';
                if (text.includes(subj) || (date && text.includes(date))) {
                    row.click();
                    return true;
                }
            }
            return false;
        }
        """, [subject[:40], date_str])

        if not clicked:
            logger.debug(f"[{client_name}] notification row not found: {subject[:40]}")
            return []

        await asyncio.sleep(1)
        await _wait_for_ui5(page, timeout=10000)

        # look for download links / attachment buttons in detail panel
        files = []
        file_selectors = [
            'a[href*="download"]', 'a[href*="attachment"]', 'a[href*="sap-content"]',
            '.sapMLnk', '[class*="attachment"] a', '[class*="Attachment"] a',
            'button[title*="تحميل"]', 'button[title*="Download"]',
            'a[download]',
        ]

        for sel in file_selectors:
            links = await page.query_selector_all(sel)
            for link in links:
                try:
                    href     = await link.get_attribute('href') or ''
                    title    = await link.get_attribute('title') or ''
                    txt      = (await link.inner_text()).strip()
                    filename = title or txt or href.split('/')[-1] or 'attachment'

                    if not any(c.isalnum() for c in filename):
                        continue

                    # intercept the download
                    async with page.expect_download(timeout=30000) as dl_info:
                        await link.click()
                    download = await dl_info.value
                    path     = await download.path()

                    if path:
                        with open(path, 'rb') as fh:
                            content = fh.read()
                        if not filename or filename == 'attachment':
                            filename = download.suggested_filename or 'attachment'
                        files.append({'name': filename, 'content': content})
                        logger.info(f"[{client_name}] downloaded: {filename} ({len(content)} bytes)")

                except Exception as e:
                    logger.debug(f"[{client_name}] file link failed: {e}")
                    continue

        return files

    async def process_client(self, client: Dict[str, str], known_hashes: Set[str] = None) -> Dict:
        cid   = client.get('client_id', 'N/A')
        cname = client.get('client_name') or client.get('sap_username', 'N/A')
        uname = client.get('sap_username', '')
        pwd   = client.get('sap_password', '')

        result = {
            'client_id':     cid,
            'client_name':   cname,
            'success':       False,
            'error':         '',
            'counts':        {'notifications': 0, 'obligations': 0, 'forms': 0, 'documents': 0},
            'notifications': [],
        }

        ctx  = await self._new_context()
        page = await ctx.new_page()
        page.set_default_timeout(config.ELEMENT_TIMEOUT)

        try:
            logged_in = False
            for attempt in range(1, config.MAX_RETRIES + 1):
                if await self.login(page, uname, pwd, cname):
                    logged_in = True
                    break
                logger.warning(f"[{cname}] login attempt {attempt}/{config.MAX_RETRIES} failed")
                await asyncio.sleep(config.RETRY_DELAY)

            if not logged_in:
                result['error'] = 'Login failed after all retries'
                return result

            result['counts']        = await self.get_home_counts(page, cname)
            result['notifications'] = await self.get_notifications(page, cname, cid, known_hashes)
            result['success']       = True
            logger.info(f"[{cname}] done — {len(result['notifications'])} notifications")

        except Exception as e:
            result['error'] = str(e)
            logger.error(f"[{cname}] unexpected error: {e}", exc_info=True)
            await _screenshot(page, cname, 'unexpected')
        finally:
            try:
                await page.close()
            except Exception:
                pass
            try:
                await ctx.close()
            except Exception:
                pass

        return result
