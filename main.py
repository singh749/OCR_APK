import os
import sys
import shutil
import threading
from datetime import datetime

# Kivy imports
from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, Screen, FadeTransition
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.image import Image as KivyImage
from kivy.uix.popup import Popup
from kivy.uix.filechooser import FileChooserListView
from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.lang import Builder
from kivy.core.window import Window

# Custom modules
import database
import ocr_engine

Window.size = (400, 750)

class MainScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_image_path = None

    def update_image_preview(self, path):
        self.ids.preview_image.source = path
        self.ids.preview_image.reload()
        self.ids.no_image_label.opacity = 0

    def set_result_number(self, text):
        self.ids.result_number.text = text

    def show_ocr_result(self, ocr_data):
        cleaned = ocr_data.get("cleaned", "")
        conf = ocr_data.get("confidence", 0)
        method = ocr_data.get("method", "")
        all_res = ocr_data.get("all_results", {})
        alts = ocr_data.get("alternatives", [])

        if method == "unavailable":
            self.ids.result_number.text = "No OCR engine on this device"
            self.ids.engine_label.text = "OCR engine not installed"
        else:
            self.ids.result_number.text = cleaned if cleaned else "No number detected"
            self.ids.engine_label.text = f"{method} OCR" if method else "OCR"
        self.ids.confidence_bar.value = min(conf, 100)
        self.ids.confidence_label.text = f"{conf:.1f}%"

        self.ids.suggestions_chips.clear_widgets()
        for alt in alts:
            if alt and alt != "---":
                btn = Button(text=alt, size_hint=(None, None), size=(dp(90), dp(30)),
                             background_normal='', background_color=(0.18, 0.22, 0.32, 1),
                             color=(0.3, 0.85, 1, 1), font_size=sp(12), bold=True)
                btn.bind(on_press=lambda b, t=alt: self.set_result_number(t))
                self.ids.suggestions_chips.add_widget(btn)

        sorted_res = sorted(all_res.items(), key=lambda x: x[1].get("confidence", 0), reverse=True)
        widget_ids = ["tess_result", "easy_result"]
        for i, wid in enumerate(widget_ids):
            if i < len(sorted_res):
                mn, mr = sorted_res[i]
                txt = mr.get("text", "---") or "---"
                c = mr.get("confidence", 0)
                self.ids[wid].text = f"{txt[:12]} ({c:.0f}%)"
            else:
                self.ids[wid].text = "---"

class HistoryScreen(Screen):
    def load_history(self, records):
        self.ids.history_list.clear_widgets()
        self.ids.total_label.text = f"{len(records)} records"
        if not records:
            self.ids.history_list.add_widget(Label(text="No scan history found", font_size=sp(14),
                                                   color=(0.4, 0.45, 0.55, 1), halign='center', valign='middle',
                                                   size_hint_y=None, height=dp(100)))
            return
        for record in records:
            self._add_history_item(record)

    def _add_history_item(self, record):
        item = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(72), spacing=dp(8), padding=[dp(12), dp(8)])
        from kivy.graphics import Color, RoundedRectangle
        with item.canvas.before:
            Color(0.15, 0.16, 0.22, 1)
            item._rect = RoundedRectangle(pos=item.pos, size=item.size, radius=[dp(12)])
        item.bind(pos=lambda inst, val: setattr(inst._rect, 'pos', val), size=lambda inst, val: setattr(inst._rect, 'size', val))

        left = BoxLayout(orientation='vertical', spacing=dp(2))
        num_label = Label(text=record.get("cleaned_num", "---") or "---", font_size=sp(20), bold=True,
                          color=(0.2, 0.9, 0.55, 1), halign='left', valign='bottom', size_hint_y=0.6)
        num_label.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        ts = record.get("timestamp", "")
        time_label = Label(text=ts, font_size=sp(10), color=(0.5, 0.55, 0.65, 1), halign='left', valign='top', size_hint_y=0.4)
        time_label.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        left.add_widget(num_label)
        left.add_widget(time_label)

        right = BoxLayout(orientation='vertical', size_hint_x=0.35, spacing=dp(2))
        eng_label = Label(text=record.get("engine", ""), font_size=sp(10), color=(0.4, 0.7, 1, 1), halign='right', valign='bottom', size_hint_y=0.5)
        eng_label.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        conf = record.get("confidence", 0) or 0
        conf_label = Label(text=f"{conf:.0f}%", font_size=sp(13), bold=True,
                           color=(0.2, 0.85, 0.5, 1) if conf >= 70 else (0.9, 0.6, 0.2, 1), halign='right', valign='top', size_hint_y=0.5)
        conf_label.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        right.add_widget(eng_label)
        right.add_widget(conf_label)

        item.add_widget(left)
        item.add_widget(right)
        self.ids.history_list.add_widget(item)

class OCRScannerApp(App):
    def build(self):
        self.title = "OCR Number Scanner"
        kv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'main.kv')
        Builder.load_file(kv_path)
        sm = ScreenManager(transition=FadeTransition(duration=0.3))
        sm.add_widget(MainScreen(name='main'))
        sm.add_widget(HistoryScreen(name='history'))
        return sm

    def open_file_picker(self):
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        from kivy.graphics import Color, Rectangle
        with content.canvas.before:
            Color(0.10, 0.11, 0.17, 1)
            bg = Rectangle(pos=content.pos, size=content.size)
        content.bind(pos=lambda i, v: setattr(bg, 'pos', v), size=lambda i, v: setattr(bg, 'size', v))

        fc = FileChooserListView(path=os.path.expanduser("~"), filters=["*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp"], size_hint_y=0.85)
        btn_row = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        select_btn = Button(text="Select", background_normal='', background_color=(0.2, 0.6, 1, 1), bold=True, font_size=sp(14))
        cancel_btn = Button(text="Cancel", background_normal='', background_color=(0.35, 0.15, 0.15, 1), bold=True, font_size=sp(14))
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(select_btn)
        content.add_widget(fc)
        content.add_widget(btn_row)

        self._file_popup = Popup(title="Select Image", content=content, size_hint=(0.95, 0.85))
        def on_select(instance):
            if fc.selection:
                self._on_image_selected(fc.selection[0])
            self._file_popup.dismiss()
        cancel_btn.bind(on_press=lambda x: self._file_popup.dismiss())
        select_btn.bind(on_press=on_select)
        self._file_popup.open()

    def _on_image_selected(self, path):
        if not os.path.exists(path): return
        app_dir = os.path.dirname(os.path.abspath(__file__))
        temp_dir = os.path.join(app_dir, "temp_images")
        os.makedirs(temp_dir, exist_ok=True)
        filename = f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}{os.path.splitext(path)[1]}"
        dest = os.path.join(temp_dir, filename)
        shutil.copy2(path, dest)
        main_screen = self.root.get_screen('main')
        main_screen.current_image_path = dest
        main_screen.update_image_preview(dest)

    def run_ocr(self):
        main_screen = self.root.get_screen('main')
        if not main_screen.current_image_path: return
        main_screen.ids.scan_btn.text = "Scanning..."
        main_screen.ids.scan_btn.disabled = True
        main_screen.ids.result_number.text = "..."

        def ocr_thread():
            try:
                result = ocr_engine.extract_numbers(main_screen.current_image_path)
                Clock.schedule_once(lambda dt: self._on_ocr_done(result), 0)
            except Exception as e:
                Clock.schedule_once(lambda dt: self._on_ocr_error(str(e)), 0)
        threading.Thread(target=ocr_thread, daemon=True).start()

    def _on_ocr_done(self, result):
        main_screen = self.root.get_screen('main')
        main_screen.ids.scan_btn.text = "SCAN NUMBER"
        main_screen.ids.scan_btn.disabled = False
        main_screen.show_ocr_result(result)
        cleaned = result.get("cleaned", "")
        if cleaned:
            database.save_result(image_path=main_screen.current_image_path, raw_text=result.get("best_text", ""),
                                 cleaned_num=cleaned, engine_used=result.get("method", "OCR"), confidence=result.get("confidence", 0))

    def _on_ocr_error(self, error_msg):
        main_screen = self.root.get_screen('main')
        main_screen.ids.scan_btn.text = "SCAN NUMBER"
        main_screen.ids.scan_btn.disabled = False
        main_screen.ids.result_number.text = "Error!"

    def go_to_history(self):
        self.root.current = 'history'
        self.root.get_screen('history').load_history(database.get_all_history())

    def go_to_main(self):
        self.root.current = 'main'

if __name__ == "__main__":
    print("="*50)
    print("  OCR Number Scanner - Starting...")
    print("="*50)
    OCRScannerApp().run()
