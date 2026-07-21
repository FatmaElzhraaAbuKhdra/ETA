"""
ETA Sync — Main entry point (GUI)
"""
import sys
import os

if getattr(sys, 'frozen', False):
    BASE_DIR_PATH = os.path.dirname(sys.executable)
else:
    BASE_DIR_PATH = os.path.dirname(os.path.abspath(__file__))

os.chdir(BASE_DIR_PATH)
sys.path.insert(0, BASE_DIR_PATH)
if getattr(sys, '_MEIPASS', None):
    sys.path.insert(0, sys._MEIPASS)

import subprocess
import threading
import webbrowser
import queue
import time
import logging
import tkinter as tk
from tkinter import scrolledtext, messagebox
from pathlib import Path

API_URL = "http://localhost:8000"

C = {
    'bg':      '#f1f5f9',
    'sidebar': '#0f172a',
    'accent':  '#1d4ed8',
    'card':    '#ffffff',
    'text':    '#1e293b',
    'muted':   '#64748b',
    'border':  '#e2e8f0',
    'green':   '#16a34a',
    'red':     '#dc2626',
    'yellow':  '#d97706',
}


class _QueueLogHandler(logging.Handler):
    """Pipe all Python log records into the GUI queue."""
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            '%Y-%m-%d %H:%M:%S',
        ))

    def emit(self, record):
        try:
            self.q.put_nowait(self.format(record))
        except Exception:
            pass


class ETASyncLauncher:

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ETA Sync — هيئة الضرائب المصرية")
        self.root.geometry("860x620")
        self.root.minsize(700, 500)
        self.root.configure(bg=C['bg'])
        try:
            self.root.iconbitmap(default='')
        except Exception:
            pass

        self._api_server    = None   # uvicorn.Server
        self._sync_thread   = None   # threading.Thread
        self._log_q         = queue.Queue()
        self._sync_start    = None
        self._clients_done  = 0
        self._clients_total = 0

        # Route ALL Python logging into the GUI queue (no subprocesses needed)
        self._log_handler = _QueueLogHandler(self._log_q)
        logging.getLogger().addHandler(self._log_handler)

        self._build_ui()
        self._check_first_run()
        self.root.after(200, self._start_api)
        self.root.after(300, self._poll_logs)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    # ── UI ──────────────────────────────────────────────────────
    def _build_ui(self):
        # Sidebar (right)
        sb = tk.Frame(self.root, bg=C['sidebar'], width=230)
        sb.pack(side='right', fill='y')
        sb.pack_propagate(False)

        tk.Label(sb, text="ETA", bg='#1d4ed8', fg='white',
                 font=('Segoe UI', 18, 'bold'),
                 width=4, pady=6).pack(fill='x')
        tk.Label(sb, text="هيئة الضرائب المصرية", bg=C['sidebar'],
                 fg='white', font=('Segoe UI', 10, 'bold')).pack(pady=(10, 2))
        tk.Label(sb, text="منظومة المزامنة الآلية", bg=C['sidebar'],
                 fg='#64748b', font=('Segoe UI', 8)).pack()

        tk.Frame(sb, bg='#1e293b', height=1).pack(fill='x', padx=12, pady=12)

        self._var_api_dot  = tk.StringVar(value='○')
        self._var_api_txt  = tk.StringVar(value='API Server: جاري...')
        self._var_syn_dot  = tk.StringVar(value='○')
        self._var_syn_txt  = tk.StringVar(value='المزامنة: متوقفة')
        self._var_clients  = tk.StringVar(value='العملاء: —')
        self._var_last     = tk.StringVar(value='آخر تشغيل: —')
        self._var_docs     = tk.StringVar(value='المستندات: —')
        self._var_eta      = tk.StringVar(value='الوقت المتوقع: —')
        self._var_progress = tk.StringVar(value='')

        for dv, tv in [(self._var_api_dot, self._var_api_txt),
                       (self._var_syn_dot, self._var_syn_txt)]:
            r = tk.Frame(sb, bg=C['sidebar'])
            r.pack(fill='x', padx=14, pady=2)
            tk.Label(r, textvariable=dv, bg=C['sidebar'],
                     fg='#64748b', font=('Segoe UI', 16)).pack(side='right')
            tk.Label(r, textvariable=tv, bg=C['sidebar'],
                     fg='#94a3b8', font=('Segoe UI', 8)).pack(side='right', padx=6)

        tk.Frame(sb, bg='#1e293b', height=1).pack(fill='x', padx=12, pady=10)

        for v in (self._var_clients, self._var_docs, self._var_last):
            tk.Label(sb, textvariable=v, bg=C['sidebar'],
                     fg='#64748b', font=('Segoe UI', 8)).pack(
                anchor='e', padx=14, pady=1)

        tk.Frame(sb, bg='#1e293b', height=1).pack(fill='x', padx=12, pady=6)
        tk.Label(sb, textvariable=self._var_progress, bg=C['sidebar'],
                 fg='#fbbf24', font=('Segoe UI', 8, 'bold')).pack(anchor='e', padx=14, pady=1)
        tk.Label(sb, textvariable=self._var_eta, bg=C['sidebar'],
                 fg='#34d399', font=('Segoe UI', 8, 'bold')).pack(anchor='e', padx=14, pady=1)

        tk.Frame(sb, bg=C['sidebar']).pack(expand=True, fill='both')

        self._btn_sync = self._sb_btn(
            sb, "▶   تشغيل المزامنة", C['accent'],
            '#1e40af', 'white', self._run_sync)

        self._btn_stop_sync = self._sb_btn(
            sb, "⏹   إيقاف المزامنة", '#78350f',
            '#92400e', '#fde68a', self._stop_sync)
        self._btn_stop_sync.configure(state='disabled')

        self._btn_browser = self._sb_btn(
            sb, "🌐   فتح الواجهة", '#1e293b',
            '#334155', 'white', self._open_browser)

        self._btn_api = self._sb_btn(
            sb, "■   إيقاف الـ API", '#450a0a',
            '#7f1d1d', '#fca5a5', self._toggle_api)
        self._btn_api.configure(state='disabled')

        tk.Frame(sb, bg=C['sidebar'], height=16).pack()

        # Main area
        main = tk.Frame(self.root, bg=C['bg'])
        main.pack(side='left', fill='both', expand=True)

        top = tk.Frame(main, bg=C['card'], bd=0)
        top.pack(fill='x')
        tk.Label(top, text="سجل العمليات", bg=C['card'],
                 fg=C['text'], font=('Segoe UI', 11, 'bold')).pack(
            side='right', padx=20, pady=12)
        tk.Button(top, text="مسح", bg=C['card'], fg=C['muted'],
                  font=('Segoe UI', 8), relief='flat',
                  cursor='hand2', command=self._clear_log,
                  activebackground='#f1f5f9').pack(side='left', padx=12, pady=10)
        tk.Frame(top, bg=C['border'], height=1).pack(fill='x', side='bottom')

        self._log = scrolledtext.ScrolledText(
            main, bg='#0f172a', fg='#cbd5e1',
            font=('Consolas', 9), relief='flat', bd=0,
            wrap='word', state='disabled',
        )
        self._log.pack(fill='both', expand=True, padx=12, pady=12)
        self._log.tag_config('OK',   foreground='#86efac')
        self._log.tag_config('ERR',  foreground='#fca5a5')
        self._log.tag_config('WARN', foreground='#fde68a')
        self._log.tag_config('INFO', foreground='#93c5fd')
        self._log.tag_config('DIM',  foreground='#475569')

        self._statusbar = tk.Label(
            main, text="جاري التشغيل...", bg='#1e293b',
            fg='#64748b', font=('Segoe UI', 8), anchor='e', padx=12, pady=3)
        self._statusbar.pack(fill='x', side='bottom')

    def _sb_btn(self, parent, text, bg, abg, fg, cmd):
        b = tk.Button(parent, text=text, bg=bg, fg=fg,
                      font=('Segoe UI', 9, 'bold'), relief='flat',
                      cursor='hand2', activebackground=abg,
                      activeforeground=fg, padx=10, pady=9,
                      command=cmd)
        b.pack(fill='x', padx=14, pady=3)
        return b

    # ── Log ─────────────────────────────────────────────────────
    def _poll_logs(self):
        while not self._log_q.empty():
            line = self._log_q.get_nowait()
            self._write_log(line)
        self.root.after(120, self._poll_logs)

    def _write_log(self, text: str):
        if not text.strip():
            return
        tag = 'INFO'
        tl = text.lower()
        if any(x in tl for x in ['error', 'critical', 'failed', 'crash', 'فشل']):
            tag = 'ERR'
        elif any(x in tl for x in ['warning', 'warn']):
            tag = 'WARN'
        elif any(x in tl for x in ['ok ', 'done', 'saved', 'success', 'ready',
                                    'نجح', 'اكتمل', '✓']):
            tag = 'OK'
        elif text.startswith('  ') or text.startswith('DEBUG'):
            tag = 'DIM'
        self._log.configure(state='normal')
        self._log.insert('end', text.rstrip() + '\n', tag)
        self._log.see('end')
        self._log.configure(state='disabled')
        self._update_eta(text)

    def _update_eta(self, text: str):
        import re
        m = re.search(r'Sync:\s*(\d+)\s*clients', text)
        if m:
            self._clients_total = int(m.group(1))
            self._clients_done  = 0
            self._sync_start    = time.time()

        if re.search(r'\|\s*main\s*\|\s*(OK|ERR)\s+\[', text):
            self._clients_done += 1

        if not self._sync_start or self._clients_total == 0:
            return

        elapsed   = time.time() - self._sync_start
        done      = max(self._clients_done, 1)
        avg_sec   = elapsed / done
        remaining = max(self._clients_total - self._clients_done, 0)
        eta_sec   = avg_sec * remaining + 1800
        eta_min   = int(eta_sec / 60)
        eta_h     = eta_min // 60
        eta_m     = eta_min % 60

        if eta_h > 0:
            eta_str = f"الوقت المتوقع: ~{eta_h}س {eta_m}د"
        else:
            eta_str = f"الوقت المتوقع: ~{eta_m} دقيقة"

        progress_str = f"التقدم: {self._clients_done}/{self._clients_total} عميل"
        self.root.after(0, lambda: self._var_eta.set(eta_str))
        self.root.after(0, lambda: self._var_progress.set(progress_str))

    def _clear_log(self):
        self._log.configure(state='normal')
        self._log.delete('1.0', 'end')
        self._log.configure(state='disabled')

    def _status(self, msg):
        self.root.after(0, lambda: self._statusbar.configure(text=msg))

    # ── First-run check ─────────────────────────────────────────
    def _check_first_run(self):
        marker = Path(BASE_DIR_PATH) / '.playwright_installed'
        if marker.exists():
            return
        self._log_q.put("=" * 55)
        self._log_q.put("أول تشغيل — جاري تثبيت متصفح Playwright...")
        self._log_q.put("(ده بيحصل مرة واحدة بس، ممكن ياخد دقيقتين)")
        self._log_q.put("=" * 55)

        def install():
            try:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0
                proc = subprocess.run(
                    [sys.executable, '-m', 'playwright', 'install', 'chromium'],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    cwd=BASE_DIR_PATH,
                    startupinfo=si,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if proc.returncode == 0:
                    marker.touch()
                    self._log_q.put("✓ Playwright chromium installed OK")
                else:
                    self._log_q.put(f"[ERROR] playwright install: {proc.stderr[:200]}")
            except Exception as e:
                self._log_q.put(f"[ERROR] {e}")

        threading.Thread(target=install, daemon=True).start()

    # ── API Server (runs in-process, no subprocess) ─────────────
    def _start_api(self):
        if self._api_server and not self._api_server.should_exit:
            return

        self._log_q.put("=" * 55)
        self._log_q.put("ETA Sync — بدء تشغيل API Server...")
        self._log_q.put("=" * 55)

        self._btn_api.configure(state='disabled', text="⏳  جاري التشغيل...")
        self._var_api_dot.set('●')
        self._var_api_txt.set('API Server: جاري...')

        def run():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                import uvicorn
                uviconfig = uvicorn.Config(
                    "api:app", host="0.0.0.0", port=8000,
                    reload=False, log_level="info",
                    use_colors=False,
                )
                self._api_server = uvicorn.Server(uviconfig)
                loop.run_until_complete(self._api_server.serve())
            except Exception as e:
                import traceback
                self._log_q.put(f"[ERROR] API فشل: {e}")
                for ln in traceback.format_exc().splitlines():
                    self._log_q.put(f"  {ln}")
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
            self.root.after(0, self._api_stopped)

        threading.Thread(target=run, daemon=True, name='eta-api').start()
        self.root.after(3000, self._check_api_health)

    def _check_api_health(self):
        def check():
            for _ in range(10):
                try:
                    import urllib.request
                    urllib.request.urlopen(f"{API_URL}/health", timeout=2)
                    self.root.after(0, self._api_ready)
                    return
                except Exception:
                    time.sleep(1.5)
            self._log_q.put("[WARN] API لم يستجب في الوقت المحدد")
            self.root.after(0, self._api_stopped)
        threading.Thread(target=check, daemon=True).start()

    def _api_ready(self):
        self._var_api_dot.set('●')
        self._var_api_txt.set('API: شغال')
        self._log_q.put(f"✓ API ready → {API_URL}/ui")
        self._status(f"API شغال — {API_URL}/ui")
        self._btn_api.configure(
            state='normal', text="■   إيقاف الـ API",
            bg='#450a0a', activebackground='#7f1d1d', fg='#fca5a5',
        )
        self._refresh_stats()

    def _api_stopped(self):
        self._var_api_dot.set('○')
        self._var_api_txt.set('API: متوقف')
        self._btn_api.configure(
            state='normal', text="▶   تشغيل الـ API",
            bg=C['green'], activebackground='#15803d', fg='white',
        )
        self._status("API متوقف — اضغط 'تشغيل الـ API' لإعادة التشغيل")

    def _toggle_api(self):
        if self._api_server and not self._api_server.should_exit:
            self._api_server.should_exit = True
            self._log_q.put("[!] API Server: جاري الإيقاف...")
        else:
            self._start_api()

    # ── Sync (runs in-process, no subprocess) ───────────────────
    def _run_sync(self):
        if self._sync_thread and self._sync_thread.is_alive():
            messagebox.showinfo("تنبيه", "المزامنة شغالة بالفعل، استنى تخلص.")
            return

        self._btn_sync.configure(state='disabled', text="⏳  جاري المزامنة...")
        self._btn_stop_sync.configure(state='normal')
        self._var_syn_dot.set('●')
        self._var_syn_txt.set('المزامنة: شغالة...')
        self._status("المزامنة شغالة...")
        self._sync_start    = None
        self._clients_done  = 0
        self._clients_total = 0
        self._var_eta.set('الوقت المتوقع: جاري الحساب...')
        self._var_progress.set('')

        def run():
            import asyncio, sys
            # Clear cached modules so .py edits take effect without restarting launcher
            for _mod in ['main', 'eta_scraper', 'db_manager', 'eta_portal']:
                sys.modules.pop(_mod, None)
            from main import run_all
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(run_all())
            except Exception as e:
                logging.getLogger('main').critical(f"Sync crashed: {e}", exc_info=True)
                result = False
            finally:
                loop.close()
            self.root.after(0, self._sync_done, result)

        self._sync_thread = threading.Thread(target=run, daemon=True, name='eta-sync')
        self._sync_thread.start()

    def _stop_sync(self):
        if self._sync_thread and self._sync_thread.is_alive():
            self._log_q.put("[!] طلب إيقاف — ستتوقف المزامنة بعد انتهاء العميل الحالي")
        self._btn_stop_sync.configure(state='disabled')

    def _sync_done(self, success: bool):
        self._btn_sync.configure(state='normal', text="▶   تشغيل المزامنة")
        self._btn_stop_sync.configure(state='disabled')
        self._var_syn_dot.set('○')
        self._var_syn_txt.set('المزامنة: انتهت')
        self._var_eta.set('الوقت المتوقع: —')
        self._var_progress.set('')
        now = time.strftime('%H:%M:%S')
        msg = f"{'✓ اكتملت المزامنة' if success else '✗ انتهت بأخطاء'} — {now}"
        self._log_q.put("=" * 55)
        self._log_q.put(msg)
        self._log_q.put("=" * 55)
        self._status(msg)
        self._refresh_stats()

    # ── Stats ───────────────────────────────────────────────────
    def _refresh_stats(self):
        def fetch():
            try:
                import urllib.request, json
                with urllib.request.urlopen(f"{API_URL}/stats", timeout=3) as r:
                    d = json.loads(r.read())
                clients = d.get('clients_with_notifs', '—')
                last    = (d.get('last_sync_date') or '')[:16].replace('T', ' ')
                notifs  = d.get('total_notifications', '—')
                self.root.after(0, lambda: self._var_clients.set(f"العملاء: {clients}"))
                self.root.after(0, lambda: self._var_last.set(f"آخر تشغيل: {last or '—'}"))
                self.root.after(0, lambda: self._var_docs.set(f"الإشعارات: {notifs}"))
            except Exception:
                pass
        threading.Thread(target=fetch, daemon=True).start()

    # ── Browser ─────────────────────────────────────────────────
    def _open_browser(self):
        webbrowser.open(f"{API_URL}/ui")

    # ── Close ───────────────────────────────────────────────────
    def _on_close(self):
        if messagebox.askyesno("إغلاق", "إغلاق البرنامج؟\nالـ API server هيتوقف."):
            if self._api_server:
                self._api_server.should_exit = True
            self.root.destroy()
            sys.exit(0)


ETASyncLauncher()
