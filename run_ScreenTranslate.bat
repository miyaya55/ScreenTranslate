REM APIキーをbat内で指定する場合は、公開前に削除する事
REM 必要ならモデルや挙動を環境変数で調整（コードが参照）
set "GEMINI_MODEL=gemini-2.5-flash"
set "OST_SAVE_CAPTURE=1"
set "OST_GUI_MODE=1"
set "OST_PREPROCESS=0"
set "OST_GUI_HOTKEYS=1"
set "OST_PRIMARY_ONLY=1"
set "OST_SAVE_ANNOTATED=1"
set "OST_ANN_FONT_JA_PT=20"
set "OST_ANN_SIDE_WIDTH=800"

start "" "%~dp0ScreenTranslate.exe"