

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
from kivy.properties import StringProperty, NumericProperty
from kivy.lang import Builder
from kivy.core.window import Window

# Custom modules
import database
import ocr_engine

# Window dimensions for desktop testing (auto-scales on mobile)
Window.size = (400, 750)


# SCREENS

class MainScreen(Screen):
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_image_path = None  # Selected image path
    
    def update_image_preview(self, path):

        self.ids.preview_image.source = path
        self.ids.preview_image.reload()
        self.ids.no_image_label.opacity = 0
    
    def set_result_number(self, text):

        self.ids.result_number.text = text

    def show_ocr_result(self, ocr_data):

        cleaned  = ocr_data.get("cleaned", "")
        conf     = ocr_data.get("confidence", 0)
        method   = ocr_data.get("method", "")
        all_res  = ocr_data.get("all_results", {})
        alts     = ocr_data.get("alternatives", [])

        # Main number display
        self.ids.result_number.text = cleaned if cleaned else "No number detected"

        # Engine label - which preprocessing worked best
        self.ids.engine_label.text = f"EasyOCR [{method}]" if method else "EasyOCR"

        # Confidence bar
        self.ids.confidence_bar.value = min(conf, 100)
        self.ids.confidence_label.text = f"{conf:.1f}%"

        # Suggestions chips update
        self.ids.suggestions_chips.clear_widgets()
        for alt in alts:
            if alt and alt != "---":
                btn = Button(
                    text=alt,
                    size_hint=(None, None),
                    size=(dp(90), dp(30)),
                    background_normal='',
                    background_color=(0.18, 0.22, 0.32, 1),
                    color=(0.3, 0.85, 1, 1),
                    font_size=sp(12),
                    bold=True
                )
                btn.bind(on_press=lambda b, t=alt: self.set_result_number(t))
                self.ids.suggestions_chips.add_widget(btn)

        # Show top 2 preprocessing results in comparison panel
        sorted_res = sorted(all_res.items(),
                            key=lambda x: x[1].get("confidence", 0),
                            reverse=True)
        widget_ids = ["tess_result", "easy_result"]
        for i, wid in enumerate(widget_ids):
            if i < len(sorted_res):
                mn, mr = sorted_res[i]
                txt = mr.get("text", "---") or "---"
                c   = mr.get("confidence", 0)
                self.ids[wid].text = f"{txt[:12]} ({c:.0f}%)"
            else:
                self.ids[wid].text = "---"


class HistoryScreen(Screen):
    """History screen - displays all saved scans"""
    
    def load_history(self, records):
        """Display history records"""
        self.ids.history_list.clear_widgets()
        self.ids.total_label.text = f"{len(records)} records"
        
        if not records:
            # No history records found
            self.ids.history_list.add_widget(
                Label(
                    text="No scan history found\nScan an image to save records",
                    font_size=sp(14),
                    color=(0.4, 0.45, 0.55, 1),
                    halign='center',
                    valign='middle',
                    size_hint_y=None,
                    height=dp(100)
                )
            )
            return
        
        for record in records:
            self._add_history_item(record)
    
    def _add_history_item(self, record):
        """Build individual history card widget"""
        # Main container
        item = BoxLayout(
            orientation='horizontal',
            size_hint_y=None,
            height=dp(72),
            spacing=dp(8),
            padding=[dp(12), dp(8)]
        )
        
        from kivy.graphics import Color, RoundedRectangle
        with item.canvas.before:
            Color(0.15, 0.16, 0.22, 1)
            item._rect = RoundedRectangle(pos=item.pos, size=item.size, radius=[dp(12)])
        
        def update_rect(instance, value):
            instance._rect.pos = instance.pos
            instance._rect.size = instance.size
        item.bind(pos=update_rect, size=update_rect)
        
        # Left side - number + time
        left = BoxLayout(orientation='vertical', spacing=dp(2))
        
        num_label = Label(
            text=record.get("cleaned_num", "---") or "---",
            font_size=sp(20),
            bold=True,
            color=(0.2, 0.9, 0.55, 1),
            halign='left',
            valign='bottom',
            size_hint_y=0.6,
        )
        num_label.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        
        ts = record.get("timestamp", "")
        time_label = Label(
            text=ts,
            font_size=sp(10),
            color=(0.5, 0.55, 0.65, 1),
            halign='left',
            valign='top',
            size_hint_y=0.4,
        )
        time_label.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        
        left.add_widget(num_label)
        left.add_widget(time_label)
        
        # Right side - engine + confidence
        right = BoxLayout(orientation='vertical', size_hint_x=0.35, spacing=dp(2))
        
        engine = record.get("engine", "")
        eng_label = Label(
            text=engine,
            font_size=sp(10),
            color=(0.4, 0.7, 1, 1),
            halign='right',
            valign='bottom',
            size_hint_y=0.5,
        )
        eng_label.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        
        conf = record.get("confidence", 0) or 0
        conf_label = Label(
            text=f"{conf:.0f}%",
            font_size=sp(13),
            bold=True,
            color=(0.2, 0.85, 0.5, 1) if conf >= 70 else (0.9, 0.6, 0.2, 1),
            halign='right',
            valign='top',
            size_hint_y=0.5,
        )
        conf_label.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        
        right.add_widget(eng_label)
        right.add_widget(conf_label)
        
        item.add_widget(left)
        item.add_widget(right)
        self.ids.history_list.add_widget(item)


# ==================================================
# MAIN APP
# ==================================================

class OCRScannerApp(App):
    """Main Application Class"""
    
    def build(self):
        self.title = "OCR Number Scanner"
        
        # Explicitly load KV layout file
        kv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'main.kv')
        Builder.load_file(kv_path)
        
        # Screen manager setup
        sm = ScreenManager(transition=FadeTransition(duration=0.3))
        sm.add_widget(MainScreen(name='main'))
        sm.add_widget(HistoryScreen(name='history'))
        
        return sm
    
    def on_start(self):
        """Application startup handler"""
        print("[APP] OCR Number Scanner started!")
    
    # --------------------------------------------------
    # FILE PICKER - Upload Image
    # --------------------------------------------------
    
    def open_file_picker(self):
        """Open file chooser dialog"""
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        
        # Dark background
        from kivy.graphics import Color, Rectangle
        with content.canvas.before:
            Color(0.10, 0.11, 0.17, 1)
            bg = Rectangle(pos=content.pos, size=content.size)
        content.bind(pos=lambda i, v: setattr(bg, 'pos', v),
                     size=lambda i, v: setattr(bg, 'size', v))
        
        # File chooser
        fc = FileChooserListView(
            path=os.path.expanduser("~"),
            filters=["*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp"],
            size_hint_y=0.85
        )
        
        btn_row = BoxLayout(size_hint_y=None, height=dp(45), spacing=dp(10))
        
        select_btn = Button(
            text="Select",
            background_normal='',
            background_color=(0.2, 0.6, 1, 1),
            bold=True,
            font_size=sp(14)
        )
        cancel_btn = Button(
            text="Cancel",
            background_normal='',
            background_color=(0.35, 0.15, 0.15, 1),
            bold=True,
            font_size=sp(14)
        )
        
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(select_btn)
        
        content.add_widget(fc)
        content.add_widget(btn_row)
        
        self._file_popup = Popup(
            title="Select Image",
            content=content,
            size_hint=(0.95, 0.85),
            background_color=(0.07, 0.08, 0.12, 1),
            title_color=(1, 1, 1, 1),
            separator_color=(0.2, 0.5, 0.9, 1)
        )
        
        def on_select(instance):
            if fc.selection:
                selected = fc.selection[0]
                self._on_image_selected(selected)
            self._file_popup.dismiss()
        
        cancel_btn.bind(on_press=lambda x: self._file_popup.dismiss())
        select_btn.bind(on_press=on_select)
        
        # Also select on double-click
        fc.bind(on_submit=lambda fc, sel, touch: on_select(None) if sel else None)
        
        self._file_popup.open()
    
    def _on_image_selected(self, path):
        """Handle selected image file"""
        if not os.path.exists(path):
            self._show_toast("File does not exist!")
            return
        
        # Copy to app temp directory
        app_dir = os.path.dirname(os.path.abspath(__file__))
        temp_dir = os.path.join(app_dir, "temp_images")
        os.makedirs(temp_dir, exist_ok=True)
        
        filename = f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}{os.path.splitext(path)[1]}"
        dest = os.path.join(temp_dir, filename)
        shutil.copy2(path, dest)
        
        main_screen = self.root.get_screen('main')
        main_screen.current_image_path = dest
        main_screen.update_image_preview(dest)
        
        print(f"[APP] Image selected: {dest}")
        self._show_toast("Image loaded! Ready to scan.")
    
    # --------------------------------------------------
    # CAMERA
    # --------------------------------------------------
    
    def open_camera(self):
        """Open camera view"""
        try:
            from kivy.uix.camera import Camera
            self._open_camera_popup()
        except Exception as e:
            # Fallback for desktop environments without webcam
            self._show_toast("Camera unavailable on desktop.\nPlease use Upload Image.")
    
    def _open_camera_popup(self):
        """Camera capture popup"""
        from kivy.uix.camera import Camera
        
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        
        camera = Camera(play=True, size_hint_y=0.85)
        
        capture_btn = Button(
            text="Take Photo",
            size_hint_y=None,
            height=dp(50),
            background_normal='',
            background_color=(0.15, 0.65, 0.45, 1),
            bold=True,
            font_size=sp(16)
        )
        
        content.add_widget(camera)
        content.add_widget(capture_btn)
        
        popup = Popup(
            title="Camera",
            content=content,
            size_hint=(0.95, 0.9)
        )
        
        def on_capture(instance):
            app_dir = os.path.dirname(os.path.abspath(__file__))
            temp_dir = os.path.join(app_dir, "temp_images")
            os.makedirs(temp_dir, exist_ok=True)
            
            filename = f"camera_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            path = os.path.join(temp_dir, filename)
            camera.export_to_png(path)
            popup.dismiss()
            self._on_image_selected(path)
        
        capture_btn.bind(on_press=on_capture)
        popup.bind(on_dismiss=lambda x: setattr(camera, 'play', False))
        popup.open()
    
    # --------------------------------------------------
    # OCR RUN
    # --------------------------------------------------
    
    def run_ocr(self):
        """Run OCR on background worker thread"""
        main_screen = self.root.get_screen('main')
        
        if not main_screen.current_image_path:
            self._show_toast("Please select an image first!")
            return
        
        if not os.path.exists(main_screen.current_image_path):
            self._show_toast("Image file not found!")
            return
        
        # Disable scan button during processing
        main_screen.ids.scan_btn.text = "Scanning..."
        main_screen.ids.scan_btn.disabled = True
        main_screen.ids.result_number.text = "..."
        
        # Run OCR in background thread to prevent UI freezing
        image_path = main_screen.current_image_path
        
        def ocr_thread():
            try:
                result = ocr_engine.extract_numbers(image_path)
                # Update UI on Kivy main loop
                Clock.schedule_once(lambda dt: self._on_ocr_done(result, image_path), 0)
            except Exception as e:
                print(f"[APP] OCR Error: {e}")
                Clock.schedule_once(
                    lambda dt: self._on_ocr_error(str(e)), 0
                )
        
        thread = threading.Thread(target=ocr_thread, daemon=True)
        thread.start()
    
    def _on_ocr_done(self, result, image_path):
        """Callback when OCR processing completes"""
        main_screen = self.root.get_screen('main')

        # UI reset
        main_screen.ids.scan_btn.text = "SCAN NUMBER"
        main_screen.ids.scan_btn.disabled = False

        # Result display
        main_screen.show_ocr_result(result)

        # Save result to database
        cleaned = result.get("cleaned", "")
        best_text = result.get("best_text", "")
        conf = result.get("confidence", 0)
        method = result.get("method", "EasyOCR")

        if cleaned:
            database.save_result(
                image_path=image_path,
                raw_text=best_text,
                cleaned_num=cleaned,
                engine_used=f"EasyOCR [{method}]",
                confidence=conf
            )
            self._show_toast(f"Saved: {cleaned}")
        else:
            self._show_toast("No serial number detected. Please try again.")
    
    def _on_ocr_error(self, error_msg):
        """Callback on OCR error"""
        main_screen = self.root.get_screen('main')
        main_screen.ids.scan_btn.text = "SCAN NUMBER"
        main_screen.ids.scan_btn.disabled = False
        main_screen.ids.result_number.text = "Error!"
        self._show_toast(f"Error: {error_msg[:50]}")
    
    def copy_number(self):
        """Copy current result number to clipboard"""
        main_screen = self.root.get_screen('main')
        text = main_screen.ids.result_number.text.strip()
        if text and text != "---" and text != "No number detected":
            from kivy.core.clipboard import Clipboard
            Clipboard.copy(text)
            self._show_toast(f"Copied: {text}")

    def save_current_number(self):
        """Save edited or verified number to database"""
        main_screen = self.root.get_screen('main')
        text = main_screen.ids.result_number.text.strip()
        if text and text != "---" and text != "No number detected":
            image_path = main_screen.current_image_path or ""
            database.save_result(
                image_path=image_path,
                raw_text=text,
                cleaned_num=text,
                engine_used="Verified/User",
                confidence=100.0
            )
            self._show_toast(f"Saved: {text}")

    # --------------------------------------------------
    # NAVIGATION
    # --------------------------------------------------
    
    def go_to_history(self):
        """Navigate to history screen"""
        self.root.current = 'history'
        
        # Load history records
        records = database.get_all_history()
        history_screen = self.root.get_screen('history')
        history_screen.load_history(records)
    
    def go_to_main(self):
        """Navigate back to main screen"""
        self.root.current = 'main'
    
    def search_history(self, query):
        """Search history records"""
        if query.strip():
            records = database.search_history(query.strip())
        else:
            records = database.get_all_history()
        
        history_screen = self.root.get_screen('history')
        history_screen.load_history(records)
    
    # --------------------------------------------------
    # TOAST NOTIFICATION
    # --------------------------------------------------
    
    def _show_toast(self, message, duration=2.5):
        """Display toast notification"""
        content = Label(
            text=message,
            font_size=sp(13),
            color=(1, 1, 1, 1),
            halign='center',
            valign='middle',
            text_size=(dp(260), None)
        )
        
        popup = Popup(
            title='',
            content=content,
            size_hint=(0.75, None),
            height=dp(100),
            background_color=(0.12, 0.13, 0.18, 0.95),
            separator_height=0,
            auto_dismiss=True
        )
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss(), duration)


# ==================================================
# APP RUN
# ==================================================

if __name__ == "__main__":
    print("="*50)
    print("  OCR Number Scanner - Metal Engraved Numbers")
    print("  PC Testing Mode")
    print("="*50)
    
    # Check dependencies
    print("\n[CHECK] Verifying dependencies...")
    
    try:
        import cv2
        print(f"  OpenCV: OK (v{cv2.__version__})")
    except ImportError:
        print("  OpenCV: MISSING - pip install opencv-python")
    
    try:
        import pytesseract
        print("  Pytesseract: OK")
    except ImportError:
        print("  Pytesseract: MISSING - pip install pytesseract")
    
    try:
        import easyocr
        print("  EasyOCR: OK")
    except ImportError:
        print("  EasyOCR: MISSING - pip install easyocr")
    
    print("\n[APP] Starting...")
    OCRScannerApp().run()
