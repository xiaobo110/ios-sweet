# 从设备取 dump（只读）

目的：确认 `board/diting.json` 里标着 pending 的几项——DRAM bank、reserved
memory 的实际 reg、哪块 L12 面板在跑、`cont_splash_region` 是否真的落在
`0xb8000000`。**整个过程只读分区，不写任何一块。**

> 不要把 dump 提交进 git（`dump/` 已在 `.gitignore` 里）。里面有机型序列号和
> 分区内容。

## 方案 A：root 的 Android（首选，拿到的就是真实 live FDT）

```sh
adb root
adb shell 'mkdir -p /sdcard/dump'
# 1) 内核看到的设备树（bootloader 合过 dtbo 之后的）
adb shell 'cp /sys/firmware/fdt /sdcard/dump/live.dtb'
# 2) dtbo 分区原样（用来确认 overlay 里到底有哪些面板节点）
adb shell 'dd if=/dev/block/by-name/dtbo of=/sdcard/dump/dtbo.img'
# 3) 物理内存地图
adb shell 'cat /proc/iomem' > dump/iomem.txt
adb pull /sdcard/dump dump
```

`dd if=... of=...` 是**从分区读出来**，方向别弄反；不要在这台机器上执行任何
`of=/dev/block/...`。

## 方案 B：没有 root，用 recovery（TWRP / OrangeFox）

```sh
fastboot boot recovery.img       # 只进 RAM，不刷 recovery
adb shell 'dd if=/dev/block/by-name/dtbo of=/tmp/dtbo.img'
adb pull /tmp/dtbo.img dump/
adb shell 'cat /proc/iomem' > dump/iomem.txt
```

recovery 里的 `/sys/firmware/fdt` 是 recovery 自己的 dtb，只能参考；reserved
memory 以 `dtbo.img` 里的 overlay 为准。`dtbo.img` 是容器（magic
`0xd7b7ab1e`），`diting-extract.py` 会自己拆。

## 解析与对差

```sh
# dtbo 容器：把每个 overlay 里的面板/内存/触摸节点都列出来
python3 tools/diting-extract.py fdt dump/dtbo.img --board board/diting.json --verbose

# live FDT：与 board/diting.json 逐项对差，任何不一致都会打印
python3 tools/diting-extract.py fdt dump/live.dtb --board board/diting.json --out dump/facts.json
```

输出里重点看四行：

- `memory`：dram-base / dram-size 是否覆盖 `0xb4000000 + 0x2c000000`；
  没有覆盖就把 `MEMPLAN` 换成 p1/p2，或者按实际 iomem 重新选窗口。
- `splash`：`cont_splash_region` 的 reg 是否仍是 `0xb8000000 0x2b00000`。
  不是的话，`shim/console.h` 的 `FB_BASE`、0095、ADT 的 `/sweet-fbcon`
  要一起改。
- `panels`：实际存在的是 42 02 0a 还是 36 02 0b；对应设置
  `DITING_PANEL=l12-36-02-0b` 后重跑 `tools/diting-apply-shim.sh`。
- `touch`：`goodix,panel-max-x/y` 与 GPIO/中断号是否与 board.json 一致。

`fdt` 模式不做猜测：属性缺失就打印 `missing`，不会用默认值补上。

## boot.img 用的 dtb

`tools/diting-build-boot.sh` 需要一个 dtb 传给 `mkbootimg --dtb`。用 live FDT
即可（K60 也是这么做的）：

```sh
DTB=dump/live.dtb tools/diting-build-boot.sh kernel.development.sweet
```

没有 dump 时脚本会直接报错退出，不会拿一个不存在的路径去打包。
