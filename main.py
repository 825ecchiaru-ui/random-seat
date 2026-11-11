#程序入口：演示"读取历史 -> 排座 -> 保存"的完整流程
from pathlib import Path

from seat_random import SeatAssignment, HistoryManager, load_class

#数据库文件放在项目目录下的data文件夹中
DB_PATH = Path(__file__).resolve().parent / "data" / "seat_history.db"
CLASS_NAME = "class_test"


def print_plan(title, plan):
    #打印一个方案。plan为方案内容字典{student_id: {"burden": 历史负担, "current_seat": [x, y]}}
    print(title)
    for student_id, info in plan.items():
        x, y = info["current_seat"]
        print(f"  {student_id}  座位({x}, {y})  历史负担{info['burden']:+.3f}")


def main():
    #1. 启动：打开数据库（文件不存在时会新建），data文件夹不存在时先建立
    DB_PATH.parent.mkdir(exist_ok=True)

    #with块结束时自动关闭数据库；块内出错时也会关闭，错误本身照常抛出
    with HistoryManager(DB_PATH) as history:
        #2. 数据库中没有class_test时，新建一份演示用的座位表和学生名单
        if CLASS_NAME not in history.get_classes():
            print(f"数据库中没有{CLASS_NAME}，新建演示数据")
            history.add_class(CLASS_NAME)
            #3排3列共9个座位
            history.set_seats(CLASS_NAME, [(x, y) for x in (1, 2, 3) for y in (0, 1, 2)])
            for student_id in ["test1", "test2", "test3", "test4", "test5", "test6"]:
                history.add_student(CLASS_NAME, student_id)

        #3、4. 按数据库中的座位表和名单搭建教室，并导入历史：
        #把 学生 的当前座位、历史座位和历史负担恢复到上次结束时的状态
        classroom, students, allocator = load_class(history, CLASS_NAME)
        print(f"{CLASS_NAME}：{len(classroom.seats)}个座位，{len(students)}名学生")

        #5. 打印读取到的最新方案（limit=1：只取最近的一个，不必读出全部历史）
        past = history.query_assignments(class_name=CLASS_NAME, limit=1)
        if past:
            latest = past[0]
            print_plan(f"最新的历史方案（编号{latest['assignment_id']}，{latest['created_at']}，"
                       f"公平性分数{latest['score']:.3f}）", latest["assignments"])
        else:
            print("还没有历史方案")

        #6. 生成新方案：生成多个候选方案，选出公平性分数最高的
        assignment, score = allocator.select_best_assignment()

        #先把方案记录到对象（必须在应用之前），再应用
        record = SeatAssignment(None)
        record.set_seat_assignment(assignment, score)
        allocator.apply_assignment(assignment)

        #7. 打印新方案，然后保存到数据库
        print_plan(f"新方案（公平性分数{score:.3f}）", record.assignments)
        assignment_id = history.record_assignment(CLASS_NAME, record)
        print(f"新方案已保存，编号{assignment_id}")


if __name__ == "__main__":
    main()
