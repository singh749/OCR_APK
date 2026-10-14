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
import sys
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
STARTUP_ERROR_PATH = os.path.join(APP_DIR, "startup_error.txt")


def log_diag(kind, detail):
    """Write a one-line record that a test - or a logcat dump - can grep for.

    On a phone, "the app closes as soon as I open it" leaves nothing readable
    behind: the process is gone before it can show anything. So every fact that
    tells "started", "started with missing modules" and "died inside build()"
    apart is also written to logcat under the `python` tag. The CI emulator job
    (.ci/emulator_boot_test.sh) asserts on these lines, which turns a UI rendered
    into the wrong box, or an exception during startup, into a failing build
    instead of another round of phone reports.
    """
    line = "OCRDIAG {} {}".format(kind, str(detail).replace("\n", " | "))
    Logger.info(line)
    return line


def write_startup_error(text):
    """Last-resort breadcrumb, in the app's own files dir (readable via adb)."""
    try:
        with open(STARTUP_ERROR_PATH, "w", encoding="utf-8") as handle:
            handle.write("OCRScanner startup failure {}\n\n{}".format(
                datetime.now().isoformat(timespec="seconds"), text))
    except Exception:  # pragma: no cover - best effort only
        pass

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
    window = getattr(sys.modules.get("kivy.core.window"), "Window", None)
    if window is None:
        # Deliberate: `import kivy.core.window` is what selects a window provider,
        # and when none works Kivy calls sys.exit(1) rather than raising - which no
        # try/except can catch. Kivy's own startup imports it, so on a machine that
        # can show a window this is already set by the time build() runs.
        Logger.debug("OCRScanner: no window module yet, leaving the size alone")
        return
    try:
        window.size = (430, 840)
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
        # Deliberately empty: the Java bridge (pyjnius autoclass, the activity
        # result listener) is only touched by ensure_activity_listener(), the
        # first time the camera or the gallery is actually used. Anything that can
        # fail on a particular device must not stand between the app and its UI.
        self._activity_listener = None
        self._pending_uri = None
        self._flash_popup = None
        self._error_popup = None

        try:
            Builder.load_file(KV_PATH)
            self.root = ScreenManager(transition=FadeTransition(duration=0.25))
            self.root.add_widget(MainScreen(name='main'))
            self.root.add_widget(HistoryScreen(name='history'))
            if PROBLEMS:
                # Not a dialog, on purpose: a Popup - and least of all a TextInput,
                # which pulls in Android's soft-input machinery - created during
                # build() is a startup risk of its own, and the first job of this
                # app is to open. The missing modules are named in the result panel
                # and in logcat instead.
                for name, err in PROBLEMS:
                    log_diag("problem", "{}: {}".format(name, err))
                Clock.schedule_once(self._note_startup_problems, 0)
            log_diag("startup", "platform={} kv=loaded screens=2 problems={}".format(
                platform, len(PROBLEMS)))
            Clock.schedule_once(self._report_layout, 1.0)
            # Optional, CI-only: if the launch intent names an image, scan it once
            # the UI exists. That is how "reads the number" gets verified on an
            # emulated phone instead of being promised in a changelog.
            Clock.schedule_once(self._start_ci_ocr_probe, 2.0)
            return self.root
        except Exception:  # noqa: BLE001 - startup must always produce a screen
            tb = traceback.format_exc()
            Logger.error("OCRScanner: startup failed\n" + tb)
            log_diag("fatal", "build() raised: " + tb[-900:])
            write_startup_error(tb)
            return self._error_view(tb)

    def _note_startup_problems(self, _dt):
        """Name the optional parts that are missing, as text in the UI."""
        try:
            label = self.root.get_screen('main').ids.engine_label
            label.text = "Not installed: {}".format(
                ", ".join(name for name, _ in PROBLEMS))
            label.color = (0.95, 0.7, 0.3, 1)
        except Exception:  # noqa: BLE001 - cosmetic only
            pass

    def _force_layout_pass(self):
        """Ask Kivy for the layout pass instead of waiting for a frame to deliver it.

        `Layout` runs its pass through `Clock.create_trigger(self.do_layout, -1)`,
        i.e. once per frame. On the CI emulator a frame takes ~45 s (EGL_emulation
        reports `avg=45179.50ms`), so a report taken one second after build()
        measures Kivy's un-laid-out 100x100 widget default and reads as a collapsed
        layout. `do_layout()` is public, documented as directly callable, and pure
        arithmetic - no drawing, no GL - so calling it makes the numbers below the
        numbers the phone will actually use, on any device at any frame rate.
        """
        targets = [self.root]
        screen = getattr(self.root, "current_screen", None)
        if screen is not None:
            targets.append(screen)
            targets.extend(list(getattr(screen, "children", []) or [])[:1])
        for widget in targets:
            try:
                widget.do_layout()
            except Exception:  # noqa: BLE001 - a diagnostic must never break the app
                pass

    #: how long to wait for the first layout pass: 40 x 0.25s = 10s of frames
    LAYOUT_REPORT_TRIES = 40

    def _report_layout(self, _dt=None):
        """Record what the window and the current screen actually measure.

        This is the automated answer to "the interface looks small": the numbers
        below are the pixels Kivy was given and the size each row ended up with, so
        a layout squeezed into a fixed box fails CI instead of being spotted by hand
        on a phone.

        It re-arms itself until the screen has actually been laid out, because a
        size read before the first layout pass is Kivy's 100x100 widget default and
        not the phone - on a cold device that default is exactly what CI reported
        (`preview=26x0 scanbtn=0x141`, i.e. 100 - 2*dp(14)), and it looked like a
        collapsed layout. Same lesson as the boot test, one level down: wait for the
        event, never for a stopwatch. The record says which case it is, so
        `laid_out=no` is a real blank-screen bug and not a slow emulator.
        """
        tries = getattr(self, "_layout_tries", 0) + 1
        self._layout_tries = tries
        self._force_layout_pass()
        parts = []
        window = laid_out = None
        screen = None
        try:
            from kivy.metrics import Metrics
            window = getattr(sys.modules.get("kivy.core.window"), "Window", None)
            if window is not None:
                parts.append("window={}x{}".format(int(window.width), int(window.height)))
            parts.append("dpi={:.0f}".format(Metrics.dpi))
            parts.append("density={:.2f}".format(Metrics.density or 0.0))
            if self.root is not None:
                parts.append("root={}x{}".format(int(self.root.width), int(self.root.height)))
                # the screen the user is actually looking at, not the one we assume
                screen = getattr(self.root, "current_screen", None)
                if screen is not None:
                    parts.append("screen={}".format(screen.name or "?"))
                    laid_out = (abs(screen.width - self.root.width) <= 1
                                and abs(screen.height - self.root.height) <= 1)
                    # The rows live in the screen's content box, so that is what has
                    # to have been resized - a Screen can match the manager while its
                    # own children are still at Kivy's defaults, which is precisely
                    # the half-measure that made a green run print `preview=26x0`.
                    if laid_out and getattr(screen, "children", None):
                        content = screen.children[0]
                        hint = getattr(content, "size_hint", (None, None))
                        if hint and hint[0]:
                            laid_out = (abs(content.width - screen.width) <= 1
                                        and abs(content.height - screen.height) <= 1)
                            parts.append("content={}x{}".format(int(content.width),
                                                                int(content.height)))
                    for key, name in (("preview_box", "preview"), ("scan_btn", "scanbtn"),
                                      ("result_number", "result")):
                        widget = screen.ids.get(key) if hasattr(screen.ids, "get") else None
                        if widget is not None:
                            parts.append("{}={}x{}".format(name, int(widget.width),
                                                           int(widget.height)))
        except Exception as exc:  # noqa: BLE001 - diagnostics must not raise
            parts.append("error={}: {}".format(type(exc).__name__, exc))
        parts.append("laid_out={}".format("yes" if laid_out else "no"))

        if laid_out is False and tries < self.LAYOUT_REPORT_TRIES:
            Clock.schedule_once(self._report_layout, 0.25)
            if tries == 1:
                log_diag("layout", "waiting for the first layout pass")
            return
        log_diag("layout", " ".join(parts))

    def _start_ci_ocr_probe(self, *_dt):
        """Scan the image named by the launch intent, if CI passed one.

        `am start -n <pkg>/org.kivy.android.PythonActivity --es ocr_file <path>` is
        the only way a pipeline can drive this app past "it opened": the probe goes
        through `run_ocr()`, i.e. the same path the SCAN button uses, with the same
        engine, on the device. A normal launch has no such extra and nothing here
        happens - and every Android call is inside a try, because a diagnostic that
        can crash the app it is diagnosing is worse than no diagnostic.
        """
        if not IS_ANDROID:
            return
        try:
            intent = android_activity().getIntent()
            path = intent.getStringExtra("ocr_file") if intent is not None else None
        except Exception as exc:  # noqa: BLE001 - the probe is optional by design
            log_diag("ciscan", "skipped={}: {}".format(type(exc).__name__, exc))
            return
        if not path:
            return                      # the ordinary case: a person opened the app
        if not os.path.exists(path):
            log_diag("ciscan", "missing={}".format(path))
            return
        try:
            screen = self.root.get_screen('main')
            screen.current_image_path = path
            self._ci_probe_active = True
            log_diag("ciscan", "scanning={}".format(os.path.basename(path)))
            self.run_ocr()
        except Exception as exc:  # noqa: BLE001
            self._ci_probe_active = False
            log_diag("ciscan", "error={}: {}".format(type(exc).__name__, exc))

    def _ci_probe_result(self, kind, detail):
        """Emit the probe's verdict, once, and only while a probe is in flight."""
        if getattr(self, "_ci_probe_active", False):
            self._ci_probe_active = False
            log_diag("ciscan", "{} {}".format(kind, detail))

    # -- messaging ----------------------------------------------------------
    def show_error(self, title, detail):
        """Show a readable, copyable error instead of dying.

        A Label inside a ScrollView rather than a TextInput: the text only has to
        be read and copied, so nothing here should involve the keyboard - and an
        error dialog that itself raises is how "it closes on the first tap" gets
        mistaken for something deeper. The popup is kept on the app so Python
        cannot garbage-collect it while it is open.
        """
        text = "{}\n\n{}".format(title, detail if isinstance(detail, str) else repr(detail))
        Logger.error("OCRScanner: " + text)
        log_diag("error", text)
        try:
            content = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(10))
            field = Label(text=text, size_hint=(1, None), halign='left', valign='top',
                          font_size=sp(13), color=(0.95, 0.85, 0.7, 1),
                          text_size=(dp(300), None))
            field.bind(width=lambda i, v: setattr(i, 'text_size', (v, None)),
                       texture_size=lambda i, v: setattr(i, 'height', v[1] + dp(6)))
            scroll = ScrollView(size_hint_y=1)
            scroll.add_widget(field)

            row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
            close_btn = Button(text="Close", size_hint_x=None, width=dp(110),
                               background_normal='', background_color=(0.3, 0.32, 0.42, 1),
                               bold=True, font_size=sp(14))
            copy_btn = Button(text="Copy", size_hint_x=None, width=dp(110),
                              background_normal='', background_color=(0.2, 0.6, 1, 1),
                              bold=True, font_size=sp(14))
            popup = self._error_popup = Popup(
                title="Something went wrong", content=content, size_hint=(0.94, 0.75),
                separator_color=(0.2, 0.85, 1, 1), background_color=(0.12, 0.13, 0.18, 1))

            def copy_and_close(_btn):
                self.clipboard = text
                popup.dismiss()
                self._flash("Error text copied")

            close_btn.bind(on_press=lambda _b: popup.dismiss())
            copy_btn.bind(on_press=copy_and_close)
            row.add_widget(close_btn)
            row.add_widget(copy_btn)
            content.add_widget(scroll)
            content.add_widget(row)
            popup.open()
        except Exception:  # noqa: BLE001 - reporting must never cause a report
            pass
    def _error_view(self, text):
        """Last-resort screen, used when the UI itself could not be built.

        Only Labels: whatever is left when build() has failed has to be the
        simplest possible thing, and no TextInput / keyboard involvement.
        """
        root = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(10))
        root.add_widget(Label(text="App could not start", font_size=sp(22), bold=True,
                              color=(0.95, 0.4, 0.35, 1), size_hint_y=None, height=dp(48)))
        scroll = ScrollView(size_hint_y=1)
        field = Label(text=text, size_hint=(1, None), halign='left', valign='top',
                      font_size=sp(12), color=(0.95, 0.85, 0.7, 1),
                      text_size=(dp(300), None))
        field.bind(width=lambda i, v: setattr(i, 'text_size', (v, None)),
                   texture_size=lambda i, v: setattr(i, 'height', v[1] + dp(6)))
        scroll.add_widget(field)
        root.add_widget(scroll)
        root.add_widget(Label(
            text="Also saved as startup_error.txt in the app's own files folder",
            font_size=sp(11), color=(0.5, 0.55, 0.65, 1), size_hint_y=None, height=dp(32),
            text_size=(dp(300), None), halign='left', valign='top'))
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

    def ensure_activity_listener(self):
        """Register for activity results the first time Android is asked for one.

        Kept out of build() on purpose: the Java bridge is the most device- and
        bootstrap-specific thing this app touches (pyjnius proxies, an interface on
        PythonActivity), so if it were ever unavailable the interface still has to
        open and say so instead of dying on the way in.
        """
        if self._activity_listener is None:
            self._activity_listener = make_activity_result_listener(self._on_activity_result)
        return self._activity_listener

    def _start_android_pick(self):
        # ACTION_GET_CONTENT needs no permission at all, unlike a gallery query.
        self.ensure_activity_listener()
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
        # Lazy on purpose (see ensure_activity_listener): pyjnius is not touched
        # until the user actually asks for the camera.
        self.ensure_activity_listener()
        from jnius import autoclass
        Intent = autoclass("android.content.Intent")
        MediaStore = autoclass("android.provider.MediaStore")

        filename = "scan_{}.jpg".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
        self._pending_uri = media_store_uri_for_new_photo(filename)

        intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
        # Without the two grant flags the camera app is not allowed to write into
        # an entry this app owns, and the capture silently never lands.
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
                # The activity doubles as the Context the on-device engine (ML Kit)
                # needs. Looked up here, on this thread, rather than in a Java
                # callback: a pyjnius proxy created and used on the same thread is
                # the case that is known to be safe.
                try:
                    ocr_context = android_activity() if IS_ANDROID else None
                except Exception as exc:  # noqa: BLE001 - only disables ML Kit
                    Logger.error("OCRScanner: no activity for OCR: {}".format(exc))
                    ocr_context = None
                result = ocr_engine.extract_numbers(image_path, context=ocr_context)
                # Readable in logcat and in `adb logcat | grep OCRDIAG`, so "which
                # engine answered on this phone" is answerable without a rebuild.
                log_diag("engine", "method={} alts={}".format(
                    result.get("method", ""), len(result.get("alternatives") or [])))
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

        self._ci_probe_result("cleaned={}".format(result.get("cleaned", "")),
                              "method={} conf={} alts={}".format(
                                  result.get("method", ""),
                                  result.get("confidence", 0),
                                  len(result.get("alternatives") or [])))

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
        self._ci_probe_result("error={}".format(str(error)[:180]), "scan=failed")
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
    try:
        OCRScannerApp().run()
    except Exception:  # noqa: BLE001 - the process is ending either way
        tb = traceback.format_exc()
        Logger.error("OCRScanner: fatal\n" + tb)
        # Kivy can fail before or after build() (no window provider, a broken GL
        # context, an interrupted first frame). Without this the only trace of it
        # is a phone report saying "it closes when I open it".
        log_diag("fatal", "run() raised: " + tb[-900:])
        write_startup_error(tb)
        raise