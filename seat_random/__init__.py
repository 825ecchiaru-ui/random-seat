#seat_random包：座位随机重排的后端逻辑
#在这里统一导出各个类，使用时可以直接写 from seat_random import SeatAllocator
#  config.py     算法参数
#  models.py     Student、Seat、Classroom
#  burden.py     BurdenCalculator
#  allocator.py  SeatAllocator
#  history.py    HistoryManager、SeatAssignment（历史记录，SQLite）
#  loader.py     load_class（按数据库搭建班级并恢复状态）
from . import config
from .models import Student, Seat, Classroom
from .burden import BurdenCalculator
from .allocator import SeatAllocator
from .history import HistoryManager, SeatAssignment
from .loader import load_class
