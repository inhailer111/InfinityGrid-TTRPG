#!/usr/bin/env python3
"""Infinite Battle Map - a nearly infinite grid drawing app for tabletop RPG maps.

Setup (Ubuntu):
    sudo apt install python3-venv libxcb-cursor0
    python3 -m venv .venv && source .venv/bin/activate
    pip install PySide6
Run:
    python3 battlemap.py [optional_map_file.json]
"""
import json
import math
import random
import re
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QRectF, QLineF, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QFont, QFontMetricsF, QIcon,
                           QKeySequence, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QUndoCommand,
                           QUndoStack, QGuiApplication, QPolygonF, QImage)
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDockWidget, QFileDialog, QFormLayout, QGraphicsItem,
                               QGraphicsPathItem, QGraphicsScene, QGraphicsView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget, QFrame, QGraphicsRectItem,
                               QStyle, QStyleOptionGraphicsItem, QHBoxLayout, QSlider)
from PySide6.QtSvg import QSvgRenderer

import worldgen   # the state generator, in worldgen.py next to this file

UNITS_PER_FT = 10         # 1 scene unit = 0.1 ft
CELL = 5 * UNITS_PER_FT   # the base 5 ft square (tokens and old maps are sized in these)
HALF = 20_000_000         # scene extends +/- this far (about 400,000 squares each way)
LIGHT_PEN, DARK_PEN = "#1f2937", "#e5e7eb"

# road style -> (colour, dashed centre line); fence style -> (colour, thickness)
ROAD_STYLES = {"Paved": ("#6b7280", True), "Cobblestone": ("#a8977a", False), "Dirt": ("#9a7b4f", False)}
BUILDING_KINDS = {"House": "#b45f3c", "Shop": "#4f6d8f", "Inn": "#8a6a3b", "Barn": "#9c3d3d",
                  "Warehouse": "#7b8088"}
FENCE_STYLES = {"Wood": ("#7a4f2a", 3), "Stone wall": ("#7f7f7f", 10), "Hedge": ("#3f7d3a", 14)}


class Snap:
    on = True          # snap-to-grid enabled
    step = CELL        # current grid step in scene units (follows the zoom level)
    dragging = False   # true only while the mouse is dragging items in Select mode


# (label, feet per grid square): the first three are the play scales, the rest are zoom-out levels
LEVELS = [("5 ft", 5), ("10 ft", 10), ("20 ft", 20), ("100 ft", 100), ("500 ft", 500),
          ("1 mile", 5280), ("5 miles", 26400)]
MIN_SQUARE_PX = 24     # the finest scale whose squares are at least this big on screen is used
JUMP_PX = 36           # on-screen square size when jumping to a scale from the toolbar
ZOOM_MIN, ZOOM_MAX = 3e-5, 8.0

TERRAIN = {"Grass": "#9cc77a", "Forest": "#3f7d4a", "Water": "#5b9bd5", "Sand": "#e3d3a1",
           "Farmland": "#cdbb6f", "Scrub": "#c2b280", "Rock": "#8d8d8d", "Snow": "#f1f5f9"}

BASE_DIR = Path(__file__).resolve().parent
MAPS_DIR, ASSETS_DIR = BASE_DIR / "maps", BASE_DIR / "assets"


class Zoom:
    dm = 1.0   # the DM view's current zoom (used to size the player-frame grab handle)
    tv = 0.0   # the player view's current zoom (0 while the player screen is closed)


class Tokens:
    min_dm = 28   # smallest a token is drawn on the DM screen, in pixels
    min_tv = 44   # smallest a token is drawn on the player screen, in pixels


def level_for_zoom(z):
    for i, (_, ft) in enumerate(LEVELS):
        if ft * UNITS_PER_FT * z >= MIN_SQUARE_PX:
            return i
    return len(LEVELS) - 1


def fmt_len(units):
    ft = units / UNITS_PER_FT
    return f"{ft / 5280:.1f} mi" if ft >= 2640 else f"{ft:,.0f} ft"

PRESETS = [("Hero", "#3b82f6", 1), ("Ally", "#22c55e", 1), ("Enemy", "#ef4444", 1),
           ("Boss", "#7f1d1d", 2), ("NPC", "#eab308", 1), ("Object", "#6b7280", 1)]


DEFAULT_CATEGORIES = [
    {"name": "Hero", "color": "#3b82f6", "size": 1, "player": True, "numbered": False},
    {"name": "Ally", "color": "#22c55e", "size": 1, "player": False, "numbered": False},
    {"name": "NPC", "color": "#eab308", "size": 1, "player": False, "numbered": False},
    {"name": "Walker", "color": "#7c8a5a", "size": 1, "player": False, "numbered": True},
    {"name": "Runner", "color": "#c2410c", "size": 1, "player": False, "numbered": True},
    {"name": "Brute", "color": "#7f1d1d", "size": 2, "player": False, "numbered": True},
    {"name": "Enemy", "color": "#ef4444", "size": 1, "player": False, "numbered": True},
    {"name": "Object", "color": "#6b7280", "size": 1, "player": False, "numbered": False},
]
CATEGORY_FILE = ASSETS_DIR / "token_categories.json"


def load_categories():
    try:
        with open(CATEGORY_FILE) as f:
            cats = json.load(f)
        cats = [c for c in cats if isinstance(c, dict) and {"name", "color", "size"} <= c.keys()]
        if cats:
            return cats
    except (OSError, ValueError, TypeError):
        pass
    return [dict(c) for c in DEFAULT_CATEGORIES]


def save_categories(cats):
    try:
        ASSETS_DIR.mkdir(exist_ok=True)
        with open(CATEGORY_FILE, "w") as f:
            json.dump(cats, f, indent=2)
    except OSError:
        pass


def spiral_offsets(n):
    """The first n cell offsets of an outward square spiral: (0,0), (1,0), (1,1), (0,1), (-1,1), ..."""
    out, x, y, dx, dy = [], 0, 0, 1, 0
    seg, walked, turns = 1, 0, 0
    while len(out) < n:
        out.append((x, y))
        x, y = x + dx, y + dy
        walked += 1
        if walked == seg:
            walked = 0
            dx, dy = -dy, dx
            turns += 1
            if turns % 2 == 0:
                seg += 1
    return out


def swatch(button, color):
    button.setStyleSheet(f"background:{QColor(color).name()}; border:1px solid #555; min-height:22px;")


def auto_initials(name):
    words = re.findall(r"[^\W\d_]+", name)
    num = re.search(r"(\d+)\s*$", name)
    if not words:
        ini = ""
    elif len(words) == 1:
        ini = words[0][0].upper() if num else words[0][:2].capitalize()
    else:
        ini = (words[0][0] + words[1][0]).upper()
    return ini + (num.group(1) if num else "")


# ----------------------------------------------------------------------------- items
class PlayerSafe:
    """Mixin: never draw selection outlines on the player screen."""

    def paint(self, p, option, widget=None):
        if widget is not None and widget.property("player_view"):
            if hidden_in_fog(self):
                return
            option = QStyleOptionGraphicsItem(option)
            option.state &= ~QStyle.State_Selected
        super().paint(p, option, widget)


class StrokeItem(PlayerSafe, QGraphicsPathItem):
    """Freehand strokes, lines and rectangles (all stored as polylines)."""

    def __init__(self, pts, color, width, fill=False):
        super().__init__()
        self.pts = [QPointF(p) for p in pts]
        self.color, self.width, self.fill = QColor(color), width, fill
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)
        self.setZValue(5)
        self.rebuild()

    def rebuild(self):
        path = QPainterPath()
        if self.pts:
            path.moveTo(self.pts[0])
            for p in self.pts[1:]:
                path.lineTo(p)
        self.setPath(path)
        self.setPen(QPen(self.color, self.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if self.fill:
            c = QColor(self.color)
            c.setAlpha(70)
            self.setBrush(QBrush(c))
        else:
            self.setBrush(Qt.NoBrush)

    def add_point(self, p):
        self.pts.append(QPointF(p))
        path = self.path()
        path.lineTo(p)
        self.setPath(path)

    def to_dict(self):
        return {"type": "stroke", "color": self.color.name(), "width": self.width, "fill": self.fill,
                "x": self.pos().x(), "y": self.pos().y(), "pts": [[p.x(), p.y()] for p in self.pts]}


class TokenItem(QGraphicsItem):
    """A round character token with initials and an optional name label."""
    snap = True

    def __init__(self, name, initials, color, size=1, player=False, vision_mod=0):
        super().__init__()
        self.name, self.initials, self.color, self.size = name, initials, QColor(color), size
        self.player, self.vision_mod = player, vision_mod   # player tokens share vision; modifier is in feet
        self._D = self.enlarged()   # diameter it is drawn at, in scene units
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(10)

    def props(self):
        return {"name": self.name, "initials": self.initials, "color": self.color.name(), "size": self.size,
                "player": self.player, "vision_mod": self.vision_mod}

    def set_props(self, d):
        self.prepareGeometryChange()
        self.name, self.initials, self.size = d["name"], d["initials"], d["size"]
        self.player, self.vision_mod = d.get("player", False), d.get("vision_mod", 0)
        self.color = QColor(d["color"])
        self._D = self.enlarged()
        self.update()

    def to_dict(self):
        d = {"type": "token", "x": self.pos().x(), "y": self.pos().y()}
        d.update(self.props())
        return d

    def circle(self):
        d = self.size * CELL
        return QRectF(3, 3, d - 6, d - 6)

    def center(self):
        d = self.size * CELL
        return QPointF(self.pos().x() + d / 2, self.pos().y() + d / 2)

    def enlarged(self):
        """Drawn diameter in scene units: its true size, or bigger when zoomed out so it stays readable."""
        d = self.size * CELL
        tv = Tokens.min_tv / Zoom.tv if Zoom.tv > 0 else 0
        return max(d, Tokens.min_dm / max(Zoom.dm, 1e-9), tv)

    def refresh_geometry(self):
        big = self.enlarged()
        if abs(big - self._D) > 1e-6:
            self.prepareGeometryChange()
            self._D = big
            self.update()

    def boundingRect(self):
        d, D = self.size * CELL, self._D
        half = D / 2
        return QRectF(d / 2 - half - CELL / 2 - 4, d / 2 - half - 4, D + CELL + 8, D + 26)

    def shape(self):
        p = QPainterPath()
        d = self.size * CELL
        k = self._D / d   # the clickable area grows with the drawn token
        p.addEllipse(QPointF(d / 2, d / 2), (d / 2 - 3) * k, (d / 2 - 3) * k)
        return p

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and Snap.on and Snap.dragging:
            g = Snap.step
            return QPointF(round(value.x() / g) * g, round(value.y() / g) * g)
        return super().itemChange(change, value)

    def paint(self, p, option, widget=None):
        on_tv = widget is not None and widget.property("player_view")
        if (on_tv and Vision.fog_on and not self.player
                and not Vision.visible.contains(self.center())):
            return   # tokens standing in fog are invisible to the players
        p.setRenderHint(QPainter.Antialiasing)
        z = p.worldTransform().m11()
        k = max(1.0, (Tokens.min_tv if on_tv else Tokens.min_dm) / (self.size * CELL * z))
        p.save()
        if k > 1.0:   # zoomed out: draw the token larger, around its true centre, so it stays readable
            c = QPointF(self.size * CELL / 2, self.size * CELL / 2)
            p.translate(c)
            p.scale(k, k)
            p.translate(-c)
        r = self.circle()
        edge = QColor("#f59e0b") if self.isSelected() and not on_tv else self.color.darker(170)
        p.setPen(QPen(edge, 3))
        p.setBrush(self.color)
        p.drawEllipse(r)
        if self.player:   # white ring marks the tokens that give the party vision
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(6, 6, -6, -6))
        lum = 0.299 * self.color.red() + 0.587 * self.color.green() + 0.114 * self.color.blue()
        p.setPen(QColor("#111") if lum > 150 else QColor("#fff"))
        if self.initials:
            font = QFont()
            font.setBold(True)
            px = int(r.height() * 0.42)
            font.setPixelSize(px)
            while px > 6 and QFontMetricsF(font).horizontalAdvance(self.initials) > r.width() * 0.8:
                px -= 1
                font.setPixelSize(px)
            p.setFont(font)
            p.drawText(r, Qt.AlignCenter, self.initials)
        if self.name and k == 1.0:   # names appear once the token is at its true size
            font = QFont()
            font.setPixelSize(13)
            p.setFont(font)
            d = self.size * CELL
            fm = QFontMetricsF(font)
            text = fm.elidedText(self.name, Qt.ElideRight, d + CELL - 14)
            w = fm.horizontalAdvance(text) + 10
            box = QRectF((d - w) / 2, d + 2, w, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 205))
            p.drawRoundedRect(box, 5, 5)
            p.setPen(QColor("#111"))
            p.drawText(box, Qt.AlignCenter, text)
        p.restore()


ENV_FLAGS = (QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
             QGraphicsItem.ItemSendsGeometryChanges)


class Snappy:
    """Mixin: item positions snap to the grid while dragging."""

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and Snap.on and Snap.dragging:
            g = Snap.step
            return QPointF(round(value.x() / g) * g, round(value.y() / g) * g)
        return super().itemChange(change, value)


class PolyItem(PlayerSafe, Snappy, QGraphicsPathItem):
    """Base for roads and fences: a straight segment stored as points."""
    kind = ""

    def __init__(self, pts, style, width=0):
        super().__init__()
        self.pts, self.style, self.width = [QPointF(p) for p in pts], style, width
        self.setFlags(ENV_FLAGS)
        self.rebuild()

    def rebuild(self):
        path = QPainterPath()
        path.moveTo(self.pts[0])
        for p in self.pts[1:]:
            path.lineTo(p)
        self.setPath(path)
        self.style_pen()

    def to_dict(self):
        return {"type": self.kind, "style": self.style, "width": self.width,
                "x": self.pos().x(), "y": self.pos().y(), "pts": [[p.x(), p.y()] for p in self.pts]}


class RoadItem(PolyItem):
    kind = "road"

    def __init__(self, pts, style="Paved", width=20):   # width in feet
        super().__init__(pts, style, width)
        self.setZValue(-5)

    def to_dict(self):
        d = super().to_dict()
        d["width_ft"] = d.pop("width")
        return d

    def style_pen(self):
        self.setPen(QPen(QColor(ROAD_STYLES[self.style][0]), self.width * UNITS_PER_FT,
                         Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        self.setBrush(Qt.NoBrush)

    def paint(self, p, option, widget=None):
        z = p.worldTransform().m11()
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(Qt.NoBrush)
        # true width in feet, but never thinner than 3 px so roads stay visible when zoomed out
        w = max(self.width * UNITS_PER_FT, 3 / z)
        p.setPen(QPen(QColor(ROAD_STYLES[self.style][0]), w, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        p.drawPath(self.path())
        if self.isSelected() and not (widget is not None and widget.property("player_view")):
            sel = QPen(QColor("#f59e0b"), 2, Qt.DashLine)
            sel.setCosmetic(True)
            p.setPen(sel)
            p.drawPath(self.shape())
        if ROAD_STYLES[self.style][1] and self.width * UNITS_PER_FT * z >= 6:
            mid = QPen(QColor("#f3efe0"), 2, Qt.DashLine)
            mid.setCosmetic(True)
            p.setPen(mid)
            p.drawPath(self.path())


class FenceItem(PolyItem):
    kind = "fence"

    def __init__(self, pts, style="Wood", width=1):
        super().__init__(pts, style, width or 1)
        self.setZValue(3)

    def style_pen(self):
        color, w = FENCE_STYLES[self.style]
        self.setPen(QPen(QColor(color), w * self.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        self.setBrush(Qt.NoBrush)

    def boundingRect(self):
        m = 6 * self.width
        return super().boundingRect().adjusted(-m, -m, m, m)

    def shape(self):  # generous hit area so thin fences are easy to click/erase
        st = QPainterPathStroker()
        st.setWidth(max(self.pen().widthF(), 14 * self.width))
        return st.createStroke(self.path())

    def paint(self, p, option, widget=None):
        if widget is not None and widget.property("player_view") and hidden_in_fog(self):
            return
        p.setRenderHint(QPainter.Antialiasing)
        super().paint(p, option, widget)
        if self.style == "Wood":
            n = max(1, round(self.path().length() / (CELL / 2 * self.width)))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#4a2f17"))
            for i in range(n + 1):
                p.drawEllipse(self.path().pointAtPercent(i / n), 4 * self.width, 4 * self.width)
        elif self.style == "Stone wall":
            p.setPen(QPen(QColor("#bdbdbd"), 4 * self.width, Qt.SolidLine, Qt.RoundCap))
            p.drawPath(self.path())


class BuildingItem(Snappy, QGraphicsItem):
    """A rectangular building (house, shop, ...) with a roof ridge, door and optional label."""

    def __init__(self, kind, w, h, label=""):
        super().__init__()
        self.kind, self.w, self.h, self.label = kind, w, h, label
        self.setFlags(ENV_FLAGS)
        self.setZValue(2)

    def props(self):
        return {"kind": self.kind, "label": self.label}

    def set_props(self, d):
        self.kind, self.label = d["kind"], d["label"]
        self.update()

    def set_rect(self, r):
        self.prepareGeometryChange()
        self.setPos(r.topLeft())
        self.w, self.h = r.width(), r.height()

    def to_dict(self):
        return {"type": "building", "kind": self.kind, "label": self.label, "w": self.w, "h": self.h,
                "x": self.pos().x(), "y": self.pos().y()}

    def boundingRect(self):
        return QRectF(-4, -4, self.w + 8, self.h + 8)

    def shape(self):
        p = QPainterPath()
        p.addRect(0, 0, self.w, self.h)
        return p

    def paint(self, p, option, widget=None):
        if widget is not None and widget.property("player_view") and hidden_in_fog(self):
            return
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.w, self.h
        base = QColor(BUILDING_KINDS.get(self.kind, "#888888"))
        p.setPen(Qt.NoPen)
        if w >= h:   # pitched roof: two shaded halves and a ridge
            p.fillRect(QRectF(0, 0, w, h / 2), base.lighter(115))
            p.fillRect(QRectF(0, h / 2, w, h / 2), base.darker(115))
            ridge = QLineF(8, h / 2, w - 8, h / 2)
        else:
            p.fillRect(QRectF(0, 0, w / 2, h), base.lighter(115))
            p.fillRect(QRectF(w / 2, 0, w / 2, h), base.darker(115))
            ridge = QLineF(w / 2, 8, w / 2, h - 8)
        ridge_pen = QPen(base.darker(160), 1.5)
        ridge_pen.setCosmetic(True)
        p.setPen(ridge_pen)
        p.drawLine(ridge)
        p.setBrush(Qt.NoBrush)
        on_tv = widget is not None and widget.property("player_view")
        wall = QPen(QColor("#f59e0b") if self.isSelected() and not on_tv else QColor("#2b2b2b"), 3)
        wall.setCosmetic(True)
        p.setPen(wall)
        p.drawRect(QRectF(0, 0, w, h))
        if w >= 60:   # 4 ft door on the bottom wall
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#3a2a1a"))
            p.drawRect(QRectF(w / 2 - 20, h - 6, 40, 12))
        z = p.worldTransform().m11()
        if self.label and w * z > 40:   # label keeps a constant on-screen size at every zoom
            lum = 0.299 * base.red() + 0.587 * base.green() + 0.114 * base.blue()
            font = QFont()
            font.setBold(True)
            font.setPixelSize(13)
            sw, sh = w * z, h * z
            p.save()
            p.translate(w / 2, h / 2)
            p.scale(1 / z, 1 / z)
            p.setFont(font)
            p.setPen(QColor("#111") if lum > 150 else QColor("#fff"))
            text = QFontMetricsF(font).elidedText(self.label, Qt.ElideRight, sw - 10)
            p.drawText(QRectF(-sw / 2, -sh / 2, sw, sh), Qt.AlignCenter, text)
            p.restore()


class TerrainItem(QGraphicsPathItem):
    """Ground cover (water, forest, ...) painted with a round brush or a filled rectangle.
    Locked in place so it never gets in the way of selecting things; remove it with the Eraser."""

    def __init__(self, pts, terrain, mode="brush", width_ft=20):
        super().__init__()
        self.pts, self.terrain, self.mode = [QPointF(p) for p in pts], terrain, mode
        self.width_ft = width_ft
        self.setZValue(-10)
        self.rebuild()

    def rebuild(self):
        path = QPainterPath()
        if self.pts:
            path.moveTo(self.pts[0])
            for p in self.pts[1:]:
                path.lineTo(p)
        self.setPath(path)
        color = QColor(TERRAIN[self.terrain])
        if self.mode == "rect":
            self.setPen(QPen(Qt.NoPen))
            self.setBrush(QBrush(color))
        else:
            self.setPen(QPen(color, self.width_ft * UNITS_PER_FT, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            self.setBrush(Qt.NoBrush)

    def add_point(self, p):
        self.pts.append(QPointF(p))
        path = self.path()
        path.lineTo(p)
        self.setPath(path)

    def to_dict(self):
        return {"type": "terrain", "terrain": self.terrain, "mode": self.mode, "width_ft": self.width_ft,
                "x": self.pos().x(), "y": self.pos().y(), "pts": [[p.x(), p.y()] for p in self.pts]}


class PropItem(Snappy, QGraphicsItem):
    """A top-down prop (car, barricade, rubble...) drawn from an SVG file in the assets folder."""

    def __init__(self, kind, w, h, rot=0):
        super().__init__()
        self.kind, self.w, self.h, self.rot = kind, w, h, rot
        self.setFlags(ENV_FLAGS)
        self.setZValue(4)
        self.setTransformOriginPoint(w / 2, h / 2)
        self.setRotation(rot)

    def boundingRect(self):
        return QRectF(-4, -4, self.w + 8, self.h + 8)

    def shape(self):
        p = QPainterPath()
        p.addRect(0, 0, self.w, self.h)
        return p

    def to_dict(self):
        return {"type": "prop", "kind": self.kind, "w": self.w, "h": self.h, "rot": self.rot,
                "x": self.pos().x(), "y": self.pos().y()}

    def paint(self, p, option, widget=None):
        on_tv = widget is not None and widget.property("player_view")
        if on_tv and hidden_in_fog(self):
            return
        p.setRenderHint(QPainter.Antialiasing)
        r = svg_renderer(self.kind)
        if r is not None and r.isValid():
            r.render(p, QRectF(0, 0, self.w, self.h))
        else:
            p.setBrush(QColor("#888888"))
            p.setPen(QPen(QColor("#222222"), 2))
            p.drawRect(QRectF(0, 0, self.w, self.h))
        if self.isSelected() and not on_tv:
            pen = QPen(QColor("#f59e0b"), 2, Qt.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(0, 0, self.w, self.h))


class RevealItem(TerrainItem):
    """An area the DM reveals to the players by hand. Only a dashed outline on the DM screen."""

    def __init__(self, pts, mode="brush", width_ft=20):
        super().__init__(pts, "Grass", mode, width_ft)
        self.setZValue(-8)

    def rebuild(self):
        super().rebuild()
        self.setBrush(Qt.NoBrush)

    def area(self):
        return self.shape().translated(self.pos())

    def to_dict(self):
        d = super().to_dict()
        d["type"] = "reveal"
        return d

    def paint(self, p, option, widget=None):
        if widget is not None and widget.property("player_view"):
            return
        pen = QPen(QColor("#22d3ee"), 1.5, Qt.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(QColor(34, 211, 238, 28))
        p.drawPath(self.shape())


class PlayerFrame(QGraphicsRectItem):
    """The window the players see through. Drag its border on the DM map to pan the player view."""

    def __init__(self, on_move):
        super().__init__(0, 0, 1000, 600)
        self.on_move = on_move
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(100)

    def margin(self):
        return 10 / max(Zoom.dm, 1e-9)   # about 10 screen pixels

    def refresh(self):
        self.prepareGeometryChange()
        self.update()

    def set_view_rect(self, r):
        self.prepareGeometryChange()
        self.setRect(0, 0, r.width(), r.height())
        self.setPos(r.topLeft())

    def boundingRect(self):
        m = self.margin()
        return self.rect().adjusted(-m, -m, m, m)

    def shape(self):   # border only, so clicks inside the frame still reach tokens underneath
        m = self.margin()
        outer, inner = QPainterPath(), QPainterPath()
        outer.addRect(self.rect().adjusted(-m, -m, m, m))
        inner.addRect(self.rect().adjusted(m, m, -m, -m))
        return outer.subtracted(inner)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.on_move()
        return super().itemChange(change, value)

    def paint(self, p, option, widget=None):
        if widget is not None and widget.property("player_view"):
            return
        pen = QPen(QColor("#22d3ee"), 3, Qt.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(self.rect())


def item_from_dict(d):
    if d["type"] == "token":
        it = TokenItem(d["name"], d["initials"], d["color"], d["size"], d.get("player", False),
                       d.get("vision_mod", 0))
    elif d["type"] == "building":
        it = BuildingItem(d["kind"], d["w"], d["h"], d.get("label", ""))
    elif d["type"] == "road":
        it = RoadItem([QPointF(x, y) for x, y in d["pts"]], d["style"],
                      d["width_ft"] if "width_ft" in d else d["width"] * 5)   # old maps stored squares
    elif d["type"] == "fence":
        it = FenceItem([QPointF(x, y) for x, y in d["pts"]], d["style"], d.get("width") or 1)
    elif d["type"] == "prop":
        it = PropItem(d["kind"], d["w"], d["h"], d.get("rot", 0))
    elif d["type"] == "reveal":
        it = RevealItem([QPointF(x, y) for x, y in d["pts"]], d["mode"], d["width_ft"])
    elif d["type"] == "terrain":
        it = TerrainItem([QPointF(x, y) for x, y in d["pts"]], d["terrain"], d["mode"], d["width_ft"])
    else:
        it = StrokeItem([QPointF(x, y) for x, y in d["pts"]], d["color"], d["width"], d.get("fill", False))
    it.setPos(d["x"], d["y"])
    return it


# ----------------------------------------------------------------------------- vision / fog of war
class Vision:
    night = False
    fog_on = True
    show_on_dm = True
    fog_alpha = 140   # 0-255: how dark the fog is on the player screen
    remember = True
    day_ft, night_ft = 200, 40
    players = 0
    visible = QPainterPath()    # what the party sees right now (also decides which tokens are shown)
    show = QPainterPath()       # visible area plus the walls they can see: the hole in the fog
    seen = QPainterPath()       # explored areas plus show
    explored = QPainterPath()
    explored_polys = []
    last_pos = {}
    seen_owners = set()
    shown_ids = set()   # buildings and hard fences the party can see right now


def vision_range_ft(tok):
    return max(10, (Vision.night_ft if Vision.night else Vision.day_ft) + tok.vision_mod)


def poly_path(pts):
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
    path.closeSubpath()
    return path


def reset_explored(polys=()):
    Vision.explored = QPainterPath()
    Vision.explored_polys = []
    Vision.last_pos = {}
    Vision.seen_owners = set()
    for flat in polys:
        pts = [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)]
        Vision.explored_polys.append(pts)
        Vision.explored = Vision.explored.united(poly_path(pts))


def wall_segments(item):
    """Line segments that block sight: building walls, stone walls and hedges (wooden fences don't)."""
    if isinstance(item, BuildingItem):
        x, y = item.pos().x(), item.pos().y()
        pts = [(x, y), (x + item.w, y), (x + item.w, y + item.h), (x, y + item.h), (x, y)]
    elif isinstance(item, FenceItem) and item.style != "Wood":
        pts = [(p.x() + item.pos().x(), p.y() + item.pos().y()) for p in item.pts]
    else:
        return None
    return [(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) for i in range(len(pts) - 1)]


def segs_near(segs, c, R):
    xs = [v for s in segs for v in (s[0], s[2])]
    ys = [v for s in segs for v in (s[1], s[3])]
    return min(xs) <= c.x() + R and max(xs) >= c.x() - R and min(ys) <= c.y() + R and max(ys) >= c.y() - R


def visibility_polygon(cx, cy, R, walls):
    """Ray-cast outline of what can be seen from (cx, cy) within R, stopped by wall segments."""
    segs = [(s, owner) for segments, owner in walls for s in segments]
    two_pi = 2 * math.pi
    angles = [two_pi * i / 90 for i in range(90)]
    if len(segs) < 400:   # extra rays just either side of every wall corner keep edges crisp
        for (x1, y1, x2, y2), _ in segs:
            for x, y in ((x1, y1), (x2, y2)):
                if (x - cx) ** 2 + (y - cy) ** 2 <= R * R:
                    a = math.atan2(y - cy, x - cx)
                    angles += [(a - 1e-4) % two_pi, a % two_pi, (a + 1e-4) % two_pi]
    angles.sort()
    pts, hits = [], set()
    for a in angles:
        dx, dy = math.cos(a), math.sin(a)
        best, owner_hit = R, None
        for (x1, y1, x2, y2), owner in segs:
            sx, sy = x2 - x1, y2 - y1
            den = dx * sy - dy * sx
            if abs(den) < 1e-12:
                continue
            t = ((x1 - cx) * sy - (y1 - cy) * sx) / den
            if 1e-6 < t < best:
                u = ((x1 - cx) * dy - (y1 - cy) * dx) / den
                if -1e-9 <= u <= 1 + 1e-9:
                    best, owner_hit = t, owner
        if owner_hit is not None:
            hits.add(owner_hit)
        pts.append((cx + dx * best, cy + dy * best))
    return pts, hits


def owner_shape(owner):
    if isinstance(owner, BuildingItem):
        path = QPainterPath()
        path.addRect(QRectF(owner.pos().x(), owner.pos().y(), owner.w, owner.h))
        return path
    return owner.shape().translated(owner.pos())


def hidden_in_fog(item):
    """True when the player screen should not draw this item because the party cannot see it right now.
    Ground (terrain and roads) is never hidden: the players know the geography."""
    if not Vision.fog_on:
        return False
    if isinstance(item, BuildingItem) or (isinstance(item, FenceItem) and item.style != "Wood"):
        return id(item) not in Vision.shown_ids
    if isinstance(item, (StrokeItem, FenceItem, PropItem)):   # drawings, wooden fences, props: only in current sight
        return not Vision.show.intersects(item.sceneBoundingRect())
    return False


def record_explored(tok, c, R, pts, hits):
    last = Vision.last_pos.get(id(tok))
    if last is not None and QLineF(last, c).length() < R / 2:
        return
    if len(Vision.explored_polys) >= 400:
        return
    Vision.last_pos[id(tok)] = QPointF(c)
    simple = pts[::3] if len(pts) > 60 else pts
    Vision.explored_polys.append(simple)
    Vision.explored = Vision.explored.united(poly_path(simple))
    for owner in hits:
        if isinstance(owner, BuildingItem) and id(owner) not in Vision.seen_owners:
            Vision.seen_owners.add(id(owner))
            x, y = owner.pos().x(), owner.pos().y()
            box = [(x, y), (x + owner.w, y), (x + owner.w, y + owner.h), (x, y + owner.h)]
            Vision.explored_polys.append(box)
            Vision.explored = Vision.explored.united(poly_path(box))


def update_vision(scene):
    tokens, reveals, walls = [], [], []
    for it in scene.items():
        if isinstance(it, TokenItem):
            if it.player:
                tokens.append(it)
        elif isinstance(it, RevealItem):
            reveals.append(it)
        else:
            segs = wall_segments(it)
            if segs:
                walls.append((segs, it))
    Vision.players = len(tokens)
    visible, show = QPainterPath(), QPainterPath()
    shown = set()
    for tok in tokens:
        c = tok.center()
        R = vision_range_ft(tok) * UNITS_PER_FT
        near = [(segs, owner) for segs, owner in walls if segs_near(segs, c, R)]
        pts, hits = visibility_polygon(c.x(), c.y(), R, near)
        visible = visible.united(poly_path(pts))
        for owner in hits:
            show = show.united(owner_shape(owner))
            shown.add(id(owner))
        if Vision.remember:
            record_explored(tok, c, R, pts, hits)
    for r in reveals:   # buildings inside a hand-revealed area count as seen
        area = r.area()
        visible = visible.united(area)
        for _segs, owner in walls:
            if area.intersects(owner.sceneBoundingRect()):
                shown.add(id(owner))
    Vision.visible, Vision.show = visible, show.united(visible)
    Vision.seen = Vision.explored.united(Vision.show) if Vision.remember else Vision.show
    Vision.shown_ids = shown   # current sight only: explored areas keep their ground, not their buildings


def ring(rect, hole):
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    path.addRect(rect)
    path.addPath(hole)
    return path


def draw_overlay(view, p, rect, is_player):
    """Night tint and fog of war, painted over the map (strong on the player screen, a preview on the DM's)."""
    if Vision.night:
        p.fillRect(rect, QColor(10, 20, 70, 130 if is_player else 95))
    if not Vision.fog_on or not (is_player or Vision.show_on_dm):
        return
    r, g, b = 5, 6, 10
    if is_player:   # the slider sets how dark unexplored fog is; explored areas are lighter still
        fog = Vision.fog_alpha
        mem = int(fog * 150 / 255)
        top = int(round(255 * (1 - (1 - fog / 255) / (1 - mem / 255))))   # second layer: unexplored totals `fog`
    else:
        fog, mem, top = 100, 60, 100
    if Vision.remember:   # explored areas stay visible but dimmed
        p.fillPath(ring(rect, Vision.show), QBrush(QColor(r, g, b, mem)))
        p.fillPath(ring(rect, Vision.seen), QBrush(QColor(r, g, b, top)))
    else:
        p.fillPath(ring(rect, Vision.show), QBrush(QColor(r, g, b, fog)))
    if is_player and Vision.players == 0 and Vision.visible.isEmpty():
        p.save()
        p.resetTransform()
        p.setPen(QColor("#9ca3af"))
        p.drawText(view.viewport().rect(), Qt.AlignCenter,
                   "No player tokens yet.\nMark a token as a Player token to give the party vision.")
        p.restore()


# ----------------------------------------------------------------------------- generated world
DETAIL_ZOOM = 0.1   # street-level detail is generated once you zoom in this far
PROP_UNITS = {k: (w * UNITS_PER_FT, h * UNITS_PER_FT) for k, (w, h) in worldgen.PROP_FT.items()}
ZOMBIE_LOOK = {"Walker": ("#7c8a5a", 1), "Runner": ("#c2410c", 1), "Brute": ("#7f1d1d", 2)}

_WHEELS = ('<rect x="20" y="1" width="26" height="9" rx="3" fill="#111"/><rect x="104" y="1" width="26" height="9" rx="3" fill="#111"/>'
           '<rect x="20" y="55" width="26" height="9" rx="3" fill="#111"/><rect x="104" y="55" width="26" height="9" rx="3" fill="#111"/>')
PROP_SVGS = {   # written to the assets folder on first run; replace any of them with your own art
    "car": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 150 65">'
            '<rect x="3" y="8" width="144" height="49" rx="14" fill="#6f7f93" stroke="#20252d" stroke-width="3"/>'
            '<rect x="40" y="13" width="62" height="39" rx="7" fill="#8b9bb0" stroke="#20252d" stroke-width="2"/>'
            '<rect x="104" y="14" width="24" height="37" rx="5" fill="#26303d"/><rect x="22" y="15" width="14" height="35" rx="4" fill="#26303d"/>'
            + _WHEELS + '<rect x="143" y="14" width="6" height="10" rx="2" fill="#f4e7a1"/><rect x="143" y="41" width="6" height="10" rx="2" fill="#f4e7a1"/></svg>'),
    "wreck": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 150 65">'
              '<rect x="3" y="8" width="144" height="49" rx="14" fill="#8a4b2d" stroke="#1a1410" stroke-width="3"/>'
              '<rect x="40" y="13" width="62" height="39" rx="7" fill="#6b4a3a" stroke="#1a1410" stroke-width="2"/>'
              '<polygon points="104,14 128,20 122,34 128,48 104,51" fill="#2a2f36"/>'
              '<path d="M44 16 L70 34 L52 50 M70 34 L98 20" stroke="#cfd6dd" stroke-width="1.5" fill="none"/>'
              '<ellipse cx="62" cy="34" rx="30" ry="16" fill="#1a1410" fill-opacity="0.45"/>'
              + _WHEELS + '</svg>'),
    "barricade": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 35">'
                  '<rect x="2" y="3" width="116" height="8" rx="2" fill="#8b5e34" stroke="#2b1a0c" stroke-width="1.5"/>'
                  '<rect x="2" y="13" width="116" height="8" rx="2" fill="#a07040" stroke="#2b1a0c" stroke-width="1.5"/>'
                  '<rect x="2" y="23" width="116" height="8" rx="2" fill="#7a5230" stroke="#2b1a0c" stroke-width="1.5"/>'
                  '<path d="M12 2 L42 33 M78 2 L108 33" stroke="#4a3018" stroke-width="5"/>'
                  '<circle cx="8" cy="7" r="1.6" fill="#ddd"/><circle cx="112" cy="27" r="1.6" fill="#ddd"/></svg>'),
    "rubble": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 100">'
               '<polygon points="10,70 40,40 70,60 60,90 20,92" fill="#8a8680" stroke="#3b3935" stroke-width="2"/>'
               '<polygon points="60,50 95,30 112,62 84,82" fill="#6f6a63" stroke="#3b3935" stroke-width="2"/>'
               '<polygon points="30,30 55,14 66,38 40,48" fill="#a29c92" stroke="#3b3935" stroke-width="2"/>'
               '<polygon points="75,78 100,70 108,92 82,96" fill="#5b5750" stroke="#3b3935" stroke-width="2"/>'
               '<rect x="22" y="58" width="22" height="7" fill="#7a5a3a" transform="rotate(-24 30 60)"/></svg>'),
    "overgrowth": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 160">'
                   '<g fill="#3f7a3a" fill-opacity="0.78" stroke="#25521f" stroke-width="2">'
                   '<circle cx="60" cy="80" r="42"/><circle cx="110" cy="62" r="46"/><circle cx="140" cy="102" r="38"/>'
                   '<circle cx="86" cy="116" r="34"/></g>'
                   '<g fill="#5fa04f" fill-opacity="0.8"><circle cx="70" cy="70" r="16"/><circle cx="118" cy="58" r="18"/>'
                   '<circle cx="132" cy="104" r="14"/></g></svg>'),
}


def ensure_assets():
    ASSETS_DIR.mkdir(exist_ok=True)
    for name, svg in PROP_SVGS.items():
        f = ASSETS_DIR / f"{name}.svg"
        if not f.exists():
            f.write_text(svg)


_SVG = {}


def svg_renderer(kind):
    r = _SVG.get(kind)
    if r is None:
        f = ASSETS_DIR / f"{kind}.svg"
        r = QSvgRenderer(str(f)) if f.exists() else False
        _SVG[kind] = r
    return r or None


class Atlas:
    current = None   # the generated state, if there is one
    warned = False


_QPATHS = {}   # road and river polylines already turned into painter paths


def world_path(pts):
    path = _QPATHS.get(id(pts))
    if path is None:
        path = QPainterPath(QPointF(pts[0][0] * UNITS_PER_FT, pts[0][1] * UNITS_PER_FT))
        for x, y in pts[1:]:
            path.lineTo(x * UNITS_PER_FT, y * UNITS_PER_FT)
        _QPATHS[id(pts)] = path
    return path


def world_palette():
    return [QColor(TERRAIN[n]).getRgb()[:3] for n in worldgen.BIOMES]


def render_terrain(w, vr, z, nx, ny, B):
    """Sample the land every B screen pixels and return it as a small image that is stretched over the view."""
    pal = world_palette()
    step = B / (z * UNITS_PER_FT)   # feet per sample
    x0, y0 = vr.left() / UNITS_PER_FT, vr.top() / UNITS_PER_FT
    buf = bytearray(nx * ny * 4)
    terrain, biome = w.terrain, w.biome
    for j in range(ny):
        y = y0 + (j + 0.5) * step
        for i in range(nx):
            x = x0 + (i + 0.5) * step
            e, m = terrain(x, y)
            r, g, b = pal[biome(e, m, x, y)]
            shade = 0.80 + 0.5 * e
            k = (j * nx + i) * 4
            buf[k], buf[k + 1], buf[k + 2], buf[k + 3] = min(255, int(b * shade)), min(255, int(g * shade)), min(255, int(r * shade)), 255
    return QImage(bytes(buf), nx, ny, nx * 4, QImage.Format_RGB32).copy()


DOT_ODDS = {worldgen.FOREST: 0.80, worldgen.SCRUB: 0.30, worldgen.GRASS: 0.14, worldgen.FARM: 0.30,
            worldgen.WATER: 0.10, worldgen.ROCK: 0.35, worldgen.SNOW: 0.08, worldgen.SAND: 0.12}
DOT_STYLE = {worldgen.FOREST: ((30, 84, 46, 215), 6, 7, False), worldgen.SCRUB: ((142, 122, 70, 200), 2.5, 2, False),
             worldgen.GRASS: ((96, 150, 70, 190), 1.5, 1.5, False), worldgen.FARM: ((170, 146, 66, 210), 6, 6, True),
             worldgen.WATER: ((255, 255, 255, 120), 4, 4, True), worldgen.ROCK: ((105, 105, 110, 210), 2.5, 3, False),
             worldgen.SNOW: ((205, 220, 240, 220), 1.5, 2, False), worldgen.SAND: ((196, 172, 120, 200), 1.2, 1.2, False)}


def render_dots(w, vr, z):
    """Trees, scrub, furrows and waves: a texture sprinkled over the ground at a constant screen density."""
    if z < 0.0005:
        return {}
    cell = 2 ** math.ceil(math.log2(40 / (z * UNITS_PER_FT)))   # feet; steps by powers of two so it stays put
    fx0, fy0 = vr.left() / UNITS_PER_FT, vr.top() / UNITS_PER_FT
    i0, i1 = math.floor(fx0 / cell), math.floor(vr.right() / UNITS_PER_FT / cell)
    j0, j1 = math.floor(fy0 / cell), math.floor(vr.bottom() / UNITS_PER_FT / cell)
    if (i1 - i0 + 1) * (j1 - j0 + 1) > 3500:
        return {}
    seed, ppf, out = w.seed, z * UNITS_PER_FT, {}
    for j in range(j0, j1 + 1):
        for i in range(i0, i1 + 1):
            r1, r2 = worldgen.hash2(i, j, seed + 31), worldgen.hash2(j, i, seed + 57)
            x, y = (i + r1) * cell, (j + r2) * cell
            e, m = w.terrain(x, y)
            b = w.biome(e, m, x, y)
            if worldgen.hash2(i * 7 + 1, j * 13 + 5, seed + 77) <= DOT_ODDS[b]:
                out.setdefault(b, []).append(((x - fx0) * ppf, (y - fy0) * ppf, 0.6 + 0.8 * r1))
    return out


def draw_dots(p, dots):
    for b, lst in dots.items():
        color, base, scale, dash = DOT_STYLE[b]
        if dash:
            p.setPen(QPen(QColor(*color), 1.6))
            for x, y, sz in lst:
                r = base + scale * sz * 0.5
                p.drawLine(QPointF(x - r, y), QPointF(x + r, y))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*color))
            for x, y, sz in lst:
                r = base + scale * sz
                p.drawEllipse(QPointF(x, y), r, r)


ROAD_LOOK = {"highway": ("#4b5160", "#8a90a0", 2.6), "county": ("#6b6455", "#b8b09a", 1.5), "spur": ("#6b6455", "#c2b79c", 1.1)}
SYMBOL_PX = {"city": 6, "town": 4, "village": 3, "farm": 2.5}
SYMBOL_COLOR = {"city": "#374151", "town": "#4b5563", "village": "#6b7280", "farm": "#8b5e34"}


def draw_world(view, p, rect):
    """The generated land: terrain, trees and texture, rivers, highways and roads, and settlement markers."""
    w = Atlas.current
    z = view.transform().m11()
    vp = view.viewport()
    W, H = vp.width(), vp.height()
    if W <= 0 or H <= 0:
        return
    vr = view.mapToScene(vp.rect()).boundingRect()
    B = 28
    nx, ny = -(-W // B), -(-H // B)
    key = (round(vr.left(), 2), round(vr.top(), 2), z, W, H, id(w))
    cache = getattr(view, "_world_cache", None)
    if cache is None or cache[0] != key:
        cache = (key, render_terrain(w, vr, z, nx, ny, B), render_dots(w, vr, z))
        view._world_cache = cache
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.drawImage(QRectF(vr.left(), vr.top(), nx * B / z, ny * B / z), cache[1])
    p.save()
    p.resetTransform()
    draw_dots(p, cache[2])
    p.restore()
    fx0, fy0 = vr.left() / UNITS_PER_FT, vr.top() / UNITS_PER_FT
    fx1, fy1 = vr.right() / UNITS_PER_FT, vr.bottom() / UNITS_PER_FT
    p.setBrush(Qt.NoBrush)
    for rv in w.rivers:
        if worldgen.overlaps(rv["bbox"], fx0, fy0, fx1, fy1):
            p.setPen(QPen(QColor("#4f93c9"), max(rv["width"] * UNITS_PER_FT, 2.4 / z), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(world_path(rv["pts"]))
    for road in w.roads_in_rect(fx0, fy0, fx1, fy1, z > 0.0003):
        casing, fill, minpx = ROAD_LOOK[road["kind"]]
        wd, path = road["width"] * UNITS_PER_FT, world_path(road["pts"])
        p.setPen(QPen(QColor(casing), max(wd * 1.25, (minpx + 1.6) / z), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        p.setPen(QPen(QColor(fill), max(wd, minpx / z), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
    if z < DETAIL_ZOOM:   # markers until the street-level detail takes over
        p.save()
        p.resetTransform()
        font = QFont()
        font.setBold(True)
        font.setPixelSize(12)
        p.setFont(font)
        for st in w.settlements_in_rect(fx0, fy0, fx1, fy1):
            if (st.kind == "village" and z < 5e-4) or (st.kind == "farm" and z < 4e-3) or (st.kind == "town" and z < 8e-5):
                continue
            sx, sy = (st.x * UNITS_PER_FT - vr.left()) * z, (st.y * UNITS_PER_FT - vr.top()) * z
            base, true_r = SYMBOL_PX[st.kind], st.radius * UNITS_PER_FT * z
            if true_r > base:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(70, 70, 80, 60))
                p.drawEllipse(QPointF(sx, sy), true_r, true_r)
            p.setPen(QPen(QColor("#ffffff"), 1.2))
            p.setBrush(QColor(SYMBOL_COLOR[st.kind]))
            p.drawEllipse(QPointF(sx, sy), base, base)
            if (st.kind == "city" and z < 0.02) or (st.kind == "town" and 5e-4 < z < 0.02):
                p.setPen(QColor(255, 255, 255, 230))
                p.drawText(QPointF(sx + base + 5, sy - 4), st.name)
                p.setPen(QColor("#1f2937"))
                p.drawText(QPointF(sx + base + 4, sy - 5), st.name)
        p.restore()


# ----------------------------------------------------------------------------- pointer and whiteboard
class Pointer:
    laser = False   # show the DM's cursor on the player screen
    dm = None       # the DM's cursor on the map (scene coordinates)
    tv = None       # the mouse over the player window (scene coordinates)


class Chalk:
    """The whiteboard: temporary marks drawn over both screens, never saved with the map."""
    mode = "Pen"    # Pen, Highlighter, Circle, Arrow or Erase
    color = QColor("#ef4444")
    width = 4       # screen pixels
    strokes = []    # {"mode", "color", "width", "pts"}
    cur = None


def seg_dist(p, a, b):
    dx, dy = b.x() - a.x(), b.y() - a.y()
    length2 = dx * dx + dy * dy
    t = 0 if length2 == 0 else max(0, min(1, ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / length2))
    return math.hypot(p.x() - (a.x() + t * dx), p.y() - (a.y() + t * dy))


def stroke_near(st, pt, r):
    pts = st["pts"]
    if st["mode"] == "Circle":
        rect = QRectF(pts[0], pts[1]).normalized()
        c = rect.center()
        a, b = max(rect.width() / 2, 1e-6), max(rect.height() / 2, 1e-6)
        d = math.hypot((pt.x() - c.x()) / a, (pt.y() - c.y()) / b)
        return abs(d - 1) * min(a, b) <= r
    if len(pts) == 1:
        return math.hypot(pt.x() - pts[0].x(), pt.y() - pts[0].y()) <= r
    return any(seg_dist(pt, pts[i], pts[i + 1]) <= r for i in range(len(pts) - 1))


def chalk_begin(pt, z):
    if Chalk.mode == "Erase":
        chalk_move(pt, z)
        return
    two = Chalk.mode in ("Circle", "Arrow")
    Chalk.cur = {"mode": Chalk.mode, "color": QColor(Chalk.color), "width": Chalk.width,
                 "pts": [QPointF(pt), QPointF(pt)] if two else [QPointF(pt)]}
    Chalk.strokes.append(Chalk.cur)


def chalk_move(pt, z):
    if Chalk.mode == "Erase":
        r = 14 / z
        Chalk.strokes[:] = [st for st in Chalk.strokes if not stroke_near(st, pt, r)]
        return
    cur = Chalk.cur
    if cur is None:
        return
    if cur["mode"] in ("Circle", "Arrow"):
        cur["pts"][1] = QPointF(pt)
    elif (pt - cur["pts"][-1]).manhattanLength() * z >= 2:
        cur["pts"].append(QPointF(pt))


def chalk_end():
    Chalk.cur = None


def draw_chalk(view, p):
    if not Chalk.strokes:
        return
    z = view.transform().m11()
    p.setRenderHint(QPainter.Antialiasing)
    for st in Chalk.strokes:
        col, w = QColor(st["color"]), st["width"]
        if st["mode"] == "Highlighter":
            col.setAlpha(110)
            w *= 3
        pen = QPen(col, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        pen.setCosmetic(True)   # constant width on screen at every zoom
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        pts = st["pts"]
        if st["mode"] == "Circle":
            p.drawEllipse(QRectF(pts[0], pts[1]).normalized())
        elif st["mode"] == "Arrow":
            a, b = pts
            p.drawLine(a, b)
            ang = math.atan2(b.y() - a.y(), b.x() - a.x())
            head = 18 / z
            for da in (2.6, -2.6):
                p.drawLine(b, QPointF(b.x() + head * math.cos(ang + da), b.y() + head * math.sin(ang + da)))
        elif len(pts) == 1:
            p.drawPoint(pts[0])
        else:
            path = QPainterPath(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
            p.drawPath(path)


def draw_pointer(view, p):
    """A bright dot, constant on screen, at the DM's laser pointer (or at the mouse over the player window)."""
    pos = Pointer.dm if (Pointer.laser and Pointer.dm is not None) else Pointer.tv
    if pos is None:
        return
    z = view.transform().m11()
    p.save()
    p.translate(pos)
    p.scale(1 / z, 1 / z)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(255, 255, 255, 230), 3))
    p.setBrush(QColor(239, 68, 68, 215))
    p.drawEllipse(QPointF(0, 0), 11, 11)
    p.setPen(QPen(QColor(239, 68, 68, 130), 2))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(0, 0), 21, 21)
    p.restore()


# ----------------------------------------------------------------------------- undo commands
class AddCommand(QUndoCommand):
    def __init__(self, scene, items, text="Add"):
        super().__init__(text)
        self.sc, self.items = scene, list(items)

    def _add(self):
        for it in self.items:
            if it.scene() is None:
                self.sc.addItem(it)

    def _remove(self):
        for it in self.items:
            if it.scene() is self.sc:
                self.sc.removeItem(it)

    def redo(self):
        self._add()

    def undo(self):
        self._remove()


class RemoveCommand(AddCommand):
    def __init__(self, scene, items):
        super().__init__(scene, items, "Delete")

    def redo(self):
        self._remove()

    def undo(self):
        self._add()


class MoveCommand(QUndoCommand):
    def __init__(self, moves):
        super().__init__("Move")
        self.moves = moves  # (item, old_pos, new_pos)

    def redo(self):
        for it, _, new in self.moves:
            it.setPos(new)

    def undo(self):
        for it, old, _ in self.moves:
            it.setPos(old)


class EditTokenCommand(QUndoCommand):
    def __init__(self, item, old, new):
        super().__init__("Edit")
        self.item, self.old, self.new = item, old, new

    def redo(self):
        self.item.set_props(self.new)

    def undo(self):
        self.item.set_props(self.old)


# ----------------------------------------------------------------------------- dialog
class TokenDialog(QDialog):
    def __init__(self, parent, props):
        super().__init__(parent)
        self.setWindowTitle("Edit Token")
        self.color = QColor(props["color"])
        form = QFormLayout(self)
        self.name = QLineEdit(props["name"])
        self.ini = QLineEdit(props["initials"])
        self.size = QSpinBox()
        self.size.setRange(1, 8)
        self.size.setValue(props["size"])
        self.size.setSuffix(" square(s)")
        self.btn = QPushButton()
        swatch(self.btn, self.color)
        self.btn.clicked.connect(self.pick)
        form.addRow("Name", self.name)
        form.addRow("Initials", self.ini)
        form.addRow("Color", self.btn)
        form.addRow("Size", self.size)
        self.player = QCheckBox("Player token (shares vision with the party)")
        self.player.setChecked(props.get("player", False))
        self.mod = QSpinBox()
        self.mod.setRange(-1000, 5000)
        self.mod.setSingleStep(10)
        self.mod.setSuffix(" ft")
        self.mod.setValue(props.get("vision_mod", 0))
        form.addRow(self.player)
        form.addRow("Vision modifier", self.mod)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def pick(self):
        c = QColorDialog.getColor(self.color, self, "Token color")
        if c.isValid():
            self.color = c
            swatch(self.btn, c)

    def result_props(self):
        name = self.name.text().strip()
        return {"name": name, "initials": self.ini.text().strip() or auto_initials(name),
                "color": self.color.name(), "size": self.size.value(),
                "player": self.player.isChecked(), "vision_mod": self.mod.value()}


class CategoryDialog(QDialog):
    def __init__(self, parent, cat):
        super().__init__(parent)
        self.setWindowTitle("Token category")
        self.color = QColor(cat["color"])
        form = QFormLayout(self)
        self.name = QLineEdit(cat["name"])
        self.ini = QLineEdit(cat.get("initials", ""))
        self.ini.setPlaceholderText("auto")
        self.size = QSpinBox()
        self.size.setRange(1, 8)
        self.size.setValue(cat["size"])
        self.size.setSuffix(" square(s)")
        self.btn = QPushButton()
        swatch(self.btn, self.color)
        self.btn.clicked.connect(self.pick)
        self.player = QCheckBox("Player token (shares vision with the party)")
        self.player.setChecked(cat.get("player", False))
        self.numbered = QCheckBox("Number each one (Walker 1, Walker 2...)")
        self.numbered.setChecked(cat.get("numbered", False))
        form.addRow("Category name", self.name)
        form.addRow("Initials", self.ini)
        form.addRow("Color", self.btn)
        form.addRow("Size", self.size)
        form.addRow(self.player)
        form.addRow(self.numbered)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def pick(self):
        c = QColorDialog.getColor(self.color, self, "Token color")
        if c.isValid():
            self.color = c
            swatch(self.btn, c)

    def result_cat(self):
        return {"name": self.name.text().strip() or "Token", "initials": self.ini.text().strip(),
                "color": self.color.name(), "size": self.size.value(),
                "player": self.player.isChecked(), "numbered": self.numbered.isChecked()}


class GenerateDialog(QDialog):
    SIZES = [("Small (120 x 80 miles)", 120, 80), ("Medium (240 x 160 miles)", 240, 160),
             ("Large (360 x 240 miles)", 360, 240)]

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Generate a state")
        form = QFormLayout(self)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2147483647)
        self.seed.setValue(random.randrange(1, 100000))
        rnd = QPushButton("Random")
        rnd.clicked.connect(lambda: self.seed.setValue(random.randrange(1, 2147483647)))
        row = QHBoxLayout()
        row.addWidget(self.seed)
        row.addWidget(rnd)
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(["Washington-style", "Varied (the layout changes with the seed)"])
        self.size_combo = QComboBox()
        self.size_combo.addItems([name for name, _w, _h in self.SIZES])
        self.size_combo.setCurrentIndex(1)
        self.density = QComboBox()
        self.density.addItems(["Low", "Medium", "High"])
        self.density.setCurrentIndex(1)
        form.addRow("Seed", row)
        form.addRow("Layout", self.layout_combo)
        form.addRow("Size", self.size_combo)
        form.addRow("Settlement density", self.density)
        form.addRow(QLabel("<b>Zombie apocalypse touches</b>"))
        self.checks = {}
        for key, text in (("cars", "Abandoned and crashed cars"), ("barricades", "Barricades"),
                          ("rubble", "Rubble"), ("overgrowth", "Overgrowth"), ("zombies", "Zombies")):
            cb = QCheckBox(text)
            cb.setChecked(True)
            self.checks[key] = cb
            form.addRow(cb)
        note = QLabel("Streets and buildings are built as you zoom in on a settlement.\\nThe map carries on past the edge of the state.")
        note.setStyleSheet("color:#777;")
        form.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def options(self):
        _name, wmi, hmi = self.SIZES[self.size_combo.currentIndex()]
        return {"seed": self.seed.value(), "layout": "washington" if self.layout_combo.currentIndex() == 0 else "varied",
                "width_mi": wmi, "height_mi": hmi, "density": self.density.currentIndex(),
                "zombies": {k: cb.isChecked() for k, cb in self.checks.items()}}


class BuildingDialog(QDialog):
    def __init__(self, parent, props):
        super().__init__(parent)
        self.setWindowTitle("Edit Building")
        form = QFormLayout(self)
        self.kind = QComboBox()
        self.kind.addItems(list(BUILDING_KINDS))
        self.kind.setCurrentText(props["kind"])
        self.label = QLineEdit(props["label"])
        form.addRow("Type", self.kind)
        form.addRow("Label", self.label)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def result_props(self):
        return {"kind": self.kind.currentText(), "label": self.label.text().strip()}


# ----------------------------------------------------------------------------- view
class MapView(QGraphicsView):
    def __init__(self, scene, undo, main):
        super().__init__(scene)
        self.undo, self.main = undo, main
        self.tool = "select"
        self.snap, self.show_grid, self.dark = True, True, False
        self.pen_color, self.pen_width, self.fill = QColor(LIGHT_PEN), 4, False
        self.road_style, self.road_width = "Paved", 20   # road width in feet
        self.terrain_type, self.terrain_mode, self.terrain_brush = "Grass", "Brush", 4
        self.prop_kind, self.prop_rot = "car", 0
        self.building_kind, self.fence_style = "House", "Wood"
        self._pan = self._pan_btn = None
        self._space = False
        self._cur = self._start = self._end = None
        self._erased, self._press_pos = [], {}
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)
        self.set_tool("select")

    # --- state
    def set_tool(self, t):
        self.tool = t
        self.setDragMode(QGraphicsView.RubberBandDrag if t == "select" else QGraphicsView.NoDrag)
        if t != "select":
            self.scene().clearSelection()
        self.refresh_cursor()

    def refresh_cursor(self):
        c = Qt.OpenHandCursor if self._space else {"select": Qt.ArrowCursor,
                                                  "token": Qt.PointingHandCursor}.get(self.tool, Qt.CrossCursor)
        self.viewport().setCursor(c)

    def snap_pt(self, p):
        if not self.snap:
            return p
        g = Snap.step
        return QPointF(round(p.x() / g) * g, round(p.y() / g) * g)

    def snap_road(self, p, width_ft):
        """Roads sit on cell centres when an odd number of squares wide, on grid lines otherwise."""
        if not self.snap:
            return p
        g = Snap.step
        n = max(1, round(width_ft * UNITS_PER_FT / g))
        if n % 2:
            return QPointF((math.floor(p.x() / g) + 0.5) * g, (math.floor(p.y() / g) + 0.5) * g)
        return QPointF(round(p.x() / g) * g, round(p.y() / g) * g)

    def building_rect(self, a, b):
        if self.snap:
            g = Snap.step
            x0, x1 = sorted((math.floor(a.x() / g), math.floor(b.x() / g)))
            y0, y1 = sorted((math.floor(a.y() / g), math.floor(b.y() / g)))
            return QRectF(x0 * g, y0 * g, (x1 - x0 + 1) * g, (y1 - y0 + 1) * g)
        return QRectF(a, b).normalized()

    def zoom(self, factor, center=False):
        cur = self.transform().m11()
        f = max(ZOOM_MIN, min(ZOOM_MAX, cur * factor)) / cur
        if center:
            self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.scale(f, f)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.refresh_level()

    def refresh_level(self):
        """Pick the grid scale for the current zoom and tell the toolbar."""
        lvl = level_for_zoom(self.transform().m11())
        Snap.step = LEVELS[lvl][1] * UNITS_PER_FT
        self.main.update_scale_ui(lvl)
        Zoom.dm = self.transform().m11()
        self.main.on_zoom_changed()

    def set_zoom(self, z):
        c = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()
        self.scale(z, z)
        self.centerOn(c)
        self.refresh_level()

    def focus_items(self, items):
        """Zoom and pan so the given tokens fill the view, with some room around them."""
        rect = None
        for it in items:
            d = it.size * CELL
            r = QRectF(it.pos().x(), it.pos().y(), d, d)
            rect = r if rect is None else rect.united(r)
        if rect is None:
            return
        rect.adjust(-4 * CELL, -4 * CELL, 4 * CELL, 4 * CELL)
        self.fitInView(rect, Qt.KeepAspectRatio)
        z = self.transform().m11()
        if z > ZOOM_MAX or z < ZOOM_MIN:
            self.set_zoom(max(ZOOM_MIN, min(ZOOM_MAX, z)))
        self.refresh_level()

    def show_status(self, sp):
        text = (f"x {sp.x() / UNITS_PER_FT:,.0f} ft, y {sp.y() / UNITS_PER_FT:,.0f} ft    "
                f"1 square = {fmt_len(Snap.step)}")
        cur, t = self._cur, self.tool
        if cur is not None:
            if isinstance(cur, BuildingItem):
                text += f"    {fmt_len(cur.w)} x {fmt_len(cur.h)}"
            elif t in ("line", "road", "fence"):
                text += f"    Length {fmt_len(QLineF(self._start, self._end).length())}"
            elif t == "rect" or (t in ("terrain", "reveal") and cur.mode == "rect"):
                text += (f"    {fmt_len(abs(self._end.x() - self._start.x()))} x "
                         f"{fmt_len(abs(self._end.y() - self._start.y()))}")
        self.main.statusBar().showMessage(text)

    # --- background grid
    def drawBackground(self, p, rect):
        p.fillRect(rect, QColor("#1e2127") if self.dark else QColor("#f5f0e6"))
        if Atlas.current is not None:
            try:
                draw_world(self, p, rect)
            except Exception:   # report a drawing problem once instead of on every repaint
                if not Atlas.warned:
                    Atlas.warned = True
                    import traceback
                    traceback.print_exc()
        g = LEVELS[level_for_zoom(self.transform().m11())][1] * UNITS_PER_FT
        step = g * self.transform().m11()
        if not self.show_grid or step < 4:
            return
        minor = QColor(255, 255, 255, 30) if self.dark else QColor(90, 70, 40, 50)
        major = QColor(255, 255, 255, 80) if self.dark else QColor(90, 70, 40, 120)
        l, t, r, b = rect.left(), rect.top(), rect.right(), rect.bottom()
        minor_lines, major_lines = [], []
        for i in range(math.floor(l / g), math.ceil(r / g) + 1):
            (major_lines if i % 5 == 0 else minor_lines).append(QLineF(i * g, t, i * g, b))
        for j in range(math.floor(t / g), math.ceil(b / g) + 1):
            (major_lines if j % 5 == 0 else minor_lines).append(QLineF(l, j * g, r, j * g))
        if step >= 10:
            p.setPen(QPen(minor, 0))
            p.drawLines(minor_lines)
        p.setPen(QPen(major, 0))
        p.drawLines(major_lines)

    def drawForeground(self, p, rect):
        draw_overlay(self, p, rect, False)
        draw_chalk(self, p)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if Pointer.dm is not None:
            Pointer.dm = None
            self.main.update_player_pointer()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.main.on_dm_view_changed()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.main.on_dm_view_changed()

    # --- input
    def wheelEvent(self, e):
        self.zoom(1.3 if e.angleDelta().y() > 0 else 1 / 1.3)
        e.accept()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.refresh_cursor()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = False
            self.refresh_cursor()
            return
        super().keyReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.tool == "select":
            it = self.itemAt(e.position().toPoint())
            if isinstance(it, (TokenItem, BuildingItem)):
                (self.main.edit_token if isinstance(it, TokenItem) else self.main.edit_building)(it)
                e.accept()
                return
            super().mouseDoubleClickEvent(e)
        else:
            self.mousePressEvent(e)

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        if e.button() in (Qt.MiddleButton, Qt.RightButton) or (e.button() == Qt.LeftButton and self._space):
            self._pan, self._pan_btn = pos, e.button()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        if e.button() != Qt.LeftButton:
            return
        sp = self.mapToScene(pos)
        t = self.tool
        if t == "select":
            super().mousePressEvent(e)
            Snap.dragging = True
            self._press_pos = {it: it.pos() for it in self.scene().selectedItems()}
        elif t == "pen":
            self._cur = StrokeItem([sp, QPointF(sp.x() + 0.01, sp.y())], self.pen_color,
                                   self.pen_width * Snap.step / CELL)
            self._last = sp
            self.scene().addItem(self._cur)
        elif t in ("line", "rect"):
            self._start = self._end = self.snap_pt(sp)
            self._cur = StrokeItem([], self.pen_color, self.pen_width * Snap.step / CELL,
                                   self.fill and t == "rect")
            self.scene().addItem(self._cur)
        elif t in ("road", "fence"):
            if t == "road":
                self._start = self._end = self.snap_road(sp, self.road_width)
                self._cur = RoadItem([self._start, self._end], self.road_style, self.road_width)
            else:
                self._start = self._end = self.snap_pt(sp)
                self._cur = FenceItem([self._start, self._end], self.fence_style, Snap.step / CELL)
            self.scene().addItem(self._cur)
        elif t == "building":
            self._start = sp
            self._cur = BuildingItem(self.building_kind, CELL, CELL)
            self._cur.set_rect(self.building_rect(sp, sp))
            self.scene().addItem(self._cur)
        elif t in ("terrain", "reveal"):
            width_ft = self.terrain_brush * Snap.step / UNITS_PER_FT   # brush size is in squares of this scale
            if self.terrain_mode == "Rectangle":
                self._start = self._end = self.snap_pt(sp)
                pts, mode = [self._start, self._start], "rect"
            else:
                pts, mode = [sp, QPointF(sp.x() + 0.01, sp.y())], "brush"
                self._last = sp
            self._cur = (TerrainItem(pts, self.terrain_type, mode, width_ft) if t == "terrain"
                         else RevealItem(pts, mode, width_ft))
            self.scene().addItem(self._cur)
        elif t == "prop":
            pw, ph = PROP_UNITS.get(self.prop_kind, (100, 100))
            c = self.snap_pt(sp)
            prop = PropItem(self.prop_kind, pw, ph, self.prop_rot)
            prop.setPos(c.x() - pw / 2, c.y() - ph / 2)
            self.undo.push(AddCommand(self.scene(), [prop], "Place prop"))
        elif t == "chalk":
            chalk_begin(sp, self.transform().m11())
            self.main.refresh_views()
        elif t == "token":
            self.place_token(sp)
        elif t == "eraser":
            self._erased = []
            self.erase_at(sp)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._pan is not None:
            d = pos - self._pan
            self._pan = pos
            h, v = self.horizontalScrollBar(), self.verticalScrollBar()
            h.setValue(h.value() - d.x())
            v.setValue(v.value() - d.y())
            return
        sp = self.mapToScene(pos)
        if Pointer.laser:
            Pointer.dm = sp
            self.main.update_player_pointer()
        t = self.tool
        if not (e.buttons() & Qt.LeftButton) or t == "select":
            super().mouseMoveEvent(e)
        elif t == "pen" and self._cur:
            if (sp - self._last).manhattanLength() * self.transform().m11() >= 2:
                self._cur.add_point(sp)
                self._last = sp
        elif t in ("line", "rect") and self._cur:
            self._end = end = self.snap_pt(sp)
            s = self._start
            if t == "line":
                self._cur.pts = [s, end]
            else:
                self._cur.pts = [s, QPointF(end.x(), s.y()), end, QPointF(s.x(), end.y()), s]
            self._cur.rebuild()
        elif t in ("road", "fence") and self._cur:
            self._end = self.snap_road(sp, self.road_width) if t == "road" else self.snap_pt(sp)
            self._cur.pts = [self._start, self._end]
            self._cur.rebuild()
        elif t == "building" and self._cur:
            self._cur.set_rect(self.building_rect(self._start, sp))
        elif t in ("terrain", "reveal") and self._cur:
            if self._cur.mode == "rect":
                self._end = self.snap_pt(sp)
                s0, end = self._start, self._end
                self._cur.pts = [s0, QPointF(end.x(), s0.y()), end, QPointF(s0.x(), end.y()), s0]
                self._cur.rebuild()
            elif (sp - self._last).manhattanLength() * self.transform().m11() >= 2:
                self._cur.add_point(sp)
                self._last = sp
        elif t == "chalk":
            chalk_move(sp, self.transform().m11())
            self.main.refresh_views()
        elif t == "eraser":
            self.erase_at(sp)
        self.show_status(sp)

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            if e.button() == self._pan_btn:
                self._pan = None
                self.refresh_cursor()
            return
        if e.button() != Qt.LeftButton:
            return
        t = self.tool
        if t == "select":
            super().mouseReleaseEvent(e)
            Snap.dragging = False
            moves = [(it, old, it.pos()) for it, old in self._press_pos.items()
                     if it.scene() is not None and it.pos() != old]
            self._press_pos = {}
            if moves:
                self.undo.push(MoveCommand(moves))
        elif t in ("pen", "line", "rect", "road", "fence", "building", "terrain", "reveal") and self._cur:
            item, self._cur = self._cur, None
            self.scene().removeItem(item)
            keep = (t in ("pen", "building") or (t in ("terrain", "reveal") and item.mode == "brush")
                    or self._start != self._end)
            if t == "building" and (item.w < 4 or item.h < 4):
                keep = False
            if keep:
                self.undo.push(AddCommand(self.scene(), [item], "Draw"))
        elif t == "chalk":
            chalk_end()
        elif t == "eraser" and self._erased:
            self.undo.push(RemoveCommand(self.scene(), self._erased))
            self._erased = []

    # --- helpers
    def erase_at(self, sp):
        r = 10 / self.transform().m11()
        big_done = False   # roads/buildings are large: erase only the topmost one per sample
        for it in self.scene().items(QRectF(sp.x() - r, sp.y() - r, 2 * r, 2 * r), Qt.IntersectsItemShape):
            big = isinstance(it, (RoadItem, BuildingItem, TerrainItem))
            if isinstance(it, (StrokeItem, FenceItem, PropItem)) or (big and not big_done):
                big_done = big_done or big
                self.scene().removeItem(it)
                self._erased.append(it)

    def place_token(self, sp):
        toks = self.main.make_tokens(sp)
        self.undo.push(AddCommand(self.scene(), toks, "Place tokens" if len(toks) > 1 else "Place token"))

    def delete_selected(self):
        items = self.scene().selectedItems()
        if items:
            self.undo.push(RemoveCommand(self.scene(), items))


# ----------------------------------------------------------------------------- player screen
class PlayerView(QGraphicsView):
    """Read-only view of the same map for the TV: no controls, with fog of war applied."""

    def __init__(self, scene, main):
        super().__init__(scene)
        self.main = main
        self.viewport().setProperty("player_view", True)
        self.viewport().setCursor(Qt.CrossCursor)
        self.viewport().setMouseTracking(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setInteractive(False)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self._pan = None
        self._draw = False

    @property
    def dark(self):
        return self.main.view.dark

    @property
    def show_grid(self):
        return self.main.player_grid

    def drawBackground(self, p, rect):
        MapView.drawBackground(self, p, rect)

    def drawForeground(self, p, rect):
        draw_overlay(self, p, rect, True)
        draw_chalk(self, p)
        draw_pointer(self, p)

    def show_rect(self, rect):
        """Show exactly this part of the map, scaled to fill the screen."""
        vp = self.viewport()
        if vp.width() <= 0 or vp.height() <= 0 or rect.width() <= 0 or rect.height() <= 0:
            return
        s = min(vp.width() / rect.width(), vp.height() / rect.height())
        self.resetTransform()
        self.scale(s, s)
        self.centerOn(rect.center())
        Zoom.tv = self.transform().m11()
        self.main.refresh_token_geometry()

    def zoom_by(self, factor, center=False):
        cur = self.transform().m11()
        f = max(ZOOM_MIN, min(ZOOM_MAX, cur * factor)) / cur
        if center:
            self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.scale(f, f)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        Zoom.tv = self.transform().m11()
        self.main.refresh_token_geometry()
        self.main.player_navigated()

    def nudge(self, dx, dy):
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(h.value() + dx)
        v.setValue(v.value() + dy)
        self.main.player_navigated()

    def wheelEvent(self, e):
        self.zoom_by(1.3 if e.angleDelta().y() > 0 else 1 / 1.3)
        e.accept()

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        if e.button() == Qt.LeftButton and self.main.view.tool == "chalk":   # draw with the Chalk tool selected
            self._draw = True
            chalk_begin(self.mapToScene(pos), self.transform().m11())
            self.main.refresh_views()
        else:
            self._pan = pos
        e.accept()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        Pointer.tv = self.mapToScene(pos)
        if self._draw:
            chalk_move(Pointer.tv, self.transform().m11())
            self.main.refresh_views()
        elif self._pan is not None:
            d = pos - self._pan
            self._pan = pos
            h, v = self.horizontalScrollBar(), self.verticalScrollBar()
            h.setValue(h.value() - d.x())
            v.setValue(v.value() - d.y())
            self.main.player_navigated()
        self.viewport().update()

    def mouseReleaseEvent(self, e):
        if self._draw:
            chalk_end()
            self._draw = False
        self._pan = None

    def leaveEvent(self, e):
        Pointer.tv = None
        self.viewport().update()
        super().leaveEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.main.sync_player_view()


class PlayerWindow(QMainWindow):
    def __init__(self, scene, main):
        super().__init__()
        self.main = main
        self.setWindowTitle("Player Screen")
        self.view = PlayerView(scene, main)
        self.setCentralWidget(self.view)
        self.resize(1280, 720)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif e.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
        elif e.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            dx = {Qt.Key_Left: -80, Qt.Key_Right: 80}.get(e.key(), 0)
            dy = {Qt.Key_Up: -80, Qt.Key_Down: 80}.get(e.key(), 0)
            self.view.nudge(dx, dy)
        elif e.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.view.zoom_by(1.3, True)
        elif e.key() == Qt.Key_Minus:
            self.view.zoom_by(1 / 1.3, True)
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self.main.player_closed()
        e.accept()


# ----------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1280, 800)
        self.path = None
        self.token_color = QColor(PRESETS[0][1])
        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(-HALF, -HALF, 2 * HALF, 2 * HALF)
        self.undo = QUndoStack(self)
        self.view = MapView(self.scene, self.undo, self)
        self.setCentralWidget(self.view)
        self.tool_actions = {}
        self.player_win, self.frame, self.mirror = None, None, False
        self.player_grid, self.ready = True, False
        self.build_menus()
        self.build_dock()
        self.build_player_dock()
        self.build_token_dock()
        self.undo.cleanChanged.connect(self.update_title)
        self.update_title()
        self.statusBar().showMessage("Ready")
        self.view.refresh_level()
        self.setup_vision()

    # --- UI construction
    def act(self, menu, text, slot, shortcut=None, checkable=False, checked=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if checkable:
            a.setCheckable(True)
            a.setChecked(checked)
            a.toggled.connect(slot)
        else:
            a.triggered.connect(lambda _=False: slot())
        menu.addAction(a)
        return a

    def build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self.act(m, "New", self.new_file, QKeySequence.New)
        self.act(m, "Open...", self.open_file, QKeySequence.Open)
        self.act(m, "Generate State...", self.generate_state, "Ctrl+Shift+G")
        self.act(m, "Save", self.save_file, QKeySequence.Save)
        self.act(m, "Save As...", self.save_as, QKeySequence.SaveAs)
        m.addSeparator()
        self.act(m, "Export Current View as PNG...", self.export_png)
        m.addSeparator()
        self.act(m, "Quit", self.close, QKeySequence.Quit)

        m = mb.addMenu("&Edit")
        u = self.undo.createUndoAction(self, "Undo")
        u.setShortcut(QKeySequence.Undo)
        r = self.undo.createRedoAction(self, "Redo")
        r.setShortcut(QKeySequence.Redo)
        m.addAction(u)
        m.addAction(r)
        m.addSeparator()
        self.act(m, "Delete Selected", self.view.delete_selected, "Del")
        self.act(m, "Select All", self.select_all, QKeySequence.SelectAll)

        m = mb.addMenu("&View")
        self.act(m, "Show Grid", lambda v: self.set_view("show_grid", v), "G", True, True)
        self.act(m, "Snap to Grid", self.set_snap, "S", True, True)
        self.act(m, "Dark Canvas", self.set_dark, None, True, False)
        self.night_action = self.act(m, "Night Mode", self.set_night, "N", True, False)
        self.act(m, "Show Grid on Player Screen", self.set_player_grid, None, True, True)
        m.addSeparator()
        self.act(m, "Zoom In", lambda: self.view.zoom(1.25, True), QKeySequence.ZoomIn)
        self.act(m, "Zoom Out", lambda: self.view.zoom(0.8, True), QKeySequence.ZoomOut)
        self.act(m, "Reset Zoom", self.reset_zoom, "Ctrl+0")
        self.act(m, "Go to Origin", lambda: self.view.centerOn(0, 0), "Home")
        self.act(m, "Zoom to Selected Token", self.zoom_to_selected, "Z")
        self.act(m, "Zoom to Party", self.zoom_to_party, "Shift+Z")
        gm = m.addMenu("Go to City or Town")
        gm.aboutToShow.connect(lambda: self.fill_city_menu(gm))

        pm = mb.addMenu("&Player")
        self.act(pm, "Open Player Screen", self.open_player_screen, "F10")
        sub = pm.addMenu("Open on Screen")
        sub.aboutToShow.connect(lambda: self.fill_screen_menu(sub))
        self.act(pm, "Close Player Screen", self.close_player_screen)
        pm.addSeparator()
        self.act(pm, "Match Player View to My View", self.match_my_view, "Ctrl+M")
        self.act(pm, "Center Player View on Party", self.center_on_party)
        self.laser_action = self.act(pm, "Laser Pointer (my cursor shows on the player screen)",
                                     self.set_laser, "H", True, False)

        tb = self.addToolBar("Tools")
        tb.setMovable(False)
        group = QActionGroup(self)
        for key, label, sc in [("select", "Select", "V"), ("pen", "Pen", "P"), ("line", "Line", "L"),
                               ("rect", "Rect", "R"), ("road", "Road", "O"), ("building", "Building", "B"),
                               ("fence", "Fence", "F"), ("terrain", "Terrain", "Y"), ("reveal", "Reveal", "X"), ("chalk", "Chalk", "C"), ("prop", "Prop", "K"),
                               ("token", "Token", "T"), ("eraser", "Eraser", "E")]:
            a = QAction(f"{label} ({sc})", self)
            a.setCheckable(True)
            a.setShortcut(sc)
            a.triggered.connect(lambda _=False, k=key: self.set_tool(k))
            group.addAction(a)
            tb.addAction(a)
            self.tool_actions[key] = a
        self.tool_actions["select"].setChecked(True)
        tb.addSeparator()
        tb.addWidget(QLabel("  Scale: "))
        self.scale_combo = QComboBox()
        self.scale_combo.addItems([f"1 square = {name}" for name, _ in LEVELS])
        self.scale_combo.activated.connect(self.jump_to_level)
        tb.addWidget(self.scale_combo)
        tb.addSeparator()
        tb.addAction(self.night_action)
        tb.addAction(self.laser_action)

    def build_dock(self):
        dock = QDockWidget("Tools", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        w = QWidget()
        lay = QVBoxLayout(w)

        lay.addWidget(QLabel("<b>Pen / Line / Rect</b>"))
        f = QFormLayout()
        self.pen_btn = QPushButton()
        swatch(self.pen_btn, self.view.pen_color)
        self.pen_btn.clicked.connect(self.pick_pen_color)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 60)
        self.width_spin.setValue(self.view.pen_width)
        self.width_spin.valueChanged.connect(lambda v: setattr(self.view, "pen_width", v))
        f.addRow("Color", self.pen_btn)
        f.addRow("Width", self.width_spin)
        lay.addLayout(f)
        fill = QCheckBox("Fill rectangles")
        fill.toggled.connect(lambda v: setattr(self.view, "fill", v))
        lay.addWidget(fill)

        lay.addWidget(QLabel("<b>Environment</b>"))
        f = QFormLayout()
        self.road_combo = QComboBox()
        self.road_combo.addItems(list(ROAD_STYLES))
        self.road_combo.currentTextChanged.connect(lambda v: setattr(self.view, "road_style", v))
        self.road_width = QSpinBox()
        self.road_width.setRange(5, 200)
        self.road_width.setSingleStep(5)
        self.road_width.setValue(self.view.road_width)
        self.road_width.setSuffix(" ft")
        self.road_width.valueChanged.connect(lambda v: setattr(self.view, "road_width", v))
        self.bld_combo = QComboBox()
        self.bld_combo.addItems(list(BUILDING_KINDS))
        self.bld_combo.currentTextChanged.connect(lambda v: setattr(self.view, "building_kind", v))
        self.fence_combo = QComboBox()
        self.fence_combo.addItems(list(FENCE_STYLES))
        self.fence_combo.currentTextChanged.connect(lambda v: setattr(self.view, "fence_style", v))
        f.addRow("Road", self.road_combo)
        f.addRow("Road width", self.road_width)
        f.addRow("Building", self.bld_combo)
        f.addRow("Fence", self.fence_combo)
        lay.addLayout(f)

        lay.addWidget(QLabel("<b>Props (Prop tool, K)</b>"))
        f = QFormLayout()
        self.prop_combo = QComboBox()
        self.prop_combo.addItems(list(worldgen.PROP_FT))
        self.prop_combo.currentTextChanged.connect(lambda v: setattr(self.view, "prop_kind", v))
        self.prop_rot_spin = QSpinBox()
        self.prop_rot_spin.setRange(0, 359)
        self.prop_rot_spin.setWrapping(True)
        self.prop_rot_spin.setSuffix(" deg")
        self.prop_rot_spin.valueChanged.connect(lambda v: setattr(self.view, "prop_rot", v))
        f.addRow("Prop", self.prop_combo)
        f.addRow("Rotation", self.prop_rot_spin)
        lay.addLayout(f)

        lay.addWidget(QLabel("<b>Terrain / Reveal brush</b>"))
        f = QFormLayout()
        self.terrain_combo = QComboBox()
        for name, color in TERRAIN.items():
            pm = QPixmap(14, 14)
            pm.fill(QColor(color))
            self.terrain_combo.addItem(QIcon(pm), name)
        self.terrain_combo.currentTextChanged.connect(lambda v: setattr(self.view, "terrain_type", v))
        self.terrain_shape = QComboBox()
        self.terrain_shape.addItems(["Brush", "Rectangle"])
        self.terrain_shape.currentTextChanged.connect(lambda v: setattr(self.view, "terrain_mode", v))
        self.terrain_size = QSpinBox()
        self.terrain_size.setRange(1, 80)
        self.terrain_size.setValue(self.view.terrain_brush)
        self.terrain_size.setSuffix(" squares")
        self.terrain_size.valueChanged.connect(lambda v: setattr(self.view, "terrain_brush", v))
        f.addRow("Type", self.terrain_combo)
        f.addRow("Shape", self.terrain_shape)
        f.addRow("Brush size", self.terrain_size)
        lay.addLayout(f)

        lay.addWidget(QLabel("<b>Token to place</b>"))
        self.categories = load_categories()
        self.presets = QListWidget()
        self.presets.setMaximumHeight(170)
        self.refresh_categories()
        self.presets.itemClicked.connect(self.apply_preset)
        lay.addWidget(self.presets)
        row = QHBoxLayout()
        for text, slot in (("Save as category...", self.new_category), ("Edit...", self.edit_category),
                           ("Delete", self.delete_category)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, sl=slot: sl())
            row.addWidget(b)
        lay.addLayout(row)
        f = QFormLayout()
        self.name_edit = QLineEdit(PRESETS[0][0])
        self.ini_edit = QLineEdit()
        self.ini_edit.setPlaceholderText("auto")
        self.tok_btn = QPushButton()
        swatch(self.tok_btn, self.token_color)
        self.tok_btn.clicked.connect(self.pick_token_color)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(1, 8)
        self.size_spin.setSuffix(" square(s)")
        f.addRow("Name", self.name_edit)
        f.addRow("Initials", self.ini_edit)
        f.addRow("Color", self.tok_btn)
        f.addRow("Size", self.size_spin)
        lay.addLayout(f)
        self.player_chk = QCheckBox("Player token (shares vision)")
        self.player_chk.setChecked(True)
        lay.addWidget(self.player_chk)
        self.number_chk = QCheckBox("Number each token (Walker 1, 2, 3...)")
        lay.addWidget(self.number_chk)
        f = QFormLayout()
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 100)
        self.count_spin.setSuffix(" per click")
        self.arrange_combo = QComboBox()
        self.arrange_combo.addItems(["Cluster", "Scatter"])
        self.spread_spin = QSpinBox()
        self.spread_spin.setRange(1, 30)
        self.spread_spin.setValue(4)
        self.spread_spin.setSuffix(" squares")
        f.addRow("Place", self.count_spin)
        f.addRow("Arrange", self.arrange_combo)
        f.addRow("Scatter radius", self.spread_spin)
        lay.addLayout(f)

        hint = QLabel("Wheel: zoom\nMiddle/right-drag or Space+drag: pan\n"
                      "Double-click token/building: edit\nRoad/Fence: drag segments, chain them\n"
                      "Building: drag a rectangle\nTerrain: brush or rectangle (Eraser removes it)\nReveal (X): show an area to the players\nDel: delete selected\n"
                      "Z: zoom to selected token, Shift+Z: zoom to party\nTokens tab: list, select and zoom to any token")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#777;")
        lay.addStretch()
        lay.addWidget(hint)
        dock.setWidget(w)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    # --- tool / view settings
    def set_tool(self, key):
        self.view.set_tool(key)
        self.view.setFocus()

    def set_view(self, attr, value):
        setattr(self.view, attr, value)
        self.view.viewport().update()

    def set_snap(self, v):
        self.view.snap = v
        Snap.on = v

    def set_dark(self, v):
        old_default = DARK_PEN if self.view.dark else LIGHT_PEN
        self.view.dark = v
        if self.view.pen_color.name() == QColor(old_default).name():
            self.view.pen_color = QColor(DARK_PEN if v else LIGHT_PEN)
            swatch(self.pen_btn, self.view.pen_color)
        self.view.viewport().update()

    # ---- player screen, fog of war and day/night
    def build_player_dock(self):
        dock = QDockWidget("Player Screen and Vision", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        w = QWidget()
        lay = QVBoxLayout(w)

        lay.addWidget(QLabel("<b>Player screen</b>"))
        b = QPushButton("Open on other monitor (F10)")
        b.clicked.connect(lambda: self.open_player_screen())
        lay.addWidget(b)
        b = QPushButton("Close player screen")
        b.clicked.connect(lambda: self.close_player_screen())
        lay.addWidget(b)
        self.mirror_chk = QCheckBox("Mirror my view (players follow my pan/zoom)")
        self.mirror_chk.toggled.connect(self.set_mirror)
        lay.addWidget(self.mirror_chk)
        self.frame_chk = QCheckBox("Show player frame on my map")
        self.frame_chk.setChecked(True)
        self.frame_chk.toggled.connect(lambda _v: self.update_frame_visibility())
        lay.addWidget(self.frame_chk)
        for text, slot in [("Match my current view", self.match_my_view),
                           ("Center on party", self.center_on_party),
                           ("Zoom player view in", lambda: self.zoom_frame(0.8)),
                           ("Zoom player view out", lambda: self.zoom_frame(1.25))]:
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, sl=slot: sl())
            lay.addWidget(b)
        self.frame_label = QLabel("Drag the cyan frame on the map to move what the players see.")
        self.frame_label.setWordWrap(True)
        lay.addWidget(self.frame_label)

        lay.addWidget(QLabel("<b>Whiteboard (Chalk tool, C)</b>"))
        f = QFormLayout()
        self.chalk_mode = QComboBox()
        self.chalk_mode.addItems(["Pen", "Highlighter", "Circle", "Arrow", "Erase"])
        self.chalk_mode.currentTextChanged.connect(lambda v: setattr(Chalk, "mode", v))
        self.chalk_btn = QPushButton()
        swatch(self.chalk_btn, Chalk.color)
        self.chalk_btn.clicked.connect(self.pick_chalk_color)
        self.chalk_width = QSpinBox()
        self.chalk_width.setRange(1, 30)
        self.chalk_width.setValue(Chalk.width)
        self.chalk_width.setSuffix(" px")
        self.chalk_width.valueChanged.connect(lambda v: setattr(Chalk, "width", v))
        f.addRow("Mark", self.chalk_mode)
        f.addRow("Color", self.chalk_btn)
        f.addRow("Width", self.chalk_width)
        lay.addLayout(f)
        row = QHBoxLayout()
        for text, slot in (("Undo mark", self.chalk_undo), ("Clear whiteboard", self.chalk_clear)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, sl=slot: sl())
            row.addWidget(b)
        lay.addLayout(row)

        lay.addWidget(QLabel("<b>Vision (day / night)</b>"))
        f = QFormLayout()
        self.day_spin, self.night_spin = QSpinBox(), QSpinBox()
        for sp_, val in ((self.day_spin, Vision.day_ft), (self.night_spin, Vision.night_ft)):
            sp_.setRange(10, 5000)
            sp_.setSingleStep(10)
            sp_.setSuffix(" ft")
            sp_.setValue(val)
            sp_.valueChanged.connect(self.set_ranges)
        f.addRow("Day range", self.day_spin)
        f.addRow("Night range", self.night_spin)
        lay.addLayout(f)
        self.fog_chk = QCheckBox("Fog of war on player screen")
        self.fog_chk.setChecked(Vision.fog_on)
        self.fog_chk.toggled.connect(lambda v: self.set_vision_flag("fog_on", v))
        self.fog_dm_chk = QCheckBox("Show fog preview on my screen")
        self.fog_dm_chk.setChecked(Vision.show_on_dm)
        self.fog_dm_chk.toggled.connect(lambda v: self.set_vision_flag("show_on_dm", v))
        self.remember_chk = QCheckBox("Remember explored areas (dimmed)")
        self.remember_chk.setChecked(Vision.remember)
        self.remember_chk.toggled.connect(lambda v: self.set_vision_flag("remember", v))
        for c in (self.fog_chk, self.fog_dm_chk, self.remember_chk):
            lay.addWidget(c)
        self.fog_label = QLabel("Fog darkness on player screen: 55%")
        lay.addWidget(self.fog_label)
        self.fog_slider = QSlider(Qt.Horizontal)
        self.fog_slider.setRange(0, 100)
        self.fog_slider.setValue(55)
        self.fog_slider.valueChanged.connect(self.set_fog_darkness)
        lay.addWidget(self.fog_slider)
        b = QPushButton("Reset explored areas")
        b.clicked.connect(self.reset_explored_ui)
        lay.addWidget(b)

        lay.addWidget(QLabel("<b>Token size when zoomed out</b>"))
        f = QFormLayout()
        self.min_dm_spin, self.min_tv_spin = QSpinBox(), QSpinBox()
        for sp_, val in ((self.min_dm_spin, Tokens.min_dm), (self.min_tv_spin, Tokens.min_tv)):
            sp_.setRange(8, 200)
            sp_.setSuffix(" px")
            sp_.setValue(val)
            sp_.valueChanged.connect(self.set_min_token)
        f.addRow("Smallest on my screen", self.min_dm_spin)
        f.addRow("Smallest on player screen", self.min_tv_spin)
        lay.addLayout(f)

        lay.addWidget(QLabel("<b>Selected player token</b>"))
        f = QFormLayout()
        self.mod_spin = QSpinBox()
        self.mod_spin.setRange(-1000, 5000)
        self.mod_spin.setSingleStep(10)
        self.mod_spin.setSuffix(" ft")
        self.mod_spin.setEnabled(False)
        self.mod_spin.valueChanged.connect(self.on_mod_changed)
        f.addRow("Vision modifier", self.mod_spin)
        lay.addLayout(f)
        self.range_label = QLabel("")
        self.range_label.setWordWrap(True)
        lay.addWidget(self.range_label)
        lay.addStretch()
        dock.setWidget(w)
        self.player_dock = dock
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    def setup_vision(self):
        self.vision_timer = QTimer(self)
        self.vision_timer.setSingleShot(True)
        self.vision_timer.setInterval(40)
        self.vision_timer.timeout.connect(self.run_vision)
        self.detail_timer = QTimer(self)
        self.detail_timer.setSingleShot(True)
        self.detail_timer.setInterval(120)
        self.detail_timer.timeout.connect(self.run_detail)
        self.scene.changed.connect(self.schedule_vision)
        self.scene.selectionChanged.connect(self.on_selection_changed)
        self.ready = True
        self.on_selection_changed()
        self.schedule_vision()

    def schedule_vision(self, *_):
        if not self.vision_timer.isActive():
            self.vision_timer.start()

    def run_vision(self):
        update_vision(self.scene)
        self.refresh_token_list()
        self.refresh_views()
        self.update_range_label()

    def refresh_views(self):
        self.view.viewport().update()
        if self.player_win is not None:
            self.player_win.view.viewport().update()

    def set_night(self, v):
        Vision.night = v
        self.schedule_vision()
        self.refresh_views()
        self.update_range_label()

    def set_ranges(self, *_):
        Vision.day_ft, Vision.night_ft = self.day_spin.value(), self.night_spin.value()
        self.schedule_vision()
        self.update_range_label()

    def set_vision_flag(self, name, value):
        setattr(Vision, name, value)
        self.schedule_vision()
        self.refresh_views()

    def reset_explored_ui(self):
        reset_explored()
        self.schedule_vision()
        self.refresh_views()

    def set_player_grid(self, v):
        self.player_grid = v
        self.refresh_views()

    def selected_player_token(self):
        toks = [it for it in self.scene.selectedItems() if isinstance(it, TokenItem) and it.player]
        return toks[0] if len(toks) == 1 else None

    def on_selection_changed(self):
        tok = self.selected_player_token()
        self.mod_spin.setEnabled(tok is not None)
        self.mod_spin.blockSignals(True)
        self.mod_spin.setValue(tok.vision_mod if tok is not None else 0)
        self.mod_spin.blockSignals(False)
        self.update_range_label()

    def on_mod_changed(self, v):
        tok = self.selected_player_token()
        if tok is not None:
            tok.vision_mod = v
            tok.update()
            self.schedule_vision()
            self.update_range_label()

    def update_range_label(self):
        tok = self.selected_player_token()
        when = "night" if Vision.night else "day"
        if tok is not None:
            self.range_label.setText(f"{tok.name or 'This token'} sees {vision_range_ft(tok):.0f} ft ({when}).")
        else:
            self.range_label.setText(f"It is {when}. Select one player token to adjust its vision. "
                                     "Tick Player token when placing a token, or in its edit dialog.")

    def apply_vision_settings(self, data):
        Vision.day_ft, Vision.night_ft = data.get("day_ft", 200), data.get("night_ft", 40)
        Vision.fog_on, Vision.remember = data.get("fog_on", True), data.get("remember", True)
        Atlas.current, Atlas.warned = None, False
        if data.get("world"):
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                Atlas.current = worldgen.World.from_dict(data["world"])
            finally:
                QApplication.restoreOverrideCursor()
        _QPATHS.clear()
        self.clear_world_caches()
        Vision.fog_alpha = data.get("fog_alpha", 140)
        Tokens.min_dm, Tokens.min_tv = data.get("min_token_px", [28, 44])
        reset_explored(data.get("explored", []))
        self.fog_slider.blockSignals(True)
        self.fog_slider.setValue(round(Vision.fog_alpha * 100 / 255))
        self.fog_slider.blockSignals(False)
        self.fog_label.setText(f"Fog darkness on player screen: {self.fog_slider.value()}%")
        for widget, val in ((self.min_dm_spin, Tokens.min_dm), (self.min_tv_spin, Tokens.min_tv)):
            widget.blockSignals(True)
            widget.setValue(val)
            widget.blockSignals(False)
        self.refresh_token_geometry()
        for widget, val in ((self.day_spin, Vision.day_ft), (self.night_spin, Vision.night_ft)):
            widget.blockSignals(True)
            widget.setValue(val)
            widget.blockSignals(False)
        for widget, val in ((self.fog_chk, Vision.fog_on), (self.remember_chk, Vision.remember)):
            widget.blockSignals(True)
            widget.setChecked(val)
            widget.blockSignals(False)
        self.night_action.setChecked(data.get("night", False))
        frame = data.get("player_frame")
        if frame:
            self.ensure_frame()
            self.frame.set_view_rect(QRectF(*frame))
        self.schedule_vision()

    # ---- the player window and what it shows
    def fill_screen_menu(self, menu):
        menu.clear()
        for scr in QGuiApplication.screens():
            g = scr.geometry()
            act = menu.addAction(f"{scr.name()}  ({g.width()}x{g.height()})")
            act.triggered.connect(lambda _=False, sc=scr: self.open_player_screen(sc))

    def open_player_screen(self, screen=None):
        if self.player_win is None:
            self.player_win = PlayerWindow(self.scene, self)
        win = self.player_win
        target = screen
        if target is None:   # default: the first monitor that is not the one this window is on
            mine = self.screen().name()
            others = [sc for sc in QGuiApplication.screens() if sc.name() != mine]
            target = others[0] if others else None
        win.show()
        if target is not None:
            win.windowHandle().setScreen(target)
            win.move(target.geometry().topLeft())
            win.showFullScreen()
        self.ensure_frame()
        QTimer.singleShot(200, self.sync_player_view)   # once the window has its final size
        self.schedule_vision()

    def close_player_screen(self):
        if self.player_win is not None:
            self.player_win.close()

    def player_closed(self):
        self.player_win = None
        Zoom.tv = 0.0
        self.refresh_token_geometry()

    def set_mirror(self, v):
        self.mirror = v
        self.update_frame_visibility()
        self.sync_player_view()

    def player_aspect(self):
        if self.player_win is not None:
            vp = self.player_win.view.viewport()
            if vp.height() > 0:
                return vp.width() / vp.height()
        return 16 / 9

    def view_rect(self):
        return self.view.mapToScene(self.view.viewport().rect()).boundingRect()

    def expand_to_aspect(self, r):
        """Grow r around its centre until it has the player screen's shape."""
        a = self.player_aspect()
        c, w, h = r.center(), r.width(), r.height()
        if w <= 0 or h <= 0:
            w, h = 1600.0, 900.0
        if w / h < a:
            w = h * a
        else:
            h = w / a
        return QRectF(c.x() - w / 2, c.y() - h / 2, w, h)

    def ensure_frame(self):
        if self.frame is None:
            self.frame = PlayerFrame(self.sync_player_view)
            self.scene.addItem(self.frame)
            self.frame.set_view_rect(self.expand_to_aspect(self.view_rect()))
        self.update_frame_visibility()

    def update_frame_visibility(self):
        if self.frame is not None:
            self.frame.setVisible(self.frame_chk.isChecked() and not self.mirror)

    def match_my_view(self):
        self.ensure_frame()
        self.frame.set_view_rect(self.expand_to_aspect(self.view_rect()))
        self.sync_player_view()

    def zoom_frame(self, factor):
        self.ensure_frame()
        r = QRectF(self.frame.pos(), self.frame.rect().size())
        c, w, h = r.center(), r.width() * factor, r.height() * factor
        self.frame.set_view_rect(QRectF(c.x() - w / 2, c.y() - h / 2, w, h))
        self.sync_player_view()

    def center_on_party(self):
        toks = [it for it in self.scene.items() if isinstance(it, TokenItem) and it.player]
        if not toks:
            return
        cx = sum(t.center().x() for t in toks) / len(toks)
        cy = sum(t.center().y() for t in toks) / len(toks)
        if self.mirror:
            self.view.centerOn(cx, cy)
            return
        self.ensure_frame()
        size = self.frame.rect().size()
        self.frame.setPos(cx - size.width() / 2, cy - size.height() / 2)
        self.sync_player_view()

    def sync_player_view(self):
        """Point the player screen at the frame (or at my view when mirroring)."""
        if self.player_win is None or getattr(self, "_syncing", False):
            return
        self._syncing = True
        try:
            if self.mirror:
                rect = self.expand_to_aspect(self.view_rect())
            else:
                self.ensure_frame()
                cur = QRectF(self.frame.pos(), self.frame.rect().size())
                rect = self.expand_to_aspect(cur)   # keeps the frame the same shape as the screen
                if (abs(rect.width() - cur.width()) > 1e-6 * cur.width()
                        or abs(rect.height() - cur.height()) > 1e-6 * cur.height()):
                    self.frame.set_view_rect(rect)
            self.player_win.view.show_rect(rect)
            self.schedule_detail()
            name = LEVELS[level_for_zoom(self.player_win.view.transform().m11())][0]
            self.frame_label.setText(f"Players see {fmt_len(rect.width())} x {fmt_len(rect.height())} "
                                     f"(1 square = {name}).")
        finally:
            self._syncing = False

    # ---- the generated state
    def clear_world_caches(self):
        self.view._world_cache = None
        if self.player_win is not None:
            self.player_win.view._world_cache = None

    def generate_state(self):
        dlg = GenerateDialog(self)
        if dlg.exec() != QDialog.Accepted or not self.confirm_discard():
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            QApplication.processEvents()
            world = worldgen.World(**dlg.options())
        finally:
            QApplication.restoreOverrideCursor()
        self.reset_scene()
        self.path = None
        Atlas.current, Atlas.warned = world, False
        _QPATHS.clear()
        self.clear_world_caches()
        self.undo.setClean()
        self.update_title()
        k = UNITS_PER_FT   # open on the whole state
        self.view.fitInView(QRectF(world.x0 * k, world.y0 * k, world.W * k, world.H * k), Qt.KeepAspectRatio)
        self.view.refresh_level()
        st = world.stats()
        self.statusBar().showMessage(f"Generated a state: {st['cities']} cities, {st['towns']} towns, "
                                     f"{st['highways']} highways. Use View > Go to City or Town, then zoom in for streets.")
        self.refresh_views()

    def fill_city_menu(self, menu):
        menu.clear()
        w = Atlas.current
        if w is None:
            menu.addAction("(generate a state first)").setEnabled(False)
            return
        places = sorted(w.cities, key=lambda s: -s.radius) + sorted(w.towns, key=lambda s: -s.radius)[:30]
        for st in places:
            act = menu.addAction(f"{st.name} ({st.kind})")
            act.triggered.connect(lambda _=False, s2=st: self.go_to_settlement(s2))

    def go_to_settlement(self, st):
        self.view.set_zoom(JUMP_PX / (20 * UNITS_PER_FT))
        self.view.centerOn(st.x * UNITS_PER_FT, st.y * UNITS_PER_FT)

    def schedule_detail(self):
        if Atlas.current is not None and hasattr(self, "detail_timer") and not self.detail_timer.isActive():
            self.detail_timer.start()

    def run_detail(self):
        """Build streets, buildings, cars and zombies for the chunks being looked at, once each."""
        w = Atlas.current
        if w is None:
            return
        rects = []
        if Zoom.dm >= DETAIL_ZOOM:
            rects.append(self.view_rect())
        if self.player_win is not None and Zoom.tv >= DETAIL_ZOOM:
            pv = self.player_win.view
            rects.append(pv.mapToScene(pv.viewport().rect()).boundingRect())
        ch, k, todo = worldgen.CHUNK_FT, UNITS_PER_FT, []
        for r in rects:
            for cx in range(math.floor(r.left() / k / ch), math.floor(r.right() / k / ch) + 1):
                for cy in range(math.floor(r.top() / k / ch), math.floor(r.bottom() / k / ch) + 1):
                    if (cx, cy) not in w.done and (cx, cy) not in todo:
                        todo.append((cx, cy))
        numbers = {}
        for key in todo[:6]:
            self.instantiate(w.plan_chunk(*key), numbers)
            w.done.add(key)
        if len(todo) > 6:
            self.detail_timer.start()

    def instantiate(self, specs, numbers):
        k = UNITS_PER_FT
        for sp in specs:
            t = sp["t"]
            if t == "road":
                it = RoadItem([QPointF(x * k, y * k) for x, y in sp["pts"]], sp.get("style", "Paved"), sp["width"])
            elif t == "building":
                it = BuildingItem(sp["kind"], sp["w"] * k, sp["h"] * k, sp.get("label", ""))
                it.setPos(sp["x"] * k, sp["y"] * k)
            elif t == "fence":
                it = FenceItem([QPointF(x * k, y * k) for x, y in sp["pts"]], sp.get("style", "Wood"), 1)
            elif t == "terrain":
                it = TerrainItem([QPointF(x * k, y * k) for x, y in sp["pts"]], sp["name"], "rect")
            elif t == "prop":
                fw, fh = worldgen.PROP_FT[sp["kind"]]
                pw, ph = sp.get("w", fw) * k, sp.get("h", fh) * k
                it = PropItem(sp["kind"], pw, ph, sp.get("rot", 0))
                it.setPos(sp["x"] * k - pw / 2, sp["y"] * k - ph / 2)
            elif t == "token":
                kind = sp["kind"]
                color, size = ZOMBIE_LOOK[kind]
                n = numbers.get(kind) or self.next_number(kind)
                numbers[kind] = n + 1
                name = f"{kind} {n}"
                it = TokenItem(name, auto_initials(name), color, size)
                it.setPos(sp["x"] * k, sp["y"] * k)
            else:
                continue
            self.scene.addItem(it)

    def set_laser(self, v):
        Pointer.laser = v
        if not v:
            Pointer.dm = None
        self.update_player_pointer()

    def update_player_pointer(self):
        if self.player_win is not None:
            self.player_win.view.viewport().update()

    def pick_chalk_color(self):
        c = QColorDialog.getColor(Chalk.color, self, "Whiteboard color")
        if c.isValid():
            Chalk.color = c
            swatch(self.chalk_btn, c)

    def chalk_undo(self):
        if Chalk.strokes:
            Chalk.strokes.pop()
            self.refresh_views()

    def chalk_clear(self):
        Chalk.strokes.clear()
        Chalk.cur = None
        self.refresh_views()

    def set_fog_darkness(self, v):
        Vision.fog_alpha = round(255 * v / 100)
        self.fog_label.setText(f"Fog darkness on player screen: {v}%")
        self.refresh_views()

    def set_min_token(self, *_):
        Tokens.min_dm, Tokens.min_tv = self.min_dm_spin.value(), self.min_tv_spin.value()
        self.refresh_token_geometry()
        self.refresh_views()

    def refresh_token_geometry(self):
        for it in self.scene.items():
            if isinstance(it, TokenItem):
                it.refresh_geometry()

    def player_navigated(self):
        """The player screen was panned or zoomed directly: move the DM's frame to match."""
        if self.player_win is None or getattr(self, "_syncing", False):
            return
        view = self.player_win.view
        self._syncing = True
        try:
            rect = view.mapToScene(view.viewport().rect()).boundingRect()
            self.ensure_frame()
            self.frame.set_view_rect(rect)
            name = LEVELS[level_for_zoom(view.transform().m11())][0]
            self.frame_label.setText(f"Players see {fmt_len(rect.width())} x {fmt_len(rect.height())} "
                                     f"(1 square = {name}).")
        finally:
            self._syncing = False
        self.schedule_detail()
        if self.mirror:   # steering the player screen by hand replaces mirroring
            self.mirror_chk.setChecked(False)

    def on_dm_view_changed(self):
        if self.ready:
            if self.mirror:
                self.sync_player_view()
            self.schedule_detail()

    def on_zoom_changed(self):
        if self.ready:
            if self.frame is not None:
                self.frame.refresh()
            self.refresh_token_geometry()
            self.on_dm_view_changed()

    def update_scale_ui(self, level):
        self.scale_combo.blockSignals(True)
        self.scale_combo.setCurrentIndex(level)
        self.scale_combo.blockSignals(False)

    def jump_to_level(self, index):
        self.view.set_zoom(JUMP_PX / (LEVELS[index][1] * UNITS_PER_FT))
        self.view.setFocus()

    def reset_zoom(self):
        c = self.view.mapToScene(self.view.viewport().rect().center())
        self.view.resetTransform()
        self.view.centerOn(c)
        self.view.refresh_level()

    def select_all(self):
        for it in self.scene.items():
            it.setSelected(True)

    def pick_pen_color(self):
        c = QColorDialog.getColor(self.view.pen_color, self, "Pen color")
        if c.isValid():
            self.view.pen_color = c
            swatch(self.pen_btn, c)

    # --- tokens
    def pick_token_color(self):
        c = QColorDialog.getColor(self.token_color, self, "Token color")
        if c.isValid():
            self.token_color = c
            swatch(self.tok_btn, c)

    def apply_preset(self, item):
        cat = self.categories[self.presets.row(item)]
        self.name_edit.setText(cat["name"])
        self.ini_edit.setText(cat.get("initials", ""))
        self.token_color = QColor(cat["color"])
        swatch(self.tok_btn, cat["color"])
        self.size_spin.setValue(cat["size"])
        self.player_chk.setChecked(cat.get("player", False))
        self.number_chk.setChecked(cat.get("numbered", False))

    def refresh_categories(self, select=None):
        self.presets.clear()
        for cat in self.categories:
            pm = QPixmap(14, 14)
            pm.fill(QColor(cat["color"]))
            label = cat["name"] + ("  (player)" if cat.get("player") else "")
            self.presets.addItem(QListWidgetItem(QIcon(pm), label))
        if select is not None:
            self.presets.setCurrentRow(select)

    def current_category(self):
        return {"name": self.name_edit.text().strip() or "Token", "initials": self.ini_edit.text().strip(),
                "color": self.token_color.name(), "size": self.size_spin.value(),
                "player": self.player_chk.isChecked(), "numbered": self.number_chk.isChecked()}

    def new_category(self):
        """Set a token up in the panel, then save it as a reusable category."""
        dlg = CategoryDialog(self, self.current_category())
        if dlg.exec() == QDialog.Accepted:
            self.categories.append(dlg.result_cat())
            save_categories(self.categories)
            self.refresh_categories(len(self.categories) - 1)

    def edit_category(self):
        row = self.presets.currentRow()
        if row < 0:
            self.statusBar().showMessage("Pick a category in the list first.")
            return
        dlg = CategoryDialog(self, self.categories[row])
        if dlg.exec() == QDialog.Accepted:
            self.categories[row] = dlg.result_cat()
            save_categories(self.categories)
            self.refresh_categories(row)

    def delete_category(self):
        row = self.presets.currentRow()
        if row < 0:
            self.statusBar().showMessage("Pick a category in the list first.")
            return
        name = self.categories[row]["name"]
        if QMessageBox.question(self, "Delete category", f"Delete the category \"{name}\"?") == QMessageBox.Yes:
            del self.categories[row]
            save_categories(self.categories)
            self.refresh_categories()

    # ---- placing several tokens at once
    def next_number(self, base):
        rx = re.compile(r"^" + re.escape(base) + r"\s+(\d+)$")
        nums = []
        for t in self.scene.items():
            if isinstance(t, TokenItem):
                m = rx.match(t.name)
                if m:
                    nums.append(int(m.group(1)))
        return max(nums, default=0) + 1

    def make_tokens(self, sp):
        """The token(s) for one click: a single one, or `count` of them in a cluster or scatter."""
        count, size = self.count_spin.value(), self.size_spin.value()
        size_units = size * CELL
        numbered = self.number_chk.isChecked()
        base = self.name_edit.text().strip()
        first = 1
        if numbered:
            base = re.sub(r"\s*\d+\s*$", "", base) or "Token"
            first = self.next_number(base)
        ini = self.ini_edit.text().strip()
        snap = self.view.snap
        pitch = max(size_units, Snap.step) if snap else size_units * 1.1
        if snap:
            ox = math.floor(sp.x() / Snap.step) * Snap.step
            oy = math.floor(sp.y() / Snap.step) * Snap.step
        else:
            ox, oy = sp.x() - size_units / 2, sp.y() - size_units / 2
        occupied = {(round(t.pos().x()), round(t.pos().y())) for t in self.scene.items()
                    if isinstance(t, TokenItem)}
        offsets = []
        if count > 1 and self.arrange_combo.currentText() == "Scatter":
            r = self.spread_spin.value()
            offsets = [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1) if dx * dx + dy * dy <= r * r]
            random.shuffle(offsets)
        offsets += spiral_offsets(count * 4 + 25)   # cluster order, and the fallback for scatter
        spots, used = [], set()
        for dx, dy in offsets:
            if (dx, dy) in used:
                continue
            used.add((dx, dy))
            x, y = ox + dx * pitch, oy + dy * pitch
            if count > 1 and (round(x), round(y)) in occupied:
                continue
            spots.append((x, y))
            if len(spots) == count:
                break
        toks = []
        for i, (x, y) in enumerate(spots):
            name = f"{base} {first + i}" if numbered else base
            tok = TokenItem(name, ini or auto_initials(name), self.token_color, size, self.player_chk.isChecked())
            tok.setPos(x, y)
            toks.append(tok)
        return toks

    # ---- token list and zooming to tokens
    def build_token_dock(self):
        dock = QDockWidget("Tokens", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Click to select, double-click to zoom to a token. Players are starred."))
        self.token_list = QListWidget()
        self.token_list.itemClicked.connect(self.select_listed_token)
        self.token_list.itemDoubleClicked.connect(self.zoom_listed_token)
        lay.addWidget(self.token_list)
        for text, slot in [("Zoom to selected token (Z)", self.zoom_to_selected),
                           ("Zoom to party (Shift+Z)", self.zoom_to_party),
                           ("Show selected token to players", self.show_selected_to_players)]:
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, sl=slot: sl())
            lay.addWidget(b)
        dock.setWidget(w)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.tabifyDockWidget(self.player_dock, dock)
        self.player_dock.raise_()

    def refresh_token_list(self):
        toks = [it for it in self.scene.items() if isinstance(it, TokenItem)]
        toks.sort(key=lambda t: (not t.player, t.name.lower(), id(t)))
        sig = tuple((id(t), t.name, t.player, t.color.name()) for t in toks)
        if sig == getattr(self, "_token_sig", None):
            return
        self._token_sig, self._token_refs = sig, toks
        self.token_list.clear()
        for t in toks:
            pm = QPixmap(14, 14)
            pm.fill(t.color)
            self.token_list.addItem(QListWidgetItem(QIcon(pm), ("\u2605 " if t.player else "") + (t.name or "(unnamed)")))

    def listed_token(self, item):
        row = self.token_list.row(item)
        toks = getattr(self, "_token_refs", [])
        if 0 <= row < len(toks):
            try:
                if toks[row].scene() is self.scene:
                    return toks[row]
            except RuntimeError:   # the token was deleted since the list was built
                pass
        return None

    def select_listed_token(self, item):
        tok = self.listed_token(item)
        if tok is not None:
            self.scene.clearSelection()
            tok.setSelected(True)

    def zoom_listed_token(self, item):
        tok = self.listed_token(item)
        if tok is not None:
            self.scene.clearSelection()
            tok.setSelected(True)
            self.view.focus_items([tok])

    def zoom_to_selected(self):
        toks = [it for it in self.scene.selectedItems() if isinstance(it, TokenItem)]
        if toks:
            self.view.focus_items(toks)
        else:
            self.statusBar().showMessage("Select a token first, or double-click one in the Tokens tab.")

    def zoom_to_party(self):
        toks = [it for it in self.scene.items() if isinstance(it, TokenItem) and it.player]
        if toks:
            self.view.focus_items(toks)
        else:
            self.statusBar().showMessage("No player tokens on the map yet.")

    def show_selected_to_players(self):
        toks = [it for it in self.scene.selectedItems() if isinstance(it, TokenItem)]
        if not toks:
            self.statusBar().showMessage("Select a token first.")
            return
        cx = sum(t.center().x() for t in toks) / len(toks)
        cy = sum(t.center().y() for t in toks) / len(toks)
        if self.mirror:
            self.view.centerOn(cx, cy)
            return
        self.ensure_frame()
        size = self.frame.rect().size()
        self.frame.setPos(cx - size.width() / 2, cy - size.height() / 2)
        self.sync_player_view()

    def make_token(self):
        name = self.name_edit.text().strip()
        ini = self.ini_edit.text().strip() or auto_initials(name)
        return TokenItem(name, ini, self.token_color, self.size_spin.value(), self.player_chk.isChecked())

    def bump_name(self):
        name = self.name_edit.text().strip()
        m = re.search(r"(\d+)\s*$", name)
        if m:
            self.name_edit.setText(name[:m.start()] + str(int(m.group(1)) + 1))

    def edit_building(self, b):
        dlg = BuildingDialog(self, b.props())
        if dlg.exec() == QDialog.Accepted:
            new = dlg.result_props()
            if new != b.props():
                self.undo.push(EditTokenCommand(b, b.props(), new))

    def edit_token(self, tok):
        dlg = TokenDialog(self, tok.props())
        if dlg.exec() == QDialog.Accepted:
            new = dlg.result_props()
            if new != tok.props():
                self.undo.push(EditTokenCommand(tok, tok.props(), new))

    # --- files
    def update_title(self, *_):
        name = self.path.split("/")[-1] if self.path else "Untitled"
        self.setWindowTitle(f"{name}{'' if self.undo.isClean() else ' *'} - Infinite Battle Map")

    def confirm_discard(self):
        if self.undo.isClean():
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes to this map first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_file()
        return r == QMessageBox.Discard

    def closeEvent(self, e):
        if not self.confirm_discard():
            e.ignore()
            return
        if self.player_win is not None:
            self.player_win.close()
        e.accept()

    def reset_scene(self):
        self.undo.clear()
        self.view._press_pos = {}
        self.scene.clear()
        self.frame = None   # the player frame lived in the scene and is gone now
        reset_explored()
        Chalk.strokes.clear()
        Atlas.current = None

    def new_file(self):
        if self.confirm_discard():
            self.reset_scene()
            self.path = None
            self.undo.setClean()
            self.update_title()

    def open_file(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open map", str(MAPS_DIR), "Battle maps (*.json)")
        if path:
            self.load_from(path)

    def load_from(self, path):
        try:
            with open(path) as f:
                data = json.load(f)
            prev, Snap.on = Snap.on, False   # keep saved positions exactly as stored
            try:
                items = [item_from_dict(d) for d in data["items"]]
            finally:
                Snap.on = prev
        except Exception as ex:
            QMessageBox.warning(self, "Could not open", str(ex))
            return
        self.reset_scene()
        for it in items:
            self.scene.addItem(it)
        self.act_dark(data.get("dark", False))
        self.view.resetTransform()
        z = data.get("zoom", 1.0)
        self.view.scale(z, z)
        self.view.centerOn(*data.get("center", [0, 0]))
        self.view.refresh_level()
        self.apply_vision_settings(data)
        self.path = path
        self.undo.setClean()
        self.update_title()

    def act_dark(self, v):
        for a in self.menuBar().actions()[2].menu().actions():
            if a.text() == "Dark Canvas":
                a.setChecked(v)

    def save_file(self):
        return self.save_to(self.path) if self.path else self.save_as()

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save map", str(MAPS_DIR / "map.json"), "Battle maps (*.json)")
        if not path:
            return False
        if not path.endswith(".json"):
            path += ".json"
        return self.save_to(path)

    def save_to(self, path):
        c = self.view.mapToScene(self.view.viewport().rect().center())
        data = {"app": "infinite-battle-map", "version": 1, "dark": self.view.dark,
                "center": [c.x(), c.y()], "zoom": self.view.transform().m11(),
                "items": [it.to_dict() for it in self.scene.items(Qt.AscendingOrder)
                          if hasattr(it, "to_dict")],
                "night": Vision.night, "day_ft": Vision.day_ft, "night_ft": Vision.night_ft,
                "fog_on": Vision.fog_on, "remember": Vision.remember, "fog_alpha": Vision.fog_alpha,
                "min_token_px": [Tokens.min_dm, Tokens.min_tv],
                "explored": [[round(v, 1) for pt in poly for v in pt] for poly in Vision.explored_polys],
                "player_frame": ([self.frame.pos().x(), self.frame.pos().y(), self.frame.rect().width(),
                                  self.frame.rect().height()] if self.frame is not None else None),
                "world": Atlas.current.to_dict() if Atlas.current is not None else None}
        try:
            with open(path, "w") as f:
                json.dump(data, f)
        except OSError as ex:
            QMessageBox.warning(self, "Could not save", str(ex))
            return False
        self.path = path
        self.undo.setClean()
        self.update_title()
        return True

    def export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export PNG", "map.png", "PNG image (*.png)")
        if path:
            self.scene.clearSelection()
            self.view.viewport().grab().save(path)


def main():
    MAPS_DIR.mkdir(exist_ok=True)
    ensure_assets()
    app = QApplication(sys.argv)
    app.setApplicationName("Infinite Battle Map")
    w = MainWindow()
    w.show()
    w.view.centerOn(0, 0)
    if len(sys.argv) > 1:
        w.load_from(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
