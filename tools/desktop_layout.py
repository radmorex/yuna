"""Расчёт положения окна компаньона: прилипание к углам экрана. Без зависимостей, чтобы легко тестировать."""
MARGIN = 16
MENU_BAR = 36
DOCK = 84
SIZES = {False: (580, 400), True: (230, 330)}  # развёрнуто / свёрнуто
CORNERS = ("tl", "tr", "bl", "br")


def size(compact):
    return SIZES[bool(compact)]


def position(corner, compact, sw, sh, sx=0, sy=0):
    """Координаты окна в углу экрана. Сверху отступ под меню, снизу под Dock."""
    w, h = size(compact)
    x = sx + MARGIN if corner[1] == "l" else sx + sw - w - MARGIN
    y = sy + MENU_BAR if corner[0] == "t" else sy + sh - h - DOCK
    return x, y


def nearest_corner(x, y, compact, sw, sh, sx=0, sy=0):
    """Ближайший угол по центру окна."""
    w, h = size(compact)
    cx, cy = x + w / 2, y + h / 2
    v = "t" if cy < sy + sh / 2 else "b"
    u = "l" if cx < sx + sw / 2 else "r"
    return v + u


def moved(x, y, ex, ey, tolerance=6):
    return abs(x - ex) > tolerance or abs(y - ey) > tolerance
