# Leeco-Serial 串口工具优化计划

## Context

当前工具是单文件 [main.py](file:///c:\Users\zhang\Desktop\Tools\serial_tool\main.py)（736 行）的 PyQt6 串口调试终端，存在以下问题：

- **性能**：串口接收在 UI 线程用 30ms QTimer + `read_all()` 轮询，高波特率下 UI 卡顿、丢数据；`QTextEdit` 无限增长导致内存与渲染性能持续劣化。
- **稳定性**：`parse_hex` 越界报错不明确；日志每包 `write+flush`；`reset_target` 用 `time.sleep` 阻塞 UI；串口关闭存在端口占用竞态。
- **结构**：单文件，UI/逻辑/样式/常量全混在一起。

用户已确认：性能优化 + 稳定性修复 + 功能增强 + 拆分为多模块。

## 模块结构（新）

```
main.py          入口：DPI 设置、QApplication、实例化主窗口（瘦身）
config.py        常量：APP_NAME、窗口尺寸、BAUD_RATES(扩充)、颜色、编码/校验位映射、资源路径
styles.py        GLOBAL_QSS + CLICK_COMBO_QSS 样式字符串
utils.py         纯函数：parse_hex(增强) 、crc16_modbus、hex_str
serial_worker.py SerialWorker(QThread)：串口打开/关闭/读写/DTR/RTS/复位命令
main_window.py   ClickComboBox + LecoSerial(QMainWindow) 主窗口全部 UI 与编排
```

## 核心设计

### 1. SerialWorker（serial_worker.py）

`class SerialWorker(QThread)`，**不用 moveToThread**（阻塞 read 不能跑在 Qt 事件循环，会饿死事件循环）。信号与接口：

```
信号：
  data_received = pyqtSignal(list)   # list of (bytes, float)，包+到达时间戳，保留包边界
  connected     = pyqtSignal(str)    # "port @ baud"
  disconnected  = pyqtSignal(str)    # 关闭原因
  error         = pyqtSignal(str)    # open 失败 / 读异常（告警去重）

方法（UI 线程调用，全部非阻塞入队）：
  open(port, baud, bits, stop, parity)   # 记录参数并 self.start()
  write(data)                            # 入队 ("write", data)
  set_dtr(on) / set_rts(on)              # 入队 ("dtr"/"rts", bool)
  stop(timeout=2.0)                      # _stop.set() + cancel_read() + wait()
```

`run()` 主循环：
1. **打开阶段**（后台 5 次重试，替代原 `open_port` 的 UI 阻塞重试）：`serial.Serial(port, baud, ..., timeout=0.05, write_timeout=2.0, dsrdtr=False)` → 失败 `emit error` 返回。成功后 reset 缓冲区、`emit connected`。
2. **接收循环**：先 drain 命令队列（write/dtr/rts 及时执行，阻塞写发生在 worker 不卡 UI），再 `ser.read(4096)`（timeout=0.05）。读到数据进入 `bytearray` 聚合：≥4KB 或 ≥50ms 时 `emit data_received(packets)`。读异常 `SerialException` 时**告警去重、继续循环不 break**（保留"拔出不断开"的既有语义，避免 ESP32 复位误判）。
3. **finally**：close 串口（此处端口才释放）→ `emit disconnected`。

**关闭竞态协议**：UI 调 `stop()` → `_stop.set()` + `ser.cancel_read()` 唤醒阻塞读 → `wait(2000)`；run 的 finally 必释放端口，旧端口的 `time.sleep(0.3)` 可删除。

**写路径**：`do_send_text`/`send_file` 只调 `worker.write(data)`；send_file 整包入队，删除原 4096 分块 + `processEvents()` 循环。

### 2. 接收渲染（main_window.py）

- `recv_box`：`QTextEdit` → **`QPlainTextEdit` + `setMaximumBlockCount(10000)`**（Qt 日志窗口官方推荐，超限自动丢头部）。现有 `charFormat` 前景色、时间戳、换行枚举、placeholder、`toPlainText()`、`clear()` 全部兼容（QPlainTextEdit 同样支持 QTextCursor API）。
- UI 槽 `on_data_received(packets)`：累加 rx_count（状态刷新并入 rate_timer，不再每包 setText）→ 逐包 `format_received`（HEX/按行断行/line_buf 逻辑原样迁移，时间戳改用 worker 传的到达时间）→ 非暂停时 `append_recv_batch(texts)` 一次 cursor 批量插入 → 非空文本写入日志缓冲。
- 暂停/HEX/时间戳/自动清除逻辑与现状顺序保持一致。

### 3. 关键稳定性修复

- **parse_hex 增强**（utils.py）：逐元素校验，返回 `(bytes|None, err_msg|None)`，明确报"第 N 个元素 'xx' 不是合法十六进制 / 超出单字节范围 (00-FF)"。
- **日志批量 flush**：`self._log_buf` 累积，rate_timer(1s) 批量写入 + flush；关闭日志/save_log/closeEvent 时强制 flush。
- **reset_target QTimer 分步**：`set_rts(True) → 50ms → set_rts(False) → 150ms → set_rts(True)`，每步入队命令，UI 零阻塞；close 时 `_reset_timer.stop()`。
- **DTR/RTS/复位/write 全部收敛到 worker**，UI 线程零接触 ser 对象。
- 删除死代码 `_allow_autoreconnect`。

### 4. 功能增强

- 波特率列表追加 `460800`、`921600`。
- 状态栏连接时长：connected 时记录 t0，rate_timer 刷新 `lab_conn`（"已连接 HH:MM:SS"）。
- **Ctrl+F 查找**：显示/隐藏内嵌查找条，`recv_box.find()` 高亮，未命中回到顶部循环查找，Esc 关闭。
- **Ctrl+L 清空**接收区。

## 函数迁移映射（main.py → 新模块）

| 原位置 | 内容 | 去向 |
|---|---|---|
| 13-26 | 常量、resource_path | config.py |
| 30-44 | ClickComboBox | main_window.py |
| 48-145 | `__init__` | main_window.py（删 ser/recv_timer，改为创建 SerialWorker + 连信号） |
| 89-111 | 全局样式 | styles.py |
| 150-301 | `_build_ui/_build_menu/_build_statusbar/_build_shortcuts` | main_window.py（recv_box 改 QPlainTextEdit、新增查找条/时长 label/快捷键） |
| 306-328 | restyle_link_btn / close_serial | main_window.py（close_serial 重写为编排 worker.stop()） |
| 330-361 | open_port | main_window.py（改为 worker.open() 后台打开，connected 信号槽做 UI 更新） |
| 363-385 | switch_serial | main_window.py（连接状态判断改 worker.isRunning()） |
| 388-411 | check_ports / refresh_port_list | main_window.py |
| 416-464 | read_data / format_received / append_recv_text | 拆解：worker.run()（读+聚合）+ on_data_received / append_recv_batch（格式化+渲染），单行 append_recv_text 保留供 TX 回显 |
| 466-468 | encoding property | main_window.py（用 config.ENCODING_MAP） |
| 473-490 | parse_hex / crc16_modbus | utils.py |
| 492-569 | do_send_text / send_preset / add_preset / add_history / on_history_changed / send_file | main_window.py（write 走 worker；send_file 整包入队） |
| 574-588 | reset_target | main_window.py（QTimer 分步） |
| 590-635 | 定时发送 / update_rate / update_status / clear_count / on_auto_wrap / on_dtr_toggled / on_rts_toggled | main_window.py（DTR/RTS 走 worker；状态栏加时长） |
| 636-666 | on_log_toggled / save_log / show_about | main_window.py（日志经缓冲） |
| 671-729 | load_settings / save_settings / closeEvent | main_window.py（closeEvent：停 timer → worker.stop() → 日志 flush → save_settings） |

## 实施顺序

1. **物理拆分**（纯搬运，零行为变化）：config.py / styles.py / utils.py / main_window.py，main.py 瘦身为入口。验证：功能与重构前一致。
2. **线程化**：serial_worker.py + 改造连接/关闭/读写/DTR/RTS/复位。验证：回环收发、快速开关、拔线不崩。
3. **渲染优化**：QPlainTextEdit + setMaximumBlockCount + 聚合信号 + 批量渲染。验证：高波特率压力、内存平稳、颜色/时间戳对照。
4. **稳定性修复**：parse_hex 增强、日志批量 flush、reset_target 分步。
5. **功能增强**：波特率扩充、连接时长、Ctrl+F、Ctrl+L。

## 验证

- `python main.py` 启动；用虚拟串口对（com0com）或真实 USB 串口回环验证收发。
- 高波特率（921600）压力测试：UI 不卡、不丢数据；任务管理器观察内存平稳（行数上限生效）。
- 组合矩阵回归：{暂停×HEX×时间戳×按行} 开关组合。
- 快速开关串口 20 次：无残留进程、端口正常释放；拔线：提示一次、不崩溃、可重连。
- 功能逐项核对：定时发送、发送文件、CRC16、预设、历史、日志文件完整性、设置持久化。
