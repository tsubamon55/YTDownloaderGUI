"""config.py の単体テスト(config.json の探索と、壊れた内容へのフォールバックを検証)"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import config
import paths


def _write_config(directory: str, data) -> str:
    path = os.path.join(directory, config.CONFIG_FILE_NAME)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f)
    return path


class ConfigFilePathTest(unittest.TestCase):
    def test_finds_config_next_to_base_dir(self):
        with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as meipass:
            expected = _write_config(base_dir, {})
            _write_config(meipass, {})
            with patch.object(paths, "get_base_dir", return_value=base_dir), \
                 patch("sys._MEIPASS", meipass, create=True):
                self.assertEqual(config._config_file_path(), expected)

    def test_falls_back_to_meipass_when_base_dir_has_no_config(self):
        """PyInstallerが同梱ファイルを_internal配下にまとめる配置でも読み込めること"""
        with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as meipass:
            expected = _write_config(meipass, {})
            with patch.object(paths, "get_base_dir", return_value=base_dir), \
                 patch("sys._MEIPASS", meipass, create=True):
                self.assertEqual(config._config_file_path(), expected)

    def test_returns_none_when_nothing_found(self):
        with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as meipass:
            with patch.object(paths, "get_base_dir", return_value=base_dir), \
                 patch("sys._MEIPASS", meipass, create=True):
                self.assertIsNone(config._config_file_path())


class LoadConfigTest(unittest.TestCase):
    def _load(self, data):
        """dataを書き出したconfig.jsonを読ませ、(AppConfig, log_debugに渡された文字列) を返す"""
        logged = []
        with tempfile.TemporaryDirectory() as base_dir:
            if data is not None:
                _write_config(base_dir, data)
            with patch.object(paths, "get_base_dir", return_value=base_dir), \
                 patch.object(config, "log_debug", side_effect=logged.append):
                return config.load_config(), logged

    def test_overrides_only_specified_keys(self):
        loaded, logged = self._load({"mp3_quality": "320"})
        self.assertEqual(loaded.mp3_quality, "320")
        self.assertEqual(loaded.thumbnail_max_candidates, config.AppConfig().thumbnail_max_candidates)
        self.assertEqual(logged, [])

    def test_dict_setting_is_merged_with_defaults(self):
        loaded, _ = self._load({"clip_video_encoder_by_codec_prefix": {"av01": ["libaom-av1", "30"]}})
        self.assertEqual(loaded.clip_video_encoder_by_codec_prefix["av01"], ["libaom-av1", "30"])
        # 指定しなかったコーデックの既定値は残る
        self.assertEqual(loaded.clip_video_encoder_by_codec_prefix["avc1"], ["libx264", "18"])

    def test_unknown_key_is_ignored_and_logged(self):
        loaded, logged = self._load({"no_such_key": 1, "mp3_quality": "320"})
        self.assertFalse(hasattr(loaded, "no_such_key"))
        self.assertEqual(loaded.mp3_quality, "320")
        self.assertEqual(len(logged), 1)

    def test_missing_file_falls_back_to_defaults_and_logs(self):
        """config.jsonは常に同梱されるため、不在はビルド不備としてログに残す
        (無言でフォールバックすると、_internal配置バグのように原因を追えなくなる)"""
        loaded, logged = self._load(None)
        self.assertEqual(loaded, config.AppConfig())
        self.assertEqual(len(logged), 1)
        self.assertIn(config.CONFIG_FILE_NAME, logged[0])

    def test_broken_json_falls_back_to_defaults_and_logs(self):
        loaded, logged = self._load("{ this is not json")
        self.assertEqual(loaded, config.AppConfig())
        self.assertEqual(len(logged), 1)

    def test_non_object_json_falls_back_to_defaults_and_logs(self):
        loaded, logged = self._load([1, 2, 3])
        self.assertEqual(loaded, config.AppConfig())
        self.assertEqual(len(logged), 1)

    def test_wrong_value_type_falls_back_to_default_and_logs(self):
        """キー名が正しくJSONとしても正当でも、値の型が違えば既定値のままにする。
        そのまま通すと下流(スライス等)で原因の分からないTypeErrorになり、
        config.jsonの型ミスがUIにもログにも現れなくなる"""
        loaded, logged = self._load({"thumbnail_max_candidates": "3"})
        self.assertEqual(loaded.thumbnail_max_candidates, config.AppConfig().thumbnail_max_candidates)
        self.assertEqual(len(logged), 1)
        self.assertIn("thumbnail_max_candidates", logged[0])

    def test_dict_setting_given_non_dict_falls_back_to_default(self):
        loaded, logged = self._load({"clip_video_encoder_by_codec_prefix": ["libx264", "18"]})
        self.assertEqual(
            loaded.clip_video_encoder_by_codec_prefix,
            config.AppConfig().clip_video_encoder_by_codec_prefix,
        )
        self.assertEqual(len(logged), 1)

    def test_string_setting_given_number_falls_back_to_default(self):
        loaded, logged = self._load({"mp3_quality": 320})
        self.assertEqual(loaded.mp3_quality, config.AppConfig().mp3_quality)
        self.assertEqual(len(logged), 1)

    def test_bool_is_not_accepted_as_int(self):
        """boolはintのサブクラスだが、件数や時間の設定として意図した値ではない"""
        loaded, logged = self._load({"info_fetch_debounce_ms": True})
        self.assertEqual(loaded.info_fetch_debounce_ms, config.AppConfig().info_fetch_debounce_ms)
        self.assertEqual(len(logged), 1)

    def test_float_setting_accepts_json_integer(self):
        """秒数のような実数設定は、JSONに整数で書かれていても受け入れる"""
        loaded, logged = self._load({"thumbnail_fetch_timeout_seconds": 8})
        self.assertEqual(loaded.thumbnail_fetch_timeout_seconds, 8.0)
        self.assertIsInstance(loaded.thumbnail_fetch_timeout_seconds, float)
        self.assertEqual(logged, [])


if __name__ == "__main__":
    unittest.main()
