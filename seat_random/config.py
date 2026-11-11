#算法参数：集中存放加权随机分配、公平性打分、多方案选优用到的全部系数
#其他文件通过 config.参数名 读取，修改这里的值即可调整算法行为
#各参数的含义和取值依据见docs/algorithm.md

#加权随机分配中三项目标的系数，对应PRD中的a、b、c
#权重为exp(a*B + b*D + c*H)，系数越大，对应目标对抽取概率的影响越强
#取值由tests/sim_weight_design.py在examples下样例布局上的模拟确定
WEIGHT_BOARD = 4.0    #a：黑板距离差异系数
WEIGHT_MOVE = 4.0     #b：移动距离系数
WEIGHT_HISTORY = 8.0  #c：历史座位负担系数

#公平性打分的权重，对应PRD中的w1、w2
SCORE_WEIGHT_VAR = 1.0    #w1：历史负担方差的权重
SCORE_WEIGHT_RANGE = 1.0  #w2：历史负担极差的权重

#每轮生成的候选方案数量
NUM_WEIGHTED_PLANS = 50   #多目标加权随机方案的数量
NUM_RANDOM_PLANS = 50     #纯随机方案的数量（由examples下样例布局的模拟测试确定）

#数据规模的上限，由HistoryManager在写入数据时检查。
#按一个教室的实际规模留了很大的余量，目的是挡住明显异常的输入（例如坐标为10亿会让界面无法显示）
MAX_COORDINATE = 100      #座位坐标的最大值：x取1到100（x=0是黑板），y取0到100
MAX_SEATS = 500           #每个班级的座位数上限
MAX_STUDENTS = 500        #每个班级在班学生数的上限
MAX_NAME_LENGTH = 50      #班级名称和学生编号的最大字数
