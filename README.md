# ios-sweet-diting — HackiOS on Redmi K50 Ultra (diting, SM8475/cape)

从 [ios-sweet-k60](https://github.com/XTOM0706/ios-sweet-k60) 分出来的 K50 至尊版
移植树。K60（mondrian）和 diting 是同一颗 SM8475、同一套 cape 平台、同一代
PMIC，K60 那条链路已经把 iOS 16 送到锁屏；所以这里**不重新造轮子**，只把
板级差异换掉：

| 项 | K60 | diting |
|---|---|---|
| 面板 | 1440x3200，stride 5760 | **1220x2712，stride 4880** |
| 面板初始化 | m11a 序列 | **L12（42 02 0a / 36 02 0b）序列** |
| 触摸坐标上限 | 14400 x 32000 | **12200 x 27120** |
| 其余（GIC/timer/UFS/RSC/触摸引脚/PMIC 按键/内存 notch） | — | 完全相同 |

## 目录

```
board/diting.json        板级事实的唯一来源（每个值都写了出处）
tools/mkadt-diting.py    由 board.json 生成 ADT（Apple Device Tree）
tools/diting-extract.py  panel：面板 dtsi -> 面板初始化字节流
                         fdt：解析 dtb/dtbo/live FDT 并与 board.json 对差
tools/fdtparse.py        无依赖 FDT/dtbo 解析器
tools/retarget-k60-patches.py  K60 补丁序列 -> diting 序列（带计数锁）
tools/patch-retarget.lock.json  每条改写应出现的次数（防上游漂移）
tools/diting-apply-shim.sh     K60 shim 源码 -> shim/（换面板几何与序列）
tools/diting-build-boot.sh     kernel + ADT + shim -> boot.img（只给 fastboot boot）
tools/diting-log-pull.sh       从 userdata 尾部的日志环拉内核日志
xnu/patches/             已重定向的 207 个补丁（不要手改，改表格）
shim/                    已 stage 的 shim 源码（由 apply-shim 生成）
```

## 快速开始（在 Linux 构建机上）

```sh
# 0) 需要对门禁工具链：NDK clang + mkbootimg + 一份 diting 的 boot dtb
#    （dtb 的取法见 docs/DEVICE-DUMP.md）

# 1) 生成 diting 补丁序列（K60 树需在旁边 ../ios-sweet-k60）
python3 tools/retarget-k60-patches.py --dry-run
python3 tools/retarget-k60-patches.py

# 2) 生成 diting shim
tools/diting-apply-shim.sh
tools/diting-apply-shim.sh --check     # 幂等校验

# 3) 编内核：推 xnu/patches 后由 GitHub Actions 出 kernel.development.sweet
#    （.github/workflows/xnu.yml）

# 4) 打包并上机（RAM，绝不刷分区）
DTB=/path/live.dtb tools/diting-build-boot.sh kernel.development.sweet
fastboot boot $HOME/hackios/diting.img

# 5) 看日志
tools/diting-log-pull.sh out.txt
```

## 红线

- 只用 `fastboot boot`（RAM）。不写 boot/system/vendor，不刷任何分区。
- 不碰 persist / modemst1 / modemst2 / fsg / fsc / devinfo / cdt / ddr。
- iOS root 装进 `userdata` 会抹掉 Android，必须单独确认后再做。
- 设备 dump、序列号、Apple 产物不进 git。

## 状态

见 [PORT-STATUS-DITING.md](PORT-STATUS-DITING.md)。一句话：**这台工作区没有
NDK/clang/CI，也没有上过机**——所有产物都只做过静态校验，真正的判据是设备上的
第一帧和第一行日志。
