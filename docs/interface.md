# 接口文档

本文说明项目的文件结构、各个类和方法的调用方式。算法本身的原理见 [algorithm.md](algorithm.md)。

## 1. 文件结构

```
Random_Seat/
├── main.py                 命令行入口，演示"读取历史 -> 排座 -> 保存"的完整流程
├── manage.py               网页服务的启动入口（Django）
├── data/                   数据库文件 seat_history.db 和 Django 密钥文件（首次运行时自动建立）
├── seat_random/            后端逻辑
│   ├── __init__.py         统一导出各个类
│   ├── config.py           算法参数
│   ├── models.py           Student、Seat、Classroom
│   ├── burden.py           BurdenCalculator
│   ├── allocator.py        SeatAllocator
│   ├── history.py          HistoryManager、SeatAssignment（历史记录，SQLite）
│   └── loader.py           load_class（按数据库搭建班级并恢复状态）
├── web/                    网页服务：只把浏览器的请求转成对 seat_random 的调用
│   ├── settings.py         Django 设置
│   ├── urls.py             网址与处理函数的对应关系
│   ├── views.py            各个接口的处理函数
│   ├── templates/index.html    网页
│   └── static/web/         app.js（交互逻辑）、style.css（样式）
├── tests/                  测试和模拟脚本
├── docs/                   文档和图例
├── examples/               座位表样例（csv 文件）
└── backup/                 被替换下来的旧文件（只在本机保留，不纳入 git）
```

所有类都可以直接从包中导入：

```python
from seat_random import Student, Seat, Classroom, BurdenCalculator, SeatAllocator
from seat_random import config
```

## 2. 典型用法

```python
from seat_random import Student, Seat, Classroom, BurdenCalculator, SeatAllocator

#1. 建立学生和座位
students = [Student(name) for name in ["张三", "李四", "王五"]]
seats = [Seat(x, y) for x in (1, 2, 3) for y in (0, 1, 2)]

#2. 建立教室，计算黑板位置和各座位到黑板的距离
classroom = Classroom(seats)
classroom.calculate_board_pos()

#3. 计算座位负担值并写入每个座位
for seat, burden in BurdenCalculator(classroom).calculate_seat_burden().items():
    seat.burden = burden

#4. 每次排座：选出最公平的方案并应用
allocator = SeatAllocator(classroom, students)
assignment, score = allocator.select_best_assignment()
allocator.apply_assignment(assignment)

#5. 读取结果
for student in students:
    seat = student.current_seat
    print(student.student_id, (seat.x, seat.y))
```

第 1 到 3 步在教室建好后做一次。第 4 步每次排座重复执行，学生的历史负担会自动累加。

## 3. 约定

**方案的形式**
方案是字典 `{Seat: Student}`，表示把某个学生安排到某个座位。没有出现在字典里的座位是空座。

**只有两个方法修改状态**
学生和座位的排列数据只由 `SeatAllocator` 的两个方法修改：`apply_assignment` 在现有状态上前进一轮，`restore_state` 把状态整体替换为历史记录中的样子。其他方法（包括 `HistoryManager` 的全部方法）都不修改学生和座位对象。不要在别处直接给 `student.current_seat`、`student.accum_burden`、`seat.student` 赋值，也不要直接调用 `Seat.assign_stu` 和 `Seat.remove_stu`。

**学生编号用字符串**
数据库中 `student_id` 一律是字符串。配合历史记录使用时，`Student` 对象的 `student_id` 也要用字符串，否则恢复状态时对不上，会报错。

**权重矩阵的行列顺序**
权重矩阵是形状为 (学生数, 座位数) 的 numpy 数组。第 i 行对应 `allocator.students[i]`，第 j 列对应 `allocator.classroom.seats[j]`。计算矩阵之后、用它生成方案之前，不要改变这两个列表的顺序或长度。

**坐标**
`x` 是前后方向，黑板在 `x = 0`；`y` 是左右方向。

**错误**
所有输入错误都以 `ValueError` 抛出，错误信息为英文。

## 4. Student

学生对象。定义在 `seat_random/models.py`。

```python
Student(student_id)
```

| 属性 | 类型 | 说明 |
|---|---|---|
| `student_id` | 任意 | 学生的标识（学号或姓名） |
| `current_seat` | `Seat` 或 `None` | 当前所在的座位；还没有被安排时为 `None` |
| `seat_history` | 列表 | 历次坐过的座位坐标 `[(x, y), ...]`，按时间先后排列 |
| `accum_burden` | 浮点数 | 历史座位负担，即坐过的所有座位的负担值之和 |

后三个属性由 `apply_assignment` 维护，其他地方只读。

## 5. Seat

座位对象。定义在 `seat_random/models.py`。

```python
Seat(x, y)
```

| 属性 | 类型 | 说明 |
|---|---|---|
| `x`、`y` | 数值 | 座位坐标 |
| `student` | `Student` 或 `None` | 当前坐在这里的学生；空座为 `None` |
| `distance_to_board` | 浮点数 | 到黑板的距离，由 `Classroom.calculate_board_pos` 写入 |
| `burden` | 浮点数 | 座位负担值，取值 [−1, 1]，由调用方根据 `BurdenCalculator` 的结果写入 |

| 方法 | 说明 |
|---|---|
| `assign_stu(stu)` | 把学生安排到这个座位，返回一条说明文字。只由 `apply_assignment` 调用 |
| `remove_stu()` | 移除座位上的学生，返回一条说明文字；本来就是空座时返回 `None`。只由 `apply_assignment` 调用 |

## 6. Classroom

教室对象，持有全部座位。定义在 `seat_random/models.py`。

```python
Classroom(seats=None)
```

| 属性 | 类型 | 说明 |
|---|---|---|
| `seats` | 列表 | `Seat` 实例的列表；不传时为空列表 |
| `blackboard_pos` | 元组或 `None` | 黑板坐标 `(0, y)`；计算之前为 `None` |

### calculate_board_pos()

计算黑板位置，并把每个座位到黑板的距离写入 `seat.distance_to_board`。

- **返回**：黑板坐标 `(0, y)`，`y` 为全部座位 y 坐标的最大值与最小值的中点。
- **报错**：教室没有座位时报 `Classroom has no seats`。
- **调用时机**：座位确定之后、排座之前调用一次。座位增减或移动后需要重新调用。

## 7. BurdenCalculator

负担计算器。定义在 `seat_random/burden.py`。

```python
BurdenCalculator(classroom)
```

| 属性 | 类型 | 说明 |
|---|---|---|
| `classroom` | `Classroom` | 要计算的教室 |
| `center` | 元组或 `None` | 全部座位的几何中心 `(cx, cy)`；计算之前为 `None` |

### calculate_center()

计算全部座位的几何中心，结果写入 `self.center`。没有返回值。通常不需要单独调用，`calculate_seat_burden` 会先调用它。

### calculate_seat_burden()

计算全部座位的负担值。

- **返回**：字典 `{Seat: 负担值}`，负担值取值 [−1, 1]。所有座位到中心的距离相同时全部为 0。
- **注意**：这个方法不会把结果写入座位。需要由调用方写入 `seat.burden`，写法见第 2 节的第 3 步。
- **调用时机**：座位确定之后调用一次。座位增减或移动后需要重新计算并写入。

## 8. SeatAllocator

座位分配器，算法的核心。定义在 `seat_random/allocator.py`。

```python
SeatAllocator(classroom, students=None)
```

| 属性 | 类型 | 说明 |
|---|---|---|
| `classroom` | `Classroom` | 教室 |
| `students` | 列表 | `Student` 实例的列表；不传时为空列表 |

使用前需要先完成黑板距离和座位负担的计算，否则权重和分数没有意义。

日常排座只需要 `select_best_assignment` 和 `apply_assignment` 两个方法。其余方法是它们的组成步骤，在需要自定义流程时使用。

### select_best_assignment()

生成多个候选方案，返回最公平的一个。

- **返回**：元组 `(方案, 分数)`。方案是 `{Seat: Student}` 字典，分数是浮点数，越高越公平，最高为 0。
- **过程**：生成 `config.NUM_WEIGHTED_PLANS` 个加权随机方案和 `config.NUM_RANDOM_PLANS` 个纯随机方案，逐个打分，取分数最高的；分数相同时取先生成的。
- **报错**：
  - 学生多于座位时报 `Not enough seats for all students`。
  - 教室没有座位时报 `Classroom has no seats`。
  - 两个候选数量都设为 0 时报 `No candidate plans to choose from`。
- **不修改状态**。首次排座也直接调用它，不需要特殊处理。

### apply_assignment(assignment)

应用一个方案。这是唯一会修改学生和座位状态的方法。

- **参数**：`assignment` 是 `{Seat: Student}` 字典。
- **效果**：
  1. 清空所有座位上的学生和所有学生的当前座位。
  2. 按方案把学生放到座位上。
  3. 为方案中的每个学生更新 `current_seat`，在 `seat_history` 末尾追加新座位的坐标，把座位负担累加到 `accum_burden`。
- **报错**：
  - 方案中有不属于当前教室的座位时报 `Assignment contains invalid seats`。
  - 同一个学生出现在多个座位上时报 `A student cannot occupy multiple seats`。
- **注意**：
  - 检查在修改之前进行，报错时状态保持不变。
  - 没有出现在方案中的学生，`current_seat` 会变成 `None`，历史记录和历史负担不变。
  - 不检查方案中的学生是否属于 `self.students`。
- **没有返回值**。

### restore_state(state)

按历史记录恢复学生和座位的状态。用于程序重新启动之后，以及回退最近一次排座之后。

- **参数**：`state` 为 `HistoryManager.load_state` 返回的字典 `{student_id: {"seat_history": [(x, y), ...], "accum_burden": 数值, "current_seat": (x, y) 或 None}}`。
- **效果**：清空所有座位，然后把每个学生的 `seat_history`、`accum_burden`、`current_seat` 和对应座位的 `student` 设置成 `state` 中的样子。不做任何累加。
- **报错**：
  - `state` 中的学生与 `self.students` 不是一一对应（多、少、编号类型不同）时报 `State does not match students`。
  - 当前座位不属于当前教室时报 `State contains invalid seats`。
  - 两个学生的当前座位相同时报 `A seat cannot hold multiple students`。
- **注意**：检查在修改之前进行，报错时状态保持不变。
- **没有返回值**。

### calculate_weights()

计算本轮的权重矩阵。

- **返回**：形状为 (学生数, 座位数) 的 numpy 数组，元素取值 [0, 1]，每行最大值为 1。没有学生时返回 0 行的数组。
- **报错**：教室没有座位时报 `Classroom has no seats`。
- **不修改状态**。同一轮内状态不变，算一次即可重复用于生成多个方案。

### generate_assignment(weight_matrix)

按权重矩阵生成一个加权随机方案。

- **参数**：`weight_matrix` 是形状为 (学生数, 座位数) 的 numpy 数组，元素必须是有限的非负数。通常直接传入 `calculate_weights()` 的结果；也可以传入自己构造或修改过的矩阵，例如把某个学生不能坐的座位置为 0。
- **返回**：`{Seat: Student}` 字典，每个学生恰好出现一次。
- **抽取顺序**：历史负担高的学生先抽；相同时当前座位负担高的先抽；都相同时随机。
- **报错**：
  - 学生多于座位时报 `Not enough seats for all students`。
  - 矩阵形状与学生数、座位数不符时报 `Weight matrix shape does not match students and seats`。
  - 矩阵含负数、无穷或 NaN 时报 `Weights must be finite and non-negative`。
- **注意**：权重为 0 的座位不会被抽中，除非某个学生剩余可选座位的权重全部为 0，这时在剩余座位中等概率抽取。因此把权重置 0 不能绝对保证学生不被分到该座位。
- **不修改状态**。每次调用结果不同。

### random_assignment()

生成一个纯随机方案。

- **返回**：`{Seat: Student}` 字典。
- **报错**：学生多于座位时报 `Not enough seats for all students`。
- **不修改状态**。

### score_assignment(assignment)

计算一个方案的公平性分数。

- **参数**：`assignment` 是 `{Seat: Student}` 字典。
- **返回**：浮点数 `−(w1·方差 + w2·极差)`，方差和极差按应用该方案后全班的历史负担计算。分数越高越公平，最高为 0。没有学生时返回 0。
- **注意**：不检查方案是否合法；方案中的学生必须都属于 `self.students`，否则会报 `KeyError`。合法性由 `apply_assignment` 在应用时检查。
- **不修改状态**。

## 9. HistoryManager 与 SeatAssignment

定义在 `seat_random/history.py`。

### HistoryManager

历史记录管理器，用一个 SQLite 数据库文件保存多个班级的座位表、学生名单和历次排座方案。使用 Python 自带的 `sqlite3`，不需要安装任何包。

```python
history = HistoryManager("data/seats.db")
...
history.close()
```

打开文件时的处理：

| 情况 | 结果 |
|---|---|
| 文件不存在，或是空文件 | 新建数据库 |
| 旧版本的数据库 | 先自动备份，再升级到当前版本，数据保留 |
| 更新版本的程序建立的数据库 | 报 `Database was created by a newer version of this program`，文件不会被改动 |
| 所在文件夹不存在 | 报 `Directory does not exist` |
| 文件不是 SQLite 数据库，或已损坏到无法读取 | 报 `Not a valid SQLite database`，文件不会被改动 |
| 是 SQLite 数据库，但表结构与本程序不同 | 报 `Not a seat history database`，文件不会被改动 |

**数据库里存什么**

共 5 张表，建表语句和每一列的含义见 `history.py` 开头的 `SCHEMA`。

| 表 | 内容 |
|---|---|
| `classes` | 班级名称 |
| `seats` | 各班当前座位表的坐标 |
| `students` | 各班学生的 `student_id`、是否在班，以及负担起点（见下文"新学生和回来的学生"） |
| `assignments` | 每次方案的编号、班级、分数、时间 |
| `assignment_entries` | 每次方案中每个学生的座位坐标和方案应用后的历史负担 |

学生的当前座位、历史座位、历史负担都从方案记录中查出，不另存一份。座位负担值和黑板距离由坐标决定，也不保存，读出座位后重新计算。

数据库文件里记有表结构的版本号（当前为 2）。以后修改表结构时，在 `history.py` 中把 `SCHEMA_VERSION` 加 1 并补上升级步骤，旧文件就能被识别并自动升级。

**名称的检查与整理**

班级名称和学生编号在写入前都经过 `clean_name` 处理，添加和改名的方法返回的是整理后的名称。

- 整理：全角空格等各种空白统一成半角空格，连续空格合并成一个，去掉首尾空格。所以"张 三"和"张　三"是同一个名字。
- 不允许：空名称（`Name must not be empty`）；超过 50 个字（`Name is too long`）；换行、制表符等控制字符和零宽字符（`Name contains invisible or control characters`）；包含 `/`，或者就是 `.`、`..`（`Name must not contain '/' or be '.' or '..'`）；班级名称不是文字（`Name must be text`）。

**数量和范围的上限**（数值在 `config.py` 中）

| 限制 | 报错 |
|---|---|
| 座位坐标必须是整数，x 在 1 到 100 之间（x=0 是黑板），y 在 0 到 100 之间 | `A seat position must be two integers`、`Seat position out of range` |
| 每个班最多 500 个座位 | `Too many seats` |
| 每个班在班学生最多 500 人（已离开的不计） | `Too many students` |

**新学生和回来的学生**

中途加入的学生、离开后又回来的学生，历史负担从全班在班学生当时的平均值开始，而不是从 0 或离开前的值开始。否则他们会因为负担明显低于其他人，连续多轮被分到较差的座位。

- 这个起点保存在 `students` 表的 `base_burden` 列，`base_after` 列记着设置起点时本班最新方案的编号。
- 学生的历史负担 = 起点之后最近一条方案记录中的 `burden`；起点之后还没有记录时就是起点值。
- 回来的学生，离开前的座位历史仍然保留，只是负担改从平均值算起。
- 班里还没有其他在班学生时，起点为 0。

**事务**

`with history.transaction():` 把代码块中的多步读写合成一个整体：要么全部生效，要么（中途出错时）全部不生效；从进入代码块开始，其他程序或请求不能写入数据库，所以"先检查、再写入"之间不会被插入别的改动。可以嵌套。本类所有修改数据的方法内部都已使用事务，只有需要把多个方法合成一步时才要在外面再套一层。

**备份**

`history.backup()` 把数据库复制到同目录的 `backups` 文件夹，文件名带时间，只保留最近 10 份，返回备份文件的路径。删除班级之前、升级旧版本数据库之前会自动调用。恢复方法：关闭程序，用备份文件替换 `seat_history.db`。

**故障**

数据库被别的程序占用超过 5 秒、文件无法写入等情况，报 `sqlite3.OperationalError`（不是 `ValueError`）。

**方法一览**

班级用名称指定，学生用 `student_id` 指定。班级不存在时所有方法都报 `Unknown class`，学生不存在时报 `Unknown student`。

| 方法 | 作用 | 返回 |
|---|---|---|
| `add_class(class_name)` | 添加班级。重名时报 `Class already exists` | 整理后的名称 |
| `get_classes()` | 列出全部班级 | 名称列表 |
| `rename_class(class_name, new_name)` | 班级改名，座位表、名单、历史都保留。新名称已存在时报 `Class already exists` | 整理后的新名称 |
| `delete_class(class_name)` | 删除班级，连同座位表、名单和全部历史方案。删除前自动备份整个数据库 | 无 |
| `set_seats(class_name, positions)` | 设置座位表 `[(x, y), ...]`，整体替换原有的。坐标重复时报 `Duplicate seat positions` | 无 |
| `get_seats(class_name)` | 读取座位表，顺序与设置时相同 | `[(x, y), ...]` |
| `add_student(class_name, student_id)` | 添加学生，负担起点为全班当前平均值。已存在时报 `Student already exists` | 整理后的编号 |
| `set_student_active(class_name, student_id, active)` | 设置学生是否在班。离开的学生不参与排座，历史保留；回来时负担起点改为全班当前平均值 | 无 |
| `get_students(class_name, active_only=True)` | 读取学生名单，按添加先后排列 | `student_id` 列表 |
| `get_roster(class_name)` | 读取完整名单，包含已离开的学生并带在班状态 | `[(student_id, 是否在班), ...]` |
| `rename_student(class_name, student_id, new_student_id)` | 修改学生编号，历史保留。新编号已存在时报 `Student already exists` | 整理后的新编号 |
| `delete_student(class_name, student_id)` | 删除从未入座的学生。已有历史的学生报 `Student has history, set inactive instead` | 无 |
| `record_assignment(class_name, record)` | 保存一次已应用的方案 | 方案编号 |
| `query_assignments(...)` | 按条件查询方案 | 字典列表 |
| `latest_assignment_id(class_name)` | 班级最新一次方案的编号。每保存或回退一次都会变，可用来判断数据是否被别的操作改动过 | 编号，没有方案时为 0 |
| `get_student_history(class_name, student_id)` | 某个学生的历次座位 | 字典列表 |
| `load_state(class_name)` | 读出全班在班学生的当前状态 | 字典 |
| `rollback_last(class_name)` | 删除最近一次方案 | 被删除的方案编号 |
| `check_integrity()` | 检查数据完整性 | 问题描述列表，没有问题时为空 |
| `backup()` | 备份数据库文件 | 备份文件的路径 |
| `transaction()` | 把多步操作合成一个整体，配合 `with` 使用 | — |
| `close()` | 关闭数据库 | 无 |

也可以用 `with` 写法，离开代码块时自动关闭数据库（块内出错时同样关闭，错误照常抛出）：

```python
with HistoryManager("data/seats.db") as history:
    print(history.get_classes())
```

数据库连接不能跨线程使用。网页服务里每个请求都用上面的写法各自打开、用完关闭。

**record_assignment(class_name, record)**

- `record` 是已经调用过 `set_seat_assignment` 的 `SeatAssignment` 对象。
- 方案编号由数据库分配，只增不减，保存后写入 `record.assignment_id` 并返回。
- 保存前检查全部内容，有问题时报错且数据库不变：
  - 记录还没有设置方案：`Record has no assignment set`
  - 学生不在该班名单中，或已离开：`Student not in class or inactive`
  - 座位不在该班座位表中：`Seat not in class layout`
  - 分数或负担值不是有限的数：`Record score must be a finite number`、`Burden must be a finite number`
  - 学生编号转成字符串后重复：`Duplicate student_id in record`
  - 两个学生在同一个座位：`A seat cannot hold multiple students`

**query_assignments(class_name=None, student_id=None, start=None, end=None, assignment_id=None, limit=None)**

- 各个条件都可以省略；给出多个时取同时满足的方案；全部省略时返回所有方案。
- `limit`：只要满足条件的方案中最近的 `limit` 个，返回时仍按时间先后排列。`limit=1` 就是最新的一个方案。不是非负整数时报 `limit must be a non-negative integer`。
- `student_id`：只要包含这个学生的方案，返回的仍是完整的方案内容。不同班级里编号相同的学生都会匹配，要区分时同时给出 `class_name`。
- `start`、`end`：`created_at` 的范围，含两端，按字符串比较。`end="2026-11-11"` 不包含当天，要包含应写 `"2026-11-11T23:59:59"`。
- 返回按方案编号排列的列表，每一项是：

```python
{"assignment_id": 12, "class_name": "高一3班", "score": -1.37, "created_at": "2026-11-11T20:15:03",
 "assignments": {student_id: {"burden": 0.37, "current_seat": [2, 5]}, ...}}
```

**get_student_history(class_name, student_id)**

返回按时间先后排列的列表，每一项是 `{"assignment_id": 12, "created_at": "...", "seat": (x, y), "burden": 0.37}`。已离开的学生也可以查询。

**load_state(class_name)**

返回 `{student_id: {"seat_history": [(x, y), ...], "accum_burden": 数值, "current_seat": (x, y) 或 None}}`，顺序与 `get_students` 相同，直接交给 `SeatAllocator.restore_state`。

- `accum_burden` 取该学生在负担起点之后最近一条记录中的 `burden`；起点之后还没有记录（刚加入、刚回来）则为起点值。
- 读到损坏的数据（负担值不是有限的数、坐标不是整数）时报 `Corrupted record: class …, student …, assignment …`，指出是哪条记录，不让坏数据进入排座计算。`get_seats` 同理，报 `Corrupted seat position in class …`。
- `current_seat` 是该学生在本班最新一次方案中的座位。学生不在最新方案中，或更换座位表后原座位已不存在，则为 `None`。

**rollback_last(class_name)**

删除该班最近一次方案的记录，**删除后不可找回**。只改数据库，之后要调用 `restore_state(load_state(...))` 把程序中的状态同步回上一次。没有方案可回退时报 `No assignment to roll back`。

**check_integrity()**

返回英文的问题描述列表，没有问题时为空列表。检查以下各项：

- 数据库文件本身是否损坏。
- 是否有记录引用了不存在的班级、学生或方案。
- 是否有方案里出现了别的班级的学生。
- 座位坐标（座位表和方案中的）是否都是整数。
- 负担值、分数、负担起点是否都是有限的数。
- 在班标记是否为 0 或 1。
- 是否有没有任何内容的方案。
- 方案的时间是否为可识别的格式。

**完整流程**

```python
from seat_random import SeatAssignment, HistoryManager, load_class

history = HistoryManager("data/seats.db")

#第一次使用：登记班级、座位表和学生（只做一次）
history.add_class("高一3班")
history.set_seats("高一3班", [(x, y) for x in (1, 2, 3) for y in (0, 1, 2)])
for name in ["张三", "李四", "王五"]:
    history.add_student("高一3班", name)

#每次启动：从数据库搭建教室并恢复状态
classroom, students, allocator = load_class(history, "高一3班")

#每次排座
best, score = allocator.select_best_assignment()
record = SeatAssignment(None)
record.set_seat_assignment(best, score)          #先记录到对象
allocator.apply_assignment(best)                 #再应用
history.record_assignment("高一3班", record)      #最后存入数据库

#回退最近一次排座
history.rollback_last("高一3班")
allocator.restore_state(history.load_state("高一3班"))

history.close()
```

如果 `record_assignment` 报错，方案已经应用到内存但没有存入数据库，此时调用 `allocator.restore_state(history.load_state(...))` 可以让内存回到数据库中的状态。

### load_class(history, class_name)

定义在 `seat_random/loader.py`。按数据库中的内容搭建一个班级并恢复到上次结束时的状态，一步完成以下工作：建立座位和教室、计算黑板位置和座位负担、建立在班学生、调用 `restore_state`。

- **返回**：`(classroom, students, allocator)`。
- **只读数据库**，不写入。
- 还没有设置座位表的班级也可以载入，此时教室没有座位。
- 班级不存在时报 `Unknown class`。

### SeatAssignment

一次排座方案的记录对象。它把方案字典 `{Seat: Student}` 转换成只含编号、数值和坐标的普通字典，可以直接写入 JSON 文件。`SeatAllocator` 各方法之间传递的方案仍是普通字典，不使用这个类。

```python
SeatAssignment(assignment_id)
```

| 属性 | 类型 | 说明 |
|---|---|---|
| `assignment_id` | 任意 | 方案的编号，创建时传入。要存入数据库时传 `None`，保存后由 `record_assignment` 写入数据库分配的编号 |
| `assignments` | 字典 | 方案内容，结构见下。记录之前为 `{}` |
| `score` | 浮点数 | 方案的公平性分数。记录之前为 `0.0` |
| `created_at` | 字符串 | 记录的时间，精确到秒的 ISO 格式，如 `"2026-11-11T20:15:03"`。记录之前为 `None` |

`assignments` 的结构（样例见根目录的 `example_assignment_dict.json`）：

```python
{
    student_id: {
        "burden": 0.37,          # 方案应用后该学生的历史负担
        "current_seat": [2, 5],  # 该学生在这个方案中的座位坐标 [x, y]
    },
    ...
}
```

只有入座的学生会出现在其中。空座位没有对应的学生编号，不会被记录；需要时可以用教室的全部座位减去记录中出现的座位得到。

### set_seat_assignment(assignment, score)

记录一次排座方案的内容、分数和时间。

- **参数**：`assignment` 为方案字典 `{Seat: Student}`；`score` 为它的公平性分数。两者就是 `select_best_assignment` 的返回值。
- **效果**：写入 `assignments`、`score`、`created_at` 三个属性。重复调用时整体覆盖上一次的记录。
- **调用时机**：**必须在 `apply_assignment` 之前调用。** `burden` 按"学生目前的历史负担 + 新座位的负担值"计算，方案应用之后再记录，新座位的负担会被重复计入。这种误用无法被可靠地检查出来，方法不会报错。
- **报错**：方案中有两个学生的 `student_id` 相同时报 `Duplicate student_id in assignment`，此时原有记录保持不变。
- **不修改**学生和座位的状态。
- **没有返回值**。
- **注意**：`student_id` 是整数时，把 `assignments` 写入 JSON 后键会变成字符串。

```python
best, score = allocator.select_best_assignment()
record = SeatAssignment(assignment_id=1)
record.set_seat_assignment(best, score)   # 先记录
allocator.apply_assignment(best)          # 再应用
```

## 10. 参数（config.py）

| 参数 | 当前值 | 含义 |
|---|---|---|
| `WEIGHT_BOARD` | 4.0 | 权重公式中黑板距离差异的系数 a |
| `WEIGHT_MOVE` | 4.0 | 权重公式中移动距离的系数 b |
| `WEIGHT_HISTORY` | 8.0 | 权重公式中历史负担的系数 c |
| `SCORE_WEIGHT_VAR` | 1.0 | 公平性打分中方差的权重 w1 |
| `SCORE_WEIGHT_RANGE` | 1.0 | 公平性打分中极差的权重 w2 |
| `NUM_WEIGHTED_PLANS` | 50 | 每轮生成的加权随机候选数量 |
| `NUM_RANDOM_PLANS` | 50 | 每轮生成的纯随机候选数量 |
| `MAX_COORDINATE` | 100 | 座位坐标的最大值（x 取 1 到 100，y 取 0 到 100） |
| `MAX_SEATS` | 500 | 每个班级的座位数上限 |
| `MAX_STUDENTS` | 500 | 每个班级在班学生数的上限 |
| `MAX_NAME_LENGTH` | 50 | 班级名称和学生编号的最大字数 |

后四项是数据规模的上限，由 `HistoryManager` 在写入时检查。网页脚本 `app.js` 开头有同名的 `MAX_COORDINATE` 和 `MAX_SEATS`，修改时要一起改。

参数在每次调用时读取，所以可以直接改文件，也可以在程序运行中修改：

```python
from seat_random import config
config.NUM_WEIGHTED_PLANS = 100
```

各参数对结果的影响见 [algorithm.md](algorithm.md) 第 9 节。

## 11. 网页服务（web/）

### 启动

在项目根目录下，用 `randomseatenv` 环境运行：

```
python manage.py runserver
```

然后用浏览器打开 `http://127.0.0.1:8000/`。按 Ctrl+C 停止服务。

- 网页服务与 `main.py` 使用同一个数据库文件 `data/seat_history.db`。设置环境变量 `SEAT_DB_PATH` 可以换成别的文件。
- 使用 Django 5.2，只用它处理网页请求，不使用 Django 自带的数据库、用户和后台功能。
- 目前的设置只适合在本机使用（调试模式，只接受本机访问）。部署到服务器前必须修改 `web/settings.py` 中的 `DEBUG` 和 `ALLOWED_HOSTS`。

### 工作方式

- **服务不保留状态**：每个请求都打开数据库，用 `load_class` 重建班级，处理完关闭。
- **排座分两步**：先预览（只计算，不保存），再确认（应用并保存）。确认时由浏览器把方案发回，所以预览后可以手动对调学生；分数由服务器重新计算。
- **修改数据的请求必须带防伪令牌**：打开网页时服务器把令牌放进 cookie，`app.js` 发请求时放在 `X-CSRFToken` 请求头里。
- **拦截过期的操作**：预览返回的 `base` 是当时班级最新方案的编号。打分和确认时浏览器把它原样发回，撤销时发回要撤销的方案编号；服务器发现它已经不是最新编号就拒绝（`Class data has changed`）。这拦住了连点两次确认、在另一个网页里已经排过座或撤销过等情况。界面收到后会放弃当前预览并刷新为最新数据。
- **确认和撤销在一个事务里完成**：从检查数据是否变化到保存完成，中间不会被别的请求插进来。
- **操作进行中按钮不可点击**：请求返回之前页面不接受新的操作。
- **出错时一律返回 JSON** `{"error": "英文错误信息"}`，`app.js` 中的 `ERROR_TEXT` 把它转成中文提示：

  | 状态码 | 情况 |
  |---|---|
  | 400 | 输入有误，或数据已经变化 |
  | 413 | 请求内容过大 |
  | 503 | 数据库被占用或无法读写（同时记入日志） |
  | 500 | 没有预料到的错误（完整的出错位置记入日志） |

  日志目前输出在运行服务的窗口里。
- **名称、坐标和数量的限制**由后端统一检查，见第 9 节。
- **名单文件的编码**：界面先按 UTF-8 读取，读不通时按 GBK 读取（Windows 记事本常用的编码）。

### 接口一览

网址中的 `{班级}`、`{学生}` 是经过编码的班级名称和学生编号。请求和返回的内容都是 JSON。

| 请求 | 作用 | 请求内容 | 返回 |
|---|---|---|---|
| `GET /api/classes` | 列出班级 | 无 | `{"classes": [名称, ...]}` |
| `POST /api/classes` | 新建班级 | `{"name": 名称}` | `{"classes": [...], "name": 整理后的名称}` |
| `GET /api/classes/{班级}` | 班级的全部当前信息 | 无 | `{"class": 班级信息}` |
| `PUT /api/classes/{班级}` | 班级改名 | `{"name": 新名称}` | `{"classes": [...], "class": 班级信息}` |
| `DELETE /api/classes/{班级}` | 删除班级及全部历史 | 无 | `{"classes": [...]}` |
| `PUT /api/classes/{班级}/seats` | 设置座位表 | `{"positions": [[x, y], ...]}` | `{"class": 班级信息}` |
| `POST /api/classes/{班级}/students` | 添加学生，可多个。有一个不合格时都不添加 | `{"student_ids": [编号, ...]}` | `{"class": 班级信息}` |
| `PUT /api/classes/{班级}/students/{学生}` | 改编号、标记离开或回来 | `{"student_id": 新编号}` 和/或 `{"active": true 或 false}` | `{"class": 班级信息}` |
| `DELETE /api/classes/{班级}/students/{学生}` | 删除从未入座的学生 | 无 | `{"class": 班级信息}` |
| `GET /api/classes/{班级}/students/{学生}/history` | 某学生的历次座位 | 无 | `{"history": [...]}` |
| `POST /api/classes/{班级}/preview` | 生成新方案供预览 | 无 | 方案信息 |
| `POST /api/classes/{班级}/score` | 给调整后的方案打分 | `{"plan": 方案, "base": 编号}` | 方案信息 |
| `POST /api/classes/{班级}/assignments` | 确认方案：应用并保存 | `{"plan": 方案, "base": 编号}` | `{"assignment_id": 编号, "class": 班级信息}` |
| `GET /api/classes/{班级}/assignments?limit=20` | 最近的历史方案，新的在前。`limit` 取 1 到 200 | 无 | `{"assignments": [...], "has_more": 是否还有更早的}` |
| `DELETE /api/classes/{班级}/assignments/latest` | 撤销上一次排座 | `{"assignment_id": 要撤销的编号}` | `{"assignment_id": 编号, "class": 班级信息}` |
| `GET /api/integrity` | 检查数据完整性 | 无 | `{"problems": [...]}` |

三种常用的内容：

```python
#方案（浏览器发给服务器）：必须恰好包含全部在班学生
{"张三": [1, 2], "李四": [3, 1]}

#方案信息（预览和打分的返回）
{"score": -1.37,
 "base": 12,                                    #生成时班级最新方案的编号（没有方案时为0），打分和确认时原样发回
 "assignments": {"张三": {"burden": 0.37, "current_seat": [1, 2]}, ...}}     #burden是应用后的历史负担

#班级信息
{"name": "高一3班",
 "blackboard": [0, 2.5],                        #没有座位时为null
 "seats": [{"x": 1, "y": 1, "burden": 0.84, "student": "张三"}, ...],        #空座位的student为null
 "students": [{"student_id": "张三", "active": true, "accum_burden": 0.37,
               "rounds": 3, "current_seat": [1, 2]}, ...],                   #rounds是排座次数
 "latest": {"assignment_id": 12, "created_at": "2026-11-11T20:15:03", "score": -1.37}}   #没有方案时为null
```

各接口调用了哪些后端方法，见 [figures/frontend_calls.svg](figures/frontend_calls.svg)。

### 网页的四个页面

| 页面 | 功能 |
|---|---|
| 排座 | 显示当前座位；生成新方案预览，点击两个座位对调学生或把学生移到空座位，确认保存；撤销上一次排座 |
| 座位表 | 在网格上点选座位，可增减行列；去掉有人坐的座位前会提醒 |
| 名单 | 粘贴或从文本文件读入名单批量添加；改名、标记离开或回来、删除从未入座的学生、查看学生的座位历史 |
| 历史 | 浏览历次方案的座位图 |

顶栏可以切换、新建、改名、删除班级。删除班级需要输入班级名称确认。

## 12. 测试和模拟脚本

所有脚本都用项目的 conda 环境 `randomseatenv` 运行，在项目根目录下执行：

| 命令 | 作用 | 耗时 |
|---|---|---|
| `python tests/test_weights.py` | 权重计算和方案生成的正确性测试 | 数秒 |
| `python tests/test_select.py` | 打分和选优的正确性测试 | 数秒 |
| `python tests/test_history.py` | 方案记录（`SeatAssignment`）的正确性测试 | 数秒 |
| `python tests/test_history_manager.py` | 历史记录（`HistoryManager`）与状态恢复的正确性测试 | 数秒 |
| `python tests/test_web.py` | 网页服务各接口的正确性测试（不需要启动服务） | 数秒 |
| `python tests/test_browser.py [截图文件夹]` | 界面操作测试：自动启动服务和不显示窗口的 Edge，像用户一样点击页面。需要本机装有 Edge | 约半分钟 |
| `python tests/sim_weight_design.py 阶段名` | 比较不同权重公式和系数。阶段名可取 `baseline`、`coarse`、`fine`、`hform`、`final` | 每个阶段数分钟 |
| `python tests/sim_random_count.py` | 比较不同的纯随机候选数量 | 数分钟 |
| `python docs/make_figures.py` | 重新生成算法说明中的图例 | 一到两分钟 |

`tests/layout_tools.py` 是这些脚本共用的工具，负责读取 `examples` 下的座位表和搭建教室，不单独运行。座位表样例是 csv 文件，可以用表格软件或文本编辑器打开，内容为 `seat` 的格子代表一个座位。

六个测试脚本全部通过时最后一行输出"全部通过"；任何一项不符会直接报错退出。修改 `seat_random` 下的代码后应当重新运行前五个；修改 `web` 下的代码后应当重新运行最后两个。测试用的数据库都建在系统临时目录中，不会动 `data` 文件夹里的数据。
