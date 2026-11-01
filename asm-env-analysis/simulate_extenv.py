#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
simulate_extenv.py — воспроизведение работы приложения ExtEnvAnalysis
(WPF, C#, QuestPDF) на Python: тот же набор шагов, та же структура
и та же вёрстка PDF-отчёта, но с осмысленными данными.

Шаги приложения (вкладки 1..7), которые эмулируются:
    1. Сегмент                -> segment
    2. PESTEL                 -> pestel
    3. Факторы и веса         -> factors
    4. Оценка компаний        -> ratings (оценки 1..10, доли рынка в %)
    5. Карты сравнения        -> C(10,2) = 45 стратегических карт
    6. Итоги                  -> заключение + сводная таблица
    7. Отчёт                  -> сборка PDF

Расчёты повторяют Services/RatingCalculationService.cs:
    взвешенный рейтинг = round(sum(вес_i * оценка_i), 2)   (0..10)
    доля рынка         = введённый процент / 100

Вёрстка повторяет ReportBuilder.cs + отчёт, сгенерированный exe:
    A4, поля 30 pt, шрифт Lato 11 pt, строка 13,2 pt,
    заголовок 18 pt, подзаголовки 14 / 11 pt, колонтитул «Стр. N / M».

Запуск:
    python simulate_extenv.py --data data/asm_emulator_env.json --out out/report.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import pymupdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_REG = os.path.join(BASE_DIR, "fonts", "Lato-Regular.ttf")
FONT_BOLD = os.path.join(BASE_DIR, "fonts", "Lato-Bold.ttf")

# --- геометрия страницы (как в отчёте приложения) ---------------------------
PAGE_W, PAGE_H = 595.0, 842.0          # A4
MARGIN = 30.0
CONTENT_W = PAGE_W - 2 * MARGIN        # 535 pt
CONTENT_TOP = 51.8                     # под шапкой «Анализ внешней среды»
CONTENT_BOTTOM = PAGE_H - 30.0         # 812 pt

REPORT_TITLE = "Анализ внешней среды"

SIZE_TITLE, SIZE_SECTION, SIZE_BODY = 18.0, 14.0, 11.0
LINE = 1.2                             # межстрочный коэффициент (как в QuestPDF)
GAP_SECTION = 10.0
GAP_TABLE_HEADER = 4.0
TABLE_CELL_PAD = 2.0

# --- цвета точек на стратегических картах (ComparisonsState.cs) ------------
COMPANY_COLORS = [
    (0x21 / 255, 0x96 / 255, 0xF3 / 255),   # Мы   #2196F3
    (0xFF / 255, 0x98 / 255, 0x00 / 255),   # A    #FF9800
    (0x78 / 255, 0x90 / 255, 0x9C / 255),   # B    #78909C
    (0x9C / 255, 0x27 / 255, 0xB0 / 255),   # C    #9C27B0
]
CHART_SIDE = 300.0                      # Height(300) для картинки 800x800
CHART_REF = 800.0                       # координаты StrategyMap заданы в px
ALPHA_FILL = 32 / 255

PESTEL_TITLES = {
    "P": "P — Политические",
    "E": "E — Экономические",
    "S": "S — Социальные",
    "T": "T — Технологические",
    "Env": "E — Экологические",
    "L": "L — Правовые",
}

LEVEL_TITLES = {"bachelor": "Бакалавр", "master": "Магистр", "developer": "Разработчик"}


# ===========================================================================
#  Шрифты и метрики
# ===========================================================================
_fonts = {
    "reg": pymupdf.Font(fontfile=FONT_REG),
    "bold": pymupdf.Font(fontfile=FONT_BOLD),
}
ASCENT = _fonts["reg"].ascender          # 0.987


def text_width(s: str, size: float, bold: bool = False) -> float:
    return _fonts["bold" if bold else "reg"].text_length(s, size)


def _num(v: float, decimals: int = 2) -> str:
    """Русская запись числа: 0,15 / 8,50."""
    s = f"{v:.{decimals}f}"
    return s.replace(".", ",")


# ===========================================================================
#  Мини-движок вёрстки (аналог QuestPDF: поток элементов с переносом страниц)
# ===========================================================================
class Report:
    def __init__(self) -> None:
        self.doc = pymupdf.open()
        self.page = None
        self.y = CONTENT_TOP
        self._fonts_on_page: set[str] = set()
        self._new_page()

    # -- служебное ---------------------------------------------------------
    def _new_page(self) -> None:
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self._fonts_on_page = set()
        self.y = CONTENT_TOP
        # шапка отчёта
        self._raw_text((PAGE_W / 2, 30.2), REPORT_TITLE, SIZE_TITLE, bold=True,
                       align="center", width=PAGE_W)

    def _ensure_font(self, bold: bool) -> str:
        name = "LatoB" if bold else "LatoR"
        if name not in self._fonts_on_page:
            self.page.insert_font(fontname=name,
                                  fontfile=FONT_BOLD if bold else FONT_REG)
            self._fonts_on_page.add(name)
        return name

    def _raw_text(self, pos, s, size, bold=False, align="left", width=0.0,
                  color=(0, 0, 0), rotate=0):
        w = text_width(s, size, bold)
        x, y_top = pos
        if align == "center":
            x -= w / 2
        elif align == "right":
            x -= w
        self.page.insert_text((x, y_top + ASCENT * size), s,
                              fontname=self._ensure_font(bold),
                              fontsize=size, color=color, rotate=rotate)
        return w

    def ensure_room(self, h: float) -> None:
        if self.y + h > CONTENT_BOTTOM:
            self._new_page()

    def page_break(self) -> None:
        self._new_page()

    # -- текстовые блоки ---------------------------------------------------
    def line(self, s, size=SIZE_BODY, bold=False, indent=0.0,
             color=(0, 0, 0), width=None):
        """Одна строка (для длинных строк — с переносом по словам)."""
        width = CONTENT_W - indent if width is None else width
        lh = size * LINE
        for chunk in self._wrap(s, size, bold, width):
            self.ensure_room(lh)
            self._raw_text((MARGIN + indent, self.y), chunk, size, bold,
                           color=color)
            self.y += lh

    def _wrap(self, s: str, size: float, bold: bool, width: float):
        if not s:
            return [""]
        out, cur = [], ""
        for word in s.split(" "):
            probe = word if not cur else cur + " " + word
            if text_width(probe, size, bold) <= width:
                cur = probe
            else:
                if cur:
                    out.append(cur)
                cur = word
        out.append(cur)
        return out

    def section(self, s: str) -> None:
        if abs(self.y - CONTENT_TOP) > 1e-6:
            self.y += GAP_SECTION
        self.ensure_room(SIZE_SECTION * LINE)
        self._raw_text((MARGIN, self.y), s, SIZE_SECTION, bold=True)
        self.y += SIZE_SECTION * LINE

    def gap(self, h: float) -> None:
        self.y += h

    # -- таблицы -----------------------------------------------------------
    def table(self, ratios, header, rows, aligns):
        """ratios — относительные ширины, aligns — 'left'/'right'/'center'."""
        unit = CONTENT_W / sum(ratios)
        widths = [r * unit for r in ratios]
        lh = SIZE_BODY * LINE

        def draw_row(cells, bold=False, pad_top=0.0, pad_bottom=0.0):
            self.ensure_room(pad_top + lh + pad_bottom)
            self.y += pad_top
            x = MARGIN
            for w, cell, al in zip(widths, cells, aligns):
                if cell:
                    if al == "left":
                        self._raw_text((x, self.y), cell, SIZE_BODY, bold)
                    elif al == "center":
                        self._raw_text((x + w / 2, self.y), cell, SIZE_BODY,
                                       bold, align="center")
                    else:
                        self._raw_text((x + w, self.y), cell, SIZE_BODY, bold,
                                       align="right")
                x += w
            self.y += lh + pad_bottom

        draw_row(header, bold=True, pad_bottom=GAP_TABLE_HEADER)
        for row in rows:
            draw_row(row, pad_top=TABLE_CELL_PAD, pad_bottom=TABLE_CELL_PAD)

    # -- стратегическая карта (аналог Controls/StrategyMap.xaml.cs) --------
    def strategy_map(self, map_data: dict) -> None:
        self.ensure_room(CHART_SIDE)
        side = CHART_SIDE
        left, right, top, bottom = 90.0, 24.0, 28.0, 90.0   # координаты в px
        k = side / CHART_REF
        x0, y0 = MARGIN, self.y
        L, R, T, B = left * k, right * k, top * k, bottom * k
        gw, gh = side - L - R, side - T - B
        px0, py0 = x0 + L, y0 + T
        cell_w, cell_h = gw / 10.0, gh / 10.0

        # 1) сетка и оси
        shape = self.page.new_shape()
        grid = (0.863, 0.863, 0.863)
        for i in range(11):
            gx = px0 + i * cell_w
            shape.draw_line((gx, py0), (gx, py0 + gh))
            gy = py0 + gh - i * cell_h
            shape.draw_line((px0, gy), (px0 + gw, gy))
        shape.finish(color=grid, width=1.0 * k, closePath=False)
        shape.draw_rect(pymupdf.Rect(px0, py0, px0 + gw, py0 + gh))
        shape.finish(color=(0, 0, 0), width=1.5 * k)
        for i in range(1, 11):
            tick_x = px0 + i * cell_w
            shape.draw_line((tick_x, py0 + gh), (tick_x, py0 + gh + 6 * k))
            tick_y = py0 + gh - i * cell_h
            shape.draw_line((px0 - 6 * k, tick_y), (px0, tick_y))
        shape.finish(color=(0, 0, 0), width=1.0 * k)
        shape.commit()

        # 2) точки компаний (размер = доля рынка, как на StrategyMap)
        to_px_x = lambda v: px0 + (min(max(v, 1), 10) - 0.5) * cell_w
        to_px_y = lambda v: py0 + gh - (min(max(v, 1), 10) - 0.5) * cell_h
        cell_size = min(cell_w, cell_h)
        r_max, r_min = 0.48 * cell_size, 0.14 * cell_size
        max_market = max(1e-9, max(p["market"] for p in map_data["points"]))

        centers = []
        shape = self.page.new_shape()
        for idx, p in enumerate(map_data["points"]):
            color = COMPANY_COLORS[idx]
            radius = max(r_min, r_max * p["market"] / max_market)
            cxp, cyp = to_px_x(p["x"]), to_px_y(p["y"])
            centers.append((cxp, cyp, radius, color, p["name"]))
            fill = tuple(1 - ALPHA_FILL * (1 - c) for c in color)
            shape.draw_circle((cxp, cyp), radius)
            shape.finish(color=color, fill=fill, width=1.6 * k)
        shape.commit()

        # 3) подписи компаний с белой подложкой (не перекрывают кружки)
        label_size = max(12 * k, 5.6)
        label_boxes = []
        for cxp, cyp, radius, color, name in centers:
            tw = text_width(name, label_size, bold=True)
            th = 0.98 * label_size
            lx = cxp + radius + 3 * k
            if lx + tw > x0 + side - 1:          # не влезает справа — слева
                lx = cxp - radius - 3 * k - tw
            ly = cyp - th / 2
            label_boxes.append((lx, ly, tw, th, name, color))
        shape = self.page.new_shape()
        for lx, ly, tw, th, _n, _c in label_boxes:
            shape.draw_rect(pymupdf.Rect(lx - 1, ly, lx + tw + 1, ly + th))
        shape.finish(color=None, fill=(1, 1, 1))
        shape.commit()
        for lx, ly, tw, th, name, color in label_boxes:
            self.page.insert_text((lx, ly + th - 0.22 * label_size), name,
                                  fontname=self._ensure_font(True),
                                  fontsize=label_size, color=color)

        # 4) подписи делений и осей
        for i in range(1, 11):
            cx = px0 + (i - 0.5) * cell_w
            self._centered(cx, py0 + gh + 34 * k, str(i), 18 * k)
            cy = py0 + gh - (i - 0.5) * cell_h
            self._centered(px0 - 34 * k, cy, str(i), 18 * k)

        self._centered(px0 + gw / 2, py0 + gh + 58 * k,
                       map_data["axis_x"], 16 * k, bold=True)
        y_title = map_data["axis_y"]
        tw = text_width(y_title, 16 * k, bold=True)
        self.page.insert_text((px0 - 58 * k, py0 + gh / 2 + tw / 2),
                              y_title, fontname=self._ensure_font(True),
                              fontsize=16 * k, color=(0, 0, 0), rotate=90)
        self.y += side

    def _centered(self, cx, cy, s, size, bold=False):
        w = text_width(s, size, bold)
        self.page.insert_text((cx - w / 2, cy + 0.35 * size), s,
                              fontname=self._ensure_font(bold),
                              fontsize=size, color=(0, 0, 0))

    # -- финализация -------------------------------------------------------
    def save(self, path: str) -> None:
        total = len(self.doc)
        for i, page in enumerate(self.doc, start=1):
            label = f"Стр. {i} / {total}"
            for name, ffile in (("LatoR", FONT_REG), ("LatoB", FONT_BOLD)):
                page.insert_font(fontname=name, fontfile=ffile)
            page.insert_text((PAGE_W - MARGIN - text_width(label, SIZE_BODY),
                              CONTENT_BOTTOM - SIZE_BODY * LINE
                              + ASCENT * SIZE_BODY),
                             label, fontname="LatoR", fontsize=SIZE_BODY,
                             color=(0, 0, 0))
        self.doc.set_metadata({
            "title": "Анализ внешней среды",
            "author": "",
            "creator": "ExtEnvAnalysis (симуляция отчёта)",
            "producer": "PyMuPDF",
            "creationDate": datetime.now().strftime("D:%Y%m%d%H%M%S"),
        })
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.doc.save(path, deflate=True, garbage=3)
        self.doc.close()


# ===========================================================================
#  6. Итоги: расчёты (RatingCalculationService.cs)
# ===========================================================================
def weighted_rating(weights, scores) -> float:
    return round(sum(w * s for w, s in zip(weights, scores)) + 1e-12, 2)


def build_directions(factors, ratings) -> list[str]:
    """Осмысленный текст «Направление развития» для каждой пары факторов.

    Для каждой оси сравниваем нашу оценку с лучшим конкурентом и формируем
    вывод: лидерство / паритет / отставание, плюс приоритетное действие
    по более слабому фактору пары.
    """
    names = ratings["companies"]
    competitors = [("a", names["a"]), ("b", names["b"]), ("c", names["c"])]
    directions = []

    def status(i):
        mine = ratings["scores"][i]["my"]
        best_key, best_val = max(((k, ratings["scores"][i][k]) for k, _ in competitors),
                                 key=lambda kv: kv[1])
        best_name = names[best_key]
        delta = mine - best_val
        if delta > 0:
            label = f"лидерство ({mine} против {best_val} у {best_name})"
        elif delta == 0:
            label = f"паритет с {best_name} (по {mine})"
        else:
            label = f"отставание ({mine} против {best_val} у {best_name})"
        return delta, label

    for i in range(len(factors) - 1):
        for j in range(i + 1, len(factors)):
            dx, sx = status(i)
            dy, sy = status(j)
            weak = i if dx <= dy else j
            directions.append(
                f"{factors[i]['name']}: {sx}; {factors[j]['name']}: {sy}. "
                f"Приоритетное направление: {factors[weak]['action']}."
            )
    return directions


# ===========================================================================
#  7. Отчёт: сборка PDF (ReportBuilder.cs)
# ===========================================================================
def build_report(data: dict, out_path: str, verbose: bool = False) -> dict:
    rep = Report()
    profile = data["profile"]
    factors = data["factors"]
    ratings = data["ratings"]
    names = ratings["companies"]

    weights = [f["weight"] for f in factors]
    scores = ratings["scores"]
    cols = ["my", "a", "b", "c"]

    totals = [weighted_rating(weights, [s[c] for s in scores]) for c in cols]
    shares = [ratings["market_share_percent"][c] / 100.0 for c in cols]

    # ---- стр. 1: профиль, сегмент, PESTEL -------------------------------
    rep.section("Профиль")
    rep.line(f"ФИО: {profile['full_name']}")
    rep.line(f"Группа: {profile['group']}")
    rep.line(f"Уровень: {LEVEL_TITLES.get(profile['level'], profile['level'])}")

    rep.section("Сегмент")
    rep.line(data["segment"] or "—")

    rep.section("PESTEL")
    for key, title in PESTEL_TITLES.items():
        rep.line(title, bold=True)
        values = [v for v in data["pestel"].get(key, []) if v.strip()]
        if not values:
            rep.line("—")
        for v in values:
            rep.line(f"• {v}")
    rep.page_break()

    # ---- стр. 2: факторы и веса, оценка компаний, сводка ---------------
    rep.section("Факторы и веса")
    rep.table([3, 1], ["Фактор", "Вес"],
              [[f["name"], _num(f["weight"])] for f in factors],
              ["left", "right"])

    rep.section("Оценка компаний")
    rep.table([4, 1, 1, 1, 1],
              ["Фактор", names["my"], names["a"], names["b"], names["c"]],
              [[f["name"]] + [str(s[c]) for c in cols] for f, s in zip(factors, scores)],
              ["left", "center", "center", "center", "center"])

    rep.gap(8)
    rep.line("Сводка по компаниям", bold=True)
    rep.table([2.0, 1.2, 1.6],
              ["Компания", "Доля рынка", "Взвеш. рейтинг (0–10)"],
              [[names[c], _num(shares[k] * 100, 1) + " %", _num(totals[k])]
               for k, c in enumerate(cols)],
              ["left", "right", "right"])

    # ---- 45 стратегических карт ----------------------------------------
    directions = build_directions(factors, ratings)
    rep.page_break()
    idx = 0
    for i in range(len(factors) - 1):
        for j in range(i + 1, len(factors)):
            idx += 1
            rep.gap(10)
            rep.line(f"Стратегическая карта {idx}", size=SIZE_SECTION, bold=True)
            rep.strategy_map({
                "axis_x": factors[i]["name"],
                "axis_y": factors[j]["name"],
                "points": [
                    {"name": names[c],
                     "x": scores[i][c], "y": scores[j][c],
                     "market": ratings["market_share_percent"][c]}
                    for c in cols
                ],
            })
            rep.gap(6)
            rep.line("Пояснение", bold=True)
            rep.line(f"X: {factors[i]['name']}")
            rep.line(f"Y: {factors[j]['name']}")
            rep.line("Подсказка: отметьте лидеров по осям и зоны роста.")
            rep.gap(19.2)
            rep.line("Направление развития", bold=True)
            rep.line(directions[idx - 1])
            rep.page_break()

    # ---- итоговая страница ---------------------------------------------
    rep.gap(10)
    rep.line("Итоги по компаниям", size=SIZE_SECTION, bold=True)
    conclusion = data.get("conclusion", "").strip()
    if conclusion:
        rep.gap(8)
        rep.line("Итоги", bold=True)
        rep.line(conclusion)
        rep.gap(13.2 + 12)

    rep.table([2.0, 1.2, 1.4],
              ["Компания", "Доля рынка", "Взвешенный рейтинг (0–10)"],
              [[names[c], _num(shares[k] * 100, 1) + " %", _num(totals[k])]
               for k, c in enumerate(cols)],
              ["left", "right", "right"])

    best = max(totals)
    leaders = [names[c] for k, c in enumerate(cols) if abs(totals[k] - best) < 1e-9]
    rep.gap(6)
    if len(leaders) > 1:
        rep.line(f"Лидеры по рейтингу: {', '.join(leaders)} (по {_num(best)}).")
    else:
        rep.line(f"Лидер по рейтингу: {leaders[0]} ({_num(best)}).")

    rep.save(out_path)

    result = {
                "totals": dict(zip(cols, totals)),
        "shares": dict(zip(cols, [round(s * 100, 1) for s in shares])),
        "maps": idx,
        "leader": leaders[0],
    }
    if verbose:
        print(f"[5] Карты сравнения: {idx} шт.")
        for c in cols:
            print(f"    {names[c]:<12} доля {result['shares'][c]:>5} %  "
                  f"рейтинг {_num(result['totals'][c])}")
        print(f"[7] PDF сохранён: {out_path}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Симуляция ExtEnvAnalysis -> PDF")
    ap.add_argument("--data", default=os.path.join(BASE_DIR, "data",
                                                   "asm_emulator_env.json"))
    ap.add_argument("--out", default=os.path.join(BASE_DIR, "out", "report.pdf"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    with open(args.data, encoding="utf-8") as fh:
        data = json.load(fh)

    if args.verbose:
        print(f"[1] Сегмент: {data['segment']}")
        print(f"[2] PESTEL: {sum(len(v) for v in data['pestel'].values())} факторов")
        print(f"[3] Факторы и веса: {len(data['factors'])} шт., "
              f"сумма весов {sum(f['weight'] for f in data['factors']):.2f}")
        print(f"[4] Оценка компаний: {', '.join(data['ratings']['companies'].values())}")

    res = build_report(data, args.out, verbose=args.verbose)

    import pymupdf as _mp
    with _mp.open(args.out) as doc:
        result_pages = len(doc)
    if args.verbose:
        print(f"[6] Итоги: лидер — {res['leader']}, страниц в отчёте — {result_pages}")
    else:
        print(f"{args.out}: {result_pages} стр., лидер — {res['leader']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
