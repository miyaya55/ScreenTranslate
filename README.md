
## 簡易導入手順(D配下前提)
CMD (コマンドプロンプト)** の例:
cmd (管理者権限での起動が安牌)
cd /d D:\ScreenTranslate
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
setx GEMINI_API_KEY 使用するAPIキー(例 setx GEMINI_API_KEY Azterststexxxxx)

## 簡易起動方法(D:配下にフォルダ置く前提)
cmd (管理者権限での起動が安牌
cd /d D:\ScreenTranslate
.venv\Scripts\activate
set GEMINI_MODEL=gemini-2.5-flash
set OST_SAVE_CAPTURE=1
set OST_DEBUG=1
set OST_GUI_MODE=1(任意。コマンドの代わりにGUIで動くようになる)
set OST_PREPROCESS=0(任意。カラーで画像が設定される)
set OST_GUI_HOTKEYS=1(任意。GUIモードでも一部のキーが有効になる)
set OST_SAVE_ANNOTATED=1(任意。訳文を併記した画像を生成する)
python ScreenTranslate.py

# ScreenTranslate (On-Screen Game Translator)

> 画面の任意範囲を選択 → OCR → **日本語へ翻訳** → その場に表示。  
> 連結キャプチャ、GUI操作、**口調プリセット**（即反映）、原文保持/コピー、
> **画像への併記保存**（下/右）、**キャンセル**、**外観とパネルを環境変数で柔軟カスタム**。

---

## 目次
- [特徴](#特徴)
- [動作環境](#動作環境)
- [インストール](#インストール)
- [APIキー/モデルの設定](#apiキーモデルの設定)
- [起動方法](#起動方法)
- [基本操作フロー](#基本操作フロー)
- [ホットキー（既定）](#ホットキー既定)
- [GUIパネル](#guiパネル)
  - [キー設定（GUIリマップ）](#キー設定guiリマップ)
  - [口調プリセット（即反映）](#口調プリセット即反映)
- [原文の保持とコピー](#原文の保持とコピー)
- [画像への併記保存（原文/訳文）](#画像への併記保存原文訳文)
  - [縦長画像は右側併記（自動/固定）](#縦長画像は右側併記自動固定)
- [外観と挙動のカスタマイズ（環境変数）](#外観と挙動のカスタマイズ環境変数)
  - [ROI枠・テキストボックス](#roi枠テキストボックス)
  - [外置きパネル](#外置きパネル)
  - [ヘルプ/編集ハンドル](#ヘルプ編集ハンドル)
  - [併記PNGの体裁](#併記pngの体裁)
  - [GUIコンパクト化/横幅・ボタン幅](#guiコンパクト化横幅ボタン幅)
  - [その他（モデル/動作）](#その他モデル動作)
- [保存物/ログ](#保存物ログ)
- [トラブルシュート](#トラブルシュート)
- [よく使う設定例](#よく使う設定例)
- [ファイル構成](#ファイル構成)
- [ライセンス](#ライセンス)

---

## 特徴
- 🖼 **ドラッグ選択 → 即翻訳**：画面の任意範囲をOCR → 日本語訳をオーバーレイ表示
- ➕ **連結キャプチャ**：複数枚を**縦連結**し一括翻訳（UIや長文に便利）
- 🧰 **GUIパネル**：ボタン操作、**口調プリセット**
- 🗣 **話者（黄枠）指定**：会話相手の枠を黄枠で指定、口調/話者名も指定
- 📋 **原文保持 & 右クリックコピー**：`{"source","ja"}`で受け取り、訳欄は**jaのみ**表示
- 🖼 **画像として保存**：訳文（＋原文）を**下側**または**右側**に併記したPNGを保存（手動/自動）
- ⏹ **キャンセル**：API中でも**Alt+X**で即キャンセル → 「（キャンセルしました）」を表示
- 🎨 **外観カスタム**：枠色/太さ/角丸/余白、パネル配色、併記PNGの体裁、**パネル横幅/ボタン幅/高さ**を環境変数で変更

---

## 動作環境
- **OS**：Windows 10/11 推奨（グローバルホットキーの互換性）  
- **Python**：3.10 以上推奨
- **依存**：`requirements.txt`（`PySide6`, `Pillow`, `mss`, `keyboard`, `requests`）
---

## インストール
```bash
# 仮想環境は任意
python -m venv .venv
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

---

## APIキーの設定
- `GEMINI_API_KEY` **または** `GOOGLE_API_KEY`（どちらでも可。事前にGoogle AI StudioでAPIキー（無料枠お勧め）を払い出すこと）
- `setx GEMINI API_KEY AIxxxxxxxxx

Windows (PowerShell):
```powershell
$env:GEMINI_API_KEY = 'xxxxxxxxxxxxxxxx'
$env:GEMINI_MODEL   = 'gemini-2.5-flash'
```

---

## 起動方法
### 標準（オーバーレイ）
```bash
python ScreenTranslate.py
```

### GUIパネルあり
```powershell
$env:OST_GUI_MODE = '1'
python .\ScreenTranslate.py
```

> **cmd.exe** は `set NAME=VALUE`（`=`の前後にスペースを入れない）。

---

## 基本操作フロー
1. **範囲選択**（Alt+C）で青枠を作る  
2. 必要なら**話者枠**（Alt+S）を黄枠で指定  
3. **翻訳**（Alt+T） → 訳文が内側/外置きに表示  
4. 右クリックで **コピー**／**画像として保存**（訳のみ / 原文＋訳文）

---

## ホットキー（既定）

| 操作 | 既定キー |
|---|---|
| 翻訳 | **Alt+T** |
| 範囲選択 | **Alt+C** |
| Reader 表示 | **Alt+R** |
| 口調ダイアログ | **Alt+K** |
| 話者ダイアログ | **Alt+F** |
| 話者枠の選択 | **Alt+S** |
| 話者クリア | **Ctrl+Shift+S** |
| 連結に追加 | **Alt+A** |
| 連結クリア | **Alt+D** |
| フォント小/大 | **F1 / F2** |
| 訳文欄 低/高 | **F3 / F4** |
| CAPTURE FULL/EXCLUDE | **F5** |
| 隠す/隠さない（キャプチャ時） | **F6** |
| 外置き/内側（メッセージ位置） | **F7** |
| パネル追従（リセット） | **Shift+F7** |
| 青枠/黄枠 表示 | **F8 / F9** |
| 青枠/黄枠 編集モード | **F10 / F11** |
| 訳文欄 表示/非表示 | **Alt+Z** |
| **キャンセル** | **Alt+X** |
| **終了** | 既定 **Ctrl+Shift+F12**（`OST_EXIT_HOTKEY` で変更可） |

---

## GUIパネル

### 口調プリセット（即反映）
- 上部プルダウンの選択 **＝即本文に反映**  
- 「現在の内容を新規保存…」「このプリセットを削除」  
- 保存先：`ost_tone_presets.json`（初回は既定セットを書き出し）

---

## 原文の保持とコピー
- 既定で `{"source":"原文","ja":"訳文"}` の**JSON返却**を期待  
- JSONでない応答・前後にゴミがつく場合でも**頑強に抽出**（`source/ja`）  
- 訳文欄は **jaのみ表示**、原文は `last_source_text` に保存  
- 右クリックメニュー：**訳文をコピー / 原文をコピー / 原文＋訳文をコピー**

---

## 画像への併記保存（原文/訳文）
- 右クリック → **「画像として保存（訳文のみ）」** / **「画像として保存（原文＋訳文）」**  
  → `captures/annotated_*.png` を保存（日時＋ナノ秒で一意化）
- **自動保存**：`OST_SAVE_ANNOTATED=1`（成功時に自動保存）  
  原文も併記するなら `OST_ANN_INCLUDE_SRC=1`

### 縦長画像は右側併記（自動/固定）
- `OST_ANN_LAYOUT=auto`（既定）：**高さ/幅 ≥ しきい値** なら**右側帯**、未満は**下帯**
- `OST_ANN_LAYOUT=side`：常に右側帯
- `OST_ANN_LAYOUT=bottom`：常に下帯

---

## 外観と挙動のカスタマイズ（環境変数）

> **色**は `"#RRGGBB"` / `"#RRGGBBAA"` / `"r,g,b"` / `"r,g,b,a"` をサポート（0–255）。  
> **数値**は px または pt。未指定は既定値（従来の見た目）。

### ROI枠・テキストボックス
| 変数 | 既定 | 説明 |
|---|---|---|
| `OST_BORDER_COLOR` | `0,210,255,230` | 青枠の色（RGBA） |
| `OST_BORDER_WIDTH` | `3` | 青枠の線の太さ(px) |
| `OST_SPEAKER_COLOR` | `255,210,0,220` | 黄枠の色（RGBA） |
| `OST_TEXT_BG` / `OST_TEXT_FG` | `20,20,20,180` / `240,240,240,255` | ROI内 訳文ボックスの背景/文字色 |
| `OST_TEXT_ROUND` | `8` | 訳文ボックスの角丸(px) |
| `OST_TEXT_MARGIN` | `10` | ROI枠→訳文ボックスの外側余白(px) |
| `OST_TEXT_PADDING_X` / `OST_TEXT_PADDING_Y` | `12` / `8` | 訳文ボックス内の左右/上下パディング(px) |

### 外置きパネル
| 変数 | 既定 | 説明 |
|---|---|---|
| `OST_PANEL_BG` / `OST_PANEL_BORDER` | `20,20,20,200` / `0,210,255,180` | パネルの背景/枠線色 |
| `OST_PANEL_TEXT_PADDING` | `8` | パネル内テキストのパディング(px) |

### ヘルプ/編集ハンドル
| 変数 | 既定 | 説明 |
|---|---|---|
| `OST_HELP_BG` / `OST_HELP_FG` | `0,0,0,120` / `220,220,220,230` | 編集/選択時の暗転/ヘルプ文字色 |
| `OST_HANDLE_FILL` / `OST_HANDLE_STROKE` | `255,255,255,220` / `0,0,0,200` | ハンドルの塗り/縁の色 |

### 併記PNGの体裁
| 変数 | 既定 | 説明 |
|---|---|---|
| `OST_SAVE_ANNOTATED` | `0` | 1で翻訳成功時に自動保存 |
| `OST_ANN_INCLUDE_SRC` | `0` | 1で原文も併記 |
| `OST_ANN_LAYOUT` | `auto` | `auto`/`side`/`bottom` |
| `OST_ANN_SIDE_THRESHOLD` | `1.6` | **高さ/幅 ≥ 値** で横併記 |
| `OST_ANN_SIDE_WIDTH` | `420` | 右側帯の幅(px) |
| `OST_ANN_MARGIN` / `OST_ANN_PAD` / `OST_ANN_GAP` | `16` / `12` / `10` | 帯の左右余白/上下パディング/原文→訳文の間隔(px) |
| `OST_ANN_ALPHA` | `180` | 帯の不透明度（0–255） |
| `OST_ANN_FONT_JA_PT` / `OST_ANN_FONT_SRC_PT` | `0` / `0` | 0=既定（UI基準から算出）、数値指定でpt固定 |

### GUIコンパクト化/横幅・ボタン幅
| 変数 | 既定 | 説明 |
|---|---|---|
| `OST_GUI_MODE` | `0` | 1でGUIパネル表示 |
| `OST_GUI_COMPACT` | `1` | 1で余白/間隔/高さを圧縮 |
| `OST_GUI_PANEL_W` | `720` | **パネル全体の横幅(px)** |
| `OST_GUI_BTN_W` | `0` | 全ボタンの**固定幅(px)**（0=自動） |
| `OST_GUI_BTN_H` | `28` | ボタン高さ(px) |
| `OST_GUI_SPACING` | `6` | ウィジェット間隔(px) |
| `OST_GUI_MARGINS` | `6,6,6,6` | 外周マージン(L,T,R,B) |

> コンパクトON時はボタンの `padding` も軽く圧縮（`2px 6px`）して見た目を詰めています。  
> **注意**：現状のパネルは上段が**3列グリッド固定**のため、列数は変わりません（自動折返しは未実装）。

### その他（モデル/動作）
| 変数 | 既定 | 説明 |
|---|---|---|
| `OST_KEEP_SOURCE` | `1` | JSONで `{"source","ja"}` を受け取り原文保持 |
| `OST_FONT_PT` | `12` | UI全体の基準フォントpt |
| `OST_CAPTURE_FULL` | `1` | 0でUI表示領域をキャプチャから除外 |
| `OST_HIDE_ON_CAPTURE` | `1` | 1でキャプチャ瞬間にUIを隠す |
| `OST_SAVE_CAPTURE` | `0` | 1で送信実画像（used\_main\_*.png 等）も保存 |
| `OST_CONCAT_MAX` | `10` | 連結の最大枚数 |
| `OST_EXIT_HOTKEY` | `ctrl+shift+f12` | 終了ホットキー |

---

## 保存物/ログ
- `captures/concat_current.png` … 連結プレビュー（翻訳完了時に日付付けで保管）  
- `captures/annotated_*.png` … 併記保存ファイル（手動/自動）  
- `captures/used_main_*.png` … 送信用実画像（必要時のみ）  
- `captures/history.tsv` … `timestamp \t source \t ja` を追記

---

## トラブルシュート
- **「APIキー未設定」**  
  `GEMINI_API_KEY` **または** `GOOGLE_API_KEY` を設定して再実行してください。  
- **ホットキーが効かない**  
  OSの権限や他アプリのグローバルショートカットと衝突している可能性があります。必要に応じて管理者権限や権限付与、`OST_EXIT_HOTKEY` の変更をご検討ください。  
- **うまくOCRできない／文字が薄い**  
  `OST_PREPROCESS=1`（既定）でコントラスト強調が入ります。枠をよりタイトに取る、解像度の高い領域を選ぶ、連結で大きく合成するなども有効です。  
- **訳文が出ない／空応答**  
  ネットワークやAPIレートの影響が考えられます。内部でリトライ（指数バックオフ）を行いますが、改善しない場合はしばらくしてから再試行してください。

---

## よく使う設定例

### 右側併記＋訳文を大きめに
```powershell
$env:OST_ANN_LAYOUT = 'side'
$env:OST_ANN_SIDE_WIDTH = '480'
$env:OST_ANN_FONT_JA_PT = '20'
python .\ScreenTranslate.py
```

### 見た目を濃く・角丸に
```bat
set OST_BORDER_COLOR=#00D2FF
set OST_BORDER_WIDTH=4
set OST_TEXT_BG=0,0,0,200
set OST_TEXT_ROUND=12
python ScreenTranslate.py
```

### パネルをコンパクト＆狭幅＋ボタン幅固定
```powershell
$env:OST_GUI_MODE = '1'
$env:OST_GUI_COMPACT = '1'
$env:OST_GUI_PANEL_W = '560'
$env:OST_GUI_BTN_W = '150'
$env:OST_GUI_BTN_H = '24'
$env:OST_GUI_SPACING = '4'
$env:OST_GUI_MARGINS = '4,4,4,4'
python .\ScreenTranslate.py
```

---

## ファイル構成
- `ScreenTranslate.py` … メイン（最新）
- `requirements.txt` … 依存
- `captures/` … 各種保存物（キャプチャ/併記画像/履歴）
- `ost_tone_presets.json` … 口調プリセット

---

## ライセンス
プロジェクト方針に合わせて選択してください（例：MIT）。
