"""MainWindowのウィジェット生成・レイアウト組み立てのみを担当する。

イベントハンドラの実装(on_xxx等)やシグナル接続はMainWindow側の責務とし、
このモジュールはウィジェットの生成・配置・初期状態の設定にとどめる
(pyuicが生成するUi_MainWindowクラスと同じ役割分担。ただしpyuicのように別オブジェクトに組み込むのではなく、
QMainWindowを継承してMainWindowの基底クラスとして使う)。
"""

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from formats import FORMAT_COLUMN_WIDTHS, FORMAT_OPTIONS
from theme import (
    ACCENT_COLOR,
    ACCENT_DISABLED_COLOR,
    ACCENT_HOVER_COLOR,
    ACCENT_PRESSED_COLOR,
    LINK_BUTTON_STYLE,
    LIVE_COLOR,
    MUTED_TEXT_COLOR,
    ON_ACCENT_DISABLED_TEXT_COLOR,
    ON_ACCENT_TEXT_COLOR,
    PLACEHOLDER_BACKGROUND,
    WARNING_TEXT_COLOR,
)
from widgets import FormatComboBox, FormatHeaderWidget, FormatItemDelegate, RangeSlider, SpinnerWidget

IDLE_STATUS_TEXT = "待機中"

APP_TITLE = "YouTube 動画ダウンローダー"

DOWNLOAD_BUTTON_TEXT = "ダウンロード開始"
# 配信中のライブを表示している間のダウンロードボタンの文言
RECORD_BUTTON_TEXT = "録画開始"

THUMBNAIL_SIZE = QSize(120, 68)

# ウィンドウの既定の幅。ダウンロード一覧を表示するとその分だけ広がるため、入力欄側は控えめにする
DEFAULT_WINDOW_WIDTH = 560

# 画面右上に並べるダウンロード一覧の幅
DOWNLOAD_PANEL_WIDTH = 240


def window_title(version: str | None) -> str:
    """ウィンドウタイトル(バージョンが分からない場合はアプリ名のみ)"""
    return f"{APP_TITLE} v{version}" if version else APP_TITLE


def toggle_button_text(label: str, expanded: bool) -> str:
    """折りたたみ式のトグルボタンの表示(展開中は▴、折りたたみ中は▾)"""
    return f"{label} {'▴' if expanded else '▾'}"


class Ui_MainWindow(QMainWindow):
    """ウィジェット属性はsetup_ui内で代入される(型チェッカ・IDE補完のための宣言)"""

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
    detail_toggle_btn: QPushButton
    detail_container: QWidget
    clip_start_edit: QLineEdit
    clip_end_edit: QLineEdit
    clip_range_slider: RangeSlider
    clip_duration_label: QLabel
    out_edit: QLineEdit
    browse_btn: QPushButton
    download_btn: QPushButton
    spinner: SpinnerWidget
    status_label: QLabel
    log_toggle_btn: QPushButton
    log_view: QPlainTextEdit
    live_header: QWidget
    live_badge_label: QLabel
    live_elapsed_label: QLabel
    live_start_container: QWidget
    live_from_start_radio: QRadioButton
    live_from_now_radio: QRadioButton
    live_unavailable_label: QLabel
    download_panel: QWidget
    download_list_layout: QVBoxLayout

    @staticmethod
    def _make_toggle_button(text: str, fixed_size: bool = True) -> QPushButton:
        """詳細表示を開閉するリンク風のトグルボタン"""
        button = QPushButton(text)
        button.setCheckable(True)
        button.setFlat(True)
        if fixed_size:
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setStyleSheet(LINK_BUTTON_STYLE)
        return button

    def _make_format_combo(self, min_width: int) -> FormatComboBox:
        combo = FormatComboBox()
        combo.setEnabled(False)
        combo.setItemDelegate(self.format_item_delegate)
        combo.setMinimumWidth(min_width)
        return combo

    def setup_ui(self, saved_out_dir: str, version: str | None = None) -> None:
        self.setWindowTitle(window_title(version))
        self.resize(DEFAULT_WINDOW_WIDTH, 420)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(16, 14, 16, 14)
        root_layout.setSpacing(12)
        layout = QVBoxLayout()
        layout.setSpacing(8)
        root_layout.addLayout(layout, stretch=1)

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
        preview_container.setFixedHeight(THUMBNAIL_SIZE.height())
        preview_row = QHBoxLayout(preview_container)
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.setSpacing(10)
        self.thumbnail_label = QLabel(preview_container)
        self.thumbnail_label.setFixedSize(THUMBNAIL_SIZE)
        # 表示枠ぴったりの画像をMainWindow._fit_thumbnail_pixmapが作るため、setScaledContentsで
        # さらに引き伸ばさない(二重に拡縮すると、高DPI画面でぼやける)
        self.thumbnail_label.setStyleSheet(f"background-color: {PLACEHOLDER_BACKGROUND}; border-radius: 3px;")
        preview_row.addWidget(self.thumbnail_label)
        # タイトルの上に、配信中のライブのときだけLIVE見出し(バッジ・配信開始からの経過時間)を出す。
        # 上下の伸縮で、見出しの有無にかかわらずタイトルを縦方向の中央に置く
        text_column = QWidget(preview_container)
        text_layout = QVBoxLayout(text_column)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        text_layout.addStretch(1)
        self.live_header = QWidget()
        live_header_layout = QHBoxLayout(self.live_header)
        live_header_layout.setContentsMargins(0, 0, 0, 0)
        live_header_layout.setSpacing(8)
        self.live_badge_label = QLabel("● LIVE")
        self.live_badge_label.setStyleSheet(f"color: {LIVE_COLOR}; font-weight: bold;")
        live_header_layout.addWidget(self.live_badge_label)
        self.live_elapsed_label = QLabel("")
        self.live_elapsed_label.setTextFormat(Qt.TextFormat.PlainText)
        self.live_elapsed_label.setStyleSheet(f"color: {MUTED_TEXT_COLOR};")
        live_header_layout.addWidget(self.live_elapsed_label)
        live_header_layout.addStretch(1)
        self.live_header.setFixedHeight(self.live_badge_label.sizeHint().height())
        self.live_header.setVisible(False)
        text_layout.addWidget(self.live_header)
        # 動画タイトルは外部由来の文字列のため、"<b>"等がHTMLとして描画されないようにする
        # (他の内容が変わるラベルも同様。dialogs参照)
        self.title_label = QLabel("")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setWordWrap(True)
        self.title_label.setMaximumHeight(THUMBNAIL_SIZE.height())
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        text_layout.addWidget(self.title_label)
        text_layout.addStretch(1)
        preview_row.addWidget(text_column, stretch=1)
        layout.addWidget(preview_container)

        # --- フォーマット選択 ---
        format_row = QHBoxLayout()
        self.auto_format_container = QWidget()
        auto_layout = QHBoxLayout(self.auto_format_container)
        auto_layout.setContentsMargins(0, 0, 0, 0)
        auto_layout.addWidget(QLabel("形式:"))
        self.format_combo = QComboBox()
        for option in FORMAT_OPTIONS:
            self.format_combo.addItem(option.label, userData=option.key)
            self.format_combo.setItemData(
                self.format_combo.count() - 1, option.tooltip, Qt.ItemDataRole.ToolTipRole
            )
        auto_layout.addWidget(self.format_combo, stretch=1)
        format_row.addWidget(self.auto_format_container, stretch=1)
        # auto_format_container が非表示のときはこのスペーサーが余白を吸収し、
        # manual_toggle_btn が引き伸ばされて中央寄りに見えるのを防ぐ
        format_row.addStretch(0)
        self.manual_toggle_btn = self._make_toggle_button("手動設定 ▾")
        format_row.addWidget(self.manual_toggle_btn)
        self.format_row = format_row
        layout.addLayout(format_row)

        self.auto_format_note_label = QLabel("")
        self.auto_format_note_label.setTextFormat(Qt.TextFormat.PlainText)
        self.auto_format_note_label.setStyleSheet(f"color: {WARNING_TEXT_COLOR};")
        self.auto_format_note_label.setVisible(False)
        layout.addWidget(self.auto_format_note_label)

        # --- 録画開始位置(配信中のライブのときだけ) ---
        # 選択肢が2つだけのため、コンボではなく両方が常に見えるラジオボタンにする
        self.live_start_container = QWidget()
        live_start_layout = QHBoxLayout(self.live_start_container)
        live_start_layout.setContentsMargins(0, 0, 0, 0)
        live_start_layout.addWidget(QLabel("録画開始位置:"))
        self.live_from_start_radio = QRadioButton("配信の最初から")
        self.live_from_start_radio.setChecked(True)
        live_start_layout.addWidget(self.live_from_start_radio)
        self.live_from_now_radio = QRadioButton("今から")
        live_start_layout.addWidget(self.live_from_now_radio)
        # さかのぼれない配信では、選べない選択肢を並べる代わりにこの一文だけを出す
        self.live_unavailable_label = QLabel("今から録画します（この配信はさかのぼって保存できません）")
        self.live_unavailable_label.setStyleSheet(f"color: {MUTED_TEXT_COLOR};")
        live_start_layout.addWidget(self.live_unavailable_label)
        live_start_layout.addStretch(1)
        self.live_start_container.setVisible(False)
        layout.addWidget(self.live_start_container)

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
        self.video_format_combo = self._make_format_combo(format_combo_min_width)
        video_row.addWidget(self.video_format_combo, stretch=1)
        manual_layout.addLayout(video_row)

        audio_row = QHBoxLayout()
        audio_row.addWidget(audio_label)
        self.audio_format_combo = self._make_format_combo(format_combo_min_width)
        audio_row.addWidget(self.audio_format_combo, stretch=1)
        manual_layout.addLayout(audio_row)

        self.merge_note_label = QLabel("")
        self.merge_note_label.setTextFormat(Qt.TextFormat.PlainText)
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

        # --- 詳細設定(任意) ---
        # 手動設定・ログと同様、大半のユーザーは使わない任意機能のため既定では折りたたんでおき、
        # トグルボタンは手動設定と同じく行の右端に寄せる
        detail_header_row = QHBoxLayout()
        detail_header_row.addStretch()
        self.detail_toggle_btn = self._make_toggle_button(toggle_button_text("詳細設定", False))
        detail_header_row.addWidget(self.detail_toggle_btn)
        layout.addLayout(detail_header_row)

        self.detail_container = QWidget()
        # 動画情報の取得前はスライダー・開始/終了欄・長さラベルをまとめて無効化しておく
        # (トグルボタン自体はdetail_containerの外にあるため開閉操作には影響しない)
        self.detail_container.setEnabled(False)
        detail_layout = QVBoxLayout(self.detail_container)
        detail_layout.setContentsMargins(0, 4, 0, 0)
        detail_layout.setSpacing(6)

        # 現時点では詳細設定の中身は切り抜き範囲のみだが、トグル名自体からは
        # 何が展開されるか分からないため、見出しラベルで内容を明示する。
        # 動画の長さは同じ見出し行の右端に添え、スライダー行には含めない
        # (含めるとスライダー自体を打ち消すためのスペーサーが必要になり、
        # 結果としてスライダーが下の開始〜終了欄と揃わず不自然にずれて見えるため)
        clip_heading_row = QHBoxLayout()
        clip_heading_row.addWidget(QLabel("切り抜き範囲:"))
        clip_heading_row.addStretch(1)
        self.clip_duration_label = QLabel("")
        self.clip_duration_label.setTextFormat(Qt.TextFormat.PlainText)
        self.clip_duration_label.setStyleSheet(f"color: {MUTED_TEXT_COLOR};")
        clip_heading_row.addWidget(self.clip_duration_label)
        detail_layout.addLayout(clip_heading_row)

        # 保存先欄・形式コンボ等の他の行と同じく、左端からコンテナ全幅まで
        # 伸ばして揃える(ドラッグ操作の分解能も上がる)
        self.clip_range_slider = RangeSlider()
        detail_layout.addWidget(self.clip_range_slider)

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
        detail_layout.addLayout(clip_values_row)

        layout.addWidget(self.detail_container)
        self.detail_container.setVisible(False)

        # --- ダウンロード ---
        btn_row = QHBoxLayout()
        self.download_btn = QPushButton(DOWNLOAD_BUTTON_TEXT)
        self.download_btn.setMinimumHeight(40)
        download_font = self.download_btn.font()
        download_font.setBold(True)
        self.download_btn.setFont(download_font)
        self.download_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {ACCENT_COLOR};
                color: {ON_ACCENT_TEXT_COLOR};
                font-weight: bold;
                padding: 6px 16px;
                border: none;
                border-radius: 4px;
            }}
            QPushButton:hover {{ background-color: {ACCENT_HOVER_COLOR}; }}
            QPushButton:pressed {{ background-color: {ACCENT_PRESSED_COLOR}; }}
            QPushButton:disabled {{
                background-color: {ACCENT_DISABLED_COLOR}; color: {ON_ACCENT_DISABLED_TEXT_COLOR};
            }}
            """
        )
        self.download_btn.setEnabled(False)
        # 保存したファイルは、画面右上の一覧の各表示にある「フォルダを開く」から開く
        btn_row.addWidget(self.download_btn, stretch=1)
        layout.addLayout(btn_row)

        status_row = QHBoxLayout()
        self.spinner = SpinnerWidget()
        status_row.addWidget(self.spinner)
        self.status_label = QLabel(IDLE_STATUS_TEXT)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        self.log_toggle_btn = self._make_toggle_button(toggle_button_text("ログ", False), fixed_size=False)
        status_row.addWidget(self.log_toggle_btn)
        layout.addLayout(status_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setVisible(False)
        layout.addWidget(self.log_view)

        root_layout.addWidget(self._build_download_panel())

    def _build_download_panel(self) -> QWidget:
        """ダウンロード開始ごとに1件ずつ積まれる一覧(画面右上)。1件もない間は隠しておく"""
        self.download_panel = QWidget()
        self.download_panel.setFixedWidth(DOWNLOAD_PANEL_WIDTH)
        # 一覧の件数でウィンドウが縦に伸びないよう、高さは左側の入力欄に合わせて決まるようにする
        self.download_panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Ignored)
        panel_layout = QVBoxLayout(self.download_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(4)
        panel_layout.addWidget(QLabel("ダウンロード"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_container = QWidget()
        self.download_list_layout = QVBoxLayout(list_container)
        self.download_list_layout.setContentsMargins(0, 0, 0, 0)
        self.download_list_layout.setSpacing(6)
        # 新しいものを上に積み、残りの余白は下に寄せる
        self.download_list_layout.addStretch(1)
        scroll.setWidget(list_container)
        panel_layout.addWidget(scroll, stretch=1)

        self.download_panel.setVisible(False)
        return self.download_panel
