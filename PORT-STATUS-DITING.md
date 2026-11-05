# HackiOS on Redmi K50 Ultra (diting, SM8475) — 移植状态

更新时间：2026-11-04。基线：[ios-sweet-k60](https://github.com/XTOM0706/ios-sweet-k60)，
后者已在 K60（mondrian）上把 iOS 16 送到锁屏。

## 一、结论先说

已经把「同一颗 SoC 上换一块屏和一颗触摸 IC」所需的差异全部落进可复现的脚本：

- 补丁序列：207 个 K60 补丁全部重定向，数值改动有计数锁，上游一动就硬失败。
- ADT：由 `board/diting.json` 生成，dram / fbcon / gic 已静态校验通过
  （`0xb4000000 + 703 MiB`，fbcon `0xb8000000 1220x2712 stride 4880`）。
- shim：从 K60 源码 stage，只换 `console.h` 几何、控制台字符串、DSI 测试图案
  尺寸和面板初始化序列；`--check` 幂等。
- 面板序列：L12 1220x2712 已从 LineageOS 的 diting devicetree 生成并做长度
  往返校验（708 / 682 字节）。

**没有做到的：这台工作区没有 NDK/clang/mkbootimg，也没有连过设备。**上面所有
产物只做过静态校验，没有编过、没有跑过。真正的判据只有两条：设备上出现第一帧，
以及日志里出现对应的判据行。

## 二、直接复用的部分（同 SoC，一个字都没改）

| 项 | 值 | 依据 |
|---|---|---|
| GIC | distributor `0x17100000`，redistributor `0x17180000`，PE 窗口 `0x40000` | cape.dtsi、`xnu/cape/CAPE.h` |
| 虚拟定时器 | intid 27（16 + PPI 11），timebase 19.2 MHz | cape.dtsi arch_timer |
| UFS | `0x1d84000` + `0x3000` | cape.dtsi ufshc |
| RSC / CMD_DB | `0x17a00000` / `0x80860000` | iomem |
| 触摸总线 | GENI SPI SE4 `0x990000`，IRQ 605，GPIO 16/17/18/19 = qup4 clk/mosi/miso/cs | diting-sm8475.dtsi + diting-pinctrl.dtsi |
| 触摸供电 | pm8350c LDO2（rpmh-regulator-ldoc2）1.8 V，HPM | cape-regulators.dtsi |
| 按键 | vol_up = pm8350_gpios 6（低有效）；power / vol_down = PM8350 PON 0x13 bit7/bit6 | cape-mtp.dtsi、K60 0251 |
| 内存 notch | xbl_ramdump+xbl_sc / global_sync / ramoops 0xa7000000 / panel / gunyah，与 K60 0184 完全一致 | cape-reserved-memory.dtsi |
| 机器配置 | `MACHINE_CONFIG=SWEET` | K60 的验证路径 |

## 三、改掉的部分（这四类，且只有这四类）

| 项 | K60 | diting | 落点 |
|---|---|---|---|
| 面板几何 | 1440x3200，stride 5760 | 1220x2712，stride 4880 | 0094 / 0095 / 0214 / 0218 / 0240 / 0242 / 0249、ADT、shim `console.h` |
| 早期控制台基址 | `0xb8000000` | 同（diting 的 cont_splash_region 也是 `0xb8000000`，`0x2b00000`） | `cape-sde-display.dtsi` |
| 触摸坐标上限 | 14400 x 32000 | 12200 x 27120 | 0240 / 0242 |
| 面板初始化序列 | m11a（live FDT） | L12 42 02 0a（备用 36 02 0b） | `shim/panel_init.h`、`panel_init_nodsc.h` |

另外两处只为一致性而改，不是新硬件事实：

- 0095 里 Stage-0 白色色带的行距 `mov x4, #4320` → `#4880`，行号
  `2300/3200` 按比例换成 `1949/2712`。K60 当年只换了基址没换行距，色带在
  K60 上本来就是斜的；这里按 diting 的真实 stride 写对。
- 0217 的 1170x2532 是 0218 之前的中间态，0218 会覆盖成面板原生尺寸；
  保留不动。

## 四、构建门（每一道都对应一次已经踩过或注定会踩的坑）

`tools/diting-build-boot.sh` 按顺序硬校验：

1. **shim 必须是 diting 的**（`diting-apply-shim.sh --check`）。stage 错成
   K60 的 1440x3200，控制台整块画到屏外，表现是“设备像死了”，其实内核在跑。
2. **ADT 必须是 `0xb4000000` 窗口**。用旧 `0x80000000` 布局的 ADT，内核会
   看到 873 MB “RAM”，守护进程随机崩（K60 2026-10-25 丢过两轮）。
3. **ADT 必须带 diting 的 fbcon 几何**。内核控制台从 `/sweet-fbcon` 拿
   1220x2712/4880；K60 尺寸会得到错位、折行的控制台。
4. **cmdline 必须含 `sweettouchoff=1`**。`bsd/dev/sweet_touch.c` 是给另一颗
   SoC 的 SE7 + GENI-I2C 写的，在这颗上 probe 阶段会整机死。

## 五、上机（RAM only）

```sh
DTB=/path/live.dtb tools/diting-build-boot.sh kernel.development.sweet
fastboot boot $HOME/hackios/diting.img
```

- `fastboot boot` 只写 RAM，重启即回到 Android。
- 设备起来后（能进 recovery / adb root）：
  `tools/diting-log-pull.sh diting.txt`

日志判据（沿用 K60 的字符串，补丁名和日志前缀都还是 `K60`，不要看成刷错机）：

| 判据行 | 含义 | 失败时先查 |
|---|---|---|
| `SWEET FB: AppleCLCD 1220x2712 stride 4880 at 0xb8000000` | 内核按 diting 几何接管了 ABL 的 splash buffer | ADT `/sweet-fbcon`、`boot_args.Video`（shim `loader.c`） |
| `SWEET FB: swap ... -> panel x,y` | CoreAnimation 开始提交帧 | `TimingElements`/`ColorElements`（0218）、GetID=1 |
| `ufs busy, retry next interval` / 正常挂载 | UFS 主机起来了 | UFS 基址、RPMh 时钟 |
| `panic flush, total` | 有 panic 落盘 | 看前后 200 行，不要只看最后一行 |
| `K60 TOUCH: abi berlin selected, axes 12200 x 27120` | 触摸 ABI 选中（K60 未过） | RSC `0x17a00000`、SE4 引脚、LDO2 |
| `K60 TOUCH: abi none` | 两套 Goodix 框法都不通 | 先确认 IC 是 Goodix 还是 FocalTech |

## 六、已知风险 / 待验证

1. **触摸很可能仍然过不去。** K60 卡在 RSC `0x17a00000` 整块不可达；diting 的
   GENI SE4 挂在同一个 RSC 下，大概率同样中招。这不是本次改动能解决的，属于
   下一个工程问题。
2. **ABL 是否像 K60 一样让面板持续扫描 splash buffer** 未证实。若不扫描，
   表现为 shim 阶段全黑但串口/日志有输出——第一件事是 `fastboot boot` 一次
   Android 的 boot.img 对照。
3. **触摸 IC 型号未知。** DT 只写 `xiaomi,l12-spi` + goodix 固件名，但 Xiaomi
   的驱动同时带 `fts,*` 属性。第一次读到芯片 ID 再决定 ABI。
4. **DRAM bank 与 reserved-memory 仍需 live FDT 确认。** 目前用的是 K60 的 p0
   窗口 + diting 的 reserved-memory 表；如果 diting 的 DRAM 没有覆盖到
   `0xdf000000`，`diting-extract.py fdt` 会直接报差（见 `docs/DEVICE-DUMP.md`）。
5. **面板 42 还是 36 由 DDIC id 决定。** 两者都是 1220x2712，选错只影响 DSI
   调试路径的初始化序列，不影响正常启动。

## 七、红线

- 只 `fastboot boot`（RAM）。不刷 boot/system/vendor，不写任何分区。
- 不碰 persist / modemst1 / modemst2 / fsg / fsc / devinfo / cdt / ddr。
- iOS root 写进 `userdata` 会抹掉 Android，必须单独确认后再做。
- 设备 dump、序列号、Apple 产物不进 git。
