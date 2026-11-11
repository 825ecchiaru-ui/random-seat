#测试和模拟脚本共用的工具：读取examples下的座位表样例、搭建教室
#样例是csv文件：用任何表格软件或文本编辑器都能打开和修改，在要放座位的格子里填seat即可
import csv
import sys
from pathlib import Path

#项目根目录（tests的上一级），加入导入路径后才能import seat_random包
PROJECT_DIR = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = PROJECT_DIR / "examples"
sys.path.insert(0, str(PROJECT_DIR))

#m是seat_random包：类通过m.Seat等访问，算法参数通过m.config.参数名访问
import seat_random as m

def load_positions(path):
    #把座位表样例（csv文件）转换为座位坐标列表[(x, y), ...]，内容为"seat"的格子是一个座位
    #csv的第几行第几列就是表格里的第几行第几列（从1开始数）
    #假设黑板在表格上方：表格行 -> x（前后方向，第2行为x=1），表格列 -> y（左右方向）
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return sorted((row - 1, col)
                  for row, cells in enumerate(rows, start=1)
                  for col, text in enumerate(cells, start=1) if text == "seat")


def example_layouts():
    #返回examples下所有样例布局：{文件名: 座位坐标列表}
    return {path.name: load_positions(path) for path in sorted(EXAMPLES_DIR.glob("*.csv"))}


def grid_positions(rows, cols):
    #生成rows排、cols列的矩形座位坐标
    return [(x, y) for x in range(1, rows + 1) for y in range(cols)]


def build(positions, n_students):
    #按座位坐标搭建教室并计算黑板距离、座位负担，返回(classroom, students, allocator)
    seats = [m.Seat(x, y) for x, y in positions]
    classroom = m.Classroom(seats)
    classroom.calculate_board_pos()
    for seat, burden in m.BurdenCalculator(classroom).calculate_seat_burden().items():
        seat.burden = burden

    students = [m.Student(i) for i in range(n_students)]
    return classroom, students, m.SeatAllocator(classroom, students)


def snapshot(classroom, students):
    #记录学生和座位的全部状态，用于验证某个方法没有修改状态
    return (
        [(s.current_seat, list(s.seat_history), s.accum_burden) for s in students],
        [seat.student for seat in classroom.seats],
    )
