# -*- coding: utf-8 -*-
"""
Screen Translate (Gemini API)
---------------------------------------------------------------------------
- F10/F11（編集モード）: 暗転あり（従来の見え方）
- Auto-Edit（直接ドラッグ）: 暗転なし／ハンドルでリサイズ、縁だけ移動（内部ドラッグ移動は既定OFF）
- **ALT+Z**: 訳文欄の表示/非表示トグル（外置き/内側どちらも制御、ReaderはRで別トグル）
- 連結/GUI/外置きパネル/話者・口調/安全終了 等は従来の v4.8 系の安定化設計のまま

参考: v4.8R12c/d の UIスレッド設計（Signal/Slot 適用）と Gemini v1beta 呼び出しを踏襲。
"""

from dataclasses import dataclass
import base64, io, os, sys, threading, time, json, re
from typing import Optional, Dict, List
import requests
from PIL import Image, ImageEnhance, ImageDraw, ImageFont, ImageFilter

from PySide6.QtCore import Qt, QRect, QTimer, QPoint, QCoreApplication, QThread, Signal, Slot, QSize
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QGuiApplication, QCursor, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QWidget, QTextEdit, QDialog, QVBoxLayout, QLabel, QDialogButtonBox, QLineEdit,
    QPushButton, QGridLayout, QHBoxLayout, QCheckBox, QMenu, QKeySequenceEdit,
    QComboBox, QInputDialog, QMessageBox, QSizePolicy, QFileDialog, QListWidget, QListWidgetItem, QListView, QAbstractItemView, QSpacerItem  # ← 追加
)

import mss, keyboard


# === 設定 ===
# === 外観設定（環境変数で変更可能） ===
# 例: OST_BORDER_COLOR="#00d2ff" / "0,210,255,230" など。RGBA(0-255)。

def _parse_color_string(s, default_rgba):
    """'#RRGGBB' or '#RRGGBBAA' or 'r,g,b' or 'r,g,b,a' -> (r,g,b,a)"""
    if not s:
        return default_rgba
    s = s.strip()
    try:
        if s.startswith("#"):
            hx = s[1:]
            if len(hx) == 6:
                r = int(hx[0:2], 16); g = int(hx[2:4], 16); b = int(hx[4:6], 16); a = default_rgba[3]
                return (r,g,b,a)
            if len(hx) == 8:
                r = int(hx[0:2], 16); g = int(hx[2:4], 16); b = int(hx[4:6], 16); a = int(hx[6:8], 16)
                return (r,g,b,a)
        # allow comma/space separated
        parts = [p for p in re.split(r'[,\s]+', s) if p]
        if len(parts) in (3,4):
            r = int(parts[0]); g = int(parts[1]); b = int(parts[2]); a = int(parts[3]) if len(parts) == 4 else default_rgba[3]
            r = max(0, min(255, r)); g = max(0, min(255, g)); b = max(0, min(255, b)); a = max(0, min(255, a))
            return (r,g,b,a)
    except Exception:
        pass
    return default_rgba

def _env_qcolor(name, default_rgba):
    s = os.environ.get(name, "").strip()
    from PySide6.QtGui import QColor
    r,g,b,a = _parse_color_string(s, default_rgba)
    return QColor(r,g,b,a)

# 枠色・太さ
BORDER_WIDTH_PX     = int(os.environ.get("OST_BORDER_WIDTH", "3"))
MAIN_BORDER_COLOR   = _env_qcolor("OST_BORDER_COLOR",    (0,210,255,230))  # 青系
SPEAKER_BORDER_COLOR= _env_qcolor("OST_SPEAKER_COLOR",   (255,210,0,220))  # 黄系

# 訳文欄（内側表示）
TEXT_BG_COLOR       = _env_qcolor("OST_TEXT_BG", (20,20,20,180))
TEXT_FG_COLOR       = _env_qcolor("OST_TEXT_FG", (240,240,240,255))
TEXT_ROUND_R_PX     = int(os.environ.get("OST_TEXT_ROUND", "8"))
TEXT_MARGIN_PX      = int(os.environ.get("OST_TEXT_MARGIN", "10"))   # ROI内側の余白
TEXT_PADDING_X_PX   = int(os.environ.get("OST_TEXT_PADDING_X", "12"))
TEXT_PADDING_Y_PX   = int(os.environ.get("OST_TEXT_PADDING_Y", "8"))

# パネル（外置き）
PANEL_TEXT_PADDING_PX = int(os.environ.get("OST_PANEL_TEXT_PADDING", "8"))
PANEL_BG_COLOR        = _env_qcolor("OST_PANEL_BG",     (20,20,20,200))
PANEL_BORDER_COLOR    = _env_qcolor("OST_PANEL_BORDER", (0,210,255,180))

# ヘルプ/ハンドルなど
HELP_BG_COLOR      = _env_qcolor("OST_HELP_BG",   (0,0,0,120))
HELP_FG_COLOR      = _env_qcolor("OST_HELP_FG",   (220,220,220,230))
HANDLE_FILL_COLOR  = _env_qcolor("OST_HANDLE_FILL",(255,255,255,220))
HANDLE_STROKE_COLOR= _env_qcolor("OST_HANDLE_STROKE",(0,0,0,200))

# 併記画像（annotated）の余白・透明度・フォント（存在する場合に使用）
ANN_MARGIN_PX      = int(os.environ.get("OST_ANN_MARGIN", "16"))
ANN_PAD_PX         = int(os.environ.get("OST_ANN_PAD", "12"))
ANN_GAP_PX         = int(os.environ.get("OST_ANN_GAP", "10"))
ANN_ALPHA          = max(0, min(255, int(os.environ.get("OST_ANN_ALPHA", "180"))))
ANN_FONT_JA_PT     = int(os.environ.get("OST_ANN_FONT_JA_PT", "0"))  # 0=既定（self.font_pt+2）
ANN_FONT_SRC_PT    = int(os.environ.get("OST_ANN_FONT_SRC_PT", "0"))
ANN_LAYOUT         = os.environ.get("OST_ANN_LAYOUT", "auto").strip().lower()  # auto|side|bottom
ANN_SIDE_THRESHOLD = float(os.environ.get("OST_ANN_SIDE_THRESHOLD", "1.6"))     # 高さ/幅 がこれ以上なら横
ANN_SIDE_WIDTH_PX  = int(os.environ.get("OST_ANN_SIDE_WIDTH", "420"))           # 右帯幅(px)
 # 0=既定（self.font_pt）

API_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
API_VERSION = "v1beta" if "2.5" in API_MODEL else "v1"
API_ENDPOINT = f"https://generativelanguage.googleapis.com/{API_VERSION}/models/{API_MODEL}:generateContent"

# メイン画面だけを対象にするモード（1で有効）
OST_PRIMARY_ONLY = os.environ.get("OST_PRIMARY_ONLY", "0") == "1"
# mss のモニタ番号（通常は 1 がプライマリ）
OST_MON_INDEX = int(os.environ.get("OST_MON_INDEX", "1"))
OST_PREPROCESS = os.environ.get("OST_PREPROCESS", "1") != "0"
OST_SAVE_CAPTURE = os.environ.get("OST_SAVE_CAPTURE", "0") == "1"
OST_HIDE_ON_CAPTURE = os.environ.get("OST_HIDE_ON_CAPTURE", "1") == "1"
OST_CAPTURE_FULL = os.environ.get("OST_CAPTURE_FULL", "1") == "1"

# --- GUI compact options ---
OST_GUI_COMPACT = os.environ.get("OST_GUI_COMPACT", "1") == "1"  # 1=compact, 0=legacy layout
OST_GUI_BTN_H = int(os.environ.get("OST_GUI_BTN_H", "28"))
OST_GUI_SPACING = int(os.environ.get("OST_GUI_SPACING", "6"))
def _parse_margins(s, default=(6,6,6,6)):
    try:
        if not isinstance(s, str):
            try:
                s = str(s)
            except Exception:
                s = ""
        parts = [int(x) for x in re.split(r"[ ,]+", s.strip()) if x]
        if len(parts) == 4:
            return tuple(parts)
    except Exception:
        pass
    return default
OST_GUI_MARGINS = _parse_margins(os.environ.get("OST_GUI_MARGINS", "6,6,6,6"))
OST_GUI_BTN_W = int(os.environ.get("OST_GUI_BTN_W", "0"))  # 0=auto, >0: 固定幅(px)
OST_GUI_PANEL_W = int(os.environ.get("OST_GUI_PANEL_W", "720"))  # 操作パネルの横幅(px)
# 訳文併記画像の自動保存（成功時）
OST_SAVE_ANNOTATED = os.environ.get("OST_SAVE_ANNOTATED", "0") == "1"
# 自動保存のとき原文も含めるか
OST_ANN_INCLUDE_SRC = os.environ.get("OST_ANN_INCLUDE_SRC", "0") == "1"
# 原文も保持してコピーできるよう、JSON出力を使うフラグ（既定ON）
KEEP_SOURCE = os.environ.get("OST_KEEP_SOURCE", "1") == "1"
# 有名テキストでRECITATIONが出たら自動で『訳文のみ』に落とす（1=有効）
OST_RECITATION_AUTO = os.environ.get("OST_RECITATION_AUTO", "1") == "1"
OST_SLICE_ON_RECITATION = os.environ.get("OST_SLICE_ON_RECITATION", "1") == "1"
OST_SLICE_PARTS = max(2, int(os.environ.get("OST_SLICE_PARTS", "3")))
# JA-only再試行を行うか (0: 行わず直接スライス, 1: 行う)。既定は 0
OST_RECITATION_JA_RETRY = os.environ.get("OST_RECITATION_JA_RETRY", "0") == "1"

DEFAULT_TEXT_RATIO = float(os.environ.get("OST_TEXT_RATIO", "0.28"))
DEFAULT_FONT_PT = int(os.environ.get("OST_FONT_PT", "12"))
DEFAULT_TONE = os.environ.get("OST_TONE", "")
DEFAULT_SPEAKER = os.environ.get("OST_SPEAKER", "")
OST_MSG_OUTSIDE = os.environ.get("OST_MSG_OUTSIDE", "1") == "1"
EXIT_HOTKEY = os.environ.get("OST_EXIT_HOTKEY", "ctrl+shift+f12").strip().lower()

CONNECT_TIMEOUT = float(os.environ.get("OST_HTTP_CONNECT_TIMEOUT", "12"))
READ_TIMEOUT    = float(os.environ.get("OST_HTTP_READ_TIMEOUT", "120"))
DEBUG           = os.environ.get("OST_DEBUG", "0") == "1"
POLL_ON         = os.environ.get("OST_POLL", "1") == "1"

# GUI モード
OST_GUI_MODE = os.environ.get("OST_GUI_MODE", "0") == "1"
# ★追加: GUIモードでもキーボードのコマンド（Alt+T 等）を有効にするフラグ
#  既定は 0（無効）。1 にすると GUI モードでもホットキーを登録/ポーリングします。
OST_GUI_HOTKEYS = os.environ.get("OST_GUI_HOTKEYS", "0") == "1"

# Concat
CONCAT_MAX     = int(os.environ.get("OST_CONCAT_MAX", "8"))
CONCAT_GAP_PX  = int(os.environ.get("OST_CONCAT_GAP", "6"))
CONCAT_MODE_L  = os.environ.get("OST_CONCAT_MODE", "L").upper()  # L or RGB

# 外置きパネル最小サイズ & ドラッグバー高
PANEL_MIN_W = int(os.environ.get("OST_PANEL_MIN_W", "280"))
PANEL_MIN_H = int(os.environ.get("OST_PANEL_MIN_H", "160"))
PANEL_DRAG_BAR_H = int(os.environ.get("OST_PANEL_DRAG_BAR_H", "18"))

# 枠の初期表示（環境変数で制御可能）
SHOW_MAIN_FRAME_DEFAULT    = os.environ.get("OST_SHOW_MAIN_FRAME", "1") == "1"
SHOW_SPEAKER_FRAME_DEFAULT = os.environ.get("OST_SHOW_SPEAKER_FRAME", "1") == "1"

# 訳文欄の表示初期値
SHOW_MSG_DEFAULT = os.environ.get("OST_MSG_VISIBLE", "1") == "1"

# ROI 編集用ハンドル
HANDLE_SIZE = int(os.environ.get("OST_HANDLE_SIZE", "12"))
ROI_MIN_W   = int(os.environ.get("OST_ROI_MIN_W", "40"))
ROI_MIN_H   = int(os.environ.get("OST_ROI_MIN_H", "30"))
HANDLE_HOT  = int(os.environ.get("OST_HANDLE_HOT", "6"))  # 当たり判定の拡張

# Auto-Edit（直接ドラッグ）
AUTO_EDIT           = os.environ.get("OST_ROI_AUTO_EDIT", "1") == "1"
AUTO_EDIT_MOVE      = os.environ.get("OST_ROI_AUTO_MOVE", "0") == "1"  # 内部ドラッグ移動（既定OFF）
BORDER_MOVE_ENABLE  = os.environ.get("OST_ROI_BORDER_MOVE", "1") == "1"  # 縁での移動（既定ON）
MOVE_BAND           = int(os.environ.get("OST_MOVE_BAND", "8"))  # 縁の幅(px)


# --- Tone preset switching (Lite/Pro) ---
OST_TONE_PRESET_MODE = os.environ.get("OST_TONE_PRESET_MODE", "lite").strip().lower()
def _tone_preset_file_for_mode(mode: str) -> str:
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.join(base_dir, "ost_tone_presets_v2.json" if (mode or "").strip().lower()=="pro" else "ost_tone_presets.json")

# --- Game-specific preset packs ---
TONE_GAMES_DIR = os.environ.get("OST_TONE_GAMES_DIR", os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "tone_games"))
def _discover_tone_games() -> list:
    try:
        d = TONE_GAMES_DIR
        if not os.path.isdir(d): return []
        names = []
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if not os.path.isdir(p): continue
            lite = os.path.join(p, "ost_tone_presets.json")
            pro  = os.path.join(p, "ost_tone_presets_v2.json")
            if os.path.exists(lite) or os.path.exists(pro):
                names.append(name)
        return names
    except Exception:
        return []

def _tone_preset_file_for_scope(mode: str, scope: str, game: str) -> str:
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    if (scope or "").strip().lower()=="game" and (game or "").strip():
        return os.path.join(TONE_GAMES_DIR, game.strip(), "ost_tone_presets_v2.json" if (mode or "").strip().lower()=="pro" else "ost_tone_presets.json")
    return _tone_preset_file_for_mode(mode)

TONE_PRESET_FILE = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "ost_tone_presets.json")

TONE_PRESETS_DEFAULT = {
    "ツンデレのナビゲーション": "ツンデレのナビゲーション",
    "ミステリアスな女性のナビゲーション": "ミステリアスな女性のナビゲーション",
    "臆病な女性のナビゲーション": "オドオドした感じの臆病な女性のナビゲーション。"
}


@dataclass
class State:
    roi: QRect
    selecting: bool = False
    busy: bool = False
    translated_text: str = ""
    dots: int = 0


class ScrollMessagePanel(QWidget):
    """外置きのスクロール表示パネル（右下角でリサイズ、上辺バーでドラッグ移動）"""
    def __init__(self, overlay: 'Overlay'):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.overlay = overlay
        self.text_edit = QTextEdit(self); self.text_edit.setReadOnly(True); self.text_edit.setFrameStyle(0)
        self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text_edit.setStyleSheet(f"QTextEdit {{ background: transparent; color: rgb(240,240,240); padding: {PANEL_TEXT_PADDING_PX}px; }}")
        self.bg = PANEL_BG_COLOR; self.border = PANEL_BORDER_COLOR

        # 手動リサイズ・移動関連
        self.user_locked = False  # Trueの間は ROI 追従を停止
        self._resizing = False
        self._moving = False
        self._drag_start = QPoint()
        self._start_geom = None
        self._grip = 16  # 右下リサイズ用グリップ領域
        
    def contextMenuEvent(self, e):
        m = QMenu(self)
        a1 = m.addAction("訳文をコピー")
        a2 = m.addAction("原文をコピー")
        a3 = m.addAction("原文＋訳文をコピー")
        a4 = m.addAction("画像として保存（訳文のみ）")
        a5 = m.addAction("画像として保存（原文＋訳文）")
        act = m.exec(e.globalPos())
        clip = QGuiApplication.clipboard()
        if act == a1:
            clip.setText(self.overlay.state.translated_text or "")
        elif act == a2:
            clip.setText(getattr(self.overlay, "last_source_text", "") or "")
        elif act == a3:
            src = getattr(self.overlay, "last_source_text", "")
            ja = self.overlay.state.translated_text or ""
            clip.setText((src + "\n" + ja).strip())
        elif act == a4:
            self.overlay.save_annotated_image(include_source=False)
        elif act == a5:
            self.overlay.save_annotated_image(include_source=True)
            
    # ---- テキスト/フォント ----
    def set_font_point(self, pt: int):
        f = self.text_edit.font(); f.setPointSize(pt); self.text_edit.setFont(f)

    def set_text(self, text: str):
        self.text_edit.setPlainText(text or "")

    # ---- ROI追従（user_lockedなら無視） ----
    def place_below_or_above(self, roi: QRect, prefer_below: bool = True, height: int = 200):
        if self.user_locked:  # 手動サイズ/位置中は追従しない
            return
        margin = 10; width = max(PANEL_MIN_W, roi.width() - margin * 2); x = roi.left() + margin; gap = 8
        if prefer_below:
            y = roi.bottom() + gap
            if y + height > self.overlay.virtual_geom.bottom():
                y = roi.top() - gap - height
        else:
            y = roi.top() - gap - height
            if y < self.overlay.virtual_geom.top():
                y = roi.bottom() + gap
        g = self.overlay.mapToGlobal(QPoint(x, y)); self.setGeometry(g.x(), g.y(), width, max(PANEL_MIN_H, height))
        self.text_edit.setGeometry(12, PANEL_DRAG_BAR_H, self.width() - 24, self.height() - PANEL_DRAG_BAR_H - 10)

    def reposition_to_roi_bottom(self, roi: QRect, ratio: float = 0.28):
        if self.user_locked:
            return
        margin = 10; inner = roi.adjusted(margin, margin, -margin, -margin)
        height = max(PANEL_MIN_H, int(inner.height() * max(0.12, min(0.9, ratio))))
        x, y, w, h = inner.left(), inner.bottom() - height, inner.width(), height
        g = self.overlay.mapToGlobal(QPoint(x, y)); self.setGeometry(g.x(), g.y(), max(PANEL_MIN_W, w), max(PANEL_MIN_H, h))
        self.text_edit.setGeometry(12, PANEL_DRAG_BAR_H, self.width() - 24, self.height() - PANEL_DRAG_BAR_H - 10)

    # ---- リサイズ/移動 ----
    def _in_grip(self, pos: QPoint) -> bool:
        r = self.rect(); return (pos.x() >= r.width() - self._grip) and (pos.y() >= r.height() - self._grip)

    def _in_drag_bar(self, pos: QPoint) -> bool:
        return pos.y() <= PANEL_DRAG_BAR_H

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            pos = e.position().toPoint()
            if self._in_grip(pos):
                self._resizing = True
                self.user_locked = True
                self._drag_start = e.globalPosition().toPoint()
                self._start_geom = self.geometry()
                self.setCursor(Qt.SizeFDiagCursor); e.accept(); return
            if self._in_drag_bar(pos):
                self._moving = True
                self.user_locked = True
                self._drag_start = e.globalPosition().toPoint()
                self._start_geom = self.geometry()
                self.setCursor(Qt.SizeAllCursor); e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._resizing:
            delta = e.globalPosition().toPoint() - self._drag_start
            new_w = max(PANEL_MIN_W, self._start_geom.width() + delta.x())
            new_h = max(PANEL_MIN_H, self._start_geom.height() + delta.y())
            vg = self.overlay.virtual_geom
            new_w = min(new_w, vg.right() - self._start_geom.left() - 8)
            new_h = min(new_h, vg.bottom() - self._start_geom.top() - 8)
            self.setGeometry(self._start_geom.left(), self._start_geom.top(), new_w, new_h)
            self.text_edit.setGeometry(12, PANEL_DRAG_BAR_H, new_w - 24, new_h - PANEL_DRAG_BAR_H - 10)
            e.accept(); return
        if self._moving:
            delta = e.globalPosition().toPoint() - self._drag_start
            nx = self._start_geom.left() + delta.x()
            ny = self._start_geom.top() + delta.y()
            vg = self.overlay.virtual_geom
            nx = max(vg.left()+4, min(nx, vg.right() - self._start_geom.width() - 4))
            ny = max(vg.top()+4,  min(ny, vg.bottom() - self._start_geom.height() - 4))
            self.move(nx, ny)
            e.accept(); return
        # カーソルヒント
        if self._in_grip(pos):
            self.setCursor(Qt.SizeFDiagCursor)
        elif self._in_drag_bar(pos):
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self._resizing or self._moving:
                self._resizing = False; self._moving = False; self.setCursor(Qt.ArrowCursor); e.accept(); return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            # ダブルクリックで ROI 追従に復帰
            self.user_locked = False
            e.accept()
        else:
            super().mouseDoubleClickEvent(e)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = self.rect(); p.setPen(QPen(self.border, 2)); p.setBrush(self.bg); p.drawRoundedRect(r.adjusted(0,0,-1,-1), 10, 10)
        # 上辺バーライン
        p.setPen(QPen(QColor(220,220,220,140), 1)); p.drawLine(10, PANEL_DRAG_BAR_H, self.width()-10, PANEL_DRAG_BAR_H)
        # 右下グリップ模様
        g = 16; p.setPen(QPen(QColor(220,220,220,180), 1)); 
        for i in range(3):
            p.drawLine(r.right()-g+6, r.bottom()-6-i*4, r.right()-6-i*4, r.bottom()-g+6)


class ReaderPanel(ScrollMessagePanel):
    
    pass

class ImagePickerDialog(QDialog):
    """非ネイティブでサムネイル付きの複数選択ダイアログ"""
    def __init__(self, parent=None, start_dir:str=None):
        super().__init__(parent)
        self.setWindowTitle("画像を選択して翻訳")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self._selected = []
        lay = QVBoxLayout(self)

        # 現在のフォルダ表示と「フォルダを開く」
        top = QHBoxLayout()
        self.lbl_dir = QLabel("—")
        btn_browse = QPushButton("フォルダを開く…")
        top.addWidget(self.lbl_dir, 1)
        top.addWidget(btn_browse, 0)
        lay.addLayout(top)

        # サムネイル一覧
        self.listw = QListWidget(self)
        self.listw.setViewMode(QListView.IconMode)
        self.listw.setIconSize(QSize(128, 128))
        self.listw.setResizeMode(QListView.Adjust)
        self.listw.setMovement(QListView.Static)
        self.listw.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.listw.setSpacing(8)
        lay.addWidget(self.listw, 1)

        # ボタン
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        lay.addWidget(btns)

        # signals
        btn_browse.clicked.connect(self._choose_dir)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        self.listw.itemDoubleClicked.connect(lambda _i: self.accept())

        # 初期フォルダ
        import os
        if start_dir and os.path.isdir(start_dir):
            self._dir = start_dir
        else:
            self._dir = os.path.expanduser("~")
        self._reload()

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "フォルダを選択", self._dir, QFileDialog.Option.DontUseNativeDialog)
        if d:
            self._dir = d
            self._reload()

    def _reload(self):
        # 画像拡張子のみを列挙し、サムネイル表示
        import os
        self.lbl_dir.setText(self._dir)
        self.listw.clear()
        exts = (".png",".jpg",".jpeg",".bmp",".webp",".gif")
        try:
            files = [f for f in os.listdir(self._dir) if f.lower().endswith(exts)]
        except Exception:
            files = []
        try:
            # 新しい → 古い（降順）。同一時刻の時は名前昇順で安定化
            files = sorted(
                files,
                key=lambda f: (-os.path.getmtime(os.path.join(self._dir, f)), f.lower())
            )
        except Exception:
            # 失敗時は名前順にフォールバック
            files = sorted(files)
        from PySide6.QtGui import QPixmap, QIcon
        for f in files:
            fp = os.path.join(self._dir, f)
            try:
                pm = QPixmap(fp)
                if not pm.isNull():
                    icon = QIcon(pm.scaled(self.listw.iconSize(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    icon = QIcon()
            except Exception:
                icon = QIcon()
            it = QListWidgetItem(icon, f)
            it.setToolTip(f)
            it.setData(Qt.UserRole, fp)
            self.listw.addItem(it)

    def selected_files(self) -> list:
        paths = []
        for it in self.listw.selectedItems():
            paths.append(it.data(Qt.UserRole))
        return paths
    pass


class ControlPanel(QWidget):

    def _apply_compact_style(self):
        """Make the panel compact if OST_GUI_COMPACT=1."""
        if not OST_GUI_COMPACT:
            return
        # layout spacings / margins
        def tighten(lay):
            if not lay:
                return
            try:
                lay.setSpacing(OST_GUI_SPACING)
            except Exception:
                pass
            try:
                l, t, r, b = OST_GUI_MARGINS
                lay.setContentsMargins(l, t, r, b)
            except Exception:
                pass
            for i in range(lay.count()):
                it = lay.itemAt(i)
                sub = it.layout() if it else None
                if sub:
                    tighten(sub)
        tighten(self.layout())

        # unify button height and reduce label margins
        from PySide6.QtWidgets import QPushButton, QLabel
        for btn in self.findChildren(QPushButton):
            try:
                btn.setFixedHeight(OST_GUI_BTN_H)
                if OST_GUI_BTN_W > 0:
                    btn.setFixedWidth(OST_GUI_BTN_W)
                    btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                else:
                    btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            except Exception:
                pass
        for lab in self.findChildren(QLabel):
            try:
                lab.setContentsMargins(0,0,0,0)
            except Exception:
                pass
    """GUIモード時の操作パネル（クリックで各操作を発火）"""
    def __init__(self, overlay: 'Overlay'):
        super().__init__(None, Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowTitle("OST 操作パネル")
        self.setAcceptDrops(True)
        self.overlay = overlay

        lay = QVBoxLayout(self)

        g = QGridLayout()
        self.btn_t = QPushButton("翻訳 (ALT+T)");        self.btn_t.clicked.connect(lambda: overlay._hk(overlay.trigger_translate))
        self.btn_c = QPushButton("範囲 (ALT+C)");        self.btn_c.clicked.connect(lambda: overlay._hk(overlay.start_select_mode))
        self.btn_cancel = QPushButton("キャンセル (Alt+X)")
        g.addWidget(self.btn_c, 0, 0)
        g.addWidget(self.btn_t, 0, 1)
        g.addWidget(self.btn_cancel, 0, 2)

        self.btn_k = QPushButton("口調 (ALT+K)");        self.btn_k.clicked.connect(lambda: overlay._hk(overlay._open_tone_editor))
        self.btn_s = QPushButton("話者 (ALT+F)");        self.btn_s.clicked.connect(lambda: overlay._hk(overlay._open_speaker_editor))
        self.btn_as= QPushButton("話者枠 (Alt+S)");  self.btn_as.clicked.connect(lambda: overlay._hk(overlay._start_select_speaker_roi))
        self.btn_ss= QPushButton("話者クリア (CTRL+Shift+S)"); 
        self.btn_ss.clicked.connect(lambda: overlay._hk(overlay._clear_speaker))
        g.addWidget(self.btn_k, 1, 0)
        g.addWidget(self.btn_s, 1, 1)
        g.addWidget(self.btn_as,1, 2)
        # 3段目に「話者クリア / 画像から翻訳 / 最後の保存から再翻訳」
        self.btn_from_img = QPushButton("画像から翻訳 (Alt+O)"); 
        self.btn_from_img.clicked.connect(lambda: overlay._hk(overlay._open_images_and_translate))
        self.btn_retry_last = QPushButton("最後の保存から再翻訳 (Alt+Shift+R)"); 
        self.btn_retry_last.clicked.connect(lambda: overlay._hk(overlay._retry_from_last_saved))
        g.addWidget(self.btn_ss, 2, 0)
        g.addWidget(self.btn_from_img, 2, 1)
        g.addWidget(self.btn_retry_last, 2, 2)
        # Concat
        self.btn_ca = QPushButton("連結に追加 (Alt+A)"); self.btn_ca.clicked.connect(lambda: overlay._hk(overlay._concat_append))
        self.btn_cd = QPushButton("連結クリア (Alt+D)"); self.btn_cd.clicked.connect(lambda: overlay._hk(overlay._concat_clear))
        g.addWidget(self.btn_ca, 3, 0, 1, 2)
        g.addWidget(self.btn_cd, 3, 2)

        lay.addLayout(g)

        # フレーム表示 & 編集 切替（チェックボックス）
        h1 = QHBoxLayout()
        self.cb_main_show = QCheckBox("青枠表示 (F8)")
        self.cb_speaker_show = QCheckBox("黄枠表示 (F9)")
        self.cb_main_edit = QCheckBox("青枠編集 (F10)")
        self.cb_speaker_edit = QCheckBox("黄枠編集 (F11)")
        self.cb_main_show.setChecked(overlay.show_main_frame)
        self.cb_speaker_show.setChecked(overlay.show_speaker_frame)
        self.cb_main_edit.setChecked(overlay.edit_main)
        self.cb_speaker_edit.setChecked(overlay.edit_speaker)
        self.cb_main_show.toggled.connect(lambda v: overlay._hk(lambda: overlay._set_main_frame_visible(v)))
        self.cb_speaker_show.toggled.connect(lambda v: overlay._hk(lambda: overlay._set_speaker_frame_visible(v)))
        self.cb_main_edit.toggled.connect(lambda v: overlay._hk(lambda: overlay._set_edit_main(v)))
        self.cb_speaker_edit.toggled.connect(lambda v: overlay._hk(lambda: overlay._set_edit_speaker(v)))
        h1.addWidget(self.cb_main_show); h1.addWidget(self.cb_speaker_show)
        h1.addWidget(self.cb_main_edit); h1.addWidget(self.cb_speaker_edit)
        lay.addLayout(h1)

        # 下段：訳文欄表示トグル/連結枚数/追従
        h = QHBoxLayout()
        self.info = QLabel(""); self.info.setVisible(False)
        self.concat = QLabel("連結: 0枚")
        self.btn_follow = QPushButton("パネル追従 (Shift+F7)"); self.btn_follow.clicked.connect(lambda: overlay._hk(overlay._panel_follow_again))
        self.btn_msgtoggle = QPushButton("訳文欄 表示/非表示 (ALT+Z)"); self.btn_msgtoggle.clicked.connect(lambda: overlay._hk(overlay._toggle_msg_visible))
        self.btn_cancel.clicked.connect(lambda: overlay._hk(overlay.trigger_cancel))
        
        h.addWidget(self.info); h.addWidget(self.concat); h.addWidget(self.btn_follow); h.addWidget(self.btn_msgtoggle)
        lay.addLayout(h)

        self.setStyleSheet("""
            QWidget { background: #202225; color: #eaeaea; }
            QPushButton { padding: 6px 10px; }
            QPushButton:disabled { color: #888; }
            QLabel { color: #cfcfcf; }
            QCheckBox { padding: 4px 8px; }
        """)
        self.setFixedWidth(OST_GUI_PANEL_W)
        if OST_GUI_COMPACT:
            self.setStyleSheet(self.styleSheet() + "\nQPushButton { padding: 2px 6px; }")
        # apply compact layout if enabled
        self._apply_compact_style()

    def set_busy(self, b: bool):
        self.btn_t.setDisabled(b); self.btn_c.setDisabled(b); self.btn_ca.setDisabled(b)
        self.btn_cd.setDisabled(False)
        if hasattr(self, "btn_cancel"):
            self.btn_cancel.setEnabled(b)  # 応答中のみキャンセル可能

    def set_concat_count(self, n: int):
        self.concat.setText(f"連結: {n}枚")

    def set_frame_state(self, show_main: bool, show_speaker: bool):
        self.cb_main_show.blockSignals(True); self.cb_speaker_show.blockSignals(True)
        self.cb_main_show.setChecked(show_main); self.cb_speaker_show.setChecked(show_speaker)
        self.cb_main_show.blockSignals(False); self.cb_speaker_show.blockSignals(False)

    def set_edit_state(self, edit_main: bool, edit_speaker: bool):
        self.cb_main_edit.blockSignals(True); self.cb_speaker_edit.blockSignals(True)
        self.cb_main_edit.setChecked(edit_main); self.cb_speaker_edit.setChecked(edit_speaker)
        self.cb_main_edit.blockSignals(False); self.cb_speaker_edit.blockSignals(False)
    # ---- Drag & Drop (画像ファイルをドロップで翻訳) ----
    def dragEnterEvent(self, e):
        try:
            if e.mimeData().hasUrls():
                for u in e.mimeData().urls():
                    if str(u.toLocalFile()).lower().endswith((".png",".jpg",".jpeg",".bmp",".webp",".gif")):
                        e.acceptProposedAction()
                        return
            e.ignore()
        except Exception:
            e.ignore()

    def dropEvent(self, e):
        try:
            paths = []
            for u in e.mimeData().urls():
                fp = u.toLocalFile()
                if str(fp).lower().endswith((".png",".jpg",".jpeg",".bmp",".webp",".gif")):
                    paths.append(fp)
            if paths:
                self.overlay._hk(lambda: self.overlay._translate_from_paths(paths))
            e.acceptProposedAction()
        except Exception:
            e.ignore()

class Overlay(QWidget):
    # --- 画像を縦に分割してPNG配列で返す（最終手段の回避用） ---
    def _slice_png_vertical(self, png_bytes: bytes, parts: int = 3) -> list[bytes]:
        try:
            from PIL import Image
            import io
            im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            W, H = im.size
            parts = max(2, int(parts))
            slice_h = max(8, H // parts)
            outs = []
            top = 0
            for i in range(parts):
                bottom = H if i == parts-1 else min(H, top + slice_h)
                crop = im.crop((0, top, W, bottom))
                buf = io.BytesIO(); crop.save(buf, format="PNG"); outs.append(buf.getvalue())
                top = bottom
            return outs
        except Exception:
            return [png_bytes]

    def _text_width_px(self, draw, s, font):
        try:
            return draw.textlength(s, font=font)
        except Exception:
            try:
                l,t,r,b = draw.textbbox((0,0), s, font=font)
                return r - l
            except Exception:
                return len(s) * 10

    # --- API送信用にPNGを最適化（長辺を制限して再エンコード） ---
    def _optimize_png_for_api(self, png_bytes: bytes) -> bytes:
        try:
            from PIL import Image
            import io, os
            lim = int(os.environ.get("OST_MAX_WH", "2048"))  # 長辺の上限。既定 2048px
            if lim <= 0:
                return png_bytes
            im = Image.open(io.BytesIO(png_bytes))
            w, h = im.size
            m = max(w, h)
            if m <= lim:
                return png_bytes
            scale = lim / float(m)
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            im = im.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return png_bytes

    # --- ユーティリティ：パス配列を更新日時でソート（古い→新しい） ---
    def _sort_paths_by_mtime(self, paths, reverse: bool = False):
        try:
            import os
            return sorted(paths, key=lambda p: os.path.getmtime(p), reverse=reverse)
        except Exception:
            try:
                return sorted(paths)
            except Exception:
                return list(paths)

    sig_apply_text = Signal(str)
    sig_set_busy   = Signal(bool)
    sig_concat_cnt = Signal(int)

    BORDER_COLOR = MAIN_BORDER_COLOR; BORDER_WIDTH = BORDER_WIDTH_PX
    SPEAKER_COLOR = SPEAKER_BORDER_COLOR
    TEXT_BG = TEXT_BG_COLOR; TEXT_FG = TEXT_FG_COLOR
    HELP_BG = HELP_BG_COLOR; HELP_FG = HELP_FG_COLOR
    HANDLE_FILL = HANDLE_FILL_COLOR; HANDLE_STROKE = HANDLE_STROKE_COLOR
    
    
    def _tone_preset_path(self) -> str:
        try:
            m = (self.tone_mode or "lite").strip().lower()
            s = (self.tone_scope or "default").strip().lower()
            g = (self.tone_game or "").strip()
        except Exception:
            m, s, g = "lite", "default", ""
        return _tone_preset_file_for_scope(m, s, g)
    def _load_tone_presets(self) -> dict:
        try:
            with open(self._tone_preset_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass
        # 初回は既定を書き出して返す
        try:
            with open(self._tone_preset_path(), "w", encoding="utf-8") as f:
                json.dump(TONE_PRESETS_DEFAULT, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return dict(TONE_PRESETS_DEFAULT)

    def _save_tone_presets(self, presets: dict):
        try:
            with open(self._tone_preset_path(), "w", encoding="utf-8") as f:
                json.dump(presets, f, ensure_ascii=False, indent=2)
        except Exception as e:
            if DEBUG: print("[OST] save tone presets failed:", e)
    
    def _extract_source_ja(self, raw_text: str):
        """
        モデル出力の揺れ（```json フェンス、前後の説明文、JSONの前後ゴミ）に強いパーサ。
        戻り値: (source, ja) どちらも str（見つからなければ ""）
        """
        import re, json

        # Defensive: ensure raw_text is a string to avoid regex TypeError
        if not isinstance(raw_text, str):
            try:
                raw_text = str(raw_text)
            except Exception:
                raw_text = ""

        if not raw_text:
            return "", ""

        s = raw_text.strip()
        # 追加: JSON抽出の強化（どこに出ても拾う）
        try:
            import json, re as _re2
            # ```json ... ``` / ``` ... ``` ブロックを先に試す
            for _m in _re2.finditer(r'```(?:json)?\s*(.*?)\s*```', s, _re2.DOTALL | _re2.IGNORECASE):
                _blk = (_m.group(1) or "").strip()
                if _blk:
                    try:
                        _obj = json.loads(_blk)
                        _src = _obj.get("source") if isinstance(_obj, dict) else ""
                        _ja  = _obj.get("ja")     if isinstance(_obj, dict) else ""
                        if isinstance(_src, str) and isinstance(_ja, str):
                            return _src, _ja
                    except Exception:
                        pass
            # { ... } 断片を順に試す
            for _m in _re2.finditer(r'\{.*?\}', s, _re2.DOTALL):
                _frag = (_m.group(0) or "").strip()
                if _frag:
                    try:
                        _obj = json.loads(_frag)
                        _src = _obj.get("source") if isinstance(_obj, dict) else ""
                        _ja  = _obj.get("ja")     if isinstance(_obj, dict) else ""
                        if isinstance(_src, str) and isinstance(_ja, str):
                            return _src, _ja
                    except Exception:
                        pass
        except Exception:
            pass


        # 1) ```json ... ``` を除去
        if s.startswith("```"):
            lines = s.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            s = "\n".join(lines).strip()

        # 2) そのまま / { ... } だけを抜き出して JSON として読む
        candidates = [s]
        if "{" in s and "}" in s:
            candidates.append(s[s.find("{"): s.rfind("}") + 1])

        for cand in candidates:
            try:
                obj = json.loads(cand)
                src = obj.get("source") or ""
                ja  = obj.get("ja") or ""
                if isinstance(src, str) and isinstance(ja, str):
                    return src, ja
            except Exception:
                pass

        # 3) 正規表現で "source":"...","ja":"..." をゆるく抽出（' も許容）
        m = re.search(
            r'''source\s*:\s*(?P<q1>["'])(?P<src>.*?)(?P=q1)\s*,\s*ja\s*:\s*(?P<q2>["'])(?P<ja>.*?)(?P=q2)''',
            s, re.IGNORECASE | re.DOTALL
        )
        if m:
            def unescape(t: str) -> str:
                try:
                    return bytes(t, "utf-8").decode("unicode_escape")
                except Exception:
                    return t
            return unescape(m.group("src")), unescape(m.group("ja"))

        # 4) どうしてもダメなら全文を「訳文」として返す（後方互換）
        return "", s

    def trigger_cancel(self):
        """API呼び出し中の翻訳を論理キャンセル（以後の結果は無視）"""
        # イベントを立てて、以後に返ってきたレスポンスは無視
        self.cancel_evt.set()
        # UIは即時にキャンセル表示・busy解除
        self.sig_apply_text.emit("（キャンセルしました）")
        self.sig_set_busy.emit(False)
        
    def __init__(self):
        # Tone preset mode/scope
        self.tone_mode  = OST_TONE_PRESET_MODE or "lite"
        self.tone_scope = os.environ.get("OST_TONE_SCOPE","default").strip().lower() or "default"
        self.tone_game  = os.environ.get("OST_TONE_GAME","").strip()
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.cancel_evt = threading.Event()
        self.active_job_id = 0  # 実行中ジョブの連番
        self.setAttribute(Qt.WA_TranslucentBackground, True); self.setMouseTracking(True)

        self.virtual_geom = self._virtual_geometry(); self.setGeometry(self.virtual_geom)

        margin = 40
        self.state = State(QRect(margin, margin, self.virtual_geom.width()-margin*2, self.virtual_geom.height()-margin*2))

        self._drag_start = QPoint(); self._drag_rect = QRect()

        self.api_key: Optional[str] = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

        self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(60)

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.text_ratio = DEFAULT_TEXT_RATIO; self.font_pt = DEFAULT_FONT_PT; self.msg_outside = OST_MSG_OUTSIDE

        # Persona
        self.tone: str = DEFAULT_TONE; self.speaker: str = DEFAULT_SPEAKER
        self.speaker_roi: Optional[QRect] = None; self._selecting_speaker_roi: bool = False

        # 枠表示フラグ
        self.show_main_frame = SHOW_MAIN_FRAME_DEFAULT
        self.show_speaker_frame = SHOW_SPEAKER_FRAME_DEFAULT

        # 訳文欄表示フラグ
        self.show_msg = SHOW_MSG_DEFAULT

        # ROI 編集フラグ（手動/F10, F11）
        self.edit_main = False
        self.edit_speaker = False

        # ROI 編集（共通ランタイム）
        self._editing_active = False
        self._edit_target = None   # "main" or "speaker"
        self._edit_handle = None   # "tl","tr","bl","br","l","r","t","b","move"
        self._edit_start_mouse = QPoint()
        self._edit_start_rect = QRect()

        # Auto-Edit 状態
        self._hover_target = None  # "main"/"speaker"/None
        self._hover_handle = None
        self._auto_grab = False
        self.hover_edit_main = False
        self.hover_edit_speaker = False

        # Panels
        self.msg_panel = ScrollMessagePanel(self); self.msg_panel.hide(); self.msg_panel.set_font_point(self.font_pt)
        self.reader = ReaderPanel(self); self.reader.hide(); self.reader.set_font_point(self.font_pt)

        # Exit
        self._exit_vk = self._vk_from_hotkey(EXIT_HOTKEY); self._exit_prev_down = False; self._exiting = False

        # Dialog-time hotkey suspend
        self._hotkeys_off = False

        # GUI
        self.gui_mode = OST_GUI_MODE
        self.gui_hotkeys = OST_GUI_HOTKEYS  # ★追加：GUIでもホットキーを使う
        self.ctrl_panel: Optional[ControlPanel] = None
        if self.gui_mode:
            self.ctrl_panel = ControlPanel(self)
            self.ctrl_panel.move(self.virtual_geom.left()+60, self.virtual_geom.top()+60)
            self.ctrl_panel.show()

        # Concat buffer
        self._concat_list: List[Image.Image] = []

        # hotkeys
        self._install_hotkeys()

        # signals
        self.sig_apply_text.connect(self._on_apply_text)
        self.sig_set_busy.connect(self._on_set_busy)
        self.sig_concat_cnt.connect(self._on_concat_cnt_changed)

        # poll
        self._prev: Dict[str,bool] = {}
        self._last_fire: Dict[str,float] = {}

        self.show()
        if DEBUG: print("[OST] Started  DEBUG=ON  GUI_MODE=", self.gui_mode)

    def EXIT_TEXT(self) -> str: return EXIT_HOTKEY

    # ---- ホットキー ----
    def _hk(self, fn): QTimer.singleShot(0, fn)

    def _suspend_hotkeys(self):
        self._hotkeys_off = True
        try:
            keyboard.unhook_all(); keyboard.clear_all_hotkeys()
            if DEBUG: print("[OST] hotkeys suspended")
        except Exception: pass

    def _resume_hotkeys(self):
        self._hotkeys_off = False
        self._install_hotkeys()
        if DEBUG: print("[OST] hotkeys resumed")
    def _apply_busy_hotkeys(self, busy: bool):
        """
        応答中(busy=True)はキャンセル(Alt+X)以外のホットキーを無効化し、
        終了ホットキー(EXIT_HOTKEY)は従来どおり有効にします。
        応答終了(busy=False)で通常のホットキーを再登録します。
        """
        try:
            keyboard.unhook_all(); keyboard.clear_all_hotkeys()
        except Exception:
            pass
        if busy:
            try:
                keyboard.add_hotkey('alt+x', lambda: self._hk(self.trigger_cancel))
                # 終了ホットキーは許可しておく
                keyboard.add_hotkey(EXIT_HOTKEY, lambda: self._hk(self._quit))
                if DEBUG: print("[OST] busy hotkeys: only Cancel/Exit enabled")
            except Exception as e:
                if DEBUG: print("[OST] busy hotkeys failed:", e)
        else:
            # 通常ホットキーに戻す
            self._install_hotkeys()
            if DEBUG: print("[OST] hotkeys restored (non-busy)")


    def _install_hotkeys(self):
        if self._hotkeys_off: return
        try:
            # Adjust/F keys
            keyboard.add_hotkey('f1', lambda: self._hk(self._font_smaller))
            keyboard.add_hotkey('f2', lambda: self._hk(self._font_larger))
            keyboard.add_hotkey('f3', lambda: self._hk(self._area_smaller))
            keyboard.add_hotkey('f4', lambda: self._hk(self._area_larger))
            keyboard.add_hotkey('f5', lambda: self._hk(self._toggle_capture_full))
            keyboard.add_hotkey('f6', lambda: self._hk(self._toggle_hide_on_capture))
            keyboard.add_hotkey('f7', lambda: self._hk(self._toggle_msg_outside))
            keyboard.add_hotkey('shift+f7', lambda: self._hk(self._panel_follow_again))
            keyboard.add_hotkey('f8', lambda: self._hk(self._toggle_main_frame))
            keyboard.add_hotkey('f9', lambda: self._hk(self._toggle_speaker_frame))
            keyboard.add_hotkey('f10', lambda: self._hk(self._toggle_edit_main))
            keyboard.add_hotkey('f11', lambda: self._hk(self._toggle_edit_speaker))
            keyboard.add_hotkey('alt+z', lambda: self._hk(self._toggle_msg_visible))
            keyboard.add_hotkey(EXIT_HOTKEY, lambda: self._hk(self._quit))

            if (not self.gui_mode) or self.gui_hotkeys:
                # Core ops
                keyboard.add_hotkey('alt+x', lambda: self._hk(self.trigger_cancel))
                keyboard.add_hotkey('alt+t',     lambda: self._hk(self.trigger_translate))
                keyboard.add_hotkey('alt+c',     lambda: self._hk(self.start_select_mode))
                keyboard.add_hotkey('alt+r',     lambda: self._hk(self._toggle_reader))
                keyboard.add_hotkey('alt+k',     lambda: self._hk(self._open_tone_editor))
                keyboard.add_hotkey('alt+f',     lambda: self._hk(self._open_speaker_editor))
                keyboard.add_hotkey('alt+s', lambda: self._hk(self._start_select_speaker_roi))
                keyboard.add_hotkey('ctrl+shift+s', lambda: self._hk(self._clear_speaker))
                # Concat
                keyboard.add_hotkey('alt+a', lambda: self._hk(self._concat_append))
                keyboard.add_hotkey('alt+d', lambda: self._hk(self._concat_clear))
            keyboard.add_hotkey('alt+o', lambda: self._hk(self._open_images_and_translate))
            keyboard.add_hotkey('alt+shift+r', lambda: self._hk(self._retry_from_last_saved))

            if DEBUG: print("[OST] Hotkeys registered (keyboard)  GUI_MODE=", self.gui_mode)
        except Exception as e:
            if DEBUG: print("[OST] Hotkey registration failed:", e)

    # ---- 画面ユーティリティ ----
    def _virtual_geometry(self) -> QRect:
        # メインだけを対象にするモードでは、プライマリ画面の矩形だけを返す
        if OST_PRIMARY_ONLY:
            scr = QGuiApplication.primaryScreen()
            return scr.geometry()
        # 従来どおり全モニタ合成
        rect = QRect()
        for s in QGuiApplication.screens():
            rect = rect.united(s.geometry())
        return rect
    def _screen_scale_for_point(self, global_pt: QPoint) -> float:
        scr = QGuiApplication.screenAt(global_pt) or QGuiApplication.primaryScreen()
        try: return float(scr.devicePixelRatio())
        except Exception: return 1.0

    # ---- ROI編集：ハンドル/ヒットテスト ----
    def _handles_for_rect(self, r: QRect, hot: int = 0):
        hs = HANDLE_SIZE + hot
        return {
            "tl": QRect(r.left()-hs//2,  r.top()-hs//2,     hs, hs),
            "tr": QRect(r.right()-hs//2, r.top()-hs//2,     hs, hs),
            "bl": QRect(r.left()-hs//2,  r.bottom()-hs//2,  hs, hs),
            "br": QRect(r.right()-hs//2, r.bottom()-hs//2,  hs, hs),
            "l":  QRect(r.left()-hs//2,  r.center().y()-hs//2, hs, hs),
            "r":  QRect(r.right()-hs//2, r.center().y()-hs//2, hs, hs),
            "t":  QRect(r.center().x()-hs//2, r.top()-hs//2, hs, hs),
            "b":  QRect(r.center().x()-hs//2, r.bottom()-hs//2, hs, hs),
        }

    def _cursor_for_handle(self, h: str):
        return {
            "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
            "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
            "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
            "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
            "move": Qt.SizeAllCursor
        }.get(h, Qt.ArrowCursor)

    def _is_in_move_band(self, r: QRect, pos: QPoint, band: int) -> bool:
        """枠の縁(内側band px)にいる時のみ True。ハンドル付近は除外。"""
        if not r.contains(pos): return False
        inner = r.adjusted(band, band, -band, -band)
        if inner.contains(pos): return False  # 内側すぎる -> 移動不可
        # ハンドル付近はリサイズ優先（排他）
        for rr in self._handles_for_rect(r, hot=HANDLE_HOT + 2).values():
            if rr.contains(pos): return False
        return True

    def _hit_test_auto(self, r: QRect, pos: QPoint) -> str:
        """Auto-Edit 用ヒットテスト: 1) ハンドル優先 2) 枠縁の移動（オプション）"""
        # 1) 拡張ハンドル当たり判定
        for k, rr in self._handles_for_rect(r, hot=HANDLE_HOT).items():
            if rr.contains(pos): return k
        # 2) 枠の縁で move（内部ドラッグ移動は AUTO_EDIT_MOVE が True のみ）
        if BORDER_MOVE_ENABLE and self._is_in_move_band(r, pos, MOVE_BAND):
            return "move"
        # 3) 内部ドラッグでの移動（明示的に許可された場合のみ）
        if AUTO_EDIT_MOVE and r.contains(pos):
            return "move"
        return ""

    # ---- 描画 ----
    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)

        # 暗転は「選択ドラッグ中」または「F10/F11の編集モード中」のみ
        if self.state.selecting or self._selecting_speaker_roi or self.edit_main or self.edit_speaker:
            p.fillRect(self.rect(), self.HELP_BG)

        # 枠
        if self.show_main_frame:
            p.setPen(QPen(self.BORDER_COLOR, self.BORDER_WIDTH)); p.setBrush(Qt.NoBrush); p.drawRect(self.state.roi)

        # ヘルプ/状態
        header = "ALT+T:翻訳  ALT+C:範囲  R:Reader"
        mode_text = f"  F5:CAPTURE={'FULL' if OST_CAPTURE_FULL else 'EXCLUDE'}  F6:HIDE={'ON' if OST_HIDE_ON_CAPTURE else 'OFF'}  F7:MSG={'OUT' if self.msg_outside else 'IN'}  ALT+Z:訳文欄表示={'ON' if self.show_msg else 'OFF'}  Exit:{EXIT_HOTKEY}"
        gui_text = "  [GUI]" if self.gui_mode else ""
        hk_text = "  HK:GUI=ON" if self.gui_mode and self.gui_hotkeys else ""
        busy_text = f"    進行状況: 翻訳中{'.' * self.state.dots}" if self.state.busy else ""
        p.setPen(self.HELP_FG); font = QFont(); font.setPointSize(11); p.setFont(font)
        p.drawText(self.state.roi.adjusted(8,6,-8,-6), Qt.AlignTop | Qt.AlignLeft, header + mode_text + gui_text + hk_text + busy_text)

        # 内側表示モード
        show_text = self.state.translated_text.strip()
        if self.show_msg and (not self.msg_outside) and show_text:
            text_rect = self._text_rect_inside_roi(self.state.roi)
            p.setPen(Qt.NoPen); p.setBrush(self.TEXT_BG); p.drawRoundedRect(text_rect, TEXT_ROUND_R_PX, TEXT_ROUND_R_PX)
            p.setPen(self.TEXT_FG); font2 = QFont(); font2.setPointSize(self.font_pt); p.setFont(font2)
            p.drawText(text_rect.adjusted(TEXT_PADDING_X_PX, TEXT_PADDING_Y_PX, -TEXT_PADDING_X_PX, -TEXT_PADDING_Y_PX), Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, show_text)

        # Reader 位置
        if self.reader.isVisible():
            self.reader.reposition_to_roi_bottom(self.state.roi, max(self.text_ratio, 0.28))
            self.reader.set_font_point(self.font_pt)

        # 外置きパネル
        if self.show_msg and self.msg_outside and (self.state.busy or show_text):
            self.msg_panel.set_font_point(self.font_pt)
            if not self.msg_panel.user_locked:
                h = max(PANEL_MIN_H, min(420, int(self.state.roi.height() * max(0.2, self.text_ratio))))
                self.msg_panel.place_below_or_above(self.state.roi, True, h)
            if not self.msg_panel.isVisible(): self.msg_panel.show()
        else:
            if self.msg_panel.isVisible(): self.msg_panel.hide()

        # 選択ガイド（矩形ドラッグ）
        if (self.state.selecting or self._selecting_speaker_roi) and not self._drag_rect.isNull():
            p.setPen(QPen(QColor(255,255,255,230),2,Qt.DashLine)); p.setBrush(Qt.NoBrush); p.drawRect(self._drag_rect)

        # 話者枠
        if self.speaker_roi and not self._selecting_speaker_roi and self.show_speaker_frame:
            p.setPen(QPen(self.SPEAKER_COLOR,2)); p.setBrush(Qt.NoBrush); p.drawRect(self.speaker_roi)

        # ROI 編集ハンドル描画（手動 or ホバー）※暗転とは独立
        p.setPen(QPen(self.HANDLE_STROKE, 1)); p.setBrush(self.HANDLE_FILL)
        if self.edit_main or self.hover_edit_main:
            for rr in self._handles_for_rect(self.state.roi).values(): p.drawRect(rr)
        if (self.edit_speaker or self.hover_edit_speaker) and self.speaker_roi and not self._selecting_speaker_roi:
            for rr in self._handles_for_rect(self.speaker_roi).values(): p.drawRect(rr)

    def _text_rect_inside_roi(self, roi: QRect) -> QRect:
        margin = TEXT_MARGIN_PX; inner = roi.adjusted(margin, margin, -margin, -margin)
        text_h = max(60, int(inner.height() * max(0.12, min(0.9, self.text_ratio))))
        return QRect(inner.left(), inner.bottom() - text_h, inner.width(), text_h)

    # ---- UIスレッドでのテキスト適用 ----
    @Slot(str)
    def _on_apply_text(self, text: str):
        self.state.translated_text = (text or "").strip()
        if self.reader.isVisible(): self.reader.set_text(self.state.translated_text)
        if self.msg_outside and self.show_msg: self.msg_panel.set_text(self.state.translated_text)
        self.update()

    @Slot(bool)
    def _on_set_busy(self, b: bool):
        self.state.busy = b
        # 応答中はキャンセル以外のホットキーを無効化
        try:
            self._apply_busy_hotkeys(b)
        except Exception:
            pass
        if b:
            self.state.dots = 0
            if self.msg_outside and self.show_msg: self.msg_panel.set_text("翻訳中")
            if self.reader.isVisible(): self.reader.set_text("翻訳中")
        if self.ctrl_panel: self.ctrl_panel.set_busy(b)
        self.update()

    @Slot(int)
    def _on_concat_cnt_changed(self, n: int):
        if self.ctrl_panel: self.ctrl_panel.set_concat_count(n)

    # ---- 枠の表示切替 ----
    def _toggle_main_frame(self):
        self.show_main_frame = not self.show_main_frame
        if self.ctrl_panel: self.ctrl_panel.set_frame_state(self.show_main_frame, self.show_speaker_frame)
        self.update()

    def _toggle_speaker_frame(self):
        self.show_speaker_frame = not self.show_speaker_frame
        if self.ctrl_panel: self.ctrl_panel.set_frame_state(self.show_main_frame, self.show_speaker_frame)
        self.update()

    def _set_main_frame_visible(self, v: bool):
        self.show_main_frame = bool(v)
        if self.ctrl_panel: self.ctrl_panel.set_frame_state(self.show_main_frame, self.show_speaker_frame)
        self.update()

    def _set_speaker_frame_visible(self, v: bool):
        self.show_speaker_frame = bool(v)
        if self.ctrl_panel: self.ctrl_panel.set_frame_state(self.show_main_frame, self.show_speaker_frame)
        self.update()

    # ---- 訳文欄の表示切替 ----
    def _toggle_msg_visible(self):
        # 訳文欄の表示/非表示をトグル
        self.show_msg = not self.show_msg

        # ★追加：枠表示も訳文欄の状態に合わせる
        self._set_main_frame_visible(self.show_msg)
        self._set_speaker_frame_visible(self.show_msg)

        # 訳文欄が非表示になったら外置きパネルを畳む
        if not self.show_msg and self.msg_panel.isVisible():
            self.msg_panel.hide()

        self.update()

    # ---- ROI 編集切替（手動） ----
    def _toggle_edit_main(self): self._set_edit_main(not self.edit_main)
    def _toggle_edit_speaker(self): self._set_edit_speaker(not self.edit_speaker)

    def _set_edit_main(self, v: bool):
        if v: self.edit_speaker = False
        self.edit_main = bool(v)
        if self.edit_main: self.show_main_frame = True
        self._refresh_editing_mouse()
        if self.ctrl_panel: self.ctrl_panel.set_edit_state(self.edit_main, self.edit_speaker)
        if self.ctrl_panel: self.ctrl_panel.set_frame_state(self.show_main_frame, self.show_speaker_frame)
        self.sig_apply_text.emit("(青枠 編集モード ON)" if self.edit_main else "(青枠 編集モード OFF)")
        self.update()

    def _set_edit_speaker(self, v: bool):
        if v and not self.speaker_roi:
            self.sig_apply_text.emit("(黄枠が未設定: Alt+S で話者領域を指定してから編集してください)"); return
        if v: self.edit_main = False
        self.edit_speaker = bool(v)
        if self.edit_speaker: self.show_speaker_frame = True
        self._refresh_editing_mouse()
        if self.ctrl_panel: self.ctrl_panel.set_edit_state(self.edit_main, self.edit_speaker)
        if self.ctrl_panel: self.ctrl_panel.set_frame_state(self.show_main_frame, self.show_speaker_frame)
        self.sig_apply_text.emit("(黄枠 編集モード ON)" if self.edit_speaker else "(黄枠 編集モード OFF)")
        self.update()

    def _refresh_editing_mouse(self):
        self._editing_active = self.edit_main or self.edit_speaker
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not (self._editing_active or self.state.selecting or self._selecting_speaker_roi))
        if not self._editing_active: self.setCursor(Qt.ArrowCursor)
        self.state.selecting = False; self._selecting_speaker_roi = False; self._drag_rect = QRect()

    # ---- Auto-Edit ホバー判定（暗転なし） ----
    def _auto_edit_hover(self):
        if not AUTO_EDIT: return
        if self._exiting or self._hotkeys_off: return
        if self.edit_main or self.edit_speaker:  # 手動編集中はホバー無効
            return
        if self.state.selecting or self._selecting_speaker_roi:
            return

        # デフォルトは掴まない
        self._hover_target = None; self._hover_handle = None
        self.hover_edit_main = False; self.hover_edit_speaker = False
        need_grab = False

        gp = QCursor.pos()
        lp = self.mapFromGlobal(gp)

        # 優先：メイン → 話者
        if self.show_main_frame:
            h = self._hit_test_auto(self.state.roi, lp)
            if h:
                self._hover_target = "main"; self._hover_handle = h
                self.hover_edit_main = True; need_grab = True
        if not need_grab and self.show_speaker_frame and self.speaker_roi:
            h = self._hit_test_auto(self.speaker_roi, lp)
            if h:
                self._hover_target = "speaker"; self._hover_handle = h
                self.hover_edit_speaker = True; need_grab = True

        # マウス入力の通し/遮断：掴める時だけ遮断
        effective_grab = need_grab or (self._edit_handle is not None)
        if effective_grab != self._auto_grab:
            self._auto_grab = effective_grab
            self.setAttribute(Qt.WA_TransparentForMouseEvents, not effective_grab)
        # カーソルヒント
        self.setCursor(self._cursor_for_handle(self._hover_handle) if need_grab else Qt.ArrowCursor)

        if need_grab or (self.hover_edit_main or self.hover_edit_speaker):
            self.update()

    # ---- 選択 ----
    def start_select_mode(self):
        if self.edit_main or self.edit_speaker:
            self.edit_main = False; self.edit_speaker = False
        self.hover_edit_main = False; self.hover_edit_speaker = False; self._auto_grab = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.state.selecting = True; self._drag_rect = QRect(); self.update()

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        # Auto-Edit/手動編集：ドラッグ開始
        if e.button() == Qt.LeftButton and (self.edit_main or self.edit_speaker or self._auto_grab):
            target = None; r = None
            if self.edit_main or self.hover_edit_main:
                target = "main"; r = QRect(self.state.roi)
            elif (self.edit_speaker or self.hover_edit_speaker) and self.speaker_roi:
                target = "speaker"; r = QRect(self.speaker_roi)

            if r:
                if self.edit_main or self.edit_speaker:
                    h = self._hit_test_auto(r, pos) or ("move" if r.contains(pos) else "")
                else:
                    h = self._hit_test_auto(r, pos)
                if h:
                    self._edit_target = target; self._edit_handle = h
                    self._edit_start_mouse = pos; self._edit_start_rect = QRect(r)
                    self.setCursor(self._cursor_for_handle(h)); return

        # 矩形選択
        if (self.state.selecting or self._selecting_speaker_roi) and e.button() == Qt.LeftButton:
            self._drag_start = pos; self._drag_rect = QRect(self._drag_start, self._drag_start); self.update()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        # ROI編集中のドラッグ
        if self._edit_handle:
            delta = pos - self._edit_start_mouse
            r0 = QRect(self._edit_start_rect)
            left, top, right, bottom = r0.left(), r0.top(), r0.right(), r0.bottom()
            if self._edit_handle == "move":
                left  += delta.x(); right += delta.x()
                top   += delta.y(); bottom += delta.y()
            else:
                if "l" in self._edit_handle: left  += delta.x()
                if "r" in self._edit_handle: right += delta.x()
                if "t" in self._edit_handle: top   += delta.y()
                if "b" in self._edit_handle: bottom+= delta.y()

            # 最小サイズ
            if right - left + 1 < ROI_MIN_W:
                if "l" in self._edit_handle: left = right - (ROI_MIN_W-1)
                else: right = left + (ROI_MIN_W-1)
            if bottom - top + 1 < ROI_MIN_H:
                if "t" in self._edit_handle: top = bottom - (ROI_MIN_H-1)
                else: bottom = top + (ROI_MIN_H-1)

            # 画面境界
            vg = self.virtual_geom
            left   = max(vg.left(), left)
            top    = max(vg.top(),  top)
            right  = min(vg.right(),  right)
            bottom = min(vg.bottom(), bottom)

            newr = QRect(QPoint(left, top), QPoint(right, bottom))
            if self._edit_target == "main": self.state.roi = newr
            else: self.speaker_roi = newr
            self.update(); return

        # 通常の矩形選択
        if (self.state.selecting or self._selecting_speaker_roi):
            end = pos; self._drag_rect = QRect(self._drag_start, end).normalized(); self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self._edit_handle:
                self._edit_handle = None; self.setCursor(Qt.ArrowCursor)
                return
            if (self.state.selecting or self._selecting_speaker_roi):
                if not self._drag_rect.isNull():
                    if self.state.selecting: self.state.roi = self._drag_rect
                    elif self._selecting_speaker_roi: self.speaker_roi = self._drag_rect
                self.state.selecting = False; self._selecting_speaker_roi = False; self._drag_rect = QRect(); self.update()
                self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    # ---- エディタ ----
    def _open_tone_editor(self):
        self._suspend_hotkeys()
        try:
            dlg = QDialog(self, Qt.WindowStaysOnTopHint)
            dlg.setWindowTitle("口調の設定")
            lay = QVBoxLayout(dlg)

            # モード（lite/pro）
            mode_row = QHBoxLayout()
            mode_row.addWidget(QLabel("モード:"))
            mode_cb = QComboBox(dlg)
            mode_cb.addItems(["かんたん","詳細"])
            mode_cb.setCurrentIndex(1 if (getattr(self,"tone_mode","lite")=="pro") else 0)
            mode_row.addWidget(mode_cb)
            lay.addLayout(mode_row)

            # 対象（default / per-game）
            scope_row = QHBoxLayout()
            scope_row.addWidget(QLabel("対象:"))
            scope_cb = QComboBox(dlg)
            games = _discover_tone_games()
            scope_cb.addItem("デフォルト")
            for g in games: scope_cb.addItem(g)
            if getattr(self, "tone_scope", "default") == "game" and getattr(self, "tone_game", "") in games:
                scope_cb.setCurrentText(getattr(self, "tone_game", ""))
            else:
                scope_cb.setCurrentIndex(0)
            scope_row.addWidget(scope_cb)
            btn_open_dir = QPushButton("ゲーム用プリセットのフォルダを開く")
            scope_row.addWidget(btn_open_dir)
            lay.addLayout(scope_row)
            # プリセット
            
            presets = self._load_tone_presets()

            row = QHBoxLayout()
            row.addWidget(QLabel("プリセット："))
            cb = QComboBox(dlg)

            def _reload_presets_for_mode_scope():
                nonlocal presets
                # update mode by UI
                self.tone_mode = "pro" if mode_cb.currentIndex()==1 else "lite"
                sel = scope_cb.currentText()
                if sel and sel != "デフォルト":
                    self.tone_scope = "game"; self.tone_game = sel
                else:
                    self.tone_scope = "default"; self.tone_game = ""
                presets = self._load_tone_presets()
                cb.blockSignals(True)
                cb.clear()
                for k in sorted(presets.keys()):
                    cb.addItem(k)
                cb.blockSignals(False)
                if cb.count()>0:
                    cb.setCurrentIndex(0)

            _reload_presets_for_mode_scope()

            row.addWidget(cb, 1)
            btn_save  = QPushButton("現在の内容を新規保存…")
            btn_del   = QPushButton("このプリセットを削除")
            row.addWidget(btn_save)
            row.addWidget(btn_del)
            lay.addLayout(row)

            # 説明
            lay.addWidget(QLabel("翻訳時の口調・文体（例: 若い冒険者、元気、一人称『オレ』など）"))

            # 本文
            edit = QTextEdit(dlg)
            edit.setPlainText(self.tone)
            edit.setMinimumSize(520, 220)
            lay.addWidget(edit)

            # ★選んだ瞬間に本文へ反映
            def on_changed(_index: int):
                name = cb.currentText()
                if name and name in presets:
                    edit.blockSignals(True)
                    edit.setPlainText(presets[name])
                    edit.blockSignals(False)
            
            def _on_mode_changed(_i:int): _reload_presets_for_mode_scope()
            def _on_scope_changed(_i:int): _reload_presets_for_mode_scope()
            mode_cb.currentIndexChanged.connect(_on_mode_changed)
            scope_cb.currentIndexChanged.connect(_on_scope_changed)

            def _open_games_dir():
                try:
                    d = TONE_GAMES_DIR
                    if not os.path.isdir(d): os.makedirs(d, exist_ok=True)
                    if sys.platform == "win32": os.startfile(d)
                    elif sys.platform == "darwin":
                        import subprocess; subprocess.Popen(["open", d])
                    else:
                        import subprocess; subprocess.Popen(["xdg-open", d])
                except Exception as e:
                    if DEBUG: print("[OST] open games dir failed:", e)
            btn_open_dir.clicked.connect(_open_games_dir)
            cb.currentIndexChanged.connect(on_changed)
            # ボタン
            btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            lay.addWidget(btns)

            # 保存/削除
            def do_save():
                name, ok = QInputDialog.getText(dlg, "プリセット名", "この内容を名前を付けて保存：", text=cb.currentText() or "")
                if not ok or not name.strip():
                    return
                name = name.strip()
                if name in presets:
                    r = QMessageBox.question(dlg, "上書き確認", f"「{name}」を上書きしますか？")
                    if r != QMessageBox.Yes:
                        return
                presets[name] = edit.toPlainText().strip()
                self._save_tone_presets(presets)
                cb.blockSignals(True)
                cb.clear(); cb.addItems(sorted(presets.keys()))
                cb.setCurrentText(name)
                cb.blockSignals(False)
                self.sig_apply_text.emit(f"(口調プリセット「{name}」を保存)")

            def do_delete():
                name = cb.currentText()
                if not name or name not in presets:
                    return
                r = QMessageBox.question(dlg, "削除確認", f"「{name}」を削除しますか？")
                if r != QMessageBox.Yes:
                    return
                try:
                    del presets[name]
                    self._save_tone_presets(presets)
                    cb.blockSignals(True)
                    cb.clear(); cb.addItems(sorted(presets.keys()))
                    cb.blockSignals(False)
                    # 削除後は本文は維持（勝手に消さない）
                    self.sig_apply_text.emit(f"(口調プリセット「{name}」を削除)")
                except Exception as e:
                    if DEBUG: print("[OST] delete tone preset failed:", e)

            btn_save.clicked.connect(do_save)
            btn_del.clicked.connect(do_delete)

            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)

            if dlg.exec() == QDialog.Accepted:
                self.tone = edit.toPlainText().strip()
                self.sig_apply_text.emit("(口調を更新)")
        finally:
            self._resume_hotkeys()

    def _open_speaker_editor(self):
        self._suspend_hotkeys()
        try:
            dlg = QDialog(self, Qt.WindowStaysOnTopHint); dlg.setWindowTitle("話者（キャラクター名）の設定")
            lay = QVBoxLayout(dlg); lay.addWidget(QLabel("現在の発話者（例: アーサー／店主／語り手 など）"))
            edit = QLineEdit(dlg); edit.setText(self.speaker); lay.addWidget(edit)
            btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); lay.addWidget(btns)
            btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
            if dlg.exec() == QDialog.Accepted:
                self.speaker = edit.text().strip(); self.sig_apply_text.emit("(話者を更新)")
        finally:
            self._resume_hotkeys()

    def _start_select_speaker_roi(self):
        if self.edit_main or self.edit_speaker:
            self.edit_main = False; self.edit_speaker = False
        self.hover_edit_main = False; self.hover_edit_speaker = False; self._auto_grab = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._selecting_speaker_roi = True; self._drag_rect = QRect(); self.update()

    def _clear_speaker(self):
        self.speaker = ""; self.speaker_roi = None; self.sig_apply_text.emit("(話者/話者領域をクリア)")

    # ---- Concat（連結） ----
    def _concat_append(self):
        if self.state.busy or self._exiting: return
        try:
            png = self._grab_roi_png_ui_thread()
            im = Image.open(io.BytesIO(png))
            im = im.convert(CONCAT_MODE_L) if CONCAT_MODE_L in ("L","RGB") else im.convert("L")
            if len(self._concat_list) >= CONCAT_MAX: self._concat_list.pop(0)
            self._concat_list.append(im)
            self.sig_apply_text.emit(f"(連結に追加: {len(self._concat_list)}枚)")
            self.sig_concat_cnt.emit(len(self._concat_list))
            if DEBUG or OST_SAVE_CAPTURE:
                os.makedirs("captures", exist_ok=True)
                self._save_concat_preview("captures/concat_current.png")
        except Exception as e:
            self.sig_apply_text.emit(f"(連結追加に失敗: {e})")

    def _concat_clear(self):
        self._concat_list.clear()
        self.sig_apply_text.emit("(連結をクリア)")
        self.sig_concat_cnt.emit(0)
        try:
            p = "captures/concat_current.png"
            if os.path.exists(p): os.remove(p)
        except Exception: pass

    def _save_concat_preview(self, path: str):
        if not self._concat_list: return
        png = self._build_concat_png()
        with open(path, "wb") as f: f.write(png)

    def _build_concat_png(self) -> bytes:
        if not self._concat_list: raise RuntimeError("concat buffer is empty")
        W = max(im.width for im in self._concat_list)
        converted = []
        for im in self._concat_list:
            if im.width != W:
                H = int(im.height * (W / im.width))
                converted.append(im.resize((W, H), Image.BICUBIC))
            else:
                converted.append(im.copy())
        total_h = sum(im.height for im in converted) + CONCAT_GAP_PX * (len(converted)-1)
        mode = "L" if CONCAT_MODE_L == "L" else "RGB"
        bg = 0 if mode == "L" else (0,0,0)
        canvas = Image.new(mode, (W, total_h), bg)
        y = 0
        sep_color = 180 if mode == "L" else (180,180,180)
        for i, im in enumerate(converted):
            canvas.paste(im, (0, y)); y += im.height
            if i != len(converted)-1 and CONCAT_GAP_PX > 0:
                for yy in range(CONCAT_GAP_PX):
                    for xx in range(W): canvas.putpixel((xx, y+yy), sep_color)
                y += CONCAT_GAP_PX
        buf = io.BytesIO(); canvas.save(buf, format="PNG"); return buf.getvalue()
# ---- 訳文併記画像（保存） ----
    def _find_ja_font(self, pt: int):
        # よくある日本語フォントの探索（見つからなければデフォルト）
        candidates = [
            r"C:\Windows\Fonts\meiryo.ttc",
            r"C:\Windows\Fonts\YuGothM.ttc",
            r"C:\Windows\Fonts\msgothic.ttc",
            "/System/Library/Fonts/Hiragino Sans W5.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf",
        ]
        for p in candidates:
            try:
                if os.path.exists(p):
                    return ImageFont.truetype(p, pt)
            except Exception:
                continue
        return ImageFont.load_default()

    def _wrap_lines(self, text: str, draw, font, max_w: int):
        """
        折り返しの厳密版:
        - 改行は \n に正規化（\r\n / \r -> \n）
        - 幅は textlength() を優先、無い環境は bbox の (right-left)
        - 英文はスペース優先、CJK/長語は文字単位で折返し
        """
        import re  # モジュール先頭にあるなら不要

        # normalize newlines
        if text is None:
            text = ""
        else:
            try:
                text = str(text).replace("\r\n", "\n").replace("\r", "\n")
            except Exception:
                text = ""

        def text_width(s: str) -> int:
            try:
                return draw.textlength(s, font=font)  # Pillow>=9
            except Exception:
                l, t, r, b = draw.textbbox((0, 0), s, font=font)
                return r - l

        lines = []
        for para in text.splitlines():
            # defensive: ensure each line is str
            if not isinstance(para, str):
                try:
                    para = str(para)
                except Exception:
                    para = ""
            if not para:
                lines.append("")
                continue

            use_words = bool(re.search(r"[A-Za-z]", para) and " " in para)
            tokens = para.split(" ") if use_words else list(para)

            buf = ""
            for t in tokens:
                cand = buf + (" " if use_words and buf else "") + t
                w = text_width(cand)
                if w <= max_w or not buf:
                    buf = cand
                else:
                    lines.append(buf)
                    buf = t
            if buf:
                lines.append(buf)

        return lines

    def _build_and_save_annotated(self, main_img_png: bytes, ja_text: str, src_text: str, include_source: bool) -> str:
        """
        右帯(side)で表示しきれない場合は自動で bottom 方式にフォールバック。
        折り返しは Pillow の textlength/bbox を使って正確に判定する。
        """
        from PIL import Image, ImageDraw
        import io, os, time

        base = Image.open(io.BytesIO(main_img_png)).convert("RGB")
        W, H = base.size

        layout = (ANN_LAYOUT or "auto").lower()
        if layout not in ("auto","side","bottom"):
            layout = "auto"
        if layout == "auto":
            ratio = H / max(1, W)
            layout = "side" if ratio >= ANN_SIDE_THRESHOLD else "bottom"

        # fonts
        font_ja  = self._find_ja_font(ANN_FONT_JA_PT  if ANN_FONT_JA_PT  > 0 else max(14, self.font_pt + 2))
        font_src = self._find_ja_font(ANN_FONT_SRC_PT if ANN_FONT_SRC_PT > 0 else max(12, self.font_pt))

        # probe context for metrics
        probe = Image.new("RGB", (10, 10))
        d0 = ImageDraw.Draw(probe)

        def _h(bb): return (bb[3]-bb[1]) if bb else 0
        h_label_ja  = _h(d0.textbbox((0,0), "訳文", font=font_ja))
        h_label_src = _h(d0.textbbox((0,0), "原文", font=font_src))
        h_ja_line   = _h(d0.textbbox((0,0), "あAg", font=font_ja))
        h_src_line  = _h(d0.textbbox((0,0), "あAg", font=font_src))

        include_src_flag = include_source and bool((src_text or "").strip())

        # helper for text width
        def _tw(s, f): 
            try:
                return d0.textlength(s, font=f)
            except Exception:
                l,t,r,b = d0.textbbox((0,0), s, font=f); return r-l

        def _wrap(text, f, max_w):
            # robust wrapping with binary-search fallback
            if text is None:
                text = ""
            try:
            # 改行を \n に正規化（Windowsの \r\n / 古い \r を \n に）
                text = str(text).replace("\r\n", "\n").replace("\r", "\n")
            except Exception:
                text = ""

            out = []
            for para in text.split("\n"):
                if not para:
                    out.append("")
                    continue
                i, n = 0, len(para)
                while i < n:
                    j = i + 1
                    last_space = -1
                    while j <= n:
                        seg = para[i:j]
                        w = _tw(seg, f)
                        if w <= max_w:
                            if seg and seg[-1].isspace():
                                last_space = j - 1
                            j += 1
                        else:
                            break
                    if j > n and _tw(para[i:j], f) <= max_w:
                        out.append(para[i:j]); i = j; continue
                    if last_space > i:
                        out.append(para[i:last_space].rstrip()); i = last_space + 1; continue
                    # force break by binary-search
                    lo, hi, best = i+1, n, i+1
                    while lo <= hi:
                        mid = (lo+hi)//2
                        if _tw(para[i:mid], f) <= max_w:
                            best = mid; lo = mid+1
                        else:
                            hi = mid-1
                    out.append(para[i:best]); i = best
            return out

        # ---- side layout (with overflow check) ----
        if layout == "side":
            side_w = max(120, ANN_SIDE_WIDTH_PX)
            margin = ANN_MARGIN_PX
            pad    = ANN_PAD_PX
            gap    = ANN_GAP_PX
            text_w = side_w - margin*2

            lines_src = _wrap(src_text or "", font_src, text_w) if include_src_flag else []
            lines_ja  = _wrap(ja_text  or "", font_ja,  text_w)

            need_h = pad
            if include_src_flag:
                need_h += h_label_src + 6 + (len(lines_src) * (h_src_line + 2) - 2 if lines_src else 0) + gap
            need_h += h_label_ja + 6 + (len(lines_ja) * (h_ja_line + 2) - 2 if lines_ja else 0) + pad

            # フィットしなければ bottom に退避
            if need_h > H:
                layout = "bottom"
            else:
                canvas = Image.new("RGB", (W + side_w, H), (0,0,0))
                canvas.paste(base, (0,0))
                band = Image.new("RGBA", (side_w, H), (0,0,0,ANN_ALPHA))
                canvas.paste(band, (W, 0), band)

                d = ImageDraw.Draw(canvas)
                x = W + margin
                y = pad

                if include_src_flag:
                    d.text((x, y), "原文", font=font_src, fill=(180,180,180)); y += h_label_src + 6
                    for line in lines_src:
                        d.text((x, y), line, font=font_src, fill=(210,210,210)); y += h_src_line + 2
                    y += gap

                d.text((x, y), "訳文", font=font_ja, fill=(235,235,235)); y += h_label_ja + 6
                for line in lines_ja:
                    d.text((x, y), line, font=font_ja, fill=(245,245,245)); y += h_ja_line + 2

                os.makedirs("captures", exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S"); ns = time.time_ns() % 1_000_000_000
                kind = ("src_ja" if include_src_flag else "ja") + "_side"
                out_path = os.path.join("captures", f"annotated_{kind}_{ts}_{ns:09d}.png")
                canvas.save(out_path, "PNG")
                return out_path

        # ---- bottom layout (default or fallback) ----
        margin = ANN_MARGIN_PX
        pad    = ANN_PAD_PX
        gap    = ANN_GAP_PX
        text_w = W - margin*2

        lines_src = _wrap(src_text or "", font_src, text_w) if include_src_flag else []
        lines_ja  = _wrap(ja_text  or "", font_ja,  text_w)

        h_src = (len(lines_src) * (h_src_line + 2) - 2) if include_src_flag and lines_src else 0
        h_ja  = (len(lines_ja)  * (h_ja_line  + 2) - 2) if lines_ja else 0

        area_h = pad + (h_label_src + 6 + h_src + gap if include_src_flag else 0) + h_label_ja + 6 + h_ja + pad

        canvas = Image.new("RGB", (W, H + area_h), (0,0,0))
        canvas.paste(base, (0,0))
        band = Image.new("RGBA", (W, area_h), (0,0,0,ANN_ALPHA))
        canvas.paste(band, (0, H), band)

        d = ImageDraw.Draw(canvas)
        y = H + pad

        if include_src_flag:
            d.text((margin, y), "原文", font=font_src, fill=(180,180,180)); y += h_label_src + 6
            for line in lines_src:
                d.text((margin, y), line, font=font_src, fill=(210,210,210)); y += h_src_line + 2
            y += gap

        d.text((margin, y), "訳文", font=font_ja, fill=(235,235,235)); y += h_label_ja + 6
        for line in lines_ja:
            d.text((margin, y), line, font=font_ja, fill=(245,245,245)); y += h_ja_line + 2

        os.makedirs("captures", exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S"); ns = time.time_ns() % 1_000_000_000
        kind = ("src_ja" if include_src_flag else "ja") + "_bottom"
        out_path = os.path.join("captures", f"annotated_{kind}_{ts}_{ns:09d}.png")
        canvas.save(out_path, "PNG")
        return out_path


    def save_annotated_image(self, include_source: bool = False):
        # 直近の翻訳に使った画像＋訳文（＋原文）で併記画像を保存
        if not getattr(self, "_last_main_img_png", None):
            # 直近のROIを取り直して代用（厳密に同一でなくてOKなら）
            try:
                self._last_main_img_png = self._grab_roi_png_ui_thread()
            except Exception:
                self.sig_apply_text.emit("(保存失敗: 直近の画像が見つかりません)"); return
        ja = self.state.translated_text or ""
        src = getattr(self, "last_source_text", "")
        try:
            path = self._build_and_save_annotated(self._last_main_img_png, ja, src, include_source)
            self.sig_apply_text.emit(f"(画像を保存しました: {os.path.basename(path)})")
        except Exception as e:
            self.sig_apply_text.emit(f"(保存に失敗: {e})")


    # ---- 画像ファイルからの翻訳（ダイアログ起点）----
    def _open_images_and_translate(self):
        self._suspend_hotkeys()
        _paused_topmost = False
        try:
            if hasattr(self, "_topmost_timer") and self._topmost_timer:
                try:
                    self._topmost_timer.stop(); _paused_topmost = True
                except Exception:
                    pass

            parent = self.ctrl_panel if getattr(self, "ctrl_panel", None) else self
            start_dir = getattr(self, "_last_image_dir", None)
            dlg = ImagePickerDialog(parent, start_dir)
            files = dlg.selected_files() if dlg.exec() else []
            if not files:
                return
            # 記憶：次回の開始ディレクトリ
            import os
            try:
                self._last_image_dir = os.path.dirname(files[0])
            except Exception:
                pass
            self._translate_from_paths(files)
        finally:
            if _paused_topmost:
                try: self._topmost_timer.start()
                except Exception: pass
            self._resume_hotkeys()
    # ---- パス列を受けて翻訳を開始 ----
    def _translate_from_paths(self, paths):
        # 更新日時（古い→新しい）で整列
        try:
            paths = self._sort_paths_by_mtime(list(paths), reverse=False)
        except Exception:
            pass
        if self.state.busy or self._exiting:
            self.sig_apply_text.emit("(実行中のため受け付けません)")
            return
        try:
            from PIL import Image, ImageEnhance
            import io, os
            imgs = []
            for fp in paths:
                if not os.path.exists(fp):
                    continue
                im = Image.open(fp)
                im = im.convert("RGB")
                if OST_PREPROCESS:
                    im = im.convert("L")
                    im = ImageEnhance.Brightness(im).enhance(1.12)
                    im = ImageEnhance.Contrast(im).enhance(1.32)
                    im = ImageEnhance.Sharpness(im).enhance(1.1)
                elif CONCAT_MODE_L in ("L","RGB"):
                    im = im.convert(CONCAT_MODE_L)
                imgs.append(im)
            if not imgs:
                self.sig_apply_text.emit("(有効な画像が見つかりません)")
                return

            # 1枚→そのまま / 複数→縦連結
            if len(imgs) == 1:
                buf = io.BytesIO()
                imgs[0].save(buf, format="PNG")
                main_png = buf.getvalue()
            else:
                W = max(im.width for im in imgs)
                converted = []
                for im in imgs:
                    if im.width != W:
                        H = int(im.height * (W / im.width))
                        converted.append(im.resize((W, H), Image.BICUBIC))
                    else:
                        converted.append(im.copy())
                total_h = sum(im.height for im in converted) + CONCAT_GAP_PX * (len(converted) - 1)
                mode = "L" if CONCAT_MODE_L == "L" else "RGB"
                bg = 0 if mode == "L" else (0, 0, 0)
                canvas = Image.new(mode, (W, total_h), bg)
                y = 0
                sep_color = 180 if mode == "L" else (180, 180, 180)
                for i, im in enumerate(converted):
                    canvas.paste(im, (0, y)); y += im.height
                    if i != len(converted) - 1 and CONCAT_GAP_PX > 0:
                        for yy in range(CONCAT_GAP_PX):
                            for xx in range(W):
                                canvas.putpixel((xx, y + yy), sep_color)
                        y += CONCAT_GAP_PX
                buf = io.BytesIO()
                canvas.save(buf, format="PNG")
                main_png = buf.getvalue()

            self._start_translation_with_images(main_png, None, note="(画像から翻訳)")
        except Exception as e:
            self.sig_apply_text.emit(f"(画像読み込みに失敗: {e})")

    # ---- 直渡し画像で翻訳（キャプチャを使わず） ----
    def _start_translation_with_images(self, main_img_png: bytes, speaker_img_png: Optional[bytes], note: str = "") -> None:
        if self.state.busy or self._exiting:
            return
        if not self.api_key:
            self.sig_apply_text.emit("（APIキー未設定：GEMINI_API_KEY または GOOGLE_API_KEY を設定してください）")
            return

        self.cancel_evt.clear()
        self.active_job_id += 1
        job_id = self.active_job_id
        self.sig_set_busy.emit(True)
        self._last_main_img_png = main_img_png  # 注釈保存で使用

        def worker(mi, si, jid):
            try:
                if self.cancel_evt.is_set() or jid != self.active_job_id:
                    return
                text = self._call_gemini_rest_with_retry(mi, si)
                if self.cancel_evt.is_set() or jid != self.active_job_id:
                    return
                self.sig_apply_text.emit(text if text else "（文字が見つかりません）")
                if OST_SAVE_ANNOTATED:
                    try:
                        self._build_and_save_annotated(self._last_main_img_png, text, getattr(self, "last_source_text", ""), OST_ANN_INCLUDE_SRC)
                    except Exception as e:
                        if DEBUG:
                            print("[OST] annotated save failed:", e)
                            import traceback
                            print(traceback.format_exc(limit=2))
                        import traceback
                        self.sig_apply_text.emit(f"（翻訳に失敗しました: {e}\\n{traceback.format_exc(limit=2)}）")
            finally:
                self._concat_list.clear()
                self.sig_concat_cnt.emit(0)
                self.sig_set_busy.emit(False)

        import threading
        threading.Thread(target=worker, args=(main_img_png, speaker_img_png, job_id), daemon=True).start()
        if note:
            self.sig_apply_text.emit(note)

    # ---- 直近の保存ファイルから再翻訳 ----
    def _retry_from_last_saved(self):
        if self.state.busy or self._exiting:
            return
        cap_dir = "captures"
        try:
            latest_main = None; latest_mtime = -1.0
            latest_concat = None; latest_concat_mtime = -1.0
            import os, io, re
            if os.path.isdir(cap_dir):
                for fn in os.listdir(cap_dir):
                    fp = os.path.join(cap_dir, fn)
                    if not os.path.isfile(fp):
                        continue
                    low = fn.lower()
                    try:
                        mt = os.path.getmtime(fp)
                    except Exception:
                        mt = 0.0
                    if low.startswith("used_main_") and low.endswith(".png") and mt > latest_mtime:
                        latest_main, latest_mtime = fp, mt
                    if low.startswith("concat_") and low.endswith(".png") and mt > latest_concat_mtime:
                        latest_concat, latest_concat_mtime = fp, mt

            if latest_main:
                m = re.match(r"used_main_(\\d{8}_\\d{6})_(\\d{9})\\.png$", os.path.basename(latest_main))
                sp_png = None
                if m:
                    sp_name = f"used_speaker_{m.group(1)}_{m.group(2)}.png"
                    sp_path = os.path.join(cap_dir, sp_name)
                    if os.path.exists(sp_path):
                        with open(sp_path, "rb") as f:
                            sp_png = f.read()
                with open(latest_main, "rb") as f:
                    mi = f.read()
                self._start_translation_with_images(mi, sp_png, note=f"(再翻訳: {os.path.basename(latest_main)})")
                return

            if latest_concat:
                with open(latest_concat, "rb") as f:
                    mi = f.read()
                self._start_translation_with_images(mi, None, note=f"(再翻訳: {os.path.basename(latest_concat)})")
                return

            self.sig_apply_text.emit("(再翻訳対象が見つかりません。captures に used_main_* または concat_* がありません)")
        except Exception as e:
            self.sig_apply_text.emit(f"(再翻訳に失敗: {e})")
    # ---- 翻訳 ----
    def trigger_translate(self):
        if self.state.busy or self._exiting: return
        if not self.api_key:
            self.sig_apply_text.emit("（APIキー未設定：GEMINI_API_KEY または GOOGLE_API_KEY を設定してください）"); return

        # ★ ジョブ開始：キャンセル状態をクリア＆ジョブID採番
        self.cancel_evt.clear()
        self.active_job_id += 1
        job_id = self.active_job_id

        self.sig_set_busy.emit(True)
        try:
            use_concat = bool(self._concat_list)
            main_img = self._build_concat_png() if self._concat_list else self._grab_roi_png_ui_thread()
            # 直近の送信用画像を保持（注釈保存に使用）
            self._last_main_img_png = main_img
            sp_img = self._grab_speaker_roi_png_ui_thread() if self.speaker_roi else None
            if (OST_SAVE_CAPTURE or DEBUG) and not use_concat:
                os.makedirs("captures", exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S"); ns = time.time_ns() % 1_000_000_000
                with open(os.path.join("captures", f"used_main_{ts}_{ns:09d}.png"), "wb") as f: f.write(main_img)
                if sp_img:
                    with open(os.path.join("captures", f"used_speaker_{ts}_{ns:09d}.png"), "wb") as f: f.write(sp_img)
        except Exception as e:
            self.sig_set_busy.emit(False); self.sig_apply_text.emit(f"(キャプチャ失敗: {e})"); return

        def worker(mi, si, jid):
            try:
                # ★ 送信用直前にもキャンセル確認
                if self.cancel_evt.is_set() or jid != self.active_job_id:
                    return
                text = self._call_gemini_rest_with_retry(mi, si)

                # ★ 応答後（UIに反映する前）にキャンセル/ジョブ不一致を確認
                if self.cancel_evt.is_set() or jid != self.active_job_id:
                    return

                self.sig_apply_text.emit(text if text else "（文字が見つかりません）")
                # 自動保存（環境変数で有効化）
                if OST_SAVE_ANNOTATED:
                    try:
                        self._build_and_save_annotated(self._last_main_img_png, text, getattr(self, "last_source_text", ""), OST_ANN_INCLUDE_SRC)
                    except Exception as e:
                        if DEBUG:
                            print("[OST] annotated save failed:", e)
                            import traceback
                            print(traceback.format_exc(limit=2))
                         # ユーザーにも通知
                        import traceback
                        self.sig_apply_text.emit(f"（翻訳に失敗しました: {e}\n{traceback.format_exc(limit=2)}）")
            finally:
                # 連結バッファのクリアとカウンタ更新
                self._concat_list.clear()
                self.sig_concat_cnt.emit(0)
                # concat_current.png のリネーム保存（既存実装）
                try:
                    p = "captures/concat_current.png"
                    if os.path.exists(p):
                        os.makedirs("captures", exist_ok=True)
                        ts = time.strftime("%Y%m%d_%H%M%S"); ns = time.time_ns() % 1_000_000_000
                        newp = os.path.join("captures", f"concat_{ts}_{ns:09d}.png")
                        try: os.replace(p, newp)
                        except Exception:
                            import shutil; shutil.copy2(p, newp); os.remove(p)
                except Exception as ee:
                    if DEBUG: print("[OST] concat rename failed:", ee)

                # ★busyは「キャンセル済みでも」必ず落とす
                self.sig_set_busy.emit(False)

        threading.Thread(target=worker, args=(main_img, sp_img, job_id), daemon=True).start()

    # ---- キャプチャ ----
    def _grab_roi_png_ui_thread(self) -> bytes:
        roi = QRect(self.state.roi)
        if OST_CAPTURE_FULL:
            cap = QRect(roi)
        else:
            text_rect = self._text_rect_inside_roi(roi)
            cap = QRect(roi.left(), roi.top(), roi.width(), max(1, text_rect.top() - 6 - roi.top()))

        # オーバーレイ等を一時的に透明化
        old_opacity = None; panel_old_opacity = None; msg_old_opacity = None
        if OST_HIDE_ON_CAPTURE or self.msg_outside:
            old_opacity = self.windowOpacity(); self.setWindowOpacity(0.0)
            if self.ctrl_panel and self.ctrl_panel.isVisible():
                panel_old_opacity = self.ctrl_panel.windowOpacity(); self.ctrl_panel.setWindowOpacity(0.0)
            if self.msg_panel and self.msg_panel.isVisible():
                msg_old_opacity = self.msg_panel.windowOpacity(); self.msg_panel.setWindowOpacity(0.0)
            QGuiApplication.processEvents(); QThread.msleep(16)

        try:
            with mss.mss() as sct:
                if OST_PRIMARY_ONLY:
                    # メイン画面（Qt 論理座標）→ mss の物理ピクセルへ変換
                    ps = QGuiApplication.primaryScreen()
                    ps_geo = ps.geometry()
                    idx = max(1, min(OST_MON_INDEX, len(sct.monitors) - 1))
                    mon = sct.monitors[idx]  # 物理px: left/top/width/height

                    # 論理(DIP)→物理(px)の倍率（X/Y で別々に算出）
                    scale_x = mon["width"]  / ps_geo.width()
                    scale_y = mon["height"] / ps_geo.height()

                    region = {
                        "left":   mon["left"] + int(cap.left()   * scale_x),
                        "top":    mon["top"]  + int(cap.top()    * scale_y),
                        "width":  max(1, int(cap.width()  * scale_x)),
                        "height": max(1, int(cap.height() * scale_y)),
                    }
                else:
                    # 従来の全画面モード（混在DPI環境ではズレる可能性あり）
                    global_center = self.mapToGlobal(cap.center())
                    scale = self._screen_scale_for_point(global_center)
                    region = {
                        "left":   int(cap.left()   * scale),
                        "top":    int(cap.top()    * scale),
                        "width":  max(1, int(cap.width()  * scale)),
                        "height": max(1, int(cap.height() * scale)),
                    }

                shot = sct.grab(region)
                img = Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)

        finally:
            if old_opacity is not None: self.setWindowOpacity(old_opacity)
            if panel_old_opacity is not None and self.ctrl_panel: self.ctrl_panel.setWindowOpacity(panel_old_opacity)
            if msg_old_opacity is not None and self.msg_panel: self.msg_panel.setWindowOpacity(msg_old_opacity)
            QGuiApplication.processEvents()

        if OST_PREPROCESS:
            img = img.convert("L")
            img = ImageEnhance.Brightness(img).enhance(1.12)
            img = ImageEnhance.Contrast(img).enhance(1.32)
            img = ImageEnhance.Sharpness(img).enhance(1.1)

        if OST_SAVE_CAPTURE or DEBUG:
            os.makedirs("captures", exist_ok=True)
            img.save(os.path.join("captures", "last_main.png"), "PNG")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    def _grab_speaker_roi_png_ui_thread(self) -> Optional[bytes]:
        r = QRect(self.speaker_roi)
        if r.isNull():
            return None

        old_opacity = None; panel_old_opacity = None; msg_old_opacity = None
        if OST_HIDE_ON_CAPTURE or self.msg_outside:
            old_opacity = self.windowOpacity(); self.setWindowOpacity(0.0)
            if self.ctrl_panel and self.ctrl_panel.isVisible():
                panel_old_opacity = self.ctrl_panel.windowOpacity(); self.ctrl_panel.setWindowOpacity(0.0)
            if self.msg_panel and self.msg_panel.isVisible():
                msg_old_opacity = self.msg_panel.windowOpacity(); self.msg_panel.setWindowOpacity(0.0)
            QGuiApplication.processEvents(); QThread.msleep(16)

        try:
            with mss.mss() as sct:
                if OST_PRIMARY_ONLY:
                    ps = QGuiApplication.primaryScreen()
                    ps_geo = ps.geometry()
                    idx = max(1, min(OST_MON_INDEX, len(sct.monitors) - 1))
                    mon = sct.monitors[idx]
                    scale_x = mon["width"]  / ps_geo.width()
                    scale_y = mon["height"] / ps_geo.height()
                    region = {
                        "left":   mon["left"] + int(r.left()   * scale_x),
                        "top":    mon["top"]  + int(r.top()    * scale_y),
                        "width":  max(1, int(r.width()  * scale_x)),
                        "height": max(1, int(r.height() * scale_y)),
                    }
                else:
                    global_center = self.mapToGlobal(r.center())
                    scale = self._screen_scale_for_point(global_center)
                    region = {
                        "left":   int(r.left()   * scale),
                        "top":    int(r.top()    * scale),
                        "width":  max(1, int(r.width()  * scale)),
                        "height": max(1, int(r.height() * scale)),
                    }

                shot = sct.grab(region)
                img = Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)

        finally:
            if old_opacity is not None: self.setWindowOpacity(old_opacity)
            if panel_old_opacity is not None and self.ctrl_panel: self.ctrl_panel.setWindowOpacity(panel_old_opacity)
            if msg_old_opacity is not None and self.msg_panel: self.msg_panel.setWindowOpacity(msg_old_opacity)
            QGuiApplication.processEvents()

        if OST_PREPROCESS:
            img = img.convert("L")
            img = ImageEnhance.Contrast(img).enhance(1.2)

        if OST_SAVE_CAPTURE or DEBUG:
            os.makedirs("captures", exist_ok=True)
            img.save(os.path.join("captures", "last_speaker.png"), "PNG")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # ---- REST（リトライ & JSON保存） ----
    def _call_gemini_rest_with_retry(self, main_img_png: bytes, speaker_img_png: Optional[bytes]) -> str:
        backoffs = [0.8, 2.0]
        last = None
        for attempt in range(1, 1+len(backoffs)+1):
            # ★ここでキャンセルなら即中断
            if self.cancel_evt.is_set():
                raise RuntimeError("canceled")
            try:
                return self._call_gemini_rest_once(main_img_png, speaker_img_png)
            except requests.RequestException as e:
                last = e
                if attempt <= len(backoffs) and not self.cancel_evt.is_set():
                    if DEBUG: print(f"[OST] network error; retry in {backoffs[attempt-1]}s: {e}")
                    time.sleep(backoffs[attempt-1])
                else:
                    raise
            except RuntimeError as e:
                last = e
                if "HTTP 5" in str(e) and attempt <= len(backoffs) and not self.cancel_evt.is_set():
                    if DEBUG: print(f"[OST] server 5xx; retry in {backoffs[attempt-1]}s")
                    time.sleep(backoffs[attempt-1])
                else:
                    raise
        if last: raise last
        return ""

    
    def _call_gemini_rest_once(self, main_img_png: bytes, speaker_img_png: Optional[bytes]) -> str:
        # --- Strict JSON 出力 & 画像最適化 ---
        main_img_png = self._optimize_png_for_api(main_img_png)
        if speaker_img_png:
            speaker_img_png = self._optimize_png_for_api(speaker_img_png)

        def build_payload(request_source: bool, img_png: bytes):
            """request_source=True: {"source","ja"} / False: {"ja"} only"""
            persona = []
            if self.speaker: persona.append(f"話者名は「{self.speaker}」。")
            if self.tone:    persona.append(f"口調/文体は「{self.tone}」。")
            persona_str = " ".join(persona) if persona else "話者/口調は特に指定なし。"

            constraint_text = (
                 " 出力は必ず1行のJSONのみ。前置き/後置き/解説/理由/箇条書き/Markdown/コードフェンス/引用符は禁止。"
            ) if getattr(self, 'tone_mode', 'lite') == 'pro' else ""

            parts = []
            if KEEP_SOURCE and request_source:
                prompt = (
                  "あなたはゲームUI/台詞の実務翻訳者です。画像からテキストを正確に読み取り、日本語に翻訳してください。"
                  + persona_str + constraint_text +
                  " 原文の改行（行区切り）は可能な限り維持し、同じ箇所で `ja` にも改行を入れてください。"
                  " 出力は必ず次のJSON文字列のみ："
                  ' {\"source\":\"OCRで認識した原文（読み取れた言語のまま）\",\"ja\":\"自然な日本語訳\"}  '
                  "。他の文字や説明は一切不要。読み取れない場合は source は空文字、ja は「（文字が見つかりません）」にしてください。"
                  " 2枚目の画像があれば話者のヒントとして参照してください。"
                )
            else:
                prompt = (
                  "あなたはゲームUI/台詞の実務翻訳者です。画像から読めるテキストを正確に日本語へ翻訳してください。"
                  + persona_str + constraint_text +
                  " 原文の改行（行区切り）は可能な限り維持し、同じ箇所で `ja` にも改行を入れてください。"
                  " 出力は必ず次のJSON文字列のみ： {\"ja\":\"自然な日本語訳\"} 。他の文字や説明は一切不要。"
                )
            parts.append({ "text": prompt })
            parts.append({ "inline_data": { "mime_type":"image/png", "data": base64.b64encode(img_png).decode("ascii") } })
            if speaker_img_png:
                parts.append({ "text":"以下は話者のヒント（名前枠/立ち絵など）です。" })
                parts.append({ "inline_data": { "mime_type": "image/png", "data": base64.b64encode(speaker_img_png).decode("ascii") } })

            if KEEP_SOURCE and request_source:
                sys_text = (
                    'あなたは画像からテキストを抽出して日本語へ翻訳するエージェント。'
                    '常に JSON のみを返答： {\"source\":\"原文\",\"ja\":\"日本語訳\"}。'
                    '前置き・後置き・説明・コードフェンスは禁止。キーは source と ja だけ。'
                )
                resp_schema = {
                    "type":"object",
                    "properties":{
                        "source":{"type":"string"},
                        "ja":{"type":"string"}
                    },
                    "required":["source","ja"]
                }
            else:
                sys_text = (
                    'あなたは画像からテキストを読み取り日本語へ翻訳するエージェント。'
                    '常に JSON のみを返答： {\"ja\":\"日本語訳\"}。'
                    '前置きや後置き、コードフェンスは禁止。キーは ja のみ。'
                )
                resp_schema = {
                    "type":"object",
                    "properties":{"ja":{"type":"string"}},
                    "required":["ja"]
                }

            gen_cfg = {
                "candidateCount": 1,
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "responseSchema": resp_schema
            }
            payload = {
                "systemInstruction": {"role":"system","parts":[{"text": sys_text}]},
                "contents": [{"role":"user","parts": parts}],
                "generationConfig": gen_cfg,
                "safetySettings": [
                    {"category":"HARM_CATEGORY_DANGEROUS_CONTENT","threshold":"BLOCK_NONE"},
                    {"category":"HARM_CATEGORY_HARASSMENT","threshold":"BLOCK_NONE"},
                    {"category":"HARM_CATEGORY_HATE_SPEECH","threshold":"BLOCK_NONE"},
                    {"category":"HARM_CATEGORY_SEXUALLY_EXPLICIT","threshold":"BLOCK_NONE"}
                ],
            }
            return payload

        headers = {"x-goog-api-key": (self.api_key or ""), "Content-Type":"application/json; charset=utf-8"}

        def request_once(request_source: bool, img_png: bytes):
            payload = build_payload(request_source, img_png)
            resp = requests.post(API_ENDPOINT, headers=headers, json=payload, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            if resp.status_code >= 400: 
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:800]}")
            data = resp.json()
            cands = data.get("candidates") or []
            cand = cands[0] if cands else None
            return data, cand

        # 1st attempt: request_source = KEEP_SOURCE
        request_source = bool(KEEP_SOURCE)
        data, cand = request_once(request_source, main_img_png)

        # エラー/停止理由
        if not cand:
            pf = data.get("promptFeedback") or {}
            return f"(空応答: {('blocked:'+str(pf)) if pf else str(data)[:400]})"

        finish = cand.get("finishReason")

        # RECITATION: もう一度、『訳文のみ』で再試行
        if finish == "RECITATION" and request_source:
            if OST_RECITATION_AUTO and OST_RECITATION_JA_RETRY:
                self.sig_apply_text.emit("（有名/既知の本文と判定され出力が停止されたため、訳文のみで再翻訳しています…）")
                if DEBUG: print("[OST] recitation detected; retry with JA-only schema")
                request_source = False
                data, cand = request_once(request_source, main_img_png)
                finish = cand.get("finishReason")
            elif OST_SLICE_ON_RECITATION:
                self.sig_apply_text.emit("（有名/既知の本文と判定されたため、画像を分割して再翻訳しています…）")
                if DEBUG: print("[OST] recitation detected; skip JA-only retry; slicing image")
                ja_all = []
                for sub_png in self._slice_png_vertical(main_img_png, OST_SLICE_PARTS):
                    try:
                        sub_png_opt = self._optimize_png_for_api(sub_png)
                    except Exception:
                        sub_png_opt = sub_png
                    data2, cand2 = request_once(False, sub_png_opt)
                    if not cand2:
                        continue
                    parts_out2 = (cand2.get("content") or {}).get("parts") or []
                    raw2 = ""
                    for p2 in parts_out2:
                        if isinstance(p2, dict) and "text" in p2 and p2["text"]:
                            raw2 += p2["text"]
                    raw2 = (raw2 or "").strip()
                    try:
                        obj2 = json.loads(raw2) if raw2 else {}
                        if isinstance(obj2, dict) and "ja" in obj2:
                            j = (obj2.get("ja") or "").strip()
                            if j:
                                ja_all.append(j)
                    except Exception:
                        if raw2:
                            ja_all.append(raw2)
                if ja_all:
                    self.last_source_text = ""
                    return "".join(ja_all)
                    
        if finish and finish != "STOP":
            # 最終手段: JA-onlyでさらにRECITATIONなら、画像を縦に分割して逐次翻訳（意訳）
            if finish == "RECITATION" and not request_source and OST_SLICE_ON_RECITATION:
                self.sig_apply_text.emit("（依然として出力が停止されたため、画像を分割して再翻訳しています…）")
                if DEBUG: print("[OST] recitation again; slicing image and concatenating JA")
                ja_all = []
                for sub_png in self._slice_png_vertical(main_img_png, OST_SLICE_PARTS):
                    # 送信前に最適化（長辺を縮小）— _optimize_png_for_api が無ければ sub_png のままでOK
                    try:
                        sub_png_opt = self._optimize_png_for_api(sub_png)
                    except Exception:
                        sub_png_opt = sub_png
                    data2, cand2 = request_once(False, sub_png_opt)
                    if not cand2:
                        continue
                    parts_out2 = (cand2.get("content") or {}).get("parts") or []
                    raw2 = ""
                    for p2 in parts_out2:
                        if isinstance(p2, dict) and "text" in p2 and p2["text"]:
                            raw2 += p2["text"]
                    raw2 = (raw2 or "").strip()
                    try:
                        obj2 = json.loads(raw2) if raw2 else {}
                        if isinstance(obj2, dict) and "ja" in obj2:
                            j = (obj2.get("ja") or "").strip()
                            if j:
                                ja_all.append(j)
                    except Exception:
                        if raw2:
                            ja_all.append(raw2)
                if ja_all:
                    self.last_source_text = ""
                    return "\n".join(ja_all)
            # ここまで来たら素直に停止理由を返す
            return f"(モデルが出力を停止: finishReason={finish} details={str(cand)[:300]})"

        # 本文取り出し
        parts_out = (cand.get("content") or {}).get("parts") or []
        raw = ""
        for p in parts_out:
            if isinstance(p, dict) and "text" in p and p["text"]:
                raw += p["text"]
        raw = (raw or "").strip()

        # JSON parse
        try:
            obj = json.loads(raw) if raw else {}
        except Exception:
            obj = {}

        if request_source:
            # {"source","ja"} 期待
            src = ""
            ja = ""
            if isinstance(obj, dict):
                src = obj.get("source") or ""
                ja  = obj.get("ja") or ""
            else:
                # 後方互換：旧パーサで救済
                src, ja = self._extract_source_ja(raw)
            self.last_source_text = (src or "").strip()
            ja = (ja or "").strip()
            if ja: return ja
            if src: return src
            return "（文字が見つかりません）"
        else:
            # {"ja"} 期待
            self.last_source_text = ""
            if isinstance(obj, dict) and "ja" in obj:
                ja = (obj.get("ja") or "").strip()
                return ja if ja else "（文字が見つかりません）"
            # JSONで無ければ raw テキストを返す
            return raw if raw else "（文字が見つかりません）"


    # ---- 調整 ----
    def _font_smaller(self): self.font_pt = max(8, self.font_pt - 1); self.update()
    def _font_larger(self):  self.font_pt = min(40, self.font_pt + 1); self.update()
    def _area_smaller(self): self.text_ratio = max(0.12, round(self.text_ratio - 0.05, 2)); self.update()
    def _area_larger(self):  self.text_ratio = min(0.9,  round(self.text_ratio + 0.05, 2)); self.update()
    def _toggle_reader(self): self.reader.setVisible(not self.reader.isVisible()); self.update()
    def _toggle_msg_outside(self): self.msg_outside = not self.msg_outside; self.update()
    def _toggle_capture_full(self):
        global OST_CAPTURE_FULL; OST_CAPTURE_FULL = not OST_CAPTURE_FULL; self.sig_apply_text.emit(f"(CAPTURE={'FULL' if OST_CAPTURE_FULL else 'EXCLUDE'})")
    def _toggle_hide_on_capture(self):
        global OST_HIDE_ON_CAPTURE; OST_HIDE_ON_CAPTURE = not OST_HIDE_ON_CAPTURE; self.sig_apply_text.emit(f"(HIDE_ON_CAPTURE={'ON' if OST_HIDE_ON_CAPTURE else 'OFF'})")
    def _panel_follow_again(self):
        self.msg_panel.user_locked = False
        self.update()

    # ---- ループ & 終了 & ポーリング ----
    def _tick(self):
        if self._exiting: return
        if self.state.busy:
            self.state.dots = (self.state.dots + 1) % 4
        if POLL_ON and not self._hotkeys_off: self._poll_keys()
        self._auto_edit_hover()
        # 終了キー（単一VK）
        if sys.platform == "win32" and self._exit_vk is not None:
            try:
                import ctypes
                down = bool(ctypes.windll.user32.GetAsyncKeyState(self._exit_vk) & 0x8000)
                if down and not self._exit_prev_down: self._quit()
                self._exit_prev_down = down
            except Exception: pass
        self.update()

    def _vk_table(self) -> Dict[str,int]:
        t = {**{f"f{i}": 0x6F + i for i in range(1,25)}}
        for ch in "abcdefghijklmnopqrstuvwxyz": t[ch] = ord(ch.upper())
        t.update({"shift":0x10,"ctrl":0x11,"control":0x11,"alt":0x12,"menu":0x12})
        return t
    _VK = property(_vk_table)

    def _is_down(self, key: str) -> bool:
        if sys.platform != "win32": return False
        import ctypes
        vk = self._VK.get(key)
        if vk is None: return False
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)

    def _edge(self, name: str, now: bool) -> bool:
        before = self._prev.get(name, False); self._prev[name] = now; return (now and not before)
    def _fire_once(self, name: str, fn, cooldown: float = 0.25):
        now = time.time(); last = self._last_fire.get(name, 0.0)
        if (now - last) >= cooldown:
            self._last_fire[name] = now
            if DEBUG: print(f"[OST] VK fired: {name}")
            self._hk(fn)

    def _poll_keys(self):
        # 応答中はキャンセル(Alt+X)以外のポーリングを無効化
        if self.state.busy:
            if sys.platform == 'win32' and not self._hotkeys_off:
                alt = self._is_down('alt'); shift = self._is_down('shift'); ctrl = self._is_down('ctrl')
                if (alt and not shift and not ctrl) and self._edge('alt+x', self._is_down('x')):
                    self._fire_once('alt+x', self.trigger_cancel)
            return
        if sys.platform != "win32": return
        if self._hotkeys_off: return
        alt = self._is_down("alt"); shift = self._is_down("shift"); ctrl = self._is_down("ctrl")

        combos = []
        if (not self.gui_mode) or self.gui_hotkeys:
            combos += [
                ("alt+t","t", (alt and not shift and not ctrl), self.trigger_translate),
                ("alt+c","c", (alt and not shift and not ctrl), self.start_select_mode),
                ("alt+r","r", (alt and not shift and not ctrl), self._toggle_reader),
                ("alt+k","k", (alt and not shift and not ctrl), self._open_tone_editor),
                ("alt+f","f", (alt and not shift and not ctrl), self._open_speaker_editor),
                ("alt+s","s", (alt and not shift), self._start_select_speaker_roi),
                ("ctrl+shift+s","s", (shift and ctrl and not alt), self._clear_speaker),
                ("alt+a","a", (alt and not shift), self._concat_append),
                ("alt+d","d", (alt and not shift), self._concat_clear),
                ("alt+o","o", (alt and not shift and not ctrl), self._open_images_and_translate),
                ("alt+shift+r","r", (alt and shift and not ctrl), self._retry_from_last_saved),
                ("alt+x","x", (alt and not shift and not ctrl), self.trigger_cancel),
            ]
        combos += [
            ("f1","f1", (not alt and not shift and not ctrl), self._font_smaller),
            ("f2","f2", (not alt and not shift and not ctrl), self._font_larger),
            ("f3","f3", (not alt and not shift and not ctrl), self._area_smaller),
            ("f4","f4", (not alt and not shift and not ctrl), self._area_larger),
            ("f5","f5", (not alt and not shift and not ctrl), self._toggle_capture_full),
            ("f6","f6", (not alt and not shift and not ctrl), self._toggle_hide_on_capture),
            ("f7","f7", (not alt and not shift and not ctrl), self._toggle_msg_outside),
            ("shift+f7","f7", (shift and not alt and not ctrl), self._panel_follow_again),
            ("f8","f8", (not alt and not shift and not ctrl), self._toggle_main_frame),
            ("f9","f9", (not alt and not shift and not ctrl), self._toggle_speaker_frame),
            ("f10","f10", (not alt and not shift and not ctrl), self._toggle_edit_main),
            ("f11","f11", (not alt and not shift and not ctrl), self._toggle_edit_speaker),
            ("alt+z","z", (alt and not shift and not ctrl), self._toggle_msg_visible),
        ]
        for name, key, cond, fn in combos:
            if cond and self._edge(name, self._is_down(key)):
                self._fire_once(name, fn)

    # ---- VK補助 ----
    def _vk_from_hotkey(self, hotkey: str) -> Optional[int]:
        s = hotkey.strip().lower()
        if '+' in s: s = s.split('+')[-1].strip()
        table = {**{f"f{i}": 0x6F + i for i in range(1, 25)},
                 "scroll lock": 0x91, "scroll_lock": 0x91, "scrolllock": 0x91,
                 "pause": 0x13, "break": 0x13, "pause/break": 0x13,
                 "end": 0x23, "home": 0x24, "insert": 0x2D, "delete": 0x2E,
                 "print screen": 0x2C, "prtsc": 0x2C, "prt sc": 0x2C}
        return table.get(s)

    def _quit(self):
        if self._exiting: return
        self._exiting = True
        try:
            self.hide()
            if self.msg_panel.isVisible(): self.msg_panel.hide()
            if self.reader.isVisible(): self.reader.hide()
            if self.ctrl_panel and self.ctrl_panel.isVisible(): self.ctrl_panel.hide()
        except Exception: pass
        try: keyboard.unhook_all(); keyboard.clear_all_hotkeys()
        except Exception: pass
        try: self.timer.stop()
        except Exception: pass
        try: QCoreApplication.quit()
        except Exception: pass
        QTimer.singleShot(120, lambda: os._exit(0))


def main():
    app = QApplication(sys.argv); app.setApplicationDisplayName("ScreenTranslate (Gemini) v1")
    w = Overlay(); sys.exit(app.exec())


# ===== LASSO INTEGRATION APPENDIX =====
# 既存の Overlay を継承し、自由選択（Alt+Shift+C）を追加。
# 最後に Overlay をこのクラスで差し替えます（既存コードの大半は無改変）。

from PySide6.QtGui import QPainter, QPen, QColor, QPolygon
from PySide6.QtCore import Qt, QRect, QPoint
import mss
from PIL import Image, ImageDraw, ImageEnhance

class _LassoOverlay(Overlay):
    def __init__(self):
        super().__init__()
        self.free_selecting = False
        self._free_dragging = False
        self.free_path = []          # list[QPoint]
        self.use_free_roi = False
        self._rect_selecting_active = False
        # hotkey
        try:
            import keyboard
            keyboard.add_hotkey('alt+shift+c', lambda: self._hk(self.start_free_select_mode))
        except Exception:
            pass
        # GUIボタン（あれば）
        try:
            if hasattr(self, "ctrl_panel") and self.ctrl_panel:
                from PySide6.QtWidgets import QPushButton
                self.btn_lasso = QPushButton("自由選択 (Alt+Shift+C)", self.ctrl_panel)
                lay0 = self.ctrl_panel.layout().itemAt(0).layout() if self.ctrl_panel.layout() else None
                if lay0:
                    lay0.addWidget(self.btn_lasso, 0, 2)
                self.btn_lasso.clicked.connect(lambda: self._hk(self.start_free_select_mode))
        except Exception:
            pass

        # 既存のキャンセルボタンは GUI 上では非表示（ラッソボタンを同位置に置くため）
        try:
            if hasattr(self, "ctrl_panel") and self.ctrl_panel and hasattr(self.ctrl_panel, "btn_cancel"):
                self.ctrl_panel.btn_cancel.setVisible(False)
        except Exception:
            pass

        # 実行中は「翻訳(ALT+T)」ボタンを「キャンセル(Alt+X)」に差し替える（押下で中断）
        try:
            if hasattr(self, "ctrl_panel") and self.ctrl_panel and hasattr(self.ctrl_panel, "btn_t"):
                import types
                _orig_set_busy = self.ctrl_panel.set_busy
                overlay_ref = self
                def _set_busy_swap(this, b: bool, _orig=_orig_set_busy, _o=overlay_ref):
                    # まず既存の busy UI を反映
                    try:
                        _orig(b)
                    except Exception:
                        pass
                    # その上で「翻訳」ボタンの表示/動作を差し替え
                    try:
                        if b:
                            this.btn_t.setText("キャンセル (Alt+X)")
                            try: this.btn_t.clicked.disconnect()
                            except Exception: pass
                            this.btn_t.clicked.connect(lambda: _o._hk(_o.trigger_cancel))
                            this.btn_t.setEnabled(True)  # 中断は常に押せるように
                        else:
                            this.btn_t.setText("翻訳 (ALT+T)")
                            try: this.btn_t.clicked.disconnect()
                            except Exception: pass
                            this.btn_t.clicked.connect(lambda: _o._hk(_o.trigger_translate))
                            this.btn_t.setEnabled(True)
                        # 別のキャンセルボタンは見せない
                        if hasattr(this, "btn_cancel"):
                            this.btn_cancel.setVisible(False)
                    except Exception:
                        pass
                self.ctrl_panel.set_busy = types.MethodType(_set_busy_swap, self.ctrl_panel)
        except Exception:
            pass

    def _install_hotkeys(self):
        super()._install_hotkeys()
        try:
            import keyboard
            keyboard.add_hotkey('alt+shift+c', lambda: self._hk(self.start_free_select_mode))
        except Exception:
            pass

    def start_select_mode(self):
        self._rect_selecting_active = True
        return super().start_select_mode()

    def start_free_select_mode(self):
        if getattr(self, "edit_main", False) or getattr(self, "edit_speaker", False):
            self.edit_main = False; self.edit_speaker = False
        self.hover_edit_main = False
        self.hover_edit_speaker = False
        self._auto_grab = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.free_selecting = True
        self._free_dragging = False
        self.free_path = []
        self.state.selecting = False
        if hasattr(self, "_drag_rect"):
            self._drag_rect = QRect()
        self.update()

    def _poll_keys(self):
        try:
            super()._poll_keys()
        finally:
            try:
                if sys.platform == "win32" and not getattr(self, "_hotkeys_off", False):
                    alt = self._is_down("alt"); shift = self._is_down("shift"); c = self._is_down("c")
                    now = (alt and shift and c)
                    prev = getattr(self, "_prev_lasso", False)
                    setattr(self, "_prev_lasso", now)
                    if now and not prev:
                        self._hk(self.start_free_select_mode)
            except Exception:
                pass
        return

    def paintEvent(self, e):
        saved_show = getattr(self, "show_main_frame", True)
        saved_selecting = getattr(self.state, "selecting", False)
        if self.use_free_roi or self.free_selecting:
            self.show_main_frame = False
            if self.free_selecting:
                self.state.selecting = True
        super().paintEvent(e)
        self.show_main_frame = saved_show
        self.state.selecting = saved_selecting

        p = QPainter(self); p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        if self.use_free_roi and len(self.free_path) >= 3:
            p.setPen(QPen(self.BORDER_COLOR, self.BORDER_WIDTH)); p.setBrush(Qt.NoBrush)
            p.drawPolygon(QPolygon(self.free_path))
        if self.free_selecting and len(self.free_path) >= 2:
            p.setPen(QPen(QColor(255,255,255,230), 2, Qt.DashLine)); p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygon(self.free_path))
            p.setPen(QPen(QColor(200,200,200,160), 1, Qt.DotLine))
            p.drawLine(self.free_path[-1], self.free_path[0])
        p.end()

    def mousePressEvent(self, e):
        if self.free_selecting and e.button() == Qt.LeftButton:
            pos = e.position().toPoint()
            self.free_path = [pos]; self._free_dragging = True; self.update(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.free_selecting and self._free_dragging:
            pos = e.position().toPoint()
            self.free_path.append(pos); self.update(); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.free_selecting and e.button() == Qt.LeftButton:
            self._free_dragging = False
            if len(self.free_path) >= 3:
                if self.free_path[-1] != self.free_path[0]:
                    self.free_path.append(self.free_path[0])
                self.use_free_roi = True
                xs = [pt.x() for pt in self.free_path]; ys = [pt.y() for pt in self.free_path]
                left, right = min(xs), max(xs); top, bottom = min(ys), max(ys)
                self.state.roi = QRect(left, top, right-left, bottom-top)
                self.edit_main = False; self.hover_edit_main = False
            else:
                self.use_free_roi = False; self.free_path = []
            self.free_selecting = False
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.update(); return

        super().mouseReleaseEvent(e)

        if self._rect_selecting_active and e.button() == Qt.LeftButton:
            self._rect_selecting_active = False
            if self.use_free_roi:
                self.use_free_roi = False; self.free_path = []; self.update()

    def _auto_edit_hover(self):
        if self.use_free_roi or self.free_selecting:
            return
        super()._auto_edit_hover()

    def _grab_roi_png_ui_thread(self) -> bytes:
        if self.use_free_roi and len(self.free_path) >= 3:
            return self._grab_free_polygon_png_ui_thread()
        return super()._grab_roi_png_ui_thread()

    def _grab_free_polygon_png_ui_thread(self) -> bytes:
        roi_box = QRect(self.state.roi)
        old_opacity = None; panel_old_opacity = None; msg_old_opacity = None
        if OST_HIDE_ON_CAPTURE or getattr(self, "msg_outside", False):
            old_opacity = self.windowOpacity(); self.setWindowOpacity(0.0)
            if self.ctrl_panel and self.ctrl_panel.isVisible():
                panel_old_opacity = self.ctrl_panel.windowOpacity(); self.ctrl_panel.setWindowOpacity(0.0)
            if self.msg_panel and self.msg_panel.isVisible():
                msg_old_opacity = self.msg_panel.windowOpacity(); self.msg_panel.setWindowOpacity(0.0)
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtCore import QThread
            QGuiApplication.processEvents(); QThread.msleep(16)

        try:
            with mss.mss() as sct:
                if OST_PRIMARY_ONLY:
                    ps = QGuiApplication.primaryScreen(); ps_geo = ps.geometry()
                    idx = max(1, min(OST_MON_INDEX, len(sct.monitors) - 1))
                    mon = sct.monitors[idx]
                    sx = mon["width"]/ps_geo.width(); sy = mon["height"]/ps_geo.height()
                    region = {
                        "left":   mon["left"] + int(roi_box.left()   * sx),
                        "top":    mon["top"]  + int(roi_box.top()    * sy),
                        "width":  max(1, int(roi_box.width()  * sx)),
                        "height": max(1, int(roi_box.height() * sy)),
                    }
                    off_x, off_y = roi_box.left(), roi_box.top()
                else:
                    global_center = self.mapToGlobal(roi_box.center())
                    scale = self._screen_scale_for_point(global_center)
                    region = {
                        "left":   int(roi_box.left()   * scale),
                        "top":    int(roi_box.top()    * scale),
                        "width":  max(1, int(roi_box.width()  * scale)),
                        "height": max(1, int(roi_box.height() * scale)),
                    }
                    sx = sy = scale; off_x, off_y = roi_box.left(), roi_box.top()

                shot = sct.grab(region)
                base = Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)

            pts = [(int((pt.x()-off_x)*sx), int((pt.y()-off_y)*sy)) for pt in self.free_path]
            mask = Image.new("L", (base.width, base.height), 0)
            d = ImageDraw.Draw(mask); d.polygon(pts, fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(0.8))  # ← 追加
            result = Image.new("RGB", base.size, (24, 24, 24)); result.paste(base, (0,0), mask)

        finally:
            if old_opacity is not None: self.setWindowOpacity(old_opacity)
            if panel_old_opacity is not None and self.ctrl_panel: self.ctrl_panel.setWindowOpacity(panel_old_opacity)
            if msg_old_opacity is not None and self.msg_panel: self.msg_panel.setWindowOpacity(msg_old_opacity)
            from PySide6.QtGui import QGuiApplication; QGuiApplication.processEvents()

        if OST_PREPROCESS:
            result = result.convert("L")
            result = ImageEnhance.Brightness(result).enhance(1.12)
            result = ImageEnhance.Contrast(result).enhance(1.32)
            result = ImageEnhance.Sharpness(result).enhance(1.1)

        if OST_SAVE_CAPTURE or DEBUG:
            os.makedirs("captures", exist_ok=True)
            result.save(os.path.join("captures", "last_main.png"), "PNG")

        buf = io.BytesIO(); result.save(buf, format="PNG"); return buf.getvalue()

# 置換：以降で使われる Overlay をラッソ対応版に差し替える
Overlay = _LassoOverlay
# ===== /LASSO INTEGRATION APPENDIX =====

if __name__ == "__main__":
    main()


# ===== FRONTMOST ENFORCER APPENDIX =====
from PySide6.QtCore import QTimer
import sys
if sys.platform == "win32":
    import ctypes
    _SetWindowPos = ctypes.windll.user32.SetWindowPos
    _HWND_TOPMOST = -1
    _SWP_NOSIZE = 0x0001
    _SWP_NOMOVE = 0x0002
    _SWP_NOACTIVATE = 0x0010
    _SWP_SHOWWINDOW = 0x0040

def _force_topmost(widget):
    try:
        if widget is None: return
        if not widget.isVisible(): return
        if sys.platform == "win32":
            hwnd = int(widget.winId())
            _SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW)
        else:
            # Non-Windows: reinforce flags & raise
            flags = widget.windowFlags()
            widget.setWindowFlags(flags | Qt.WindowStaysOnTopHint | Qt.Tool)
            widget.show()
            widget.raise_()
    except Exception:
        pass

# Hook into Overlay (lasso版) to enforce topmost periodically and on key lifecycle points
_old_init = _LassoOverlay.__init__
def _init_frontmost(self, *a, **k):
    _old_init(self, *a, **k)
    try:
        # 初期表示後・パネル作成後に強制
        _force_topmost(self)
        if hasattr(self, "ctrl_panel") and self.ctrl_panel:
            _force_topmost(self.ctrl_panel)
        if hasattr(self, "msg_panel") and self.msg_panel:
            _force_topmost(self.msg_panel)
        # 定期的に再適用（1.5秒ごと）
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(1500)
        self._topmost_timer.timeout.connect(lambda: (_force_topmost(self),
                                                     _force_topmost(getattr(self, "ctrl_panel", None)),
                                                     _force_topmost(getattr(self, "msg_panel", None))))
        self._topmost_timer.start()
    except Exception:
        pass
_LassoOverlay.__init__ = _init_frontmost

# also reinforce on select-mode transitions
_old_start_select_mode = _LassoOverlay.start_select_mode
def _start_select_mode_front(self, *a, **k):
    r = _old_start_select_mode(self, *a, **k)
    _force_topmost(self); _force_topmost(getattr(self, "ctrl_panel", None)); _force_topmost(getattr(self, "msg_panel", None))
    return r
_LassoOverlay.start_select_mode = _start_select_mode_front

_old_start_free = _LassoOverlay.start_free_select_mode
def _start_free_front(self, *a, **k):
    r = _old_start_free(self, *a, **k)
    _force_topmost(self); _force_topmost(getattr(self, "ctrl_panel", None)); _force_topmost(getattr(self, "msg_panel", None))
    return r
_LassoOverlay.start_free_select_mode = _start_free_front

# If reader window exists and is shown later, enforce there too
def _maybe_force_reader(self):
    try:
        rd = getattr(self, "reader", None)
        if rd and rd.isVisible():
            _force_topmost(rd)
    except Exception:
        pass
# piggyback on paintEvent to occasionally bring reader topmost
_old_paint = _LassoOverlay.paintEvent
def _paint_front(self, e):
    _old_paint(self, e)
    _maybe_force_reader(self)
_LassoOverlay.paintEvent = _paint_front
# ===== /FRONTMOST ENFORCER APPENDIX =====
