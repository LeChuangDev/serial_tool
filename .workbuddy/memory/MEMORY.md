# 项目长期记忆：Leeco-Serial 樂創串口调试终端

## 定位
面向嵌入式调试（ESP32 / STM32 / 串口模块）的桌面串口工具，PyQt6 + pyserial，深蓝工业风。
对标对象：SSCOM / XCOM（功能全）、Tera Term（脚本化）、YAT（多口）、Termite（轻量）。

## 运行与验证（不要猜命令）
- 解释器：`py -3.14`（Python 3.14.6 / PyQt6 6.11 / pyserial 3.5）。系统 `python` 是 3.13 且无依赖。
- 测试：`py -3.14 -m pytest tests -q`（当前 211 用例，offscreen，无需硬件）
- 检查：`py -3.14 -m ruff check .` / `py -3.14 -m ruff format --check .`（line-length 100）

## 架构铁律
1. UI 线程绝不碰 `serial.Serial`：写/DTR/RTS/BREAK 一律经命令队列到工作线程。
2. 可测逻辑与 Qt 解耦：`ports` / `reconnect` / `highlight` / `timestamps` / `recv_pipeline` 不 import 控件。
3. 配置只经 `SettingsStore`（QSettings 在注册表会读回字符串）。
4. 错误三分：`open_failed`（弹窗）/ `link_lost`（触发重连）/ `error`（写接收区）。
5. 布局用 `panels.py` 声明式构建 + `_bind_panel()` 挂同名属性；`main_window.py` 只写编排。
6. 测试隔离：`tests/conftest.py` 把 QSettings 重定向到临时 INI 并在每个用例前清空。

## 已踩过的坑（回退前先看）
- `TS_MODE_LABELS` 必须与 `TIMESTAMP_MODES` 成对维护，否则恢复配置永远落回默认。
- `_combo()` 只能用 `setMinimumWidth`，不能用固定宽度（长选项会被省略号截断）。
- 窗口最小尺寸用 `max(常量, minimumSizeHint())`，写死会随字体失效。
- 本机 offscreen 环境 `QFontDatabase.families()` 为 0（无字体），截图是豆腐块 → 版式验证用
  `TestLayoutAudit`（sizeHint vs 实际尺寸），不要靠截图目视。

## 已知限制
- 无序列号 CH340 换口后只能按 VID:PID 匹配，双插同型号可能认错板子。
- 虚拟串口驱动拔出不一定抛异常，此时靠 5s 一次端口扫描兜底。
