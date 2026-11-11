#生成程序图标：蓝色圆角方块上3排3列白色小方块（座位），其中一个是黄色（被选中的座位）
#用法：python desktop/make_icon.py
#输出：desktop/assets/app.ico（程序和安装包的图标）、web/static/web/favicon.ico（窗口左上角和任务栏的图标）
#环境里没有图像处理的工具包，这里用标准库直接写出图标文件：
#逐个像素计算颜色，小尺寸存成位图、256像素的存成PNG，再按ICO文件的格式拼在一起
import struct
import zlib
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = DESKTOP_DIR.parent

BLUE = (37, 99, 235)
WHITE = (255, 255, 255)
YELLOW = (251, 191, 36)


def in_rounded_square(x, y, left, top, size, radius):
    #点(x, y)是否在一个圆角正方形内。四个角各是半径为radius的圆弧
    if not (left <= x <= left + size and top <= y <= top + size):
        return False
    #离最近的"圆角圆心"的距离：只有落在角上的小方块里时才需要判断
    cx = min(max(x, left + radius), left + size - radius)
    cy = min(max(y, top + radius), top + size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def color_at(x, y):
    #图标上一点的颜色，坐标范围是0到1；返回(红, 绿, 蓝)，在图标之外返回None（透明）
    if not in_rounded_square(x, y, 0.02, 0.02, 0.96, 0.2):
        return None
    #9个座位：每个边长0.2，间隔0.07，整体居中
    for row in range(3):
        for col in range(3):
            if in_rounded_square(x, y, 0.13 + col * 0.27, 0.13 + row * 0.27, 0.2, 0.045):
                return YELLOW if (row, col) == (1, 1) else WHITE
    return BLUE


def render(size):
    #画出size×size像素的图标，返回每个像素的(红, 绿, 蓝, 不透明度)列表，按从上到下、从左到右排列
    #每个像素内取4×4个采样点求平均，边缘才会平滑
    samples = 4
    pixels = []
    for py in range(size):
        for px in range(size):
            total = [0, 0, 0]
            covered = 0
            for sy in range(samples):
                for sx in range(samples):
                    color = color_at((px + (sx + 0.5) / samples) / size, (py + (sy + 0.5) / samples) / size)
                    if color is not None:
                        covered += 1
                        for i in range(3):
                            total[i] += color[i]
            if covered:
                pixels.append((total[0] // covered, total[1] // covered, total[2] // covered,
                               255 * covered // samples ** 2))
            else:
                pixels.append((0, 0, 0, 0))
    return pixels


def png_bytes(size, pixels):
    #把像素写成PNG格式（每行前面加一个0表示这一行不做预处理，然后整体压缩）
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + bytes(v for pixel in pixels[y * size:(y + 1) * size] for v in pixel)
                    for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def bitmap_bytes(size, pixels):
    #把像素写成ICO文件里使用的位图格式：行的顺序是从下到上，颜色顺序是蓝、绿、红、不透明度；
    #后面还要跟一张"透明掩码"（这里全部填0，透明度已经由每个像素的不透明度表示）
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    body = b"".join(bytes((b, g, r, a)) for y in range(size - 1, -1, -1)
                    for r, g, b, a in pixels[y * size:(y + 1) * size])
    mask_row = ((size + 31) // 32) * 4      #掩码每行的字节数要凑成4的倍数
    return header + body + bytes(mask_row * size)


def build_icon(sizes):
    #把多个尺寸的图像拼成一个ICO文件。Windows会按显示的位置挑选合适的尺寸
    images = []
    for size in sizes:
        pixels = render(size)
        images.append(png_bytes(size, pixels) if size >= 256 else bitmap_bytes(size, pixels))

    data = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    for size, image in zip(sizes, images):
        #宽、高用一个字节表示，256写成0
        data += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    return data + b"".join(images)


if __name__ == "__main__":
    (DESKTOP_DIR / "assets").mkdir(exist_ok=True)
    (DESKTOP_DIR / "assets" / "app.ico").write_bytes(build_icon([16, 24, 32, 48, 64, 256]))
    (PROJECT_DIR / "web" / "static" / "web" / "favicon.ico").write_bytes(build_icon([16, 32, 48, 64]))
    #另存一张PNG，用在使用说明里
    (DESKTOP_DIR / "assets" / "app.png").write_bytes(png_bytes(128, render(128)))
    print("图标已生成")
