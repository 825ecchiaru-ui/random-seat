#按数据库中的内容搭建一个班级：程序启动时、以及网页服务处理每个请求时都要做这件事
from .models import Student, Seat, Classroom
from .burden import BurdenCalculator
from .allocator import SeatAllocator


def load_class(history, class_name):
    #从历史记录中读出一个班级，搭建好教室并恢复到上次结束时的状态
    #history为HistoryManager实例，class_name为班级名称（班级不存在时报ValueError）
    #返回(classroom, students, allocator)：
    #  classroom  Classroom实例，已计算黑板位置、各座位的黑板距离和负担值
    #  students   在班学生的Student实例列表，顺序与history.get_students相同
    #  allocator  SeatAllocator实例，学生的当前座位、历史座位和历史负担已经恢复
    #本函数只读数据库，不写入
    seats = [Seat(x, y) for x, y in history.get_seats(class_name)]
    classroom = Classroom(seats)

    #还没有设置座位表的班级没有黑板位置和座位负担可算，跳过这两步
    if seats:
        classroom.calculate_board_pos()

        #座位负担不保存在数据库中，每次都由座位坐标重新计算
        for seat, burden in BurdenCalculator(classroom).calculate_seat_burden().items():
            seat.burden = burden

    students = [Student(student_id) for student_id in history.get_students(class_name)]
    allocator = SeatAllocator(classroom, students)

    #把学生的当前座位、历史座位和历史负担恢复到数据库中记录的状态
    allocator.restore_state(history.load_state(class_name))

    return classroom, students, allocator
