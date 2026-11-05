# K60 (mondrian) → K50 Ultra (diting) 差分

两个机型都是 SM8475 / cape。表中每个 diting 值都给了出处；标 **pending** 的
必须用 `docs/DEVICE-DUMP.md` 的 live FDT 确认后才能当成事实。

## 显示

| 项 | K60 | diting | diting 出处 |
|---|---|---|---|
| 面板分辨率 | 1440x3200 | 1220x2712 | `lineage-diting-dt/qcom/display/display/dsi-panel-l12-42-02-0a-dsc-cmd.dtsi`（`qcom,mdss-dsi-panel-width/height`） |
| stride | 5760 B | 4880 B（1220 × 4） | BGRA，同 K60 的 `simplefb` 约定 |
| 刷新率 | 60 Hz | 60 / 90 / 120 Hz，取 60 | 同上 `qcom,mdss-dsi-panel-framerate` |
| 模式 | DSC command mode | DSC command mode，30 bpp | `qcom,mdss-dsi-panel-type = "dsi_cmd_mode"`、`qcom,mdss-dsi-bpp = <30>` |
| 面板时钟 | — | 1.1 GHz | `qcom,mdss-dsi-pll-clockrate` |
| splash buffer | `0xb8000000`，45 MB | `0xb8000000`，`0x2b00000`（43 MB） | `cape-sde-display.dtsi`：`splash_memory: splash_region { reg = <0x0 0xb8000000 0x0 0x02b00000>; label = "cont_splash_region"; }` |
| DSI 控制器 | `0x0ae94000` | 同（SoC 级） | shim `disp_bring_up()`；`mdss` `0x0ae00000`、`disp_cc` `0x0af00000` |
| 面板 reset | — | TLMM GPIO 0 | `diting-sde-display-mtp.dtsi` |
| 面板供电 | — | vddio = pm8350c L12C 1.8 V；vddi/vddd 1.2 V 经 TLMM 66；vci = L13C 3.0 V | 同上 `vddio-supply` / `vddd-supply` / `vci-supply` |
| 面板节点 | m11a | `dsi_l12_42_02_0a_dsc_cmd`（备用 `dsi_l12_36_02_0b_dsc_cmd`） | `diting-sde-display-mtp.dtsi` 的 `qcom,dsi-display-list` |

注意：`cape-sde-display.dtsi` 里还有一个 `qcom,dsi-default-panel =
<&dsi_r66451_amoled_video>`（1080x2400）。那不是零售 diting 的面板，忽略。

## 触摸

| 项 | K60 | diting | diting 出处 |
|---|---|---|---|
| 总线 | GENI SPI SE4 | 同：`spi@990000`，`0x4000` 窗口，IRQ 605 | `diwali-qupv3.dtsi`、`diting-sm8475.dtsi` |
| 片选/时钟/数据 | GPIO 16-19（qup4） | 同 | `diting-pinctrl.dtsi` |
| reset / irq | GPIO 20 / 21 | 同（reset 低有效，irq flags `0x2008`） | `diting-sm8475.dtsi` 的 `xiaomi_touch@0` |
| avdd | GPIO 71 | 同 | 同上 `avdd-gpio` |
| iovdd | pm8350c LDO2 1.8 V | 同（`L2C: pm8350c_l2`，HPM） | `cape-regulators.dtsi` |
| 坐标上限 | 14400 x 32000 | 12200 x 27120 | `goodix,panel-max-x/y`（0x2fa8 / 0x69f0），单位 0.1 mm |
| 固件 | m11a | `goodix_firmware_l12` / `goodix_cfg_group_l12` | 同上 |
| 型号 | 未确认 | 未确认（属性同时带 goodix 与 fts） | `xiaomi,l12-spi`，驱动里两套属性并存 |

## 按键

| 键 | K60 | diting |
|---|---|---|
| vol_up | `pm8350_gpios 6`，低有效 | 同（`cape-mtp.dtsi` `gpio-keys`） |
| power / vol_down | PM8350 PON，SID 0，periph 0x13，bit 7 / bit 6 | 同（同一代 PMIC） |

## 内存

K60 的 p0 窗口与 diting 的 reserved memory 完全兼容，notch 表一字未改：

| 区间 | 用途 |
|---|---|
| `0xa6b80000 + 0x2c0000` | xbl_ramdump + xbl_sc |
| `0xa6f00000 + 0x100000` | global_sync |
| `0xa7000000 + 0x400000` | ramoops（console 2 MiB + pmsg 2 MiB） |
| `0xb8000000 + 0x1200000` | panel（notch 按 K60 的 18 MiB，比 splash 的 43 MiB 早结束，避免 notch 切在扫描区中间） |
| `0xe0000000 + 0xb00000` | qheebsp + cpusys_vm + hyp_reserved（gunyah） |

ADT 的 DRAM 窗口仍是 `0xb4000000`，大小 `window - 1 MiB`，顶部 1 MiB 是
embedded panic log（`0xdff00000`）。**pending**：diting 的 DRAM bank 是否真的
覆盖这一段，必须用 live FDT 的 `/memory` 验证。

## 身份

| 项 | 值 | 说明 |
|---|---|---|
| compatible | `D22AP`、`iPhone10,3`、`xiaomi,diting`、`xiaomi,sweet`、`qcom,sm8475`、`AppleARM` | `xiaomi,sweet` 必须留：`pexpert` 的 `SweetPE` 按 `IONameMatch` 匹配（0016） |
| chip-id | `0x8095` | SM8475 |
| ECID | `0x5357454554000002` | 合成值，不来自设备 |
| Wi-Fi MAC | `52:54:00:12:34:56` | 合成；缺 `/chosen/mac-address-wifi0` 时 lsd 会卡死 |
| 机型字符串 | `iPhone10,3` / D22AP | 沿用 K60，iOS 用户态才认 |

## 命名约定

补丁文件名、`K60_TOUCH_*` 宏、日志前缀 `K60 TOUCH:` 都沿用 K60。这是有意的：
这条序列就是“K60 bring-up 套到 diting 上”，改名只会让 diff 和日志判据对不上。
上机 grep 时按 `SWEET FB:` / `K60 TOUCH:` 搜。
