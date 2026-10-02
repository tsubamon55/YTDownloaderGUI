"""アプリ全体で共有する配色。色を変えるときはここだけを書き換える"""

# ボタン・スピナー・スライダー等の強調色(Googleブルー)と、ボタンの状態ごとの濃淡
ACCENT_COLOR = "#1a73e8"
ACCENT_HOVER_COLOR = "#1765cc"
ACCENT_PRESSED_COLOR = "#145bb5"
ACCENT_DISABLED_COLOR = "#a7c6f5"
ON_ACCENT_TEXT_COLOR = "#ffffff"
ON_ACCENT_DISABLED_TEXT_COLOR = "#f0f0f0"

# 注意を促す補足(画質が制限される旨など)と、控えめな補足(動画の長さ等)の文字色
WARNING_TEXT_COLOR = "#b06000"
MUTED_TEXT_COLOR = "#808080"

# スライダーの溝・無効時の色・一覧の非推奨行など、ライト/ダーク両方の背景に馴染む半透明のグレー
NEUTRAL_RGB = (128, 128, 128)
PLACEHOLDER_BACKGROUND = "rgba(128, 128, 128, 35)"
DISABLED_CONTROL_RGB = (160, 160, 160)

# スライダーのドラッグ中に出すプレビューのポップアップ(背景を問わず読める暗い地に白文字)
POPUP_BACKGROUND_COLOR = "#202124"
POPUP_IMAGE_BACKGROUND = "rgba(255, 255, 255, 30)"
POPUP_TEXT_COLOR = "#ffffff"
