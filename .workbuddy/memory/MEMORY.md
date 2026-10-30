# 项目长期记忆：Leeco-Serial 樂創串口调试终端

## 定位
面向嵌入式调试（ESP32 / STM32 / 串口模块）的桌面串口工具，PyQt6 + pyserial，深蓝工业风。
对标对象：SSCOM / XCOM（功能全）、Tera Term（脚本化）、YAT（多口）、Termite（轻量）。

## 运行与验证（不要猜命令）
- 解释器：`py -3.14`（Python 3.14.6 / PyQt6 6.11 / pyserial 3.5）。系统 `python` 是 3.13 且无依赖。
- 测试：`py -3.14 -m pytest tests -q`（当前 295 用例，约 2m10s，offscreen，无需硬件；
  工具默认 120s 超时会杀掉整套，要显式加大 timeout）
- 检查：`py -3.14 -m ruff check .` / `py -3.14 -m ruff format --check .`（line-length 100）

## 架构铁律
1. UI 线程绝不碰 `serial.Serial`：写/DTR/RTS/BREAK 一律经命令队列到工作线程。
2. 可测逻辑与 Qt 解耦：`ports` / `reconnect` / `highlight` / `timestamps` / `recv_pipeline` /
   `plot_protocol` / `analysis`（FFT/直方图/统计）/ `scope`（抽稀 + `transform_value` + `ChannelMeta`）
   不 import 控件或仅弱依赖。
3. 配置只经 `SettingsStore`（QSettings 在注册表会读回字符串）。
4. 错误三分：`open_failed`（弹窗）/ `link_lost`（触发重连）/ `error`（写接收区）。
5. 布局用 `panels.py` 声明式构建 + `_bind_panel()` 挂同名属性；`main_window.py` 只写编排。
6. 测试隔离：`tests/conftest.py` 把 QSettings 重定向到临时 INI 并在每个用例前清空。
7. 双模式（终端/绘图）用 `QStackedWidget`（第 0 页 main_split，第 1 页 plot_panel），
   模式切换走 `switch_mode()`，同一串口会话不重连。
8. 绘图编排在 `plot_controller.py`（`PlotController` mixin，混入 `LecoSerial`），
   信号绑定收在 `bind_plot_signals()`；`main_window.py` 只留连接/收发/文件/多条/日志/配置。
9. 绘制成本必须"有预算"：分析结果按 `_revision` 缓存、点数超列数时等距抽样、
   线段数超 `SCOPE_MAX_LINE_SEGMENTS` 时并列、按实测耗时在 10~30FPS 自适应降帧、
   数据无变化时 `_tick()` 直接不重绘。改动绘制路径要跑 `TestRenderBudget`。

## 已踩过的坑（回退前先看）
- `TS_MODE_LABELS` 必须与 `TIMESTAMP_MODES` 成对维护，否则恢复配置永远落回默认。
- `_combo()` 只能用 `setMinimumWidth`，不能用固定宽度（长选项会被省略号截断）。
- 窗口最小尺寸用 `max(常量, minimumSizeHint())`，写死会随字体失效；`_MIN_HEIGHT` 当前 760，
  需容纳"模式切换条 + 连接栏 + 左侧三卡片"。
- **offscreen 复现真机字体问题**：本机 offscreen `QFontDatabase.families()` 为 0，导致版式审计
  "无裁剪"却与真机不符。真机有微软雅黑、文字更宽。**复现手法**：
  `QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")` 再跑 sizeHint vs 实际尺寸审计，
  就能在 offscreen 复现真机的"复选框被压扁重叠"。版式回归务必加载真实字体，别只看豆腐块截图。
  **顺序铁律**：必须先 `QApplication([])` 再 `addApplicationFont`，反过来**直接段错误**
  （进程静默崩、无任何输出）。
- **`setMinimumSize` 的时机**：必须在 `load_settings()` 之后（控件状态会改变 sizeHint），
  否则 `minimumSizeHint()` 被低估、左侧卡片被压缩。已抽成 `_apply_minimum_size()`。
- **`QCheckBox` 的 `minimumSizeHint` 不含文字**（Qt 行为）：布局压缩时复选框文字会被裁。
  用 `_checkbox()` 统一 `setMinimumHeight(20)` 兜底。
- `decimate` 的列映射：`int((t-t0)*scale)` 对负值向零截断会误入第 0 列，先判 `pos<0` 再 `int(pos)`。
- float32 经 `struct.pack`/`unpack` 有量化误差，测试别 `== 原始小数`。
- 绘图协议解码器（plot_protocol.py）必须给无帧尾流设硬上限，否则内存无界增长。
- FFT 测试断言用 `pytest.approx`；`_nearest_index` 对等距点返回后者（二分自然结果）。
- **QSS 会覆盖 item 级背景**：`QTableWidget::item{background-color:...}` 会让 `item.setBackground()`
  失效（颜色块不显示）。表格里的色块用 `setIcon(QIcon(QPixmap.fill(色)))` 兜底，别用 setBackground。
- 通道标定（比例/偏置/小数位/名称/颜色）放 `scope.ChannelMeta` + `transform_value` 纯函数，
  绘制/图例/游标/统计/FFT/直方图/CSV 导出全部走标定后值（`channel_values()` 快路径返回原 deque）。
  导出只经 `scope.export_rows()`，别碰 `_times/_series` 私有成员。
- `QLineF` 在 `PyQt6.QtCore`（不是 QtGui）。`_qcolor()` 返回共享 QColor，要改 alpha 先拷一份。
- 本机负载波动大：微基准必须**交替 A/B 多轮取最小值**，单次测量能差 2 倍，否则会误判优化无效。
- 面板 dataclass 加字段后，务必确认 `_bind_signals` 里接了信号 —— `btn_plot_pause` 曾建好却从未接线。

## 已知限制
- 无序列号 CH340 换口后只能按 VID:PID 匹配，双插同型号可能认错板子。
- 虚拟串口驱动拔出不一定抛异常，此时靠 5s 一次端口扫描兜底。
