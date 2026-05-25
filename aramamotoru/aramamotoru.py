import os
import re
import math
import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.parse import urlparse
from collections import defaultdict
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import time

# ================= KONFİG ===================
BASLANGIC_URLS = [
    "https://tr.wikipedia.org/wiki/Anasayfa",
    "https://en.wikipedia.org/wiki/Main_Page",
    "https://www.bbc.com",
    "https://www.reddit.com",
    "https://github.com",
    "https://stackoverflow.com",
    "https://haberler.com",
    "https://hurriyet.com.tr",
]
MAX_THREADS = 20          # %70 CPU civarı (Ryzen 3100)
KAYIT_ARALIGI = 200
INDEX_DOSYASI = "benim_arama_index.json"
SONSUZ_TARAMA = True
REQUEST_TIMEOUT = 2

# ================= ARAMA MOTORU SINIFI =================
class BenimAramaMotorum:
    def __init__(self):
        self.index = defaultdict(lambda: defaultdict(float))
        self.sayfalar = {}
        self.ziyaret_edilen = set()
        self.kuyruk = []
        self.taranan_sayac = 0
        self.duruyor = False
        self.kilit = threading.RLock()
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'BenimMotor/1.0'})

    def temizle(self, metin):
        metin = metin.lower()
        kelimeler = re.findall(r'\b[a-zğüşıöç]{3,}\b', metin)
        stop = {"ve", "veya", "ile", "bir", "bu", "şu", "the", "and", "for", "with"}
        return [k for k in kelimeler if k not in stop][:40]

    def indeksle(self, url, baslik, icerik):
        kelimeler = self.temizle(baslik + " " + icerik)
        if not kelimeler:
            return
        with self.kilit:
            self.sayfalar[url] = {
                "baslik": baslik[:150],
                "icerik": icerik[:400],
                "kelime_sayisi": len(kelimeler),
                "tarih": datetime.now().strftime("%H:%M:%S")
            }
            sayac = defaultdict(int)
            for k in kelimeler:
                sayac[k] += 1
            for k, v in sayac.items():
                self.index[k][url] = v

    def sayfa_indir_ve_parse(self, url):
        try:
            r = self.session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                return None, None, []
            soup = BeautifulSoup(r.text, "html.parser")
            baslik = soup.find("title")
            baslik = baslik.text.strip() if baslik else url
            icerik = " ".join([p.get_text() for p in soup.find_all("p")[:15]])[:4000]
            linkler = set()
            for a in soup.find_all("a", href=True)[:30]:
                href = a["href"]
                if href.startswith("http") and not any(x in href for x in [".jpg", ".png", ".js", ".css"]):
                    linkler.add(href)
            return baslik, icerik, list(linkler)[:15]
        except:
            return None, None, []

    def calistir_sonsuz(self, baslangic_list, callback=None):
        self.kuyruk = baslangic_list[:]
        self.ziyaret_edilen = set()
        self.taranan_sayac = 0
        self.duruyor = False
        baslangic_zamani = time.time()

        def worker():
            while not self.duruyor:
                if not self.kuyruk:
                    time.sleep(0.1)
                    continue
                url = self.kuyruk.pop(0)
                if url in self.ziyaret_edilen:
                    continue
                self.ziyaret_edilen.add(url)
                with self.kilit:
                    self.taranan_sayac += 1
                    anlik = self.taranan_sayac

                baslik, icerik, yeni_linkler = self.sayfa_indir_ve_parse(url)
                if baslik:
                    self.indeksle(url, baslik, icerik)
                    for link in yeni_linkler:
                        if link not in self.ziyaret_edilen and link not in self.kuyruk:
                            self.kuyruk.append(link)

                if anlik % KAYIT_ARALIGI == 0:
                    self.tfidf_hesapla()
                    self.kaydet_json()
                    if callback:
                        hiz = anlik / (time.time() - baslangic_zamani)
                        callback(f"💾 {anlik} sayfa | hız: {hiz:.1f} sayfa/s | kuyruk: {len(self.kuyruk)}")
                if callback and anlik % 20 == 0:
                    callback(f"⚡ [{anlik}] {url[:70]}...")

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(MAX_THREADS)]
        for t in threads:
            t.start()
        while not self.duruyor:
            time.sleep(0.5)

    def tfidf_hesapla(self):
        toplam = len(self.sayfalar)
        if toplam == 0:
            return
        with self.kilit:
            for kelime, doklar in list(self.index.items())[-3000:]:
                idf = math.log((toplam + 1) / (len(doklar) + 1)) + 1
                for url, tf_raw in list(doklar.items()):
                    if url in self.sayfalar:
                        self.index[kelime][url] = (tf_raw / self.sayfalar[url]["kelime_sayisi"]) * idf

    def kaydet_json(self):
        with self.kilit:
            veri = {
                "index": {k: dict(v) for k, v in list(self.index.items())[-20000:]},
                "sayfalar": dict(list(self.sayfalar.items())[-30000:]),
                "tarih": str(datetime.now())
            }
            with open(INDEX_DOSYASI, "w", encoding="utf-8") as f:
                json.dump(veri, f, ensure_ascii=False, indent=2)

    def yukle_json(self):
        if os.path.exists(INDEX_DOSYASI):
            with open(INDEX_DOSYASI, "r", encoding="utf-8") as f:
                v = json.load(f)
                self.index = defaultdict(lambda: defaultdict(float), {k: defaultdict(float, v2) for k, v2 in v["index"].items()})
                self.sayfalar = v["sayfalar"]
            return True
        return False

    def ara(self, sorgu):
        kelimeler = self.temizle(sorgu)
        skorlar = defaultdict(float)
        with self.kilit:
            for k in kelimeler[:5]:
                if k in self.index:
                    for url, skor in self.index[k].items():
                        skorlar[url] += skor
        sonuc = []
        for url, skor in sorted(skorlar.items(), key=lambda x: x[1], reverse=True)[:50]:
            if url in self.sayfalar:
                sonuc.append((url, self.sayfalar[url]["baslik"], skor))
        return sonuc

# ================= TAM WEB GÖRÜNTÜLEYİCİ (Edge/Chromium) =================
try:
    from tkinterweb import HtmlFrame  # pip install tkinterweb
    TAM_TARAYICI = True
except ImportError:
    TAM_TARAYICI = False
    print("⚠️ 'pip install tkinterweb' yap -> görseller ve videolar için")

class Arayuz:
    def __init__(self):
        self.motor = BenimAramaMotorum()
        self.root = tk.Tk()
        self.root.title("🔥 KENDİ ARAMA MOTORUM + TAM TARAYICI")
        self.root.geometry("1400x850")
        self.root.configure(bg='#1e1e1e')

        self.tarama_aktif = False
        self.motor.yukle_json()

        self._build_gui()
        self.root.protocol("WM_DELETE_WINDOW", self.kapat)

    def _build_gui(self):
        # ----- ÜST ALAN (Arama+butonlar) -----
        top = tk.Frame(self.root, bg='#2d2d2d')
        top.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(top, text="🔍 KENDİ MOTORUM:", bg='#2d2d2d', fg='#0f0', font=('Arial', 11, 'bold')).pack(side=tk.LEFT, padx=5)
        self.entry = tk.Entry(top, font=('Arial', 12), width=60, bg='#3d3d3d', fg='white')
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.entry.bind('<Return>', lambda e: self.arama_yap())
        tk.Button(top, text="ARA", command=self.arama_yap, bg='#00aa00', fg='white', font=('Arial', 10, 'bold')).pack(side=tk.LEFT, padx=2)

        self.baslat_btn = tk.Button(top, text="🚀 SONSUZ TARAMA BAŞLAT", command=self.tarama_baslat, bg='#ff6600', fg='white')
        self.baslat_btn.pack(side=tk.LEFT, padx=10)
        self.durdur_btn = tk.Button(top, text="⏹️ DURDUR", command=self.tarama_durdur, bg='#cc0000', fg='white', state='disabled')
        self.durdur_btn.pack(side=tk.LEFT, padx=2)

        self.progress = ttk.Progressbar(top, mode='indeterminate', length=150)
        self.progress.pack(side=tk.LEFT, padx=10)

        # Bilgi etiketleri
        self.lbl_sayfa = tk.Label(top, text="📄 0 sayfa", bg='#2d2d2d', fg='#ffaa00')
        self.lbl_sayfa.pack(side=tk.RIGHT, padx=10)
        self.lbl_hiz = tk.Label(top, text="⚡ hız: 0", bg='#2d2d2d', fg='#0f0')
        self.lbl_hiz.pack(side=tk.RIGHT, padx=10)

        # ----- ORTA PANEL (SOL: Sonuç listesi, SAĞ: TAM TARAYICI) -----
        paned = tk.PanedWindow(self.root, bg='#1e1e1e', sashrelief=tk.RAISED, sashwidth=6)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Sol: sonuç listesi (linkler)
        sol_frame = tk.Frame(paned, bg='#2d2d2d')
        paned.add(sol_frame, width=500)
        tk.Label(sol_frame, text="📋 ARAMA SONUÇLARI (çift tıkla aç)", bg='#2d2d2d', fg='#ffaa00', font=('Arial', 10, 'bold')).pack(anchor='w', padx=5, pady=2)
        self.liste = tk.Listbox(sol_frame, bg='#1e1e1e', fg='#0f0', font=('Consolas', 10), selectbackground='#0f0')
        self.liste.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.liste.bind('<Double-Button-1>', self.liste_tiklandi)

        # Sağ: TAM WEB GÖRÜNTÜLEYİCİ (görsel/js/video full)
        sag_frame = tk.Frame(paned, bg='#2d2d2d')
        paned.add(sag_frame, width=800)
        tk.Label(sag_frame, text="🌐 TAM EKRAN TARAYICI (görsel/video/js çalışır)", bg='#2d2d2d', fg='#ffaa00').pack(anchor='w', padx=5)
        url_frame = tk.Frame(sag_frame, bg='#2d2d2d')
        url_frame.pack(fill=tk.X, padx=5, pady=2)
        self.url_bar = tk.Entry(url_frame, bg='#3d3d3d', fg='white', font=('Arial', 10))
        self.url_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.url_bar.bind('<Return>', lambda e: self.sayfa_yukle(self.url_bar.get()))
        tk.Button(url_frame, text="GİT", command=lambda: self.sayfa_yukle(self.url_bar.get()), bg='#00aa00', fg='white').pack(side=tk.RIGHT)

        if TAM_TARAYICI:
            self.web = HtmlFrame(sag_frame)  # tam özellikli
            self.web.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        else:
            self.web = tk.Text(sag_frame, bg='white', fg='black')
            self.web.pack(fill=tk.BOTH, expand=True)
            self.web.insert("1.0", "⚠️ 'pip install tkinterweb' kur. Şimdilik sadece yazı modu.")

        # Durum çubuğu
        self.status = tk.Label(self.root, text="✅ HAZIR | KENDİ ARAMA MOTORUN", bd=1, relief=tk.SUNKEN, anchor='w', bg='#333', fg='#0f0')
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

    def arama_yap(self):
        sorgu = self.entry.get().strip()
        if not sorgu:
            return
        if len(self.motor.sayfalar) == 0:
            messagebox.showwarning("UYARI", "Önce sonsuz tarama başlat!")
            return
        self.status.config(text=f"🔍 '{sorgu}' aranıyor...")
        self.root.update()
        sonuc_list = self.motor.ara(sorgu)
        self.liste.delete(0, tk.END)
        if not sonuc_list:
            self.liste.insert(tk.END, "❌ SONUÇ YOK")
            self.status.config(text="❌ Sonuç yok")
            return
        for idx, (url, baslik, skor) in enumerate(sonuc_list[:30], 1):
            self.liste.insert(tk.END, f"{idx}. {baslik[:80]} | skor: {skor:.2f}")
        self.status.config(text=f"✅ {len(sonuc_list)} sonuç bulundu")
        self.sonuc_url_cache = {i+1: url for i, (url, _, _) in enumerate(sonuc_list[:30])}

    def liste_tiklandi(self, event):
        secim = self.liste.curselection()
        if not secim:
            return
        idx = secim[0] + 1
        if hasattr(self, "sonuc_url_cache") and idx in self.sonuc_url_cache:
            url = self.sonuc_url_cache[idx]
            self.sayfa_yukle(url)

    def sayfa_yukle(self, url):
        if not url.startswith("http"):
            url = "https://" + url
        self.url_bar.delete(0, tk.END)
        self.url_bar.insert(0, url)
        self.status.config(text=f"🌐 Yükleniyor: {url}")
        if TAM_TARAYICI:
            self.web.load_url(url)
        else:
            threading.Thread(target=self._fallback_yazi_yukle, args=(url,), daemon=True).start()

    def _fallback_yazi_yukle(self, url):
        try:
            r = requests.get(url, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            metin = soup.get_text()[:5000]
            self.root.after(0, lambda: self.web.delete(1.0, tk.END))
            self.root.after(0, lambda: self.web.insert(tk.END, metin))
            self.root.after(0, lambda: self.status.config(text="✅ Metin modda gösterildi (tkinterweb kur tam sürüm için)"))
        except:
            self.root.after(0, lambda: self.status.config(text="❌ Hata"))

    def tarama_baslat(self):
        if self.tarama_aktif:
            return
        self.tarama_aktif = True
        self.motor.duruyor = False
        self.baslat_btn.config(state='disabled')
        self.durdur_btn.config(state='normal')
        self.progress.start(10)
        self.status.config(text="🚀 Sonsuz tarama başladı... CPU %70")

        baslangic = time.time()
        def callback(msg):
            self.root.after(0, lambda: self.status.config(text=msg[:100]))
            self.root.after(0, lambda: self.lbl_sayfa.config(text=f"📄 {self.motor.taranan_sayac} sayfa"))
            gecen = time.time() - baslangic
            if gecen > 0 and self.motor.taranan_sayac > 0:
                hiz = self.motor.taranan_sayac / gecen
                self.root.after(0, lambda: self.lbl_hiz.config(text=f"⚡ hız: {hiz:.1f}"))

        def thread_worker():
            self.motor.calistir_sonsuz(BASLANGIC_URLS, callback)
            self.root.after(0, self.tarama_bitti)

        threading.Thread(target=thread_worker, daemon=True).start()

    def tarama_durdur(self):
        if self.tarama_aktif:
            self.motor.duruyor = True
            self.status.config(text="⏹️ Durduruluyor...")

    def tarama_bitti(self):
        self.tarama_aktif = False
        self.progress.stop()
        self.baslat_btn.config(state='normal')
        self.durdur_btn.config(state='disabled')
        self.status.config(text="✅ Tarama durdu | Index kaydedildi")

    def kapat(self):
        if self.tarama_aktif:
            self.motor.duruyor = True
            time.sleep(0.5)
        self.root.destroy()

if __name__ == "__main__":
    app = Arayuz()
    app.root.mainloop()