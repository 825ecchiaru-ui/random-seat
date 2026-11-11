#座位分配器：权重计算、方案生成、公平性打分、多方案选优、应用方案
import random

import numpy as np

from . import config


class SeatAllocator:
    """座位分配器，负责生成排座方案、为方案打分并应用方案。

    属性：
        classroom：Classroom实例。
        students：Student实例构成的列表。列表顺序即权重矩阵中行的顺序。

    方案的形式：
        字典{Seat: Student}，表示把某个学生安排到某个座位。没有出现在字典中的座位为空座。

    常用流程（每次排座）：
        assignment, score = allocator.select_best_assignment()   #生成候选方案并选出最公平的
        allocator.apply_assignment(assignment)                    #应用方案

    各方法的分工：
        calculate_weights()            计算权重矩阵，只读。
        generate_assignment(矩阵)      按权重矩阵生成一个加权随机方案，只读。
        random_assignment()            生成一个纯随机方案，只读。
        score_assignment(方案)         计算方案的公平性分数，只读。
        select_best_assignment()       生成多个候选方案并返回分数最高的，只读。
        apply_assignment(方案)         应用方案，修改学生和座位的状态。
        restore_state(状态)            按历史记录恢复学生和座位的状态（程序重启或回退之后使用）。

    学生和座位的状态只由apply_assignment和restore_state这两个方法修改：
    前者在现有状态上前进一轮，后者把状态整体替换为历史记录中的样子。

    算法参数（系数、候选数量）在config.py中，算法说明见docs/algorithm.md。
    """

    def __init__(self, classroom, students=None):
        self.classroom = classroom
        self.students = students if students is not None else []

    def calculate_weights(self):
        #计算本轮的完整权重矩阵，返回形状为(学生数N, 座位数M)的numpy数组
        #  第i行对应self.students[i]，第j列对应self.classroom.seats[j]
        #  矩阵元素[i][j]即PRD中的Weight(i,j)：座位j对学生i的随机权重，越大越容易被抽中
        #  Weight(i,j) = exp(a*B + b*D + c*H)，每行再整体缩放到最大值为1，取值(0,1]
        #权重只取决于本轮开始前的状态，同一轮内可以算一次后重复用于生成多个方案
        #本方法只读取数据，不修改任何学生或座位的状态
        students = self.students
        seats = self.classroom.seats

        if not seats:
            raise ValueError("Classroom has no seats")

        weight_matrix = np.zeros((len(students), len(seats)))
        if not students:
            return weight_matrix

        #把座位属性整理成长度为M的数组，下标与self.classroom.seats一致
        seat_board = np.array([seat.distance_to_board for seat in seats])
        seat_x = np.array([seat.x for seat in seats], dtype=float)
        seat_y = np.array([seat.y for seat in seats], dtype=float)
        seat_burden = np.array([seat.burden for seat in seats])

        #归一化用的教室常量
        #board_range：全教室黑板距离的最大值与最小值之差，作为B的分母
        board_range = seat_board.max() - seat_board.min()
        #max_move：任意两个座位间的最大距离，作为D的分母
        #  seat_x[:, None] - seat_x[None, :]得到M×M的两两横坐标差，纵坐标同理
        max_move = np.hypot(
            seat_x[:, None] - seat_x[None, :],
            seat_y[:, None] - seat_y[None, :]
        ).max()

        #H使用的两个参照值
        #mean_accum：全班历史负担的平均值
        mean_accum = sum(student.accum_burden for student in students) / len(students)
        #ref_burden：负担最低的N个座位的平均负担（N为学生数），代表“本轮人均应承担的负担”
        #  满员时就是全教室座位负担的平均值；有空座时以较好的N个座位为准，
        #  引导学生坐满较好的座位、把最差的座位留空
        ref_burden = np.sort(seat_burden)[:len(students)].mean()

        #逐个学生计算一行权重，每一行内对全部M个座位同时运算
        for i, student in enumerate(students):
            current = student.current_seat

            #B：当前座位与各座位的黑板距离差异，归一化到[0,1]
            #学生没有当前座位或分母为0时取0
            if current is None or board_range == 0:
                b_row = np.zeros(len(seats))
            else:
                b_row = np.abs(current.distance_to_board - seat_board) / board_range

            #D：从当前座位移动到各座位的距离，归一化到[0,1]
            #学生没有当前座位或分母为0时取0
            if current is None or max_move == 0:
                d_row = np.zeros(len(seats))
            else:
                d_row = np.hypot(seat_x - current.x, seat_y - current.y) / max_move

            #H：换座后的历史负担超出全班平均水平的部分，取负值作为扣分，取值(-∞,0]
            #h为换座后相对全班平均水平的负担偏差：h>0表示高于平均，h<0表示低于平均
            #只对高于平均的部分扣分，低于平均不扣分：
            #  历史负担高的学生只有坐到好座位才不被扣分，因此强烈偏向好座位；
            #  历史负担低的学生坐大多数座位都不被扣分，由B、D决定去向
            h = (student.accum_burden - mean_accum) + (seat_burden - ref_burden)
            h_row = -np.maximum(h, 0)

            #三项加权求和后取指数得到权重，系数越大对应目标的偏好越强
            exponent = (
                config.WEIGHT_BOARD * b_row
                + config.WEIGHT_MOVE * d_row
                + config.WEIGHT_HISTORY * h_row
            )
            #先减去该行的最大值再取指数：整行权重同乘一个倍数，不改变抽取概率，
            #同时保证权重落在(0,1]内，避免指数运算溢出
            weight_matrix[i] = np.exp(exponent - exponent.max())

        return weight_matrix

    def random_assignment(self):#生成纯随机座位方案，用于测试和作为选优时的纯随机候选方案
        assignment = {}
        if len(self.students) > len(self.classroom.seats):
            raise ValueError("Not enough seats for all students")#排除座位不足的情况

        #随机安排座位
        seats_list = random.sample(
            self.classroom.seats,
            len(self.classroom.seats)
        )

        for student, seat in zip(self.students, seats_list):
            assignment[seat] = student

        return assignment

    def generate_assignment(self, weight_matrix):
        #根据权重矩阵生成一个有优先级的加权随机方案，返回{seat: student}
        #weight_matrix由calculate_weights生成，行对应self.students，列对应self.classroom.seats
        #本方法只生成方案，不修改任何学生或座位的状态，应用方案须调用apply_assignment
        students = self.students
        seats = self.classroom.seats

        if len(students) > len(seats):
            raise ValueError("Not enough seats for all students")

        #验证矩阵的形状与当前学生、座位数量一致，且权重均为有限的非负数
        if weight_matrix.shape != (len(students), len(seats)):
            raise ValueError("Weight matrix shape does not match students and seats")

        if not np.all(np.isfinite(weight_matrix)) or np.any(weight_matrix < 0):
            raise ValueError("Weights must be finite and non-negative")

        #决定抽取顺序（保存的是学生在self.students中的下标）
        #  历史座位负担高的学生优先；相同者当前座位负担高的优先；都相同的随机
        #  先随机打乱，再做稳定排序，这样完全相同的学生之间保持打乱后的随机顺序
        order = random.sample(range(len(students)), len(students))
        order.sort(
            key=lambda i: (
                students[i].accum_burden,
                students[i].current_seat.burden if students[i].current_seat else 0.0
            ),
            reverse=True
        )

        #available[j]为True表示座位j还没有被抽走
        available = np.ones(len(seats), dtype=bool)
        assignment = {}

        for i in order:
            #取出学生i的一行权重，把已被抽走的座位的权重屏蔽为0
            row = np.where(available, weight_matrix[i], 0.0)

            if row.sum() > 0:
                #按权重随机抽取一个座位下标，权重为0的座位不会被抽中
                j = random.choices(range(len(seats)), weights=row.tolist(), k=1)[0]
            else:
                #剩余座位权重全为0时（例如剩余座位的权重都小到被计算机舍入为0），在剩余座位中等概率抽取
                j = random.choice(np.flatnonzero(available).tolist())

            assignment[seats[j]] = students[i]
            available[j] = False

        return assignment

    def score_assignment(self, assignment):
        #为一个方案进行公平性打分，分数越高越公平
        #Score = -(w1 * Var + w2 * Range)，Var、Range为应用该方案后全班历史座位负担的方差和极差
        #本方法只是模拟计算应用后的负担，不修改任何学生或座位的状态
        #本方法不验证方案是否合法，合法性由apply_assignment在应用时验证
        if not self.students:
            return 0.0

        #先取每个学生当前的历史负担，再加上方案中分到的座位的负担
        new_burdens = {student: student.accum_burden for student in self.students}
        for seat, student in assignment.items():
            new_burdens[student] += seat.burden

        values = np.array(list(new_burdens.values()))
        variance = values.var()                     #总体方差
        value_range = values.max() - values.min()   #极差

        #用0.0减而不是直接取负，避免方差和极差都为0时得到-0.0
        return float(0.0 - (config.SCORE_WEIGHT_VAR * variance + config.SCORE_WEIGHT_RANGE * value_range))

    def select_best_assignment(self):
        #生成多个候选方案并打分，返回元组(分数最高的方案, 该方案的分数)
        #候选方案由config.NUM_WEIGHTED_PLANS个加权随机方案和config.NUM_RANDOM_PLANS个纯随机方案组成
        #首次排座不做特殊处理：学生没有当前座位时权重中的B、D两项为0，流程照常进行
        #本方法只挑选方案，不修改任何状态，应用方案须调用apply_assignment
        candidates = []

        #同一轮内状态不变，权重矩阵只算一次，供所有加权方案复用
        weight_matrix = self.calculate_weights()
        for _ in range(config.NUM_WEIGHTED_PLANS):
            candidates.append(self.generate_assignment(weight_matrix))

        for _ in range(config.NUM_RANDOM_PLANS):
            candidates.append(self.random_assignment())

        if not candidates:
            raise ValueError("No candidate plans to choose from")

        #逐个打分，取分数最高的方案；分数相同时保留先生成的方案
        best_assignment = None
        best_score = None
        for assignment in candidates:
            score = self.score_assignment(assignment)
            if best_score is None or score > best_score:
                best_assignment = assignment
                best_score = score

        return best_assignment, best_score

    def apply_assignment(self, assignment):#应用方案
        # 验证座位属于当前教室
        if not set(assignment).issubset(set(self.classroom.seats)):
            raise ValueError("Assignment contains invalid seats")

        # 验证没有学生重复出现
        assigned_students = list(assignment.values())

        if len(set(assigned_students)) != len(assigned_students):
            raise ValueError("A student cannot occupy multiple seats")

        # 清理所有旧状态
        for seat in self.classroom.seats:
            seat.remove_stu()

        for student in self.students:
            student.current_seat = None

        # 应用新方案
        for seat, student in assignment.items():
            seat.assign_stu(student)

            #current_seat保存Seat实例本身，便于直接读取座位的黑板距离和负担值
            student.current_seat = seat
            student.seat_history.append((seat.x, seat.y))
            student.accum_burden += seat.burden

    def restore_state(self, state):#按历史记录恢复学生和座位的状态
        #用于程序重新启动后的恢复，以及回退最近一次排座之后的恢复
        #state为HistoryManager.load_state返回的字典：
        #  {student_id: {"seat_history": [(x, y), ...], "accum_burden": 历史负担, "current_seat": (x, y)或None}}
        #与apply_assignment的区别：apply_assignment是在现有状态上前进一轮（追加历史、累加负担），
        #本方法是把状态整体替换为记录中的样子，不做任何累加
        students = self.students
        #由坐标找到座位对象
        seat_at = {(seat.x, seat.y): seat for seat in self.classroom.seats}

        # 验证state中的学生与self.students一一对应
        student_ids = [student.student_id for student in students]

        if len(set(student_ids)) != len(student_ids) or set(student_ids) != set(state):
            raise ValueError("State does not match students")

        # 验证当前座位属于当前教室，且没有两个学生在同一个座位上
        current_positions = [
            tuple(info["current_seat"])
            for info in state.values()
            if info["current_seat"] is not None
        ]

        if not set(current_positions).issubset(set(seat_at)):
            raise ValueError("State contains invalid seats")

        if len(set(current_positions)) != len(current_positions):
            raise ValueError("A seat cannot hold multiple students")

        # 清理所有旧状态
        for seat in self.classroom.seats:
            seat.remove_stu()

        # 写入记录中的状态
        for student in students:
            info = state[student.student_id]

            student.seat_history = [tuple(position) for position in info["seat_history"]]
            student.accum_burden = float(info["accum_burden"])
            student.current_seat = None

            if info["current_seat"] is not None:
                seat = seat_at[tuple(info["current_seat"])]
                seat.assign_stu(student)
                student.current_seat = seat
