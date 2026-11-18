"""SSTRPVST 实例生成器 —— 对齐 arXiv:2308.14744 的参数口径。

论文（Alkaabneh & Bonku, 2023）§6.1 给出的参数：
    q_min ~ U[1.5, 3.5]；q_max = 2.5 * q_min
    xi = 3（一次加注的时间）；Qs = 15（喷洒车罐容）；eta = 2（每单位肥料 2 单位时间）；beta = 2
    节点数：小农场 15-25，中农场 25-40；喷洒车：小/中 2-3 辆
    仓库位于 (0,0)

论文没有写明的三个量（本文件显式参数化，必须写进论文的"实现细节"）：
    1) 农场坐标尺度 FARM_SIDE：论文只给英亩面积，没给坐标范围。此处用 [0,100]^2。
    2) 喷洒车速度 SPEED_SPRAYER：把欧氏距离换算成"时间单位"。此处取 20，使平均弧长约
       1 个时间单位，与 xi=3、s=2*q（3~7）处于同一量级。
    3) 补给车罐容 Qr：论文只说 4000-6000 加仑 vs 喷洒车 1300-1600 加仑，未给"单位"值。
       此处取 3.5 * Qs（区间中位比例——需要你确认）。

用法：
    python sstrp_gen.py --out ../grader/data/sstrp_n25_uniform.pkl \
        --n-min 15 --n-max 25 --k 2 3 --num 1000 --seed 2025
"""
from __future__ import annotations

import argparse
import pickle
from pathlib import Path

import numpy as np

FARM_SIDE = 100.0
SPEED_SPRAYER = 20.0

# 论文 §6.1 的常量
XI = 3.0
QS = 15.0
ETA = 2.0
BETA = 2.0
QR = 3.5 * QS          # 见文件头"未写明"第 3 条


def generate(num_instances: int, n_min: int, n_max: int, k_choices, seed: int):
    rng = np.random.default_rng(seed)
    instances = []
    for _ in range(num_instances):
        n = int(rng.integers(n_min, n_max + 1))
        k = int(rng.choice(k_choices))

        nodes = rng.uniform(0.0, FARM_SIDE, size=(n, 2))
        q_min = np.round(rng.uniform(1.5, 3.5, size=n), 1)
        q_max = np.round(2.5 * q_min, 1)

        instances.append(
            {
                "n": n,
                "K": k,
                "depot": [0.0, 0.0],
                "nodes": nodes.tolist(),
                "q_min": q_min.tolist(),
                "q_max": q_max.tolist(),
                "Qs": QS,
                "Qr": QR,
                "xi": XI,
                "eta": ETA,
                "beta": BETA,
                "speed_sprayer": SPEED_SPRAYER,
                "speed_tanker": BETA * SPEED_SPRAYER,
                "farm_side": FARM_SIDE,
            }
        )
    return instances


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-min", type=int, default=15)
    ap.add_argument("--n-max", type=int, default=25)
    ap.add_argument("--k", type=int, nargs="+", default=[2, 3])
    ap.add_argument("--num", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=2025)
    args = ap.parse_args()

    data = generate(args.num, args.n_min, args.n_max, args.k, args.seed)

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)

    ns = np.array([d["n"] for d in data])
    ks = np.array([d["K"] for d in data])
    print(f"wrote {len(data)} instances -> {out}")
    print(f"  n: mean {ns.mean():.2f}, min {ns.min()}, max {ns.max()}")
    print(f"  K: {dict(zip(*np.unique(ks, return_counts=True)))}")
    print(f"  seed={args.seed}  (固定种子，重跑得到同一份 pkl)")


if __name__ == "__main__":
    main()
