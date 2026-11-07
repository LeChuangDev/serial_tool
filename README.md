<div align="center">

<img src="assets/icon.png" width="120" alt="Leeco-Serial"/>

# Leeco-Serial 樂創串口调试终端

**基于 PyQt6 的跨平台串口调试助手** —— 终端收发 + VOFA+ 风格实时绘图，面向嵌入式调试（ESP32 / STM32 / 各类串口模块）的日常使用。

A cross-platform serial terminal built with PyQt6 — text I/O + VOFA+-style real-time plotting.

<br/>

![Version](https://img.shields.io/badge/version-3.5-2f6bff)
![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![PyQt6](https://img.shields.io/badge/PyQt6-6.5%2B-41cd52)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey)
![Dependencies](https://img.shields.io/badge/deps-PyQt6%20%7C%20pyserial-9f7ede)
![Tests](https://img.shields.io/badge/tests-326%20passed-2ea043)
![License](https://img.shields.io/badge/license-MIT-blue)

<br/>

[特性速览](#✨-特性速览) · [截图](#📸-截图) · [快速开始](#🚀-快速开始) · [下载安装](#📦-下载安装) · [打包与发布](#🛠️-打包与发布) · [详细功能](#🎯-详细功能) · [设计要点](#🧠-设计要点) · [开发与测试](#🧪-开发与测试) · [项目结构](#📁-项目结构) · [架构约定](#📐-架构约定) · [快捷键](#⌨️-快捷键) · [日志说明](#📝-日志说明) · [已知限制](#⚠️-已知限制) · [路线图](#🗺️-路线图) · [许可证](#📄-许可证)

<!-- 发到 GitHub 后取消注释并替换用户名/仓库名，即可显示 CI 徽章
[![Build](https://github.com/你的用户名/serial_tool/actions/workflows/release.yml/badge.svg)](https://github.com/你的用户名/serial_tool/actions/workflows/release.yml)
-->

</div>

---

## ✨ 特性速览

| 类别 | 能力 |
| :--- | :--- |
| 🔌 **稳定连接** | 掉线**自动重连**：按 VID:PID / 序列号「设备身份」找回（换 COM 号也能连回），指数退避 + 次数上限 |
| 🔀 **双模式** | 终端 / 绘图**一键切换，不重连串口** |
| 📈 **实时绘图** | VOFA+ 兼容（JustFloat / FireWater），波形 / FFT 频谱 / 直方图 / 游标测量 / 通道统计 |
| 🎚️ **通道标定** | 重命名 / 线条颜色 / 比例偏置 / 小数位，标定值贯穿曲线、游标、统计与 CSV 导出 |
| 📨 **可靠收发** | 增量解码（UTF-8 / GBK 跨包不乱码）、文件发送限速 + 进度 + 取消、写队列背压 |
| 🐞 **调试细节** | 三种时间戳（时钟 / 相对 / 包间隔增量）、关键字高亮、端口参数记忆、发送 BREAK |
| 💾 **内存有界** | 示波器每通道 20 万点仅约 26MB，导出流式写出，长时间挂机内存不涨 |
| 🪶 **零重量依赖** | 仅 PyQt6 + pyserial，FFT / 统计全部纯 Python 实现，无需 numpy |

## 📸 截图

> 💡 把「终端模式」与「绘图模式」两张图放到 `docs/screenshots/` 后，取消下面注释即可展示。

<!--
![终端模式](docs/screenshots/terminal.png)
![绘图模式](docs/screenshots/plot.png)
-->

## 🚀 快速开始

```bash
git clone https://github.com/你的用户名/serial_tool.git
cd serial_tool
pip install -r requirements.txt
python main.py
```

- 需 **Python 3.10+**（已在 3.14.6 / PyQt6 6.11 / pyserial 3.5 上验证）
- 运行依赖只有两个：`PyQt6`、`pyserial`

## 📦 下载安装

到 [Releases](../../releases) 页面下载对应平台的压缩包，**解压即用，无需安装 Python**：

| 平台 | 文件 | 使用方法 |
| :--- | :--- | :--- |
| Windows x64 | `LeecoSerial-vX.Y-win64.zip` | 解压后双击 `LeecoSerial.exe` |
| Linux x64 | `LeecoSerial-vX.Y-linux-x64.tar.gz` | 解压后 `chmod +x LeecoSerial && ./LeecoSerial` |

- CH340 等 USB 串口驱动见 [drivers/](drivers/)（Windows）；CP210x / FT232 请从厂商官网获取
- 打包版把**配置与日志写在 exe 所在目录**（`logs/` 等），移动 exe 即整体迁移
- 单文件模式首次启动需解压到临时目录，会慢 1~2 秒，属正常现象

## 🛠️ 打包与发布

### 本地打包

```bat
:: Windows（cmd / PowerShell）
build_windows.bat        :: 产出 dist\LeecoSerial.exe 与 dist\LeecoSerial-v3.5-win64.zip
```

```bash
# Linux
./build_linux.sh         # 产出 dist/LeecoSerial 与 dist/LeecoSerial-v3.5-linux-x64.tar.gz
```

### GitHub Actions 自动构建（推荐）

仓库已内置工作流 [.github/workflows/release.yml](.github/workflows/release.yml)：推送 `v*` 标签即自动在 Windows / Linux 两个 runner 上构建，并把压缩包挂到对应 Release。

```bash
git tag v3.5
git push origin main --tags
```

> ⚠️ PyInstaller 不支持交叉编译 —— Linux 产物必须在 x86_64 Linux 上构建（CI 已自动处理）。
> 改版本号时同步更新四处：`serialtool/config.py` 的 `APP_VERSION`、`serial_tool.spec`、两个打包脚本里的 `VERSION`、`pyproject.toml`。

## 🎯 详细功能

### 📨 收发

- ASCII / HEX 双模式收发，毫秒级时间戳（使用数据包真实到达时间）
- 增量解码：UTF-8 / GBK 多字节字符被包边界切开也不乱码
- 按包 / 按行两种断行模式，长行保护（缓冲区上限自动冲刷）
- 定时发送、预设命令库、发送历史、多条发送列表（逐条延迟 + 循环）
- 文件发送：分块流式 + 写队列背压，进度条、限速、取消
- 追加 Modbus CRC16、TX 回显、发送 BREAK

### 🔌 串口

- 全参数：波特率（含自定义 50~12000000）、数据位、停止位、校验位（含 Mark/Space）、流控（无 / RTS-CTS / XON-XOFF）
- 端口热插拔检测（后台线程枚举，不卡 UI）、自然排序（COM9 在 COM10 前）、蓝牙虚拟口过滤、下拉显示设备描述
- DTR / RTS 电平控制，ESP32 一键复位（RTS 低 150ms；控制队列优先于数据队列，UI 零阻塞）
- 断线自动重连（按 VID:PID / 序列号找回，指数退避 + 次数上限 + 手动接管）

### 👁️ 观测与留存

- 接收区实时字节波形、收发速率迷你图（60 秒）
- 配色 / 字号可调（Ctrl+滚轮）、Ctrl+F 查找、右键菜单
- 日志按日归档（跨天自动切换）、HEX 原始数据导出、接收区另存
- 配置导入 / 导出（JSON）、参数记忆、端口档案
- 结构化运行日志 `logs/app.log`（2MB × 4 滚动）+ 全局异常兜底（落盘 + 弹窗）

### 📈 绘图模式（左上角切换）

- 多通道滚动示波器：自动量程 / 手动固定 Y 轴、时间窗、图例 + 最新值 + 帧率叠加显示
- JustFloat（二进制）/ FireWater（CSV）两种协议，自动识别通道数（上限 16）
- min/max 抽稀渲染 + 30 FPS 节流（重载时 10~30 FPS 自适应降帧），每通道最多 20 万点（环形缓冲）
- **视图切换**：波形 / FFT 频谱 / 直方图；**游标测量**：悬停显示最近采样点时间与各通道值
- **通道统计**：min / max / 均值 / RMS 实时刷新
- **通道数据解析**：重命名 / 线条颜色 / 比例偏置（标定）/ 小数位，配置持久化、重启不丢
- **CSV 导出**：导出标定后的值（与曲线所见一致），流式写出不占内存
- 暂停冻结曲线、协议切换 / 清空；两种模式切换不重连

## 🧠 设计要点

<details open>
<summary><b>自动重连：按「设备身份」而非端口名</b></summary>

USB 转串口拔插后 COM 号经常会变（COM3 → COM15），只按端口名重连在真机上几乎必然失败。读取持续失败 2s 判定链路中断 → 指数退避扫描 → 身份打分 → 重新打开：

| 匹配依据 | 分值 | 说明 |
| :--- | :--- | :--- |
| 端口名 + VID/PID 全同 | 100 | 同一设备、同一口 |
| 仅端口名相同（无 USB 信息） | 85~90 | 老式板载串口 |
| 序列号 + VID/PID 全同 | 80 | **换了 COM 号也能找回**（CP210x / FT232 有唯一序列号） |
| 仅 VID/PID 相同 | 60 | 廉价 CH340 常无序列号，退而求其次 |
| 描述完全一致 | 40 | 最后兜底 |

退避：首次冷却 1.2s（等 USB 重新枚举），之后 0.8s → 1.6s → 3.2s → … 封顶 15s，默认最多 10 次（`config.RECONNECT_MAX_ATTEMPTS`，设 0 为无限），每次等待带 ±20% 抖动，避免多开时同时抢口。

> 💡 设计上的两个刻意选择：
> - **重连期间不弹模态框**：失败只写进接收区并继续退避，避免用户离开工位时被对话框卡死。
> - **区分「掉线」与「用户主动断开」**：只有非用户操作的断开才触发重连。

</details>

<details open>
<summary><b>JustFloat / FireWater 协议</b></summary>

| 协议 | 格式 | 适用 |
| :--- | :--- | :--- |
| JustFloat | 小端 float32 数组，`+inf`（`00 00 80 7f`）作帧尾 | 通道多、频率高、省带宽 |
| FireWater | CSV 文本 `[prefix:]ch0,ch1,...\n` | 通道少、直观、像 printf |

相比 VOFA+ 官方实现刻意强化的四处（见 `scope.py` / `plot_protocol.py` / `analysis.py` 注释）：

1. **防卡死**：未闭合的浮点列表有硬上限（4096），超限丢最旧，**无帧尾也绝不打爆内存**。
2. **解析走 C**：`struct.unpack` 一次把整段按 float 解出，不逐字节 Python 循环。
3. **抽稀 + 节流**：按像素列聚合 min/max 包络（`decimate`，复杂度 O(像素宽) 与采样数解耦），固定 30 FPS 重绘。
4. **零依赖分析**：FFT / 直方图 / 统计用纯 Python 实现（迭代 radix-2），无需 numpy / scipy。

</details>

<details>
<summary><b>调试向的新能力</b></summary>

| 能力 | 为什么加 |
| :--- | :--- |
| 时间戳三模式（时钟 / 相对连接时间 / **包间隔增量**） | 排查轮询周期、超时重发、卡顿定位，`+50ms` 比绝对时间有用得多 |
| 关键字高亮（字面量 / 正则，可编辑，JSON 持久化） | `error`、`ready`、`reset` 一眼可见 |
| 会话卡片显示 VID:PID / 序列号 / 厂商 | 插着三块 CH340 时，靠这几行才能确认自己在调哪块板 |
| 行尾选择（无 / `\r\n` / `\n` / `\r`） | 二进制协议必须能选「无」 |
| 端口参数记忆 | 每个 COM 口单独记住波特率 / 校验 / 流控（类似 PuTTY 会话档案） |
| 连接后自动记录日志 | 无人值守跑长时间测试时，不会忘记开日志 |
| 发送 BREAK（TX 中断 250ms） | 部分 MCU 的 ISP / 调试模式入口 |
| 文件发送限速（分块间隔 ms） | 慢速 MCU 全速灌入会丢字节 |
| 统计卡（收 / 发 / 帧数 / 错误 / 峰值 / 时长） | 读异常、写失败次数直接可见 |
| 发送框 ↑/↓ 翻历史 | 调试时反复发同几条指令 |

</details>

## 🧪 开发与测试

```bash
pip install -r requirements-dev.txt
python -m ruff check .        # 静态检查（E/F/I/UP/B/C4/SIM/N/RET）
python -m ruff format .       # 统一格式
python -m pytest              # 326 个用例，offscreen 运行，无需硬件
```

测试分层：

| 层次 | 文件 | 覆盖 |
| :--- | :--- | :--- |
| 纯逻辑 | `test_utils` `test_recv_pipeline` `test_timestamps` `test_highlight` `test_plot_protocol` `test_scope` `test_analysis` `test_series_store` `test_raw_buffer` | HEX/CRC、增量解码、三种时间戳、高亮、JustFloat/FireWater 解码、示波器抽稀、通道标定、FFT/直方图/统计、分块采样存储、分块原始字节环形缓冲 |
| 状态机 | `test_reconnect` `test_ports` | 退避时序（假时钟）、设备身份打分 |
| 线程 | `test_serial_worker` `test_worker_loopback` | 打开失败、停止语义、聚合收包、链路中断判定 |
| 集成 | `test_main_window` `test_auto_reconnect_e2e` `test_settings_store` | 渲染链路、暂停、行尾、端口档案、配置往返、掉线换 COM 号自动连回、双模式切换、CSV 导出 |
| 版式 | `test_main_window.TestLayoutAudit` | 默认 / 最小 / 绘图三种场景下无控件被裁剪 |

> 🧪 假 `serial.Serial` 替身让「拔线 → 重连」这类时序也能在 CI 里断言，不依赖真实硬件。

## 📁 项目结构

```
serial_tool/
├── main.py                      # 入口：日志 → 依赖检查 → DPI → 主窗口 → 异常兜底
├── serial_tool.spec             # PyInstaller 打包配置（Windows / Linux 通用）
├── build_windows.bat            # Windows 一键打包脚本
├── build_linux.sh               # Linux 一键打包脚本
├── .github/workflows/           # 推送 v* 标签自动构建双平台并发布 Release
├── serialtool/
│   ├── config.py                # 常量与映射表（颜色 / 参数 / 路径 / 主题 / 重连策略）
│   ├── main_window.py           # 主窗口：编排 + 状态机（不含布局细节）
│   ├── panels.py                # 声明式面板（连接栏 / 会话 / 显示 / 统计 / 接收 / 发送页签）
│   ├── ports.py                 # 串口枚举、设备身份（VID:PID/SN）匹配、扫描线程
│   ├── reconnect.py             # 重连时序状态机（纯逻辑，可注入时钟）
│   ├── serial_worker.py         # 串口线程（读 / 写 / 信号线 / BREAK / 链路中断判定）
│   ├── recv_pipeline.py         # 接收格式化管线（解码 / 断行 / HEX）
│   ├── timestamps.py            # 时间戳三模式
│   ├── highlight.py             # 关键字高亮规则（纯逻辑）
│   ├── plot_protocol.py         # JustFloat / FireWater 协议解码（纯逻辑）
│   ├── analysis.py              # FFT / 直方图 / 通道统计（纯逻辑，无 numpy）
│   ├── scope.py                 # 多通道滚动示波器（抽稀 + 节流 + 频谱 / 直方图 / 游标）
│   ├── series_store.py          # 分块浮点序列（array('d')，8 字节 / 点）
│   ├── raw_buffer.py            # 分块字节环形缓冲（HEX 导出用，O(1) 丢弃，上限 8MB）
│   ├── plot_controller.py       # 绘图模式编排（以 mixin 混入主窗口）
│   ├── rules_dialog.py          # 高亮规则编辑器
│   ├── settings_store.py        # QSettings 类型安全封装
│   ├── widgets.py               # 自绘控件（速率图 / 波形 / 接收区 / 下拉框）
│   ├── logging_setup.py         # 日志与全局异常钩子
│   ├── styles.py                # 全局 QSS
│   ├── tools_dialog.py          # 校验和 / HEX-ASCII 转换工具窗
│   └── utils.py                 # 纯函数（HEX / CRC / 格式化 / 波特率校验）
├── tests/                       # pytest 测试套件
├── assets/ drivers/ docs/ logs/
└── serial-tool-redesign/        # 界面设计稿
```

## 📐 架构约定

1. **UI 线程不碰 `serial.Serial`**：写数据、DTR/RTS、BREAK 一律经命令队列交给工作线程。
2. **可测逻辑与 Qt 解耦**：`ports` / `reconnect` / `highlight` / `timestamps` / `recv_pipeline` / `plot_protocol` / `analysis` 均为纯逻辑模块。
3. **配置只经 `SettingsStore` 读写**：避免 QSettings 类型漂移（注册表可能读回字符串）。
4. **错误分级**：`open_failed`（需弹窗）、`link_lost`（触发重连）、`error`（写接收区）三条通道分开。
5. **静默失败为零**：所有异常至少落一条日志。
6. **重连不阻塞 UI**：状态机只输出「到点 / 未到点」，扫描在后台线程，打开在串口线程。
7. **测试不碰真实配置**：`tests/conftest.py` 把 `QSettings` 重定向到临时 INI 目录，每个用例前清空。

## ⌨️ 快捷键

| 快捷键 | 功能 |
| :--- | :--- |
| Ctrl+Enter / Ctrl+Return | 发送输入区内容 |
| Ctrl+Shift+1 / Ctrl+Shift+2 | 切换终端 / 绘图模式 |
| Ctrl+D | 连接 / 断开 |
| Ctrl+R | 立即重连一次 |
| Ctrl+F | 查找（Esc 关闭） |
| Ctrl+L | 清空接收区 |
| Ctrl+S | 保存接收日志 |
| F5 | 刷新端口列表 |
| ↑ / ↓ | 在发送框内浏览历史 |
| Ctrl+滚轮 | 接收区字号缩放 |

## 📝 日志说明

- `logs/app.log`：程序运行日志（错误、连接、重连事件），滚动归档。
- `logs/serial_YYYYMMDD.log`：勾选「记录到文件」或「连接后自动记录」后写入的接收数据，带绝对时间戳，跨天自动切换。
- 菜单「文件 → 保存接收日志」另存当前显示内容；「导出接收数据(HEX)」导出原始字节。
- 打包版（exe）的 `logs/` 位于 exe 所在目录。

## ⚠️ 已知限制

- 重连基于 USB 身份：**没有序列号的 CH340 换口后只能按 VID/PID 匹配**，同时插两块同型号时可能选到另一块。
- 重连依赖 `serial.read` 抛异常来判定掉线（2s 阈值）。个别虚拟串口驱动拔出后不抛异常，此时只能靠 5s 一次的端口扫描发现「端口消失」，重连会慢 5 秒左右。
- 大文件发送进度反映「已入队字节」，不是「设备已收字节」；对端无流控时真实到达时间会略滞后。
- 原始字节缓冲（HEX 导出用）默认保留最近 **8MB**，可在配置里把 `rx_raw_cap_mb` 调小、设 `0` 即完全不留存；接收区显示上限 10000 行（超出丢最旧，这是有意的内存上限）。
- 绘图 Y 轴只有**单一全局量程**（自动或手动固定），未做每通道独立 Y 轴 / 双 Y 轴 / 对数坐标；每通道的比例 / 偏置是数据标定，不是独立坐标轴。
- FFT 为相对幅值谱（去直流 + 汉宁窗），未做绝对物理标定。
- 波形绘制成本 ≈ 通道数 × 像素列数，自适应降帧会保证界面可操作；通道数 × 采样率控制在 8 × 1kHz 以内最流畅。
- 未在真实硬件上做 921600 长时间压测与快速插拔循环压测。

## 🗺️ 路线图

**P0：调试闭环**

1. Modbus RTU 组包 / 解析（填从站地址 / 功能码 / 寄存器，自动算 CRC 并解析响应）
2. 脚本 / 宏（expect 式：发送 → 等匹配 → 再发送，可存成文件回放）
3. 发送快捷键绑定（F1~F8）

**P1：能力边界扩展**

4. TCP/UDP 调试（串口 + 网络同窗）
5. 绘图增强：双游标 / 单通道放大 / 多 Y 轴 / 对数坐标 / 通道可见性开关
6. 多串口同时打开

**P2：工程化补充**

7. 接收侧环形缓冲落盘（长时间挂机采集把原始流写文件，而不只留内存 8MB）
8. 协议插件机制（帧解析做成可加载模块）

**已完成**

- [x] v3.5：PyInstaller 单文件打包 + Windows 版本信息 + 图标 + GitHub Actions 自动发布

## 📄 许可证

[MIT](LICENSE)
