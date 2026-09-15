"""MainWindowのウィジェット生成・レイアウト組み立てのみを担当する。

イベントハンドラの実装(on_xxx等)やシグナル接続はMainWindow側の責務とし、
このモジュールはウィジェントの生成・配置・初期状態の設定にとどめる
(pyuicが生成するUi_MainWindowクラスと同じ役割分担)。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from formats import FORMAT_COLUMN_WIDTHS, FORMAT_OPTION_TOOLTIPS, FORMAT_OPTIONS
from widgets import FormatComboBox, FormatHeaderWidget, FormatItemDelegate, RangeSlider, SpinnerWidget

IDLE_STATUS_TEXT = "待機中"

LINK_BUTTON_STYLE = """
QPushButton {
    color: #1a73e8;
    border: none;
    padding: 2px 4px;
    background: transparent;
}
QPushButton:hover { text-decoration: underline; }
"""


class Ui_MainWindow(QMainWindow):
    """ウィジェント属性はsetup_ui内で代入される(型チェッカ・IDE補完のための宣言)"""

    url_edit: QLineEdit
    paste_btn: QPushButton
    thumbnail_label: QLabel
    title_label: QLabel
    auto_format_container: QWidget
    format_combo: QComboBox
    format_row: QHBoxLayout
    manual_toggle_btn: QPushButton
    auto_format_note_label: QLabel
    manual_container: QWidget
    format_item_delegate: FormatItemDelegate
    format_header: FormatHeaderWidget
    video_format_combo: FormatComboBox
    audio_format_combo: FormatComboBox
    merge_note_label: QLabel
    mp3_checkbox: QCheckBox
    mp3_label: QLabel
    clip_toggle_btn: QPushButton
    clip_container: QWidget
    clip_start_edit: QLineEdit
    clip_end_edit: QLineEdit
    clip_range_slider: RangeSlider
    clip_duration_label: QLabel
    out_edit: QLineEdit
    browse_btn: QPushButton
    download_btn: QPushButton
    cancel_btn: QPushButton
    open_folder_btn: QPushButton
    progress_bar: QProgressBar
    spinner: SpinnerWidget
    status_label: QLabel
    log_toggle_btn: QPushButton
    log_view: QPlainTextEdit
    input_widgets: list[QWidget]

    def setup_ui(self, saved_out_dir: str) -> None:
        self.setWindowTitle("YouTube 動画ダウンローダー")
        self.resize(760, 420)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        # --- URL入力 ---
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL:"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.url_edit.setMinimumHeight(32)
        self.url_edit.setClearButtonEnabled(True)
        url_row.addWidget(self.url_edit, stretch=1)
        self.paste_btn = QPushButton("貼り付け")
        url_row.addWidget(self.paste_btn)
        layout.addLayout(url_row)

        # --- 動画プレビュー ---
        # wordWrap付きQLabelをQHBoxLayout経由で直接QVBoxLayoutに入れると、
        # heightForWidthの計算がずれて縦方向に大きく間延びするため、
        # 高さを固定したコンテナで包んで挙動を安定させる
        preview_container = QWidget()
        preview_container.setFixedHeight(68)
        preview_row = QHBoxLayout(preview_container)
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.setSpacing(10)
        self.thumbnail_label = QLabel(preview_container)
        self.thumbnail_label.setFixedSize(120, 68)
        self.thumbnail_label.setScaledContents(True)
        self.thumbnail_label.setStyleSheet("background-color: rgba(128, 128, 128, 35); border-radius: 3px;")
        preview_row.addWidget(self.thumbnail_label)
        self.title_label = QLabel("", preview_container)
        self.title_label.setWordWrap(True)
        self.title_label.setMaximumHeight(68)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        preview_row.addWidget(self.title_label, stretch=1)
        layout.addWidget(preview_container)

        # --- フォーマット選択 ---
        format_row = QHBoxLayout()
        self.auto_format_container = QWidget()
        auto_layout = QHBoxLayout(self.auto_format_container)
        auto_layout.setContentsMargins(0, 0, 0, 0)
        auto_layout.addWidget(QLabel("形式:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(FORMAT_OPTIONS.keys())
        for i, label in enumerate(FORMAT_OPTIONS.keys()):
            tooltip = FORMAT_OPTION_TOOLTIPS.get(label)
            if tooltip:
                self.format_combo.setItemData(i, tooltip, Qt.ItemDataRole.ToolTipRole)
        auto_layout.addWidget(self.format_combo, stretch=1)
        format_row.addWidget(self.auto_format_container, stretch=1)
        # auto_format_container が非表示のときはこのスペーサーが余白を吸収し、
        # manual_toggle_btn が引き伸ばされて中央寄りに見えるのを防ぐ
        format_row.addStretch(0)
        self.manual_toggle_btn = QPushButton("手動設定 ▾")
        self.manual_toggle_btn.setCheckable(True)
        self.manual_toggle_btn.setFlat(True)
        self.manual_toggle_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.manual_toggle_btn.setStyleSheet(LINK_BUTTON_STYLE)
        format_row.addWidget(self.manual_toggle_btn)
        self.format_row = format_row
        layout.addLayout(format_row)

        self.auto_format_note_label = QLabel("")
        self.auto_format_note_label.setStyleSheet("color: #b06000;")
        self.auto_format_note_label.setVisible(False)
        layout.addWidget(self.auto_format_note_label)

        self.manual_container = QWidget()
        manual_layout = QVBoxLayout(self.manual_container)
        manual_layout.setContentsMargins(0, 2, 0, 0)

        self.format_item_delegate = FormatItemDelegate()
        format_combo_min_width = sum(FORMAT_COLUMN_WIDTHS) + 40

        video_label = QLabel("動画:")
        audio_label = QLabel("音声:")
        label_width = max(video_label.sizeHint().width(), audio_label.sizeHint().width())
        video_label.setFixedWidth(label_width)
        audio_label.setFixedWidth(label_width)

        header_row = QHBoxLayout()
        header_spacer = QLabel("")
        header_spacer.setFixedWidth(label_width)
        header_row.addWidget(header_spacer)
        self.format_header = FormatHeaderWidget()
        header_row.addWidget(self.format_header, stretch=1)
        manual_layout.addLayout(header_row)

        video_row = QHBoxLayout()
        video_row.addWidget(video_label)
        self.video_format_combo = FormatComboBox()
        self.video_format_combo.setEnabled(False)
        self.video_format_combo.setItemDelegate(self.format_item_delegate)
        self.video_format_combo.setMinimumWidth(format_combo_min_width)
        video_row.addWidget(self.video_format_combo, stretch=1)
        manual_layout.addLayout(video_row)

        audio_row = QHBoxLayout()
        audio_row.addWidget(audio_label)
        self.audio_format_combo = FormatComboBox()
        self.audio_format_combo.setEnabled(False)
        self.audio_format_combo.setItemDelegate(self.format_item_delegate)
        self.audio_format_combo.setMinimumWidth(format_combo_min_width)
        audio_row.addWidget(self.audio_format_combo, stretch=1)
        manual_layout.addLayout(audio_row)

        self.merge_note_label = QLabel("")
        manual_layout.addWidget(self.merge_note_label)

        mp3_checkbox_row = QHBoxLayout()
        self.mp3_checkbox = QCheckBox()
        self.mp3_checkbox.setEnabled(False)
        mp3_checkbox_row.addWidget(self.mp3_checkbox)
        self.mp3_label = QLabel("音声のみのダウンロードの場合、mp3に変換する")
        self.mp3_label.setEnabled(False)
        mp3_checkbox_row.addWidget(self.mp3_label)
        mp3_checkbox_row.addStretch()
        manual_layout.addLayout(mp3_checkbox_row)

        layout.addWidget(self.manual_container)
        self.manual_container.setVisible(False)

        # --- 保存先 ---
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("保存先:"))
        self.out_edit = QLineEdit(saved_out_dir)
        out_row.addWidget(self.out_edit, stretch=1)
        self.browse_btn = QPushButton("参照...")
        out_row.addWidget(self.browse_btn)
        layout.addLayout(out_row)

        # --- クリップ範囲(任意) ---
        # 手動設定・ログと同様、大半のユーザーは使わない任意機能のため既定では折りたたんでおき、
        # トグルボタンは手動設定と同じく行の右端に寄せる
        clip_header_row = QHBoxLayout()
        clip_header_row.addStretch()
        self.clip_toggle_btn = QPushButton("クリップ範囲(任意) ▾")
        self.clip_toggle_btn.setCheckable(True)
        self.clip_toggle_btn.setFlat(True)
        self.clip_toggle_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.clip_toggle_btn.setStyleSheet(LINK_BUTTON_STYLE)
        clip_header_row.addWidget(self.clip_toggle_btn)
        layout.addLayout(clip_header_row)

        self.clip_container = QWidget()
        # 動画情報の取得前はスライダー・開始/終了欄・長さラベルをまとめて無効化しておく
        # (トグルボタン自体はclip_containerの外にあるため開閉操作には影響しない)
        self.clip_container.setEnabled(False)
        clip_layout = QVBoxLayout(self.clip_container)
        clip_layout.setContentsMargins(0, 4, 0, 0)
        clip_layout.setSpacing(6)

        # start_edit/end_editをスライダーの両端に離して置くと視線が左右に散るため、
        # 「スライダー→中央に寄せた開始〜終了」の2段に積んで一体感を持たせる。
        # スライダーをコンテナ全幅(728px程度)まで伸ばすと、下の開始〜終了欄(約210px)
        # に対して幅が広すぎて不自然に見えるため、最大幅を設けて中央寄せにする
        clip_slider_row = QHBoxLayout()
        clip_slider_row.setSpacing(8)
        clip_slider_row.addStretch(1)
        # 動画の長さラベルをスライダーの右に置くと、その分だけスライダー自体が
        # コンテナ中央より左に寄って見えるため、同じ幅の透明スペーサーを左側にも
        # 置いてスライダー単体が中央に来るよう相殺する
        self.clip_duration_label = QLabel("")
        self.clip_duration_label.setStyleSheet("color: #808080;")
        # 空文字列から「動画の長さ: ...」に変わると幅が広がり、左右均等のstretchが
        # 詰められてスライダーごと左にずれてしまうため、想定最大幅を先取りして固定する
        duration_metrics = self.clip_duration_label.fontMetrics()
        duration_label_width = duration_metrics.horizontalAdvance("動画の長さ: 99:59:59")
        self.clip_duration_label.setFixedWidth(duration_label_width)
        left_spacer = QWidget()
        left_spacer.setFixedWidth(duration_label_width)
        clip_slider_row.addWidget(left_spacer)
        self.clip_range_slider = RangeSlider()
        self.clip_range_slider.setFixedWidth(320)
        clip_slider_row.addWidget(self.clip_range_slider)
        # 動画の長さは開始・終了欄に入力すると(プレースホルダーが消えて)見えなくなるため、
        # 「合計がいくつか」を示す目盛りの役割としてスライダーの右に固定表示する
        # (開始・終了欄はユーザーが選んだ値であり、動画全体の長さとは意味が異なるため
        # 隣接させると混同しやすい。範囲そのものを表すスライダーに添えるほうが自然)
        clip_slider_row.addWidget(self.clip_duration_label)
        clip_slider_row.addStretch(1)
        clip_layout.addLayout(clip_slider_row)

        # プレースホルダーは入力すると消えてラベルの役目を失うため、
        # 「開始:」「終了:」は恒久的に見えるQLabelとして左に添える
        clip_values_row = QHBoxLayout()
        clip_values_row.setSpacing(6)
        clip_values_row.addStretch(1)
        clip_values_row.addWidget(QLabel("開始:"))
        self.clip_start_edit = QLineEdit()
        # 空欄時は「先頭(0:00)から」が実際のデフォルト動作なので、それをそのまま表示する
        self.clip_start_edit.setPlaceholderText("0:00")
        self.clip_start_edit.setClearButtonEnabled(True)
        self.clip_start_edit.setFixedWidth(90)
        self.clip_start_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        clip_values_row.addWidget(self.clip_start_edit)
        clip_values_row.addWidget(QLabel("終了:"))
        self.clip_end_edit = QLineEdit()
        # 動画の長さが判明するまでは空欄時の実際の値が分からないため、取得後に
        # _update_clip_slider_range で動画の長さをプレースホルダーとして設定する
        self.clip_end_edit.setClearButtonEnabled(True)
        self.clip_end_edit.setFixedWidth(90)
        self.clip_end_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        clip_values_row.addWidget(self.clip_end_edit)
        clip_values_row.addStretch(1)
        clip_layout.addLayout(clip_values_row)

        layout.addWidget(self.clip_container)
        self.clip_container.setVisible(False)

        # --- ダウンロード ---
        btn_row = QHBoxLayout()
        self.download_btn = QPushButton("ダウンロード開始")
        self.download_btn.setMinimumHeight(40)
        download_font = self.download_btn.font()
        download_font.setBold(True)
        self.download_btn.setFont(download_font)
        self.download_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #1a73e8;
                color: white;
                font-weight: bold;
                padding: 6px 16px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #1765cc; }
            QPushButton:pressed { background-color: #145bb5; }
            QPushButton:disabled { background-color: #a7c6f5; color: #f0f0f0; }
            """
        )
        self.download_btn.setEnabled(False)
        btn_row.addWidget(self.download_btn, stretch=1)
        self.cancel_btn = QPushButton("キャンセル")
        self.cancel_btn.setEnabled(False)
        btn_row.addWidget(self.cancel_btn)
        self.open_folder_btn = QPushButton("フォルダを開く")
        self.open_folder_btn.setEnabled(False)
        btn_row.addWidget(self.open_folder_btn)
        layout.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        status_row = QHBoxLayout()
        self.spinner = SpinnerWidget()
        status_row.addWidget(self.spinner)
        self.status_label = QLabel(IDLE_STATUS_TEXT)
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        self.log_toggle_btn = QPushButton("ログ ▾")
        self.log_toggle_btn.setCheckable(True)
        self.log_toggle_btn.setFlat(True)
        self.log_toggle_btn.setStyleSheet(LINK_BUTTON_STYLE)
        status_row.addWidget(self.log_toggle_btn)
        layout.addLayout(status_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setVisible(False)
        layout.addWidget(self.log_view)

        self.input_widgets = [
            self.url_edit,
            self.paste_btn,
            self.out_edit,
            self.browse_btn,
            self.format_combo,
            self.manual_toggle_btn,
            self.video_format_combo,
            self.audio_format_combo,
            self.mp3_checkbox,
            self.mp3_label,
            self.clip_toggle_btn,
            self.clip_container,
        ]
