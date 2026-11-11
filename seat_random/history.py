#历史记录相关的类：
#  HistoryManager  用SQLite数据库文件保存和读取多个班级的座位表、学生名单和历次排座方案
#  SeatAssignment  一次排座方案的记录对象
import math
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import config

#数据库表结构的版本号，保存在数据库文件中。以后修改表结构时把它加1，
#并在HistoryManager.__init__中补上从旧版本升级的步骤，旧文件就能被识别并自动升级
#  版本1：最初的5张表（文件中的版本号为0，因为当时还没有记录版本号）
#  版本2：students表增加base_burden、base_after两列；增加按学生查找方案内容的索引
SCHEMA_VERSION = 2

#数据库的表结构。数据库只保存算不出来的数据：班级、座位坐标、学生名单、每次方案的内容。
#学生的当前座位、历史座位和历史负担都从方案记录中查出，不另存一份；
#座位负担和黑板距离由座位坐标决定，也不保存，读出座位后重新计算。
SCHEMA = """
-- 班级
CREATE TABLE classes (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE              -- 班级名称
);

-- 班级当前的座位表，对应Classroom.seats
CREATE TABLE seats (
    class_id  INTEGER NOT NULL REFERENCES classes(id),
    x         INTEGER NOT NULL,             -- 前后方向坐标，黑板在x=0
    y         INTEGER NOT NULL,             -- 左右方向坐标
    PRIMARY KEY (class_id, x, y)
);

-- 学生名单，对应Student
CREATE TABLE students (
    id          INTEGER PRIMARY KEY,        -- 数据库内部编号，只用于表之间关联
    class_id    INTEGER NOT NULL REFERENCES classes(id),
    student_id  TEXT    NOT NULL,           -- Student.student_id（学号或姓名）
    active      INTEGER NOT NULL DEFAULT 1, -- 1=在班，参与排座；0=已离开，只保留历史
    -- 负担起点：学生加入班级、或离开后回来时，全班在班学生历史负担的平均值。
    -- 在这之后学生还没有新的排座记录时，他的历史负担就是这个值（见load_state）
    base_burden REAL    NOT NULL DEFAULT 0,
    -- 设置负担起点时，本班最新一次方案的编号（当时还没有方案则为0）。
    -- 编号大于它的方案记录才是"起点之后的记录"
    base_after  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (class_id, student_id)
);

-- 每次已应用的方案，对应SeatAssignment的assignment_id、score、created_at
-- AUTOINCREMENT保证编号只增不减：回退删除的方案，其编号不会被之后的方案重新使用
CREATE TABLE assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id    INTEGER NOT NULL REFERENCES classes(id),
    score       REAL    NOT NULL,
    created_at  TEXT    NOT NULL
);

-- 方案内容，对应SeatAssignment.assignments中的每一条
CREATE TABLE assignment_entries (
    assignment_id  INTEGER NOT NULL REFERENCES assignments(id),
    student        INTEGER NOT NULL REFERENCES students(id),
    x              INTEGER NOT NULL,        -- "current_seat"[0]
    y              INTEGER NOT NULL,        -- "current_seat"[1]
    burden         REAL    NOT NULL,        -- "burden"：方案应用后该学生的历史负担
    PRIMARY KEY (assignment_id, student),   -- 一个方案里一个学生只出现一次
    UNIQUE (assignment_id, x, y)            -- 一个方案里一个座位只坐一个人
);

-- 按学生查找他的方案内容（读取状态、查询学生历史时使用）
CREATE INDEX entries_by_student ON assignment_entries (student, assignment_id);
"""

#版本1的表结构（各表的列名），用来识别还没有升级的旧数据库文件
V1_COLUMNS = {
    "classes": ["id", "name"],
    "seats": ["class_id", "x", "y"],
    "students": ["id", "class_id", "student_id", "active"],
    "assignments": ["id", "class_id", "score", "created_at"],
    "assignment_entries": ["assignment_id", "student", "x", "y", "burden"],
}

#从版本1升级到版本2要执行的语句
UPGRADE_1_TO_2 = """
ALTER TABLE students ADD COLUMN base_burden REAL NOT NULL DEFAULT 0;
ALTER TABLE students ADD COLUMN base_after INTEGER NOT NULL DEFAULT 0;
CREATE INDEX entries_by_student ON assignment_entries (student, assignment_id);
"""

#自动备份保留的份数，更早的备份会被清除
BACKUP_KEEP = 10


def _table_columns(conn):
    #返回数据库中各表的列名{表名: [列名, ...]}，用于判断一个文件是不是本程序的数据库
    #名称以sqlite_开头的是SQLite自己维护的表，不参与比较
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {table: [row[1] for row in conn.execute(f"PRAGMA table_info({table})")] for table in tables}


def _is_integer(value):
    #是否为整数（True和False在Python里也算整数，这里要排除）
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    #是否为有限的数值（排除无穷大和NaN）
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def clean_name(value):
    #检查并整理一个班级名称或学生编号，返回整理后的文字；不合格时报ValueError
    #整理：全角空格等各种空白统一成半角空格，连续的空格合并成一个，去掉首尾空格
    #     （这样"张 三"和"张　三"会被当成同一个名字，不会出现看起来一样的重名）
    #不允许：
    #  空的名称；超过config.MAX_NAME_LENGTH个字
    #  换行、制表符等控制字符和零宽字符（看不见，会造成看起来一样的重名）
    #  "/"、"."、".."（名称会出现在网址里，它们在网址中有特殊含义）
    if not isinstance(value, str):
        raise ValueError("Name must be text")
    #Unicode类别以C开头的是控制字符、格式字符（含零宽字符）、未定义的字符等
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise ValueError("Name contains invisible or control characters")

    name = " ".join(value.split())
    if not name:
        raise ValueError("Name must not be empty")
    if len(name) > config.MAX_NAME_LENGTH:
        raise ValueError("Name is too long")
    if "/" in name or name in (".", ".."):
        raise ValueError("Name must not contain '/' or be '.' or '..'")
    return name


class HistoryManager:
    """历史记录管理器，用一个SQLite数据库文件保存多个班级的座位表、学生名单和历次排座方案。

    属性：
        db_path：数据库文件的路径。
        conn：数据库连接。用完后调用close()关闭，或者用with写法自动关闭。

    创建：
        history = HistoryManager("data/seats.db")
        文件不存在（或是空文件）时会新建数据库；旧版本的数据库会先自动备份、再升级。
        所在的文件夹不存在、文件不是SQLite数据库、是别的程序的数据库、
        或者是更新版本的程序建立的数据库时报ValueError。

    约定：
        班级用名称指定，学生用student_id指定。student_id在数据库中一律是字符串，
        所以配合历史记录使用时，Student对象的student_id也应当用字符串。
        本类只读写数据库，不修改任何Student和Seat对象；
        把读出的状态写回对象由SeatAllocator.restore_state完成。
        输入有误、数据损坏时一律报ValueError；数据库被占用等故障报sqlite3.OperationalError。

    各方法的分工：
        管理  add_class / get_classes                     添加班级、列出班级
              rename_class / delete_class                 班级改名、删除班级（连同全部历史）
              set_seats / get_seats                       设置、读取班级的座位表
              add_student / get_students / get_roster     添加学生、读取名单
              set_student_active                          标记学生离开或回来
              rename_student / delete_student             改学生编号、删除从未入座的学生
              check_integrity                             检查数据完整性
              backup                                      备份数据库文件
              transaction                                 把多步操作合成一个整体
              close                                       关闭数据库（也可以用with写法自动关闭）
        记录  record_assignment                           保存一次已经应用的方案
        查询  query_assignments                           按班级、学生、时间、方案编号查询方案
              latest_assignment_id                        班级最新一次方案的编号
              get_student_history                         某个学生的历次座位
        恢复  load_state                                  读出全班学生的当前状态
              rollback_last                               删除最近一次方案（回退）

    每次排座：
        best, score = allocator.select_best_assignment()
        record = SeatAssignment(None)
        record.set_seat_assignment(best, score)        #先记录到对象
        allocator.apply_assignment(best)               #再应用
        history.record_assignment("高一3班", record)    #最后存入数据库

    程序重新启动后恢复（load_class在loader.py中，一步完成搭建教室和恢复状态）：
        classroom, students, allocator = load_class(history, "高一3班")

    回退最近一次排座：
        history.rollback_last("高一3班")
        allocator.restore_state(history.load_state("高一3班"))
    """

    def __init__(self, db_path):
        path = Path(db_path)
        if not path.parent.is_dir():
            raise ValueError(f"Directory does not exist: {path.parent}")

        self.db_path = str(path)
        #文件不存在时，connect会新建一个空文件
        #isolation_level=None：不让sqlite3模块自动开启事务，事务全部由transaction()明确控制
        self.conn = sqlite3.connect(self.db_path, isolation_level=None)

        #读取现有的表结构和版本号；文件不是SQLite数据库（或已损坏）时，这一步会失败
        try:
            found = _table_columns(self.conn)
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.DatabaseError:
            self.conn.close()
            raise ValueError(f"Not a valid SQLite database: {self.db_path}") from None

        #当前版本应有的表结构：在内存中按SCHEMA建一个空数据库得到，避免把列名再写一遍
        template = sqlite3.connect(":memory:")
        template.executescript(SCHEMA)
        expected = _table_columns(template)
        template.close()

        try:
            if not found:
                #新文件或空文件：建表并写入版本号
                with self.transaction():
                    for statement in SCHEMA.split(";"):
                        self.conn.execute(statement)
                    self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version > SCHEMA_VERSION:
                raise ValueError(f"Database was created by a newer version of this program: {self.db_path}")
            elif version == SCHEMA_VERSION and found == expected:
                pass
            elif version == 0 and found == V1_COLUMNS:
                #版本1的旧数据库：先备份，再升级到当前版本。升级在一个事务中完成，中途出错则文件保持原样
                self.backup()
                with self.transaction():
                    for statement in UPGRADE_1_TO_2.split(";"):
                        self.conn.execute(statement)
                    self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            else:
                raise ValueError(f"Not a seat history database: {self.db_path}")
        except Exception:
            #无论哪种原因没有打开成功，都要关闭连接，错误照常抛出
            self.conn.close()
            raise

        #SQLite默认不检查外键，每次连接后都要手动开启
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self):#关闭数据库
        self.conn.close()

    #支持with写法：with HistoryManager(path) as history: ...
    #离开with块时自动关闭数据库，块内出错时也会关闭，错误照常抛出
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @contextmanager
    def transaction(self):
        #事务：把with块中的多步读写合成一个整体。用法：with history.transaction(): ...
        #  - 块中的修改要么全部生效，要么（中途出错时）全部不生效
        #  - 从进入块开始，其他程序或其他请求不能写入这个数据库，
        #    所以"先检查、再写入"之间不会被别人插进来改动数据
        #  - 可以嵌套：已经在事务中时不再重新开启，由最外层统一提交或撤销
        #本类所有修改数据的方法内部都使用它；需要把多个方法合成一步时，在外面再套一层即可
        if self.conn.in_transaction:
            yield
            return

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            #有些错误发生时SQLite已经自动撤销了事务，这时不需要（也不能）再撤销一次
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def backup(self):#把数据库复制一份到同目录的backups文件夹中，返回备份文件的路径
        #文件名带有时间，例如 seat_history_20261111_201503_123456.db
        #只保留最近的BACKUP_KEEP份，更早的备份会被清除
        #删除班级之前、升级旧版本数据库之前会自动调用
        source = Path(self.db_path)
        folder = source.parent / "backups"
        folder.mkdir(exist_ok=True)
        target = folder / f"{source.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{source.suffix}"

        #用SQLite自带的备份功能复制，得到的是一份完整一致的数据库文件
        copy = sqlite3.connect(target)
        try:
            self.conn.backup(copy)
        finally:
            copy.close()

        #文件名中的时间是按位数对齐的，按名称排序即按时间先后排序
        backups = sorted(folder.glob(f"{source.stem}_*{source.suffix}"))
        for old in backups[:-BACKUP_KEEP]:
            old.unlink()
        return target

    def _class_id(self, class_name):
        #由班级名称得到数据库内部的班级编号，班级不存在时报错
        row = self.conn.execute("SELECT id FROM classes WHERE name = ?", (class_name,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown class: {class_name}")
        return row[0]

    def _student_row_id(self, class_name, student_id):
        #由班级名称和student_id得到数据库内部的学生编号，学生不存在时报错
        row = self.conn.execute(
            "SELECT id FROM students WHERE class_id = ? AND student_id = ?",
            (self._class_id(class_name), str(student_id))).fetchone()
        if row is None:
            raise ValueError(f"Unknown student: {student_id}")
        return row[0]

    def _burden_base(self, class_id):
        #新加入或刚回来的学生的负担起点：(全班在班学生历史负担的平均值, 本班最新方案的编号)
        #每个在班学生的历史负担 = 他在自己的起点之后最近一条记录的burden；没有这样的记录则是他的起点值
        #班里还没有在班学生时，平均值取0
        mean = self.conn.execute(
            "SELECT AVG(COALESCE("
            "  (SELECT e.burden FROM assignment_entries e"
            "    WHERE e.student = s.id AND e.assignment_id > s.base_after"
            "    ORDER BY e.assignment_id DESC LIMIT 1),"
            "  s.base_burden))"
            " FROM students s WHERE s.class_id = ? AND s.active = 1", (class_id,)).fetchone()[0]
        return (0.0 if mean is None else mean), self._latest_id(class_id)

    def _latest_id(self, class_id):
        #班级最新一次方案的编号；还没有方案时为0（方案编号从1开始）
        latest = self.conn.execute("SELECT MAX(id) FROM assignments WHERE class_id = ?", (class_id,)).fetchone()[0]
        return 0 if latest is None else latest

    def _active_count(self, class_id):
        return self.conn.execute(
            "SELECT COUNT(*) FROM students WHERE class_id = ? AND active = 1", (class_id,)).fetchone()[0]

    # ---------- 管理：班级、座位表、学生名单 ----------

    def add_class(self, class_name):#添加一个班级，返回整理后的班级名称
        class_name = clean_name(class_name)
        with self.transaction():
            if self.conn.execute("SELECT 1 FROM classes WHERE name = ?", (class_name,)).fetchone():
                raise ValueError(f"Class already exists: {class_name}")
            self.conn.execute("INSERT INTO classes (name) VALUES (?)", (class_name,))
        return class_name

    def get_classes(self):#返回全部班级的名称列表，按添加的先后排列
        return [row[0] for row in self.conn.execute("SELECT name FROM classes ORDER BY id")]

    def rename_class(self, class_name, new_name):#给班级改名，座位表、名单和历史都保留。返回整理后的新名称
        new_name = clean_name(new_name)
        with self.transaction():
            class_id = self._class_id(class_name)
            if self.conn.execute("SELECT 1 FROM classes WHERE name = ? AND id != ?", (new_name, class_id)).fetchone():
                raise ValueError(f"Class already exists: {new_name}")
            self.conn.execute("UPDATE classes SET name = ? WHERE id = ?", (new_name, class_id))
        return new_name

    def delete_class(self, class_name):#删除班级，连同它的座位表、学生名单和全部历史方案
        #删除前自动备份整个数据库（见backup），误删时可以从备份文件中找回
        #调用前仍应当先向用户确认
        self._class_id(class_name)      #班级不存在时直接报错，不做备份
        self.backup()

        #按"先删引用别人的、再删被引用的"顺序删除，否则外键检查不允许
        with self.transaction():
            class_id = self._class_id(class_name)
            self.conn.execute(
                "DELETE FROM assignment_entries WHERE assignment_id IN"
                " (SELECT id FROM assignments WHERE class_id = ?)", (class_id,))
            self.conn.execute("DELETE FROM assignments WHERE class_id = ?", (class_id,))
            self.conn.execute("DELETE FROM students WHERE class_id = ?", (class_id,))
            self.conn.execute("DELETE FROM seats WHERE class_id = ?", (class_id,))
            self.conn.execute("DELETE FROM classes WHERE id = ?", (class_id,))

    def set_seats(self, class_name, positions):#设置班级的座位表，整体替换原有的座位表
        #positions为座位坐标列表[(x, y), ...]
        #坐标必须是整数，x在1到config.MAX_COORDINATE之间（x=0是黑板所在的位置），
        #y在0到config.MAX_COORDINATE之间；座位总数不超过config.MAX_SEATS
        #历史方案中保存的是坐标本身，更换座位表不会影响已有的历史记录
        positions = [tuple(position) for position in positions]
        if len(positions) > config.MAX_SEATS:
            raise ValueError(f"Too many seats (at most {config.MAX_SEATS})")
        for position in positions:
            if len(position) != 2 or not all(_is_integer(value) for value in position):
                raise ValueError("A seat position must be two integers")
            x, y = position
            if not (1 <= x <= config.MAX_COORDINATE and 0 <= y <= config.MAX_COORDINATE):
                raise ValueError(f"Seat position out of range: {position}")
        if len(set(positions)) != len(positions):
            raise ValueError("Duplicate seat positions")

        with self.transaction():
            class_id = self._class_id(class_name)
            self.conn.execute("DELETE FROM seats WHERE class_id = ?", (class_id,))
            self.conn.executemany("INSERT INTO seats (class_id, x, y) VALUES (?, ?, ?)",
                                  [(class_id, x, y) for x, y in positions])

    def get_seats(self, class_name):#返回班级的座位坐标列表[(x, y), ...]，顺序与设置时相同
        seats = self.conn.execute(
            "SELECT x, y FROM seats WHERE class_id = ? ORDER BY rowid", (self._class_id(class_name),)).fetchall()
        #数据库被外部改动后坐标可能不是整数，这里明确报出，不让坏数据进入计算
        for position in seats:
            if not all(_is_integer(value) for value in position):
                raise ValueError(f"Corrupted seat position in class {class_name}: {position}")
        return seats

    def add_student(self, class_name, student_id):#向班级的名单中添加一名学生，返回整理后的student_id
        #新学生的历史负担从全班在班学生当前的平均值开始，而不是从0开始：
        #否则中途加入的学生会因为负担明显低于其他人，连续多轮被分到较差的座位
        student_id = clean_name(str(student_id))
        with self.transaction():
            class_id = self._class_id(class_name)
            if self.conn.execute("SELECT 1 FROM students WHERE class_id = ? AND student_id = ?",
                                 (class_id, student_id)).fetchone():
                raise ValueError(f"Student already exists: {student_id}")
            if self._active_count(class_id) >= config.MAX_STUDENTS:
                raise ValueError(f"Too many students (at most {config.MAX_STUDENTS})")
            base_burden, base_after = self._burden_base(class_id)
            self.conn.execute(
                "INSERT INTO students (class_id, student_id, base_burden, base_after) VALUES (?, ?, ?, ?)",
                (class_id, student_id, base_burden, base_after))
        return student_id

    def set_student_active(self, class_name, student_id, active):#设置学生是否在班
        #active为False表示学生已离开：不再参与排座，但历史记录保留；设回True即可恢复
        #学生回来时，历史负担改为从全班在班学生当前的平均值开始（原因同add_student）；
        #他离开前的座位历史仍然保留。状态没有变化时什么都不做
        with self.transaction():
            row_id = self._student_row_id(class_name, student_id)
            class_id = self._class_id(class_name)
            was_active = self.conn.execute("SELECT active FROM students WHERE id = ?", (row_id,)).fetchone()[0]
            if bool(was_active) == bool(active):
                return
            if active:
                if self._active_count(class_id) >= config.MAX_STUDENTS:
                    raise ValueError(f"Too many students (at most {config.MAX_STUDENTS})")
                #平均值要在他回来之前计算，不把他自己算进去
                base_burden, base_after = self._burden_base(class_id)
                self.conn.execute("UPDATE students SET active = 1, base_burden = ?, base_after = ? WHERE id = ?",
                                  (base_burden, base_after, row_id))
            else:
                self.conn.execute("UPDATE students SET active = 0 WHERE id = ?", (row_id,))

    def get_students(self, class_name, active_only=True):#返回班级的student_id列表，按添加的先后排列
        #默认只返回在班的学生；active_only=False时连同已离开的学生一起返回
        sql = "SELECT student_id FROM students WHERE class_id = ?"
        if active_only:
            sql += " AND active = 1"
        return [row[0] for row in self.conn.execute(sql + " ORDER BY id", (self._class_id(class_name),))]

    def get_roster(self, class_name):#返回班级的完整名单[(student_id, 是否在班), ...]，按添加的先后排列
        #与get_students的区别：这里包含已离开的学生，并带上在班状态，供名单界面显示
        return [(student_id, bool(active)) for student_id, active in self.conn.execute(
            "SELECT student_id, active FROM students WHERE class_id = ? ORDER BY id",
            (self._class_id(class_name),))]

    def rename_student(self, class_name, student_id, new_student_id):#修改学生的student_id，历史保留。返回整理后的新编号
        new_student_id = clean_name(str(new_student_id))
        with self.transaction():
            row_id = self._student_row_id(class_name, student_id)
            if self.conn.execute("SELECT 1 FROM students WHERE class_id = ? AND student_id = ? AND id != ?",
                                 (self._class_id(class_name), new_student_id, row_id)).fetchone():
                raise ValueError(f"Student already exists: {new_student_id}")
            self.conn.execute("UPDATE students SET student_id = ? WHERE id = ?", (new_student_id, row_id))
        return new_student_id

    def delete_student(self, class_name, student_id):#从名单中删除一名从未入座的学生（例如录错的）
        #已经有历史记录的学生不能删除：删掉后历史方案会缺人。这种情况应当用set_student_active标记为离开
        with self.transaction():
            row_id = self._student_row_id(class_name, student_id)
            if self.conn.execute("SELECT 1 FROM assignment_entries WHERE student = ?", (row_id,)).fetchone():
                raise ValueError(f"Student has history, set inactive instead: {student_id}")
            self.conn.execute("DELETE FROM students WHERE id = ?", (row_id,))

    # ---------- 记录 ----------

    def record_assignment(self, class_name, record):#保存一次已经应用的排座方案，返回方案编号
        #record为已经调用过set_seat_assignment的SeatAssignment对象
        #方案编号由数据库分配，保存后写入record.assignment_id并返回
        if record.created_at is None:
            raise ValueError("Record has no assignment set")
        if not _is_number(record.score):
            raise ValueError("Record score must be a finite number")

        #检查和写入在同一个事务中：检查通过之后、写入之前，名单和座位表不会被别的操作改动
        with self.transaction():
            class_id = self._class_id(class_name)

            #本班在班学生的{student_id: 数据库内部编号}，以及本班座位坐标的集合
            roster = dict(self.conn.execute(
                "SELECT student_id, id FROM students WHERE class_id = ? AND active = 1", (class_id,)))
            seats = set(self.conn.execute("SELECT x, y FROM seats WHERE class_id = ?", (class_id,)))

            #先检查全部内容，都没有问题才开始写入
            rows = []
            for student_id, info in record.assignments.items():
                if str(student_id) not in roster:
                    raise ValueError(f"Student not in class or inactive: {student_id}")
                x, y = info["current_seat"]
                if (x, y) not in seats:
                    raise ValueError(f"Seat not in class layout: {(x, y)}")
                if not _is_number(info["burden"]):
                    raise ValueError(f"Burden must be a finite number: {student_id}")
                rows.append((roster[str(student_id)], x, y, info["burden"]))

            #student_id转成字符串后可能重复（如整数1和字符串"1"）
            if len({row[0] for row in rows}) != len(rows):
                raise ValueError("Duplicate student_id in record")
            if len({(row[1], row[2]) for row in rows}) != len(rows):
                raise ValueError("A seat cannot hold multiple students")

            cursor = self.conn.execute(
                "INSERT INTO assignments (class_id, score, created_at) VALUES (?, ?, ?)",
                (class_id, record.score, record.created_at))
            assignment_id = cursor.lastrowid
            self.conn.executemany(
                "INSERT INTO assignment_entries (assignment_id, student, x, y, burden) VALUES (?, ?, ?, ?, ?)",
                [(assignment_id, *row) for row in rows])

        record.assignment_id = assignment_id
        return assignment_id

    # ---------- 读取与查询 ----------

    def latest_assignment_id(self, class_name):#返回班级最新一次方案的编号；还没有方案时返回0
        #每保存或回退一次方案，这个编号都会变化，可以用来判断"班级的排座数据是否被别的操作改动过"
        return self._latest_id(self._class_id(class_name))

    def query_assignments(self, class_name=None, student_id=None, start=None, end=None,
                          assignment_id=None, limit=None):
        #查询历史方案，返回列表，按方案编号（即时间先后）排列，每一项是一个字典：
        #  {"assignment_id": 编号, "class_name": 班级, "score": 分数, "created_at": 时间,
        #   "assignments": {student_id: {"burden": ..., "current_seat": [x, y]}}}
        #各个条件都可以省略，给出多个条件时取同时满足的方案；全部省略时返回所有方案
        #  class_name      只要这个班级的方案
        #  student_id      只要包含这个学生的方案（返回的仍是完整的方案内容）
        #  start、end      只要created_at在这个范围内的方案（含两端）。按字符串比较，
        #                  所以end写"2026-11-11"不包含当天，要包含应写"2026-11-11T23:59:59"
        #  assignment_id   只要这个编号的方案
        #  limit           只要满足条件的方案中最近的limit个（返回时仍按时间先后排列）；
        #                  例如limit=1就是最新的一个方案。省略时不限数量
        if limit is not None and (not _is_integer(limit) or limit < 0):
            raise ValueError("limit must be a non-negative integer")

        sql = ("SELECT a.id, c.name, a.score, a.created_at"
               " FROM assignments a JOIN classes c ON c.id = a.class_id")
        conditions = []
        params = []
        if class_name is not None:
            conditions.append("a.class_id = ?")
            params.append(self._class_id(class_name))
        if student_id is not None:
            conditions.append("a.id IN (SELECT e.assignment_id FROM assignment_entries e"
                              " JOIN students s ON s.id = e.student WHERE s.student_id = ?)")
            params.append(str(student_id))
        if start is not None:
            conditions.append("a.created_at >= ?")
            params.append(start)
        if end is not None:
            conditions.append("a.created_at <= ?")
            params.append(end)
        if assignment_id is not None:
            conditions.append("a.id = ?")
            params.append(assignment_id)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        #有limit时先按编号从大到小取出最近的limit个，再倒回从小到大的顺序
        sql += " ORDER BY a.id DESC"
        if limit is not None:
            #SQLite只能处理64位以内的整数，limit再大也没有意义
            sql += " LIMIT ?"
            params.append(min(limit, 2 ** 31))
        found = self.conn.execute(sql, params).fetchall()
        found.reverse()

        results = []
        for found_id, found_class, score, created_at in found:
            entries = self.conn.execute(
                "SELECT s.student_id, e.x, e.y, e.burden FROM assignment_entries e"
                " JOIN students s ON s.id = e.student WHERE e.assignment_id = ? ORDER BY s.id", (found_id,))
            results.append({
                "assignment_id": found_id,
                "class_name": found_class,
                "score": score,
                "created_at": created_at,
                "assignments": {sid: {"burden": burden, "current_seat": [x, y]} for sid, x, y, burden in entries},
            })
        return results

    def get_student_history(self, class_name, student_id):
        #返回某个学生的历次座位，按时间先后排列，每一项是一个字典：
        #  {"assignment_id": 方案编号, "created_at": 时间, "seat": (x, y), "burden": 该次方案后的历史负担}
        #已离开的学生也可以查询
        rows = self.conn.execute(
            "SELECT e.assignment_id, a.created_at, e.x, e.y, e.burden FROM assignment_entries e"
            " JOIN assignments a ON a.id = e.assignment_id WHERE e.student = ? ORDER BY e.assignment_id",
            (self._student_row_id(class_name, student_id),))
        return [{"assignment_id": aid, "created_at": created_at, "seat": (x, y), "burden": burden}
                for aid, created_at, x, y, burden in rows]

    # ---------- 恢复 ----------

    def load_state(self, class_name):
        #从历史记录中读出全班在班学生的当前状态，返回字典，顺序与get_students相同：
        #  {student_id: {"seat_history": [(x, y), ...], "accum_burden": 历史负担, "current_seat": (x, y)或None}}
        #把它交给SeatAllocator.restore_state即可重建学生和座位的状态。本方法只读数据库。
        #  seat_history   该学生在历次方案中的座位，按时间先后排列
        #  accum_burden   该学生在自己的负担起点之后最近一条记录中的burden；
        #                 起点之后还没有记录（刚加入、刚回来）则为起点值base_burden
        #  current_seat   该学生在本班最新一次方案中的座位；不在最新方案中则为None。
        #                 座位表更换后原座位已不存在时也为None
        #读到损坏的数据（负担值不是有限的数、坐标不是整数）时报ValueError并指出是哪条记录，
        #不让坏数据进入排座计算
        class_id = self._class_id(class_name)
        seats = set(self.conn.execute("SELECT x, y FROM seats WHERE class_id = ?", (class_id,)))
        latest = self._latest_id(class_id)

        students = self.conn.execute(
            "SELECT id, student_id, base_burden, base_after FROM students"
            " WHERE class_id = ? AND active = 1 ORDER BY id", (class_id,)).fetchall()

        #一次读出全班在班学生的全部方案记录，再按学生分组（每组内按方案编号从小到大）
        entries_of = {row_id: [] for row_id, _, _, _ in students}
        for row_id, assignment_id, x, y, burden in self.conn.execute(
                "SELECT e.student, e.assignment_id, e.x, e.y, e.burden FROM assignment_entries e"
                " JOIN students s ON s.id = e.student WHERE s.class_id = ? AND s.active = 1"
                " ORDER BY e.assignment_id", (class_id,)):
            entries_of[row_id].append((assignment_id, x, y, burden))

        state = {}
        for row_id, student_id, base_burden, base_after in students:
            entries = entries_of[row_id]
            for assignment_id, x, y, burden in entries:
                if not (_is_integer(x) and _is_integer(y) and _is_number(burden)):
                    raise ValueError(f"Corrupted record: class {class_name}, student {student_id}, "
                                     f"assignment {assignment_id}")
            if not _is_number(base_burden) or not _is_integer(base_after):
                raise ValueError(f"Corrupted record: class {class_name}, student {student_id}, burden base")

            current_seat = None
            accum_burden = base_burden
            if entries:
                last_assignment, x, y, burden = entries[-1]
                if last_assignment > base_after:
                    accum_burden = burden
                if last_assignment == latest and (x, y) in seats:
                    current_seat = (x, y)

            state[student_id] = {
                "seat_history": [(x, y) for _, x, y, _ in entries],
                "accum_burden": float(accum_burden),
                "current_seat": current_seat,
            }
        return state

    def rollback_last(self, class_name):#回退：删除班级最近一次方案的记录，返回被删除的方案编号
        #只修改数据库。删除后用load_state和SeatAllocator.restore_state把程序中的状态恢复到上一次
        #被删除的记录不可找回
        with self.transaction():
            latest = self._latest_id(self._class_id(class_name))
            if latest == 0:
                raise ValueError("No assignment to roll back")
            self.conn.execute("DELETE FROM assignment_entries WHERE assignment_id = ?", (latest,))
            self.conn.execute("DELETE FROM assignments WHERE id = ?", (latest,))
        return latest

    # ---------- 管理：数据完整性 ----------

    def check_integrity(self):#检查数据完整性，返回发现的问题描述列表；没有问题时返回空列表
        problems = []

        #1. 文件本身是否损坏（SQLite自带的检查，正常时只返回一行"ok"）
        for (result,) in self.conn.execute("PRAGMA integrity_check"):
            if result != "ok":
                problems.append(f"Database file is damaged: {result}")

        #2. 是否有记录引用了不存在的班级、学生或方案
        for table, rowid, parent, _ in self.conn.execute("PRAGMA foreign_key_check"):
            problems.append(f"Row {rowid} of {table} refers to a missing row of {parent}")

        #3. 是否有方案里出现了别的班级的学生
        for assignment_id, student_id in self.conn.execute(
                "SELECT e.assignment_id, s.student_id FROM assignment_entries e"
                " JOIN assignments a ON a.id = e.assignment_id JOIN students s ON s.id = e.student"
                " WHERE a.class_id != s.class_id"):
            problems.append(f"Assignment {assignment_id} contains student {student_id} of another class")

        #4. 数值是否正常。SQLite不强制列的类型，文件被别的程序改动后，
        #   数值列里可能是文字，也可能是无穷大；typeof()返回一个值实际的类型
        #   每一项为：(问题描述, 查出有问题的行的SQL)。有限的数：类型是整数或小数，且绝对值不超过1e300
        finite = "(typeof({0}) IN ('integer', 'real') AND {0} BETWEEN -1e300 AND 1e300)"
        checks = [
            ("Seat position is not an integer: class {}, row {}",
             "SELECT c.name, s.rowid FROM seats s JOIN classes c ON c.id = s.class_id"
             " WHERE typeof(s.x) != 'integer' OR typeof(s.y) != 'integer'"),
            ("Seat position in assignment is not an integer: assignment {}, row {}",
             "SELECT assignment_id, rowid FROM assignment_entries"
             " WHERE typeof(x) != 'integer' OR typeof(y) != 'integer'"),
            ("Burden is not a finite number: assignment {}, row {}",
             "SELECT assignment_id, rowid FROM assignment_entries WHERE NOT " + finite.format("burden")),
            ("Score is not a finite number: assignment {}",
             "SELECT id FROM assignments WHERE NOT " + finite.format("score")),
            ("Burden base is not valid: student {} (row {})",
             "SELECT student_id, id FROM students WHERE NOT " + finite.format("base_burden")
             + " OR typeof(base_after) != 'integer'"),
            ("Active flag is not 0 or 1: student {} (row {})",
             "SELECT student_id, id FROM students WHERE typeof(active) != 'integer' OR active NOT IN (0, 1)"),
            ("Assignment has no content: assignment {}",
             "SELECT a.id FROM assignments a WHERE NOT EXISTS"
             " (SELECT 1 FROM assignment_entries e WHERE e.assignment_id = a.id)"),
        ]
        for text, sql in checks:
            for row in self.conn.execute(sql):
                problems.append(text.format(*row))

        #5. 方案的时间是否为可识别的格式
        for assignment_id, created_at in self.conn.execute("SELECT id, created_at FROM assignments"):
            try:
                datetime.fromisoformat(created_at)
            except (TypeError, ValueError):
                problems.append(f"Time is not valid: assignment {assignment_id}")

        return problems


class SeatAssignment:
    """一次排座方案的记录对象，用于历史记录和日志。

    它把SeatAllocator生成的方案字典{Seat: Student}转换成只含编号、数值和坐标的普通字典，
    不保存Student和Seat对象本身，因此可以直接写入JSON文件。

    属性：
        assignment_id：方案的编号，创建时传入。要存入数据库时传None即可，
            HistoryManager.record_assignment保存后会把数据库分配的编号写到这里。
        assignments：方案内容，结构为
            {student_id: {"burden": 方案应用后该学生的历史负担, "current_seat": [x, y]}}
            只包含有学生入座的座位；空座位不在其中。调用set_seat_assignment之前为空字典。
        score：方案的公平性分数。调用set_seat_assignment之前为0.0。
        created_at：记录的时间，精确到秒的ISO格式字符串，如"2026-11-11T20:15:03"。
            调用set_seat_assignment之前为None。

    用法（注意顺序，必须先记录、再应用）：
        best, score = allocator.select_best_assignment()
        record = SeatAssignment(None)
        record.set_seat_assignment(best, score)
        allocator.apply_assignment(best)
        history.record_assignment("高一3班", record)    #需要保存时，应用之后存入数据库

    注意：
        set_seat_assignment必须在apply_assignment之前调用。
        "burden"是用"学生目前的历史负担 + 新座位的负担值"算出来的，
        如果方案已经应用，新座位的负担会被重复计入，而这种情况无法被可靠地检查出来。
    """

    def __init__(self, assignment_id):
        self.assignment_id = assignment_id
        self.assignments = {}
        self.score = 0.0
        self.created_at = None

    def set_seat_assignment(self, assignment, score):#记录一次排座方案的内容、分数和时间
        #assignment为SeatAllocator生成的方案字典{Seat: Student}，score为它的公平性分数
        #必须在apply_assignment之前调用（原因见类的说明）
        #这里只读取学生和座位的属性，不做任何修改
        records = {}
        for seat, student in assignment.items():
            records[student.student_id] = {
                #方案应用后该学生的历史负担：目前的历史负担加上新座位的负担值
                "burden": student.accum_burden + seat.burden,
                "current_seat": [seat.x, seat.y],
            }

        #学生编号重复时，后一条会覆盖前一条，记录将少于方案中的人数
        if len(records) != len(assignment):
            raise ValueError("Duplicate student_id in assignment")

        #全部检查通过后再写入属性，报错时原有记录保持不变；重复调用时整体覆盖上一次的记录
        self.assignments = records
        self.score = float(score)
        #时间记为精确到秒的ISO格式字符串，方便之后直接写入JSON文件
        self.created_at = datetime.now().isoformat(timespec="seconds")
