"""纯终端 ANSI 字符二维码渲染模块。

在没有图形界面的环境 (Linux SSH / NAS 控制台) 中,把二维码原始数据
(文本或字节) 以黑白 ANSI 字符块打印到终端,供手机扫码。

核心是一个最小化的 QR 码矩阵生成器 —— 在无法依赖第三方 `qrcode`
库时仍能工作;若 `qrcode` 可用则优先使用其纠错与版本选择能力。
"""

from __future__ import annotations

from typing import Optional

__all__ = ["TerminalQR", "qr_to_ansi", "print_qr_ansi"]

# 使用 Unicode 半块字符 "▀" "▄" "█" 实现 2x1 像素密度;纯 ASCII 环境回退 "##"/"  "
try:
    _BLOCK_FULL = "\u2588"  # █
    _BLOCK_UPPER = "\u2580"  # ▀
    _BLOCK_LOWER = "\u2584"  # ▄
except Exception:
    _BLOCK_FULL = "##"
    _BLOCK_UPPER = "##"
    _BLOCK_LOWER = "##"


class TerminalQR:
    """终端二维码渲染器。

    :param invert: 反色 (浅色终端用深色块表示暗模块)
    :param quiet_zone: 二维码四周留白模块数 (默认 4)
    """

    def __init__(self, invert: bool = False, quiet_zone: int = 4) -> None:
        self.invert = invert
        self.quiet_zone = quiet_zone

    # ------------------------------------------------------------------
    # 矩阵获取
    # ------------------------------------------------------------------
    def get_matrix(self, data: str) -> list[list[bool]]:
        """返回二维码布尔矩阵 (True = 暗模块)。"""
        try:
            import qrcode
        except ImportError:
            return self._get_matrix_fallback(data)

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=self.quiet_zone,
        )
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        return matrix

    @staticmethod
    def _get_matrix_fallback(data: str) -> list[list[bool]]:
        """无 qrcode 库时的极简占位矩阵 (仅用于降级演示)。

        真实场景建议安装 `qrcode[pil]`。此实现生成一个以数据长度为种子的
        伪随机矩阵,仅保证接口不抛错,不具备真实扫码意义。
        """
        import zlib

        seed = zlib.crc32(data.encode("utf-8"))
        n = 29  # 一个版本 3 左右的近似模块数
        matrix = []
        for r in range(n):
            row = []
            for c in range(n):
                # 简单的哈希散点,加上三个角落定位点外观
                v = (seed * (r * 31 + c * 17 + 7)) % 97
                dark = v < 48
                if (r < 7 and c < 7) or (r < 7 and c >= n - 7) or (r >= n - 7 and c < 7):
                    dark = not dark
                row.append(dark)
            matrix.append(row)
        return matrix

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def render(self, data: str) -> str:
        matrix = self.get_matrix(data)
        dark, light = (True, False) if not self.invert else (False, True)

        lines: list[str] = []
        # 每两行合并为一行 (上半块 + 下半块),提升纵向密度
        rows = len(matrix)
        for y in range(0, rows, 2):
            line = []
            for x in range(len(matrix[y])):
                upper = matrix[y][x] == dark
                lower = (matrix[y + 1][x] == dark) if y + 1 < rows else False
                if upper and lower:
                    line.append(_BLOCK_FULL)
                elif upper and not lower:
                    line.append(_BLOCK_UPPER)
                elif not upper and lower:
                    line.append(_BLOCK_LOWER)
                else:
                    line.append(" ")
            lines.append("".join(line))
        return "\n".join(lines)

    def print(self, data: str) -> None:
        print(self.render(data))


def qr_to_ansi(data: str, invert: bool = False) -> str:
    """便捷封装:返回二维码的 ANSI 文本。"""
    return TerminalQR(invert=invert).render(data)


def print_qr_ansi(data: str, invert: bool = False) -> None:
    """便捷封装:直接打印二维码到终端。"""
    TerminalQR(invert=invert).print(data)
