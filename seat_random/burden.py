#座位负担值的计算
import math


class BurdenCalculator:
    """负担计算器，根据座位到教室几何中心的距离计算每个座位的负担值。

    负担值的含义：
        越靠近全部座位的几何中心，负担值越小（最小为-1）；越远离中心越大（最大为1）。
        座位表固定后，负担值是座位的固定属性。

    属性：
        classroom：要计算的Classroom实例。
        center：全部座位的几何中心坐标(cx, cy)；计算之前为None。

    用法：
        burdens = BurdenCalculator(classroom).calculate_seat_burden()
        返回{Seat: 负担值}字典。本类不会把结果写入座位，需要由调用方写入seat.burden。
    """

    def __init__(self, classroom):
        self.classroom = classroom
        self.center = None

    def calculate_center(self):#计算座位的几何中心点
        seats_pos_list = []
        for s in self.classroom.seats:#建立包含所有作为坐标的列表
            pos = (s.x, s.y)
            seats_pos_list.append(pos)

        #求几何中心xy坐标
        n = len(seats_pos_list)
        cx = sum([p[0] for p in seats_pos_list]) / n
        cy = sum([p[1] for p in seats_pos_list]) / n
        self.center = (cx, cy)


    def calculate_seat_burden(self):#计算classroom全部座位的负担值，返回dict
        self.calculate_center()
        cx = self.center[0]
        cy = self.center[1]
        seats = [s for s in self.classroom.seats]#获取classroom中的座位实例

        #计算所有座位到中心点的距离，输出列表.键seat是seat实例
        distances = {
        seat: math.hypot(seat.x - cx, seat.y - cy)
        for seat in seats
        }

        #求距离最大最小值
        d_min = min(distances.values())
        d_max = max(distances.values())

        if math.isclose(d_max, d_min):
            return {seat: 0.0 for seat in seats}

        # Min-Max 线性归一化
        burdens = {
            seat: 2 * (distance - d_min) / (d_max - d_min) - 1
            for seat, distance in distances.items()
        }

        return burdens
