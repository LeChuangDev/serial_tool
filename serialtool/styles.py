# ====================== 全局样式（深蓝工业风 · v2 新界面） ======================
# 设计令牌对齐 serial-tool-redesign（--srl-*）：
#   背景 #0b1018 / 卡片 #131a26 → #182130 / 主色 #2f6bff / 环 #4a8cff
#   描边 #232e3f / 次要 #1a2332 / 前景 #dde5f0 / 弱化 #7c8798
#   成功 #3fb950 / 错误 #ef4a4e / 图表 RX #2f6bff TX #e6a23c

# 点击即展开的下拉框样式
CLICK_COMBO_QSS = """
QComboBox{background-color:#1a2332;color:#dde5f0;border:1px solid #232e3f;
    border-radius:8px;padding:5px 8px;font-size:13px;selection-background-color:#2f6bff;}
QComboBox:hover{border-color:#4a8cff;}
QComboBox:on{border-color:#2f6bff;background-color:#182130;}
QComboBox:focus{border-color:#4a8cff;}
QComboBox::drop-down{border-width:0px;width:22px;}
QComboBox::down-arrow{border-left:4px solid transparent;
    border-right:4px solid transparent;border-top:5px solid #7c8798;margin-right:6px;}
QComboBox::down-arrow:on{border-top-color:#dde5f0;}
QComboBox QAbstractItemView{background-color:#182130;color:#dde5f0;
    border:1px solid #232e3f;border-radius:8px;padding:4px;
    selection-background-color:#2f6bff;outline:0;}
QComboBox QAbstractItemView::item{height:26px;border-radius:4px;padding:0 6px;}
QComboBox QAbstractItemView::item:hover{background-color:#1f2a3c;}
"""

# 卡片样式（QFrame#card）：渐变底板 + 顶部内高光 + 圆角，营造悬浮层次
CARD_QSS = """
QFrame#card{background-color:qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #131a26,stop:1 #182130);
    border:1px solid #232e3f;border-top:1px solid #394454;
    border-radius:12px;}
QLabel#cardTitle{color:#dde5f0;font-size:13px;font-weight:600;
    letter-spacing:1px;background:transparent;border-left:3px solid #2f6bff;
    padding-left:8px;}
"""

# 全局主窗口样式
GLOBAL_QSS = """
QMainWindow,QDialog{background-color:qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #0c1119,stop:1 #090c12);}
QWidget{color:#dde5f0;font-family:Microsoft YaHei;}

QTabWidget::pane{border:1px solid #232e3f;border-radius:8px;
    background-color:#131a26;top:-1px;}
QTabBar::tab{background:#1a2332;color:#7c8798;padding:6px 18px;
    border:1px solid #232e3f;border-bottom:none;
    border-top-left-radius:6px;border-top-right-radius:6px;margin-right:2px;}
QTabBar::tab:selected{background:#2f6bff;color:#fff;}
QTabBar::tab:hover:!selected{background:#1f2a3c;color:#dde5f0;}
QLabel{font-size:13px;background:transparent;}
QLabel#hint{color:#7c8798;font-size:12px;}
QLabel#sectionTitle{color:#dde5f0;font-size:13px;font-weight:600;
    letter-spacing:1px;border-left:3px solid #2f6bff;padding-left:8px;}
QLabel#dot{background-color:#4a5160;border:1px solid #5a6270;border-radius:4px;}
QLabel#dotGreen{background-color:#3fb950;border:1px solid #56d96a;border-radius:4px;}
QLabel#portChip{color:#7c8798;font-size:12px;font-family:Consolas,monospace;}
QLabel#brandTitle{color:#dde5f0;font-size:13px;font-weight:600;
    letter-spacing:1px;padding:0 16px 0 6px;background:transparent;}
QLabel#brandVer{color:#7c8798;font-size:12px;font-family:Consolas,monospace;
    padding:0 10px 0 0;background:transparent;}
QFrame#hsep{background-color:#1c2432;border:none;min-height:1px;max-height:1px;}

/* 状态栏连接状态 pill：圆点 + 文字一体胶囊 */
QFrame#pill{border:1px solid #232e3f;border-radius:12px;background-color:#1a2332;}
QFrame#pill QLabel{background:transparent;font-size:12px;color:#dde5f0;}

QPushButton{background-color:qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #232c3b,stop:1 #1a2332);
    border:1px solid #2a3547;border-radius:8px;color:#dde5f0;
    padding:7px 14px;font-size:13px;}
QPushButton:hover{border-color:#4a8cff;
    background-color:qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #27313f,stop:1 #1e2735);}
QPushButton:pressed{background-color:#141a25;border-color:#232e3f;}
QPushButton:disabled{color:#5a6270;background-color:#10151f;border-color:#1c2432;}
QPushButton#primary{background-color:qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #3b7bff,stop:1 #2f6bff);
    border:1px solid #4a8cff;font-weight:600;color:#fff;}
QPushButton#primary:hover{border-color:#6ba1ff;
    background-color:qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #4a8cff,stop:1 #3b7bff);}
QPushButton#primary:pressed{background-color:#1d55c8;border-color:#2f6bff;}
QPushButton#danger{background-color:qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #ef4a4e,stop:1 #d93b40);border:1px solid #f06a6e;color:#fff;}
QPushButton#danger:hover{border-color:#ff8a8d;}
QPushButton#small{padding:4px 10px;font-size:12px;border-radius:6px;}
QPushButton#small:checked{background-color:#2f6bff;border-color:#4a8cff;color:#fff;}
QPushButton#tool{background-color:transparent;border:1px solid #2a3547;
    padding:4px 10px;font-size:12px;}
QPushButton#tool:hover{border-color:#4a8cff;color:#fff;
    background-color:#1a2332;}
QPushButton#tool:checked{background-color:#2f6bff;border-color:#4a8cff;color:#fff;}

QLineEdit{background-color:#1a2332;
    border:1px solid #232e3f;border-radius:8px;color:#dde5f0;
    padding:5px 8px;font-size:13px;selection-background-color:#2f6bff;}
QPlainTextEdit,QTextEdit{background-color:#0b1018;
    border:1px solid #232e3f;border-radius:8px;color:#dde5f0;
    padding:6px 8px;font-size:13px;selection-background-color:#2f6bff;}
QLineEdit:hover,QPlainTextEdit:hover,QTextEdit:hover{border-color:#3d4a61;}
QLineEdit:focus,QPlainTextEdit:focus,QTextEdit:focus{border-color:#4a8cff;}
QLineEdit:disabled{color:#5a6270;background-color:#10151f;border-color:#1c2432;}

QCheckBox{spacing:6px;font-size:13px;}
QCheckBox::indicator{width:15px;height:15px;border:1px solid #3d4a61;
    border-radius:4px;background-color:#1a2332;}
QCheckBox::indicator:hover{border-color:#4a8cff;}
QCheckBox::indicator:checked{background-color:#2f6bff;border-color:#4a8cff;}
QRadioButton{spacing:6px;font-size:13px;}
QRadioButton::indicator{width:14px;height:14px;border:1px solid #3d4a61;
    border-radius:7px;background-color:#1a2332;}
QRadioButton::indicator:hover{border-color:#4a8cff;}
QRadioButton::indicator:checked{background-color:#2f6bff;
    border:4px solid #1a2332;}

QMenuBar{background:#0c1017;color:#dde5f0;padding:2px 6px;
    border-bottom:1px solid #1a2332;}
QMenuBar::item{padding:4px 10px;border-radius:4px;}
QMenuBar::item:selected{background:#2f6bff;}
QMenu{background:#182130;color:#dde5f0;border:1px solid #232e3f;
    border-radius:8px;padding:4px;}
QMenu::item{padding:5px 24px;border-radius:4px;}
QMenu::item:selected{background:#2f6bff;}
QMenu::item:disabled{color:#5a6270;}
QMenu::separator{height:1px;background:#232e3f;margin:4px 8px;}

QStatusBar{background:#0a0e14;color:#7c8798;font-size:12px;
    border-top:1px solid #1c2432;}
QStatusBar::item{border:none;}

QSplitter::handle{background-color:#131a26;}
QSplitter::handle:hover{background-color:#2f6bff;}
QSplitter::handle:horizontal{width:3px;border-radius:1px;}
QSplitter::handle:vertical{height:3px;border-radius:1px;}

QTableWidget{background-color:#0f141d;border:1px solid #232e3f;
    border-radius:8px;color:#dde5f0;font-size:12px;gridline-color:#1c2432;
    selection-background-color:#2f6bff;selection-color:#fff;}
QTableWidget::item{padding:2px 6px;border:none;}
QTableWidget::item:hover{background-color:#1c2432;}
QTableWidget::indicator{width:15px;height:15px;border:1px solid #3d4a61;
    border-radius:4px;background-color:#1a2332;}
QTableWidget::indicator:hover{border-color:#4a8cff;}
QTableWidget::indicator:checked{background-color:#2f6bff;border-color:#4a8cff;}
QHeaderView::section{background-color:#1a2332;color:#7c8798;
    border:none;border-bottom:1px solid #232e3f;padding:4px 6px;
    font-size:12px;font-weight:600;}
QTableCornerButton::section{background-color:#1a2332;border:none;}
QScrollBar:vertical{background:transparent;width:9px;margin:0;}
QScrollBar::handle:vertical{background:#232e3f;border-radius:4px;min-height:30px;}
QScrollBar::handle:vertical:hover{background:#4a8cff;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}
QScrollBar:horizontal{background:transparent;height:9px;margin:0;}
QScrollBar::handle:horizontal{background:#232e3f;border-radius:4px;min-width:30px;}
QScrollBar::handle:horizontal:hover{background:#4a8cff;}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0px;}
"""
