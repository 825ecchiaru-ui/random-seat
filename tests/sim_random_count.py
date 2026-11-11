#模拟纯随机候选方案的数量对公平性和学生满意度的影响
#使用主程序当前的权重公式，加权候选数量取主程序的NUM_WEIGHTED_PLANS
#用法：python tests/sim_random_count.py
from layout_tools import m
from sim_weight_design import run

if __name__ == "__main__":
    for n_random in (0, 10, 50, 100):
        run(["main"], (1.0, 0.85, 0.7), 12, n_weighted=m.config.NUM_WEIGHTED_PLANS, n_random=n_random)
    #对照：完全不用加权候选，只在100个纯随机方案中选优
    run(["main"], (1.0, 0.85, 0.7), 12, n_weighted=0, n_random=100)
