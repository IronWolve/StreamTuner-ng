"""Isolated offline checks, with explicit optional network/Qt/null-audio probes."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from . import __version__, asset_path
from .config import Config
from .plugins.channels import BUILTINS, PLUGIN_ERRORS
from .plugins.channels._selftest import BrokenGarbage, BrokenHang, BrokenThrow
from .plugins.host import PluginHost
from .plugins.result import Status
from .security import backup_bundle, csv_cell, station_rows, valid_url


def run_selftest(*, network=False, gui=False, engine=False, report=None) -> int:
    checks = []

    def check(name, passed, detail=''):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail})
        print(f'[{"PASS" if passed else "FAIL"}] {name}' + (f': {detail}' if detail else ''))

    with tempfile.TemporaryDirectory(prefix='streamtuner-selftest-') as folder:
        config = Config(Path(folder))
        host = PluginHost(config)
        for cls in BUILTINS + [BrokenThrow, BrokenHang, BrokenGarbage]:
            host.register(cls(config))
        check('plugin exception is contained', host.streams('_broken_throw', 'x').status is Status.ERROR)
        check('hung plugin times out', host.streams('_broken_hang', 'x', timeout=0.05).status is Status.TIMEOUT)
        check('garbage plugin degrades safely', host.streams('_broken_garbage', 'x').status is Status.EMPTY)
        host.set_enabled('_broken_throw', True)
        for _ in range(3):
            host.streams('_broken_throw', 'x')
        check('crash budget disables plugin', host.health['_broken_throw'].auto_disabled and not host.is_enabled('_broken_throw'))
        check('healthy local plugin remains usable', host.categories('bookmarks').status is Status.OK)
        import inspect
        from .plugins.loader import user_plugin_dir
        provider_files = [Path(inspect.getfile(cls)).resolve() for cls in BUILTINS
                          if cls.id not in ('bookmarks', 'local')]
        check('providers load from the adjacent plugins directory', len(provider_files) >= 12 and
              all(path.parent == user_plugin_dir().resolve() for path in provider_files))

        check('local and executable schemes blocked', all(not valid_url(u) for u in ('file:///private', 'javascript:bad', 'smb://host/share')))
        check('supported network URLs accepted', valid_url('https://example.com/live'))
        check('malformed station rows excluded', not station_rows([None, {'url': 'file:///private'}]))
        check('CSV formulas neutralized', csv_cell('=1+1').startswith("'"))
        config.settings['example_secret'] = 'selftest-placeholder'
        check('secrets omitted from backup', 'selftest-placeholder' not in json.dumps(config.export_bundle()))
        config.settings.pop('example_secret')
        config.cache_save('example', 'stations', [{'title': 'Fixture', 'url': 'https://example.com/live'}])
        loaded = config.cache_load('example', 'stations')
        check('atomic cache round-trip', bool(loaded and len(loaded[0]) == 1))
        config.cache_save('example', '__categories__', ['First', 'Second'])
        check('category cache preserves names', config.cache_load('example', '__categories__')[0] == ['First', 'Second'])
        check('backup schema normalizes empty lists', backup_bundle({'app': 'streamtuner-ng'})['favourite'] == [])
        check('bundled window icon resolves', Path(asset_path('logo.png')).is_file())
        try:
            import importlib
            backend = ("keyring.backends.Windows" if os.name == "nt" else
                       "keyring.backends.macOS" if sys.platform == "darwin" else "keyring.backends.SecretService")
            importlib.import_module(backend)
            check('native credential backend is importable without accessing credentials', True)
        except Exception as exc:
            check('native credential backend is importable without accessing credentials', False, type(exc).__name__)
        if engine:
            from .player.mpv_player import Player
            player = Player(audio_driver='null')
            check('native audio engine initializes with null output', player.available, player.error)
            if player.available:
                import array
                import math
                import wave
                tone = Path(folder) / "null-output fixture.wav"
                samples = array.array("h", (int(1200 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(16000)))
                with wave.open(str(tone), "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(16000)
                    audio.writeframes(samples.tobytes())
                try:
                    with player._mpv.prepare_and_wait_for_event('end_file', timeout=10):
                        player._mpv.play(str(tone))
                        player._mpv.wait_until_playing(timeout=5)
                        decoded = bool(player.audio_format()[1])
                    check('generated PCM decodes through null audio output', decoded)
                except Exception as exc:
                    check('generated PCM decodes through null audio output', False, type(exc).__name__)
            player.shutdown()
        if gui:
            check('Qt offscreen UI initializes', _gui_smoke(config))
        if network:
            for cid, category in (('radiobrowser', 'jazz'), ('somafm', 'All')):
                result = host.streams(cid, category, timeout=50)
                check(f'{cid} live directory fetch', result.ok and bool(result.data),
                      f'{len(result.data or [])} stations' if result.ok else result.message)
    document = {'version': __version__, 'network': network, 'gui': gui, 'null_audio': engine,
                'loaded_plugins': sorted(host.channels), 'plugin_load_errors': PLUGIN_ERRORS, 'checks': checks}
    if report:
        Path(report).write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    passed = sum(item['passed'] for item in checks)
    print(f'{passed}/{len(checks)} checks passed')
    return 0 if passed == len(checks) else 1


def _gui_smoke(config) -> bool:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from .plugins.base import Channel
    from .plugins.channels.bookmarks import Bookmarks
    from .plugins.channels.local import Local
    from .ui.mainwindow import MainWindow
    from .ui.options import OptionsDialog
    from .ui.worker import drain_cache

    class SilentPlayer:
        available, is_playing, volume, error = False, False, 70, 'Silent verification'
        def set_normalize(self, *_): pass
        def set_volume(self, *_): pass
        def stop(self): pass
        def shutdown(self): pass
        def play(self, *_): raise AssertionError('GUI verification must not play audio')

    app = QApplication.instance() or QApplication([])
    config.set('station_icon_mode', 'off')
    config.set('show_album_art', False)
    host = PluginHost(config)
    host.register(Bookmarks(config))
    host.register(Local(config))
    class Fixture(Channel):
        id, title, priority = 'verification', 'Verification', 'standard'
        revision = 1
        def update_categories(self): return ['First', 'Second']
        def update_streams(self, category, search=None):
            return [{'title': f'{category} {self.revision}', 'url': 'https://example.com/stream'}]
        def invalidate_cache(self): self.revision += 1

    host.register(Fixture(config))
    config.cache_save('verification', '__categories__', ['First', 'Second'])
    def wait_until(predicate):
        import time
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        return bool(predicate())

    # Do not register OS-global media keys or contact directories during this probe.
    original = MainWindow._setup_media_keys
    MainWindow._setup_media_keys = lambda self: None
    window = options = None
    drained = False
    try:
        window = MainWindow(config, host, SilentPlayer())
        window.show()
        app.processEvents()
        options = OptionsDialog(host, config, window)
        options.show()
        app.processEvents()
        good = not window.grab().isNull() and not options.grab().isNull()
        options.close()
        for index in range(window.channels.count()):
            if window.channels.item(index).data(Qt.UserRole) == 'verification':
                window.channels.setCurrentRow(index)
                break
        good &= wait_until(lambda: window.model.rowCount() == 1 and window.categories.count() == 2)
        window.categories.setCurrentRow(1)
        good &= wait_until(lambda: window.model.rowCount() == 1 and window.model.row_at(0)['title'] == 'Second 1')
        window._reload()
        good &= wait_until(lambda: window.model.rowCount() == 1 and window.model.row_at(0)['title'] == 'Second 2')
    finally:
        if options is not None:
            options.close()
        if window is not None:
            window._really_quit = True
            window.close()
        app.processEvents()
        drained = drain_cache()
        app.processEvents()
        MainWindow._setup_media_keys = original
    return good and drained
