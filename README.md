# Leeco-Serial 樂創串口调试终端

基于 PyQt6 开发的跨平台串口调试助手，深蓝工业风界面，功能全面。

## 功能特性
- 串口热插拔自动检测，全参数配置（波特率/数据位/停止位/校验位）
- ASCII / HEX 双模式收发，支持毫秒级时间戳
- 定时发送、预设命令库、历史发送记录、多条发送列表（支持循环）
- CRC16 校验自动追加、文件分块发送、日志按日归档、HEX 导出
- DTR/RTS 电平控制，支持 ESP32 一键复位
- UTF-8 / GBK / ASCII 多编码切换（增量解码，跨包不乱码）
- 实时波形与收发速率图，参数自动持久化记忆

## 运行环境
- Python 3.8 及以上
- 依赖库：PyQt6、pyserial
- 驱动：CH340 等 USB 串口驱动安装包见 `drivers/`

## 快速运行
1. 安装依赖
```bash
pip install PyQt6 pyserial
```
2. 在项目根目录运行
```bash
python main.py
```

## 目录结构
```
serial_tool/
├── main.py              # 程序入口
├── serialtool/          # 应用源码包
│   ├── config.py        # 全局配置（颜色/参数/路径）
│   ├── main_window.py   # 主窗口与全部交互逻辑
│   ├── serial_worker.py # 串口后台线程（读/写/信号线）
│   ├── styles.py        # 全局 QSS 样式
│   ├── tools_dialog.py  # 校验和 / HEX 转换工具窗口
│   └── utils.py         # 纯函数工具（HEX 解析 / CRC / 校验和）
├── assets/              # 图标等静态资源
├── drivers/             # USB 串口驱动安装包
├── docs/                # 优化方案与设计说明
├── serial-tool-redesign/ # 界面设计稿（.design 工程 + HTML 原型）
└── logs/                # 运行期日志（按日期命名，自动生成）
```

## 日志说明
开启「记录到文件」后，接收数据按日期写入 `logs/serial_YYYYMMDD.log`，
跨天自动切换新文件；可随时用菜单「文件 → 保存接收日志」另存当前显示内容。
