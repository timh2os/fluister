"""Fluister: hold a key, speak, release. Formatted text appears at your
cursor. A local rebuild of the Wispr Flow interaction loop.

Pipeline: hotkey -> mic capture -> local/cloud ASR -> LLM formatting pass
-> paste into the focused app.

Run with: python -m fluister.main
"""
import json
import sys
import threading
import time

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import config as config_module
from . import corrections, vad
from .asr import ASR, SAMPLE_RATE, StreamingTranscriber, has_foreign_script
from .audio import Recorder
from .feedback import Feedback
from .hotkey import HoldKeyListener, start_fn_listener
from .inject import frontmost_app, paste_text, press_return_later
from .llm import format_transcript
from .overlay import Pill, tray_icon

# Delay before muting system audio, so the start sound is not cut off.
MUTE_DELAY_MS = 150


class Fluister(QObject):
    # Hotkey callbacks arrive on listener threads; these signals hop the
    # work over to the Qt main thread.
    record_started = Signal()
    record_stopped = Signal()
    record_cancelled = Signal()
    pipeline_done = Signal(str, dict)
    # Emitted from the streaming worker; queued to the main thread so the
    # pill can safely repaint with the live draft text.
    live_text = Signal(str)

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.cfg = config_module.load()
        self.enabled = True
        audio_cfg = self.cfg["audio"]
        self.recorder = Recorder(audio_cfg["always_on"], audio_cfg["preroll_ms"])
        self.feedback = Feedback(self.cfg)
        self.asr = ASR(self.cfg)
        self.pill = Pill(self.recorder)
        self._target_app = ""
        self._stream = None
        # True between key release and the end of the recording tail.
        self._stopping = False

        self.record_started.connect(self._on_record_started)
        self.record_stopped.connect(self._on_record_stopped)
        self.record_cancelled.connect(self._on_record_cancelled)
        self.pipeline_done.connect(self._on_pipeline_done)
        self.live_text.connect(self.pill.set_live_text)

        self._build_tray()
        self._open_mic()
        self._start_hotkey()
        threading.Thread(target=self._warm, daemon=True).start()

    def _start_hotkey(self):
        mode = self.cfg["hotkey"].get("mode", "pynput")
        if mode == "fn":
            start_fn_listener(self.record_started.emit, self.record_stopped.emit)
        else:
            self._listener = HoldKeyListener(
                self.cfg["hotkey"].get("key", "alt_r"),
                self.record_started.emit,
                self.record_stopped.emit,
                self.record_cancelled.emit,
            )
            self._listener.start()

    def _open_mic(self):
        try:
            self.recorder.open()
        except Exception as exc:
            self.tray.setToolTip(f"Fluister: microphone error ({exc})")

    def _warm(self):
        try:
            self.asr.warm()
            self.tray.setToolTip("Fluister: ready")
        except Exception as exc:
            self.tray.setToolTip(f"Fluister: ASR model failed to load ({exc})")

    @Slot()
    def _on_record_started(self):
        if not self.enabled or self.recorder.recording or self._stopping:
            return
        self._target_app = frontmost_app()
        # Hook the streaming session up before the mic starts so no chunk
        # is dropped. When the model is not warm yet, start() returns False
        # and this dictation simply uses the batch path.
        if self.asr.streaming:
            stream = StreamingTranscriber(self.asr, on_draft=self.live_text.emit)
            if stream.start():
                self._stream = stream
                self.recorder.on_chunk = stream.feed
        try:
            self.recorder.start()
        except Exception as exc:
            self._drop_stream()
            self.tray.setToolTip(f"Fluister: microphone error ({exc})")
            return
        self.feedback.recording_started()
        QTimer.singleShot(MUTE_DELAY_MS, self._mute_if_recording)
        self.pill.show_recording()

    def _mute_if_recording(self):
        if self.recorder.recording:
            self.feedback.mute()

    def _drop_stream(self):
        self.recorder.on_chunk = None
        if self._stream is not None:
            self._stream.abort()
            self._stream = None

    @Slot()
    def _on_record_cancelled(self):
        self._drop_stream()
        if self.recorder.recording:
            self.recorder.stop()
            self.feedback.recording_stopped()
        self.pill.dismiss()

    @Slot()
    def _on_record_stopped(self):
        # Keep recording a little after release so the last word is not
        # clipped (Handy: extra_recording_buffer_ms).
        if self._stopping or not self.recorder.recording:
            return
        self._stopping = True
        QTimer.singleShot(self.cfg["audio"]["tail_ms"], self._finish_recording)

    @Slot()
    def _finish_recording(self):
        self._stopping = False
        if not self.recorder.recording:
            return  # cancelled during the tail
        audio = self.recorder.stop()
        self.feedback.recording_stopped()
        self.recorder.on_chunk = None
        stream, self._stream = self._stream, None
        duration = len(audio) / SAMPLE_RATE
        if duration < self.cfg["audio"]["min_seconds"]:
            if stream is not None:
                stream.abort()
            self.pill.dismiss()
            return
        self.pill.show_processing()
        threading.Thread(
            target=self._pipeline, args=(audio, self._target_app, stream), daemon=True
        ).start()

    def _pipeline(self, audio, app_name, stream=None):
        meta = {"app": app_name, "duration_s": round(len(audio) / SAMPLE_RATE, 2)}
        vad_cfg = self.cfg["vad"]
        speech_s = vad.speech_seconds(audio) if vad_cfg["enabled"] else None
        if speech_s is not None:
            meta["speech_s"] = round(speech_s, 2)
        if vad.has_too_little_speech(speech_s, vad_cfg):
            if stream is not None:
                stream.abort()
            self.pipeline_done.emit("", {**meta, "skipped": "no speech"})
            return
        t0 = time.time()
        raw = stream.finish() if stream is not None else None
        if raw:
            meta["streamed"] = True
        else:
            try:
                raw = self.asr.transcribe(audio)
            except Exception as exc:
                self.pipeline_done.emit("", {"error": f"ASR failed: {exc}"})
                return
        if raw and self.cfg["asr"].get("rescue_foreign_script", True) and has_foreign_script(raw):
            rescued = self.asr.rescue(audio)
            if rescued:
                meta["parakeet_raw"] = raw
                raw = rescued
                meta["rescued"] = True
        meta["raw"] = raw
        meta["asr_ms"] = int((time.time() - t0) * 1000)
        if vad_cfg["enabled"] and vad.is_hallucination(raw, speech_s, vad_cfg):
            self.pipeline_done.emit("", {**meta, "skipped": "hallucination"})
            return

        t1 = time.time()
        text = format_transcript(self.cfg, raw, app_name)
        meta["llm_ms"] = int((time.time() - t1) * 1000)
        self.pipeline_done.emit(text, meta)

    @Slot(str, dict)
    def _on_pipeline_done(self, text, meta):
        self.pill.dismiss()
        if meta.get("error"):
            self.tray.setToolTip(f"Fluister: {meta['error']}")
            return
        if meta.get("skipped"):
            self.tray.setToolTip(f"Fluister: nothing pasted ({meta['skipped']})")
        else:
            if text:
                self._paste(text, meta.get("app", ""))
            self.tray.setToolTip(
                "Fluister: last dictation "
                f"{meta.get('asr_ms', 0)} ms ASR + {meta.get('llm_ms', 0)} ms LLM"
            )
        if self.cfg["history"]["enabled"]:
            self._log(text, meta)

    def _paste(self, text, app_name):
        injection = self.cfg["injection"]
        pasted = text + " " if injection["trailing_space"] else text
        paste_text(
            pasted,
            self.cfg,
            remote=_app_matches(app_name, injection.get("remote_apps")),
        )
        corrections.watch_for_correction(self.cfg, pasted, self.tray)
        if _app_matches(app_name, injection["auto_submit_apps"]):
            press_return_later()

    def _log(self, text, meta):
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "text": text, **meta}
        try:
            with open(config_module.HISTORY_PATH, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _build_tray(self):
        self.tray = QSystemTrayIcon(tray_icon(), self.app)
        menu = QMenu()

        self.toggle_action = QAction("Enabled", menu)
        self.toggle_action.setCheckable(True)
        self.toggle_action.setChecked(True)
        self.toggle_action.triggered.connect(self._toggle)
        menu.addAction(self.toggle_action)

        lang_menu = menu.addMenu("Language")
        lang_group = QActionGroup(lang_menu)
        lang_group.setExclusive(True)
        current = self.cfg["asr"].get("language") or None
        for label, code in (
            ("Auto (Dutch + English)", None),
            ("Nederlands", "nl"),
            ("English", "en"),
        ):
            action = QAction(label, lang_menu)
            action.setCheckable(True)
            action.setChecked(code == current)
            action.triggered.connect(lambda _checked=False, c=code: self._set_language(c))
            lang_group.addAction(action)
            lang_menu.addAction(action)
        self._lang_group = lang_group

        open_config = QAction("Open config.yaml", menu)
        open_config.triggered.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(config_module.CONFIG_PATH))
            )
        )
        menu.addAction(open_config)

        open_history = QAction("Open history", menu)
        open_history.triggered.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(config_module.HISTORY_PATH))
            )
        )
        menu.addAction(open_history)

        menu.addSeparator()
        quit_action = QAction("Quit Fluister", menu)
        quit_action.triggered.connect(self.app.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Fluister: loading ASR model...")
        self.tray.show()
        self._menu = menu  # keep a reference so the menu is not GC'd

    def _set_language(self, code):
        self.cfg["asr"]["language"] = code
        try:
            config_module.save_asr_language(code)
        except Exception as exc:
            print(f"fluister: could not save language ({exc})")
        names = {None: "auto", "nl": "Nederlands", "en": "English"}
        self.tray.setToolTip(f"Fluister: language {names[code]}")

    def _toggle(self, checked):
        self.enabled = checked


def _app_matches(app_name: str, apps: list) -> bool:
    # WhatsApp reports its name with a leading left-to-right mark.
    name = app_name.strip("\u200e").strip().lower()
    return bool(name) and name in {a.strip().lower() for a in apps or []}


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Fluister")

    # macOS: behave as a menu bar app without a Dock icon, like Wispr Flow.
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().setActivationPolicy_(1)  # Accessory
    except Exception:
        pass

    controller = Fluister(app)
    _ = controller
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
