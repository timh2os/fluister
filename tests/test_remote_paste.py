"""Tests for pasting into remote-desktop viewers (injection.remote_apps).

Run: .venv/bin/python -m unittest tests.test_remote_paste
"""
import unittest
from unittest import mock

from pynput.keyboard import Key

from fluister import inject
from fluister.main import _app_matches


class RemotePasteTests(unittest.TestCase):
    def _paste(self, key):
        cfg = {"injection": {"remote_paste_modifier": key, "ax_insert": True}}
        with mock.patch.object(inject, "_keyboard") as keyboard, \
                mock.patch.object(inject, "pyperclip") as clipboard, \
                mock.patch.object(inject, "_try_ax_insert") as ax, \
                mock.patch.object(inject.time, "sleep"):
            inject.paste_text("Eerder PowerPoints? Één ding.", cfg, remote=True)
        return keyboard, clipboard, ax

    def test_remote_skips_accessibility_and_keeps_clipboard(self):
        keyboard, clipboard, ax = self._paste("cmd")
        ax.assert_not_called()
        clipboard.copy.assert_called_once_with("Eerder PowerPoints? Één ding.")
        # No restore of the previous clipboard: the viewer reads it late.
        clipboard.paste.assert_not_called()

    def test_modifier_is_configurable(self):
        for name, expected in (("cmd", Key.cmd), ("ctrl", Key.ctrl)):
            keyboard, _, _ = self._paste(name)
            presses = [c.args[0] for c in keyboard.press.call_args_list]
            self.assertEqual(presses, [expected, "v"])

    def test_keys_are_released_in_reverse_order(self):
        keyboard, _, _ = self._paste("ctrl")
        releases = [c.args[0] for c in keyboard.release.call_args_list]
        self.assertEqual(releases, ["v", Key.ctrl])

    def test_app_matching_is_case_insensitive(self):
        self.assertTrue(_app_matches("minimalVNC", ["minimalvnc"]))
        self.assertFalse(_app_matches("Terminal", ["minimalVNC"]))


if __name__ == "__main__":
    unittest.main()
