"""
OCR Number Scanner - Kivy app for desktop and Android.

Rules this file follows, because on Android an unhandled exception simply
closes the app ("it crashes") with nothing to read:

* every optional native module is imported defensively and a missing one is
  reported in the UI instead of killing startup;
* `Window.size` is only touched on desktop - on Android the device owns the
  window size, and forcing it makes Kivy lay the UI out into a small box
  (that is the "everything looks tiny / cut off on the phone" symptom);
* Camera and gallery picking go through the Android intents, which need no
  storage permission on API 29+;
* every UI callback is wrapped, and the traceback is shown on screen, so a
  problem can be reported instead of guessed at.
"""

import os
import shutil
import threading
import traceback
from datetime import datetime
from functools import wraps

from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.logger import Logger
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import FadeTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import platform

IS_ANDROID = platform == "android"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
KV_PATH = os.path.join(APP_DIR, "main.kv")
TEMP_DIR = os.path.join(APP_DIR, "temp_images")

# --- optional native / heavy modules ---------------------------------------
# A missing one must degrade to a message, never to a crash.
PROBLEMS = []

try:
    import cv2
except Exception as exc:  # pragma: no cover - depends on the device
    cv2 = None
    PROBLEMS.append(("OpenCV (cv2)", exc))

try:
    import database
except Exception as exc:
    database = None
    PROBLEMS.append(("scan history database", exc))

try:
    import ocr_engine
except Exception as exc:
    ocr_engine = None
    PROBLEMS.append(("ocr_engine", exc))

def apply_desktop_window_size():
    """Give the desktop dev window a phone-ish shape.

    Never on Android: the device owns the real window size there, and forcing a
    400x750 window is what makes the whole interface look small and clipped on
    a phone. It is also wrapped because `kivy.core.window.Window` can be `None`
    when no window provider exists (headless runs, broken GL) - touching it at
    import time would then kill the app before it can even report anything.
    """
    if IS_ANDROID:
        return
    try:
        from kivy.core.window import Window
        if Window is not None:
            Window.size = (430, 840)
    except Exception as exc:  # pragma: no cover - cosmetic only
        Logger.debug("OCRScanner: desktop window size skipped ({})".format(exc))

# --- Android request codes --------------------------------------------------
CAMERA_REQUEST = 4711
PICK_REQUEST = 4712
RESULT_OK = -1  # android.app.Activity.RESULT_OK


def safe(handler):
    """Turn a crash into a readable popup; the app keeps running."""
    @wraps(handler)
    def wrapper(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except Exception as exc:
            Logger.error("OCRScanner: %s failed\n%s" % (handler.__name__, traceback.format_exc()))
            app = App.get_running_app()
            if app is not None:
                app.show_error(handler.__name__, exc)
    return wrapper


# ---------------------------------------------------------------------------
# Android plumbing (pyjnius is only imported on the device)
# ---------------------------------------------------------------------------
def android_activity():
    from jnius import autoclass
    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    activity = PythonActivity.mActivity
    if activity is None:
        raise RuntimeError("Android activity is not available yet")
    return activity


def make_activity_result_listener(on_result):
    """Register a Python callback for startActivityForResult results.

    The returned object must be kept referenced, or Java's weak-ish handling of
    the proxy lets the callback disappear.
    """
    from jnius import PythonJavaClass, java_method

    class _Listener(PythonJavaClass):
        __javainterfaces__ = ["org/kivy/android/PythonActivity$ActivityResultListener"]

        def __init__(self, callback):
            super().__init__()
            self._callback = callback

        @java_method("(IILandroid/content/Intent;)V")
        def onActivityResult(self, request_code, result_code, data):
            try:
                self._callback(request_code, result_code, data)
            except Exception as exc:
                # never let an exception escape into Java: it kills the process
                Logger.error("OCRScanner: activity result\n" + traceback.format_exc())
                app = App.get_running_app()
                if app is not None:
                    Clock.schedule_once(lambda _dt: app.show_error("Android returned an error", exc), 0)

    listener = _Listener(on_result)
    android_activity().registerActivityResultListener(listener)
    return listener


def media_store_uri_for_new_photo(filename):
    """A content:// URI the camera app may write a new JPEG into.

    A plain file:// path cannot be handed to another app any more (Android 7+
    throws FileUriExposedException), and buildozer cannot inject the
    FileProvider that would be the alternative, so the photo is created through
    MediaStore and its Uri is shared with a write grant.

    Nested classes are spelled with '$' (android.provider.MediaStore$Images$Media)
    because that is what pyjnius resolves - the same form python-for-android's own
    `android` helpers use, e.g. autoclass("android.os.Build$VERSION").
    """
    from jnius import autoclass
    Build_VERSION = autoclass("android.os.Build$VERSION")
    ContentValues = autoclass("android.content.ContentValues")
    MediaStore = autoclass("android.provider.MediaStore")
    Images = autoclass("android.provider.MediaStore$Images$Media")

    values = ContentValues()
    values.put(Images.DISPLAY_NAME, filename)
    values.put(Images.MIME_TYPE, "image/jpeg")
    if int(Build_VERSION.SDK_INT) >= 29:
        values.put(Images.RELATIVE_PATH, "Pictures/OCRScanner")

    uri = android_activity().getContentResolver().insert(
        Images.EXTERNAL_CONTENT_URI, values)
    if uri is None:
        raise RuntimeError("Android refused to create a photo entry in the gallery")
    return uri


def copy_content_uri_to_file(uri, dest_path):
    """content:// -> a real file path, which is what OpenCV needs."""
    from jnius import autoclass
    Channels = autoclass("java.nio.channels.Channels")
    FileOutputStream = autoclass("java.io.FileOutputStream")

    resolver = android_activity().getContentResolver()
    input_stream = resolver.openInputStream(uri)
    if input_stream is None:
        raise RuntimeError("Could not open the selected image")
    channel = Channels.newChannel(input_stream)
    output = FileOutputStream(dest_path)
    try:
        output.getChannel().transferFrom(channel, 0, 1 << 30)
    finally:
        output.close()
        channel.close()
        input_stream.close()
    return dest_path


def unique_temp_path(extension="jpg"):
    os.makedirs(TEMP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return os.path.join(TEMP_DIR, "scan_{}.{}".format(stamp, extension))


def prune_temp_images(keep=30):
    """temp_images/ is inside the app dir; don't let it grow forever."""
    try:
        files = sorted(
            (os.path.join(TEMP_DIR, name) for name in os.listdir(TEMP_DIR)),
            key=os.path.getmtime,
            reverse=True,
        )
        for stale in files[keep:]:
            try:
                os.remove(stale)
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------
class MainScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_image_path = None

    def update_image_preview(self, path):
        # Only the source is set here: main.kv shows the preview (and hides the
        # placeholder) as soon as the texture has actually loaded, so a broken or
        # unreadable file can never leave a blank panel with no explanation.
        self.ids.preview_image.source = path
        self.ids.preview_image.reload()

    def set_result_number(self, text):
        self.ids.result_number.text = text

    def show_ocr_result(self, ocr_data):
        cleaned = ocr_data.get("cleaned", "")
        conf = ocr_data.get("confidence", 0) or 0
        method = ocr_data.get("method", "")
        all_res = ocr_data.get("all_results", {}) or {}
        alts = ocr_data.get("alternatives", []) or []

        if method == "unavailable":
            self.ids.result_number.text = "No OCR engine on this device"
            self.ids.engine_label.text = "Engine: not installed"
        else:
            self.ids.result_number.text = cleaned if cleaned else "No number detected"
            variant = ocr_data.get("variant", "")
            label = "Engine: {} OCR".format(method) if method else "Engine: -"
            if variant:
                label += "  |  image: {}".format(variant)
            self.ids.engine_label.text = label

        self.ids.confidence_bar.value = min(conf, 100)
        self.ids.confidence_label.text = "{:.1f}%".format(conf)

        self.ids.suggestions_chips.clear_widgets()
        for alt in alts:
            if alt and alt != "---":
                btn = Button(text=alt, size_hint=(None, None), size=(dp(96), dp(34)),
                              background_normal='', background_color=(0.18, 0.22, 0.32, 1),
                              color=(0.3, 0.85, 1, 1), font_size=sp(13), bold=True)
                btn.bind(on_press=lambda b, t=alt: self.set_result_number(t))
                self.ids.suggestions_chips.add_widget(btn)

        sorted_res = sorted(all_res.items(), key=lambda kv: kv[1].get("confidence", 0), reverse=True)
        for widget_id, entry in zip(("tess_result", "easy_result"), sorted_res + [None, None]):
            if entry:
                method_name, record = entry
                text = record.get("text", "---") or "---"
                self.ids[widget_id].text = "{}: {} ({:.0f}%)".format(
                    method_name, text[:12], record.get("confidence", 0))
            else:
                self.ids[widget_id].text = "---"


class HistoryScreen(Screen):
    def load_history(self, records):
        self.ids.history_list.clear_widgets()
        self.ids.total_label.text = "{} records".format(len(records))
        if not records:
            self.ids.history_list.add_widget(Label(text="No scan history found", font_size=sp(15),
                                                   color=(0.4, 0.45, 0.55, 1), halign='center',
                                                   valign='middle', size_hint_y=None, height=dp(100)))
            return
        for record in records:
            self._add_history_item(record)

    def _add_history_item(self, record):
        item = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(76),
                         spacing=dp(8), padding=[dp(12), dp(8)])
        from kivy.graphics import Color, RoundedRectangle
        with item.canvas.before:
            Color(0.15, 0.16, 0.22, 1)
            item._rect = RoundedRectangle(pos=item.pos, size=item.size, radius=[dp(12)])
        item.bind(pos=lambda inst, val: setattr(inst._rect, 'pos', val),
                  size=lambda inst, val: setattr(inst._rect, 'size', val))

        left = BoxLayout(orientation='vertical', spacing=dp(2))
        num_label = Label(text=record.get("cleaned_num", "---") or "---", font_size=sp(21), bold=True,
                          color=(0.2, 0.9, 0.55, 1), halign='left', valign='bottom', size_hint_y=0.6)
        num_label.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        time_label = Label(text=record.get("timestamp", ""), font_size=sp(11),
                           color=(0.5, 0.55, 0.65, 1), halign='left', valign='top', size_hint_y=0.4)
        time_label.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        left.add_widget(num_label)
        left.add_widget(time_label)

        right = BoxLayout(orientation='vertical', size_hint_x=0.35, spacing=dp(2))
        engine_label = Label(text=record.get("engine", "") or "", font_size=sp(11),
                             color=(0.4, 0.7, 1, 1), halign='right', valign='bottom', size_hint_y=0.5)
        engine_label.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        conf = record.get("confidence", 0) or 0
        conf_label = Label(text="{:.0f}%".format(conf), font_size=sp(14), bold=True,
                           color=(0.2, 0.85, 0.5, 1) if conf >= 70 else (0.9, 0.6, 0.2, 1),
                           halign='right', valign='top', size_hint_y=0.5)
        conf_label.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
        right.add_widget(engine_label)
        right.add_widget(conf_label)

        item.add_widget(left)
        item.add_widget(right)
        self.ids.history_list.add_widget(item)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
class OCRScannerApp(App):
    def build(self):
        self.title = "OCR Number Scanner"
        apply_desktop_window_size()
        self._activity_listener = None
        self._pending_uri = None
        self._flash_popup = None

        try:
            Builder.load_file(KV_PATH)
            self.root = ScreenManager(transition=FadeTransition(duration=0.25))
            self.root.add_widget(MainScreen(name='main'))
            self.root.add_widget(HistoryScreen(name='history'))
            if IS_ANDROID:
                self._activity_listener = make_activity_result_listener(self._on_activity_result)
            if PROBLEMS:
                Clock.schedule_once(self._show_startup_problems, 0.6)
            return self.root
        except Exception:
            Logger.error("OCRScanner: startup failed\n" + traceback.format_exc())
            return self._error_view(traceback.format_exc())

    # -- messaging ----------------------------------------------------------
    def _show_startup_problems(self, _dt):
        lines = ["{}: {}".format(name, err) for name, err in PROBLEMS]
        self.show_error("Missing modules", "\n".join(lines))

    def show_error(self, title, detail):
        """Show a readable, copyable error instead of dying."""
        text = "{}\n\n{}".format(title, detail if isinstance(detail, str) else repr(detail))
        Logger.error("OCRScanner: " + text)
        try:
            field = TextInput(text=text, readonly=True, multiline=True, size_hint=(1, 1),
                              background_normal='', background_color=(0.1, 0.11, 0.16, 1),
                              foreground_color=(0.95, 0.85, 0.7, 1), font_size=sp(13))
            popup = self._error_popup = Popup(
                title="Something went wrong", content=field, size_hint=(0.94, 0.7),
                separator_color=(0.2, 0.85, 1, 1), background_color=(0.12, 0.13, 0.18, 1))
            popup.open()
        except Exception:
            pass  # never let error reporting cause an error

    def _error_view(self, text):
        """Last-resort screen, used when the UI itself could not be built."""
        root = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(10))
        root.add_widget(Label(text="App could not start", font_size=sp(22), bold=True,
                              color=(0.95, 0.4, 0.35, 1), size_hint_y=None, height=dp(48)))
        scroll = ScrollView(size_hint_y=1)
        field = TextInput(text=text, readonly=True, multiline=True, size_hint_y=None,
                          background_normal='', background_color=(0.1, 0.11, 0.16, 1),
                          foreground_color=(0.95, 0.85, 0.7, 1), font_size=sp(12))
        def _grow(inst, _val):
            # documented TextInput-in-a-ScrollView sizing, with a guard so the
            # binding can never feed itself back in a loop
            target = max(dp(44), inst.minimum_height)
            if inst.height != target:
                inst.height = target
        field.bind(minimum_height=_grow)
        scroll.add_widget(field)
        root.add_widget(scroll)
        return root

    def _flash(self, message):
        label = Label(text=message, font_size=sp(16), color=(1, 1, 1, 1),
                      halign='center', valign='middle', size_hint=(1, None), height=dp(60))
        label.bind(size=lambda inst, val: setattr(inst, 'text_size', (val[0], None)))
        popup = self._flash_popup = Popup(content=label, size_hint=(0.8, None), height=dp(70),
                                          separator_color=(0.2, 0.85, 1, 1), auto_dismiss=True,
                                          background_color=(0.16, 0.18, 0.25, 1))
        try:
            popup.open()
            Clock.schedule_once(lambda _dt: popup.dismiss(), 1.6)
        except Exception as exc:
            Logger.info("OCRScanner: {}".format(message))

    # -- image selection ----------------------------------------------------
    @safe
    def open_file_picker(self, *args):
        """Android: the system gallery picker. Desktop: Kivy's file chooser."""
        if IS_ANDROID:
            self._start_android_pick()
            return
        self._open_desktop_chooser()

    def _start_android_pick(self):
        # ACTION_GET_CONTENT needs no permission at all, unlike any gallery query.
        from jnius import autoclass
        Intent = autoclass("android.content.Intent")
        intent = Intent(Intent.ACTION_GET_CONTENT)
        intent.setType("image/*")
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        android_activity().startActivityForResult(intent, PICK_REQUEST)

    def _open_desktop_chooser(self):
        from kivy.uix.filechooser import FileChooserListView
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        start = os.path.join(os.path.expanduser("~"), "Pictures")
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        chooser = FileChooserListView(path=start, filters=["*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp"],
                                      size_hint_y=0.85)
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
        select = Button(text="Select", background_normal='', background_color=(0.2, 0.6, 1, 1),
                        bold=True, font_size=sp(15))
        cancel = Button(text="Cancel", background_normal='', background_color=(0.35, 0.15, 0.15, 1),
                        bold=True, font_size=sp(15))
        row.add_widget(cancel)
        row.add_widget(select)
        content.add_widget(chooser)
        content.add_widget(row)
        popup = Popup(title="Select Image", content=content, size_hint=(0.95, 0.85))
        select.bind(on_press=lambda _b: (self._on_image_selected(chooser.selection[0])
                                         if chooser.selection else None, popup.dismiss()))
        cancel.bind(on_press=lambda _b: popup.dismiss())
        popup.open()

    @safe
    def open_camera(self, *args):
        if IS_ANDROID:
            self._start_android_capture()
        else:
            self._start_desktop_capture()

    def _start_android_capture(self):
        from jnius import autoclass
        Intent = autoclass("android.content.Intent")
        MediaStore = autoclass("android.provider.MediaStore")

        filename = "scan_{}.jpg".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
        # FLAG_GRANT_* (below) is what lets the camera app write into an entry
        # this app owns; without it the intent is refused on every modern release.
        self._pending_uri = media_store_uri_for_new_photo(filename)

        intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
        intent.putExtra(MediaStore.EXTRA_OUTPUT, self._pending_uri)
        intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION |
                        Intent.FLAG_GRANT_READ_URI_PERMISSION)
        if intent.resolveActivity(android_activity().getPackageManager()) is None:
            raise RuntimeError("No camera app on this device - use Upload Image")
        android_activity().startActivityForResult(intent, CAMERA_REQUEST)

    def _start_desktop_capture(self):
        if cv2 is None:
            raise RuntimeError("OpenCV (cv2) is not installed, so the desktop camera cannot run")

        def worker():
            cap = cv2.VideoCapture(0)
            try:
                if not cap.isOpened():
                    raise RuntimeError("No camera available on this computer")
                for _ in range(12):  # let auto-exposure settle
                    cap.grab()
                ok, frame = cap.read()
                if not ok or frame is None:
                    raise RuntimeError("Camera returned no frame")
                dest = unique_temp_path()
                cv2.imwrite(dest, frame)
                Clock.schedule_once(lambda _dt: self._use_image(dest), 0)
            except Exception as exc:
                message = str(exc)
                Clock.schedule_once(lambda _dt: self.show_error("Camera", message), 0)
            finally:
                cap.release()

        threading.Thread(target=worker, daemon=True).start()

    def _on_activity_result(self, request_code, result_code, data):
        # Called on the Android UI thread by the Java listener: hand everything
        # over to the Kivy event loop, which is the only thread allowed to touch
        # widgets, and do the same for the (blocking) copy of the picked image.
        try:
            Clock.schedule_once(lambda _dt: self._handle_activity_result(
                request_code, result_code, data), 0)
        except Exception:
            Logger.error("OCRScanner: cannot schedule activity result\n" + traceback.format_exc())

    @safe
    def _handle_activity_result(self, request_code, result_code, data):
        from jnius import autoclass
        Activity = autoclass("android.app.Activity")

        if result_code != Activity.RESULT_OK:
            Clock.schedule_once(lambda _dt: self._flash("Cancelled"), 0)
            return

        if request_code == CAMERA_REQUEST:
            uri = self._pending_uri
        elif data is not None and data.getData() is not None:
            uri = data.getData()
        else:
            raise RuntimeError("The picker returned no image")
        if uri is None:
            raise RuntimeError("No image was received from Android")

        dest = unique_temp_path()
        copy_content_uri_to_file(uri, dest)
        self._pending_uri = None
        Clock.schedule_once(lambda _dt: self._use_image(dest), 0)

    @safe
    def _use_image(self, path, auto_scan=False):
        if not os.path.exists(path):
            self._flash("Image file is missing")
            return
        screen = self.root.get_screen('main')
        screen.current_image_path = path
        screen.update_image_preview(path)
        self.root.current = 'main'
        prune_temp_images()
        if auto_scan:
            self.run_ocr()

    def _on_image_selected(self, path):
        """Copy the picked file next to the app so OpenCV can always read it."""
        if not os.path.exists(path):
            self._flash("Cannot read that file")
            return
        extension = os.path.splitext(path)[1].lstrip(".").lower() or "jpg"
        dest = unique_temp_path(extension)
        try:
            shutil.copy2(path, dest)
        except Exception as exc:
            raise RuntimeError("Could not copy the image: {}".format(exc))
        self._use_image(dest)

    # -- actions ------------------------------------------------------------
    @safe
    def run_ocr(self, *args):
        screen = self.root.get_screen('main')
        if not screen.current_image_path:
            self._flash("Choose an image first")
            return
        if ocr_engine is None:
            raise RuntimeError("ocr_engine could not be imported: " +
                               "; ".join(name for name, _ in PROBLEMS))

        screen.ids.scan_btn.text = "Scanning..."
        screen.ids.scan_btn.disabled = True
        screen.ids.result_number.text = "..."

        image_path = screen.current_image_path

        def worker():
            try:
                result = ocr_engine.extract_numbers(image_path)
                Clock.schedule_once(lambda _dt: self._on_ocr_done(result), 0)
            except Exception as exc:
                Logger.error("OCRScanner: OCR failed\n" + traceback.format_exc())
                Clock.schedule_once(lambda _dt: self._on_ocr_error(exc), 0)

        threading.Thread(target=worker, daemon=True).start()

    @safe
    def _on_ocr_done(self, result):
        screen = self.root.get_screen('main')
        screen.ids.scan_btn.text = "SCAN NUMBER"
        screen.ids.scan_btn.disabled = False
        screen.show_ocr_result(result)

        cleaned = result.get("cleaned", "")
        if cleaned and database is not None:
            try:
                database.save_result(image_path=screen.current_image_path,
                                      raw_text=result.get("best_text", ""),
                                      cleaned_num=cleaned,
                                      engine_used=result.get("method", "OCR"),
                                      confidence=result.get("confidence", 0))
            except Exception as exc:
                Logger.error("OCRScanner: saving history failed: {}".format(exc))

    def _on_ocr_error(self, error):
        screen = self.root.get_screen('main')
        screen.ids.scan_btn.text = "SCAN NUMBER"
        screen.ids.scan_btn.disabled = False
        screen.ids.result_number.text = "Scan failed"
        self.show_error("OCR error", error)

    @safe
    def copy_number(self, *args):
        text = (self.root.get_screen('main').ids.result_number.text or "").strip()
        if not text or text in ("---", "...", "No number detected", "Scan failed"):
            self._flash("Nothing to copy yet")
            return
        self.clipboard = text
        self._flash("Copied: {}".format(text))

    # -- history ------------------------------------------------------------
    @safe
    def go_to_history(self, *args):
        self.root.current = 'history'
        self.root.get_screen('history').load_history(self._records())

    def _records(self, query=""):
        if database is None:
            return []
        records = database.get_all_history()
        needle = (query or "").strip().lower()
        if not needle:
            return records
        wanted = []
        for record in records:
            haystack = " ".join(str(record.get(key, "") or "") for key in
                                 ("cleaned_num", "raw_text", "engine", "notes", "timestamp"))
            if needle in haystack.lower():
                wanted.append(record)
        return wanted

    @safe
    def search_history(self, text="", *args):
        self.root.get_screen('history').load_history(self._records(text))

    @safe
    def go_to_main(self, *args):
        self.root.current = 'main'


if __name__ == "__main__":
    if PROBLEMS:
        print("Optional modules unavailable:")
        for name, error in PROBLEMS:
            print("  - {}: {}".format(name, error))
    OCRScannerApp().run()
