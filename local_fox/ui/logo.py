"""
The fox of Local Fox, in braille dots (each character a 2 x 4 cell of dots): FOX below is the logo, the only copy of it.

It is drawn in two ways. In the start dialog each dot is a small round dot on an even grid (as text, braille comes
from a fallback font with gaps between the lines, which break the picture). As the icon of the window and the
executable, each dot is one pixel in the accent color on the window's background, scaled up in whole pixels so
that it stays sharp; write_ico writes the executable's icon for build.ps1.
"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap

from . import theme

FOX = "\n".join([
    "⢰⠢⣄⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠀⠀⠀⠀⠀",
    "⠈⡄⢀⠙⢦⣀⠀⠀⠀⢀⢎⢀⠦⢹⡄⠀⠀⠀",
    "⠀⢡⠨⣄⠈⠿⣕⣄⢀⡾⠊⠨⣸⡀⢧⠀⠀⠀",
    "⠀⠈⢞⠻⡗⢆⢷⢇⠛⢀⡀⢴⣶⡇⣸⣦⠀⠀",
    "⠀⠀⠸⠢⠕⠈⠀⠈⠀⠞⢱⠿⠛⢠⢿⡙⠀⠀",
    "⠀⠀⢐⠆⠀⠀⠀⠀⠀⠀⠁⣄⣄⡼⢎⡀⢣⠀",
    "⠀⠀⠙⢢⠀⠀⢰⣧⣤⠠⣆⡀⠫⡱⡢⡀⢹⠀",
    "⠀⠀⠀⡟⠀⡤⡏⠈⠀⠐⠊⠙⠢⡸⣞⡌⠘⡀",
    "⠀⢀⠎⠀⠀⣴⡷⡤⢂⠁⠀⠀⢠⠈⠟⡞⢆⡇",
    "⠀⢿⣿⠞⠉⢙⣥⠮⠍⠒⠒⠠⠼⡀⠀⠇⡼⠀",
    "⠀⠀⠉⠉⠛⠋⠀⠀⠀⠀⠀⠀⠀⠀⠑⠞⠁⠀",
])
# The dots of a braille character by their bit: (column, row) in its 2 x 4 cell
_BRAILLE_DOTS = {0x01: (0, 0), 0x02: (0, 1), 0x04: (0, 2), 0x40: (0, 3), 0x08: (1, 0), 0x10: (1, 1), 0x20: (1, 2), 0x80: (1, 3)}
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

def dots() -> tuple[list[tuple[int, int]], int, int]:
    """
    Returns the fox's dots as (x, y) on its dot grid, and the grid's width and height.
    """
    lines = FOX.splitlines()
    found = []
    for row, line in enumerate(lines):
        for column, char in enumerate(line):
            bits = ord(char) - 0x2800
            found += [(column * 2 + x, row * 4 + y) for bit, (x, y) in _BRAILLE_DOTS.items() if bits & bit]
    return found, max(len(line) for line in lines) * 2, len(lines) * 4

def fox_pixmap(color: str, spacing: int = 3, radius: float = 1.1) -> QPixmap:
    """
    Paints the fox with round dots, for the start dialog.
    """
    points, width, height = dots()
    ratio = 2  # Drawn at twice the size, so that the dots are round also on a high-DPI screen
    pixmap = QPixmap(width * spacing * ratio, height * spacing * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    for x, y in points:
        painter.drawEllipse(QPointF((x + 0.5) * spacing, (y + 0.5) * spacing), radius, radius)
    painter.end()
    return pixmap

def fox_image(size: int) -> QImage:
    """
    Returns the icon at a size: a square of the window's background with the fox in the middle, one pixel per dot,
    each dot made a whole number of pixels wide so that it stays sharp. Below the fox's own size the full-size icon
    is scaled down smoothly.
    """
    points, width, height = dots()
    side = max(width, height)
    if size < side:
        return fox_image(side).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    scale = size // side
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(QColor(theme.BG))
    left, top = (size - width * scale) // 2, (size - height * scale) // 2
    painter = QPainter(image)
    for x, y in points:
        painter.fillRect(left + x * scale, top + y * scale, scale, scale, QColor(theme.ACCENT))
    painter.end()
    return image

def fox_icon() -> QIcon:
    """
    Returns the window's icon, with the sizes Windows asks for.
    """
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(fox_image(size)))
    return icon

def _png(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())

def write_ico(path: Path) -> None:
    """
    Writes the icon as a Windows .ico file with all ICON_SIZES, each stored as a PNG, which Windows reads since Vista.
    """
    images = [(size, _png(fox_image(size))) for size in ICON_SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))  # Reserved, type 1 (icon), count
    offset = len(header) + 16 * len(images)
    entries, data = b"", b""
    for size, png in images:
        dimension = 0 if size >= 256 else size  # 0 means 256 in the directory
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + data)

# The types of an .icns file's PNG images by their size in pixels (ic10 is 512 points at double resolution)
ICNS_TYPES = {128: b"ic07", 256: b"ic08", 512: b"ic09", 1024: b"ic10"}

def write_icns(path: Path) -> None:
    """
    Writes the icon as a macOS .icns file, for the application bundle: a PNG image per size of ICNS_TYPES.
    """
    data = b""
    for size, kind in ICNS_TYPES.items():
        png = _png(fox_image(size))
        data += kind + struct.pack(">I", 8 + len(png)) + png
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"icns" + struct.pack(">I", 8 + len(data)) + data)

if __name__ == "__main__":
    # python -m local_fox.ui.logo <path.ico or path.icns>: the build scripts write the application's icon with it
    target = Path(sys.argv[1])
    write_icns(target) if target.suffix == ".icns" else write_ico(target)
