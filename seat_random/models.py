#数据模型：学生、座位、教室
import math


class Student:
    """学生对象，保存一名学生的身份和座位相关状态。

    属性：
        student_id：学生的标识（学号或姓名）。
        current_seat：学生当前所在的Seat实例；还没有被安排座位时为None。
        seat_history：学生历次坐过的座位坐标列表[(x, y), ...]，按时间先后排列。
        accum_burden：历史座位负担，即坐过的所有座位的负担值之和。

    注意：
        current_seat、seat_history、accum_burden只应由SeatAllocator的apply_assignment
        和restore_state修改，其他地方只读取，不要直接赋值。
        配合历史记录（HistoryManager）使用时，student_id应当用字符串。
    """

    def __init__(self,student_id):
        self.student_id = student_id
        self.current_seat = None
        self.seat_history = []
        self.accum_burden = 0.0


class Seat:
    """座位对象，保存一个座位的位置、属性和当前学生。

    属性：
        x：前后方向的坐标，黑板位于x=0，x越大离黑板越远。
        y：左右方向的坐标。
        student：当前坐在这个座位上的Student实例；空座位为None。
        distance_to_board：座位到黑板的距离，由Classroom.calculate_board_pos计算并写入。
        burden：座位负担值，取值[-1, 1]，越靠近教室几何中心越小。
            由BurdenCalculator.calculate_seat_burden计算，座位表固定后不再变化。

    注意：
        assign_stu和remove_stu只由SeatAllocator的apply_assignment和restore_state调用，
        不要在其他地方直接调用，否则学生一侧的状态不会同步更新。
    """

    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.student = None
        self.distance_to_board = 0.0
        self.burden = 0.0

    def assign_stu(self,stu):#为座位安排学生
        self.student = stu

        return f"Student {self.student.student_id} at {(self.x,self.y)}"

    def remove_stu(self):#移除学生
        if self.student is None:
            return None

        student = self.student
        self.student = None

        return f"Student {student.student_id} at {(self.x, self.y)} removed"


class Classroom:
    """教室对象，持有全部座位并确定黑板位置。

    属性：
        seats：Seat实例构成的列表。列表顺序即权重矩阵中列的顺序。
        blackboard_pos：黑板位置坐标(0, y)；调用calculate_board_pos之前为None。

    用法：
        创建后先调用calculate_board_pos()，再进行负担计算和座位分配。
    """

    def __init__(self, seats=None):
        self.seats = seats if seats is not None else []
        self.blackboard_pos = None

    def calculate_board_pos(self):#计算黑板位置、设置黑板到各个座位的距离
        if not self.seats:
            raise ValueError("Classroom has no seats")

        pos_y = (
            max(seat.y for seat in self.seats)
            + min(seat.y for seat in self.seats)
        ) / 2

        self.blackboard_pos = (0, pos_y)

        for seat in self.seats:
            seat.distance_to_board = math.dist(
                (seat.x, seat.y),
                self.blackboard_pos
            )

        return self.blackboard_pos
