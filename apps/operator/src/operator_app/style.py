"""운영 화면의 공통 색상과 Qt 스타일."""

AUTO = "#1A73E8"
EDIT = "#9334E6"
INK = "#342F3B"
MUTED = "#766F7D"

QSS = """
QMainWindow, QSplitter, QWidget#page { background: #F8F4F1; color: #342F3B; font-family: 'Malgun Gothic'; font-size: 13px; }
QGroupBox { background: #FFFCFA; border: 1px solid #E9DFDA; border-radius: 16px;
            margin-top: 12px; padding: 18px 14px 12px; font-weight: 700; }
QGroupBox::title { subcontrol-origin: margin; left: 18px; padding: 0 8px; color: #342F3B; }
QGroupBox#draftCard { border: 2px solid #C9A4ED; background: #FEFBFF; }
QGroupBox#draftCard::title { color: #9334E6; }
QGroupBox#showCard { background: #F5F0ED; }
QLabel { color: #342F3B; }
QLabel#sectionTitle { font-size: 15px; font-weight: 700; }
QLabel#helper { color: #766F7D; }
QLabel#status { background: #FFF9F4; border: 1px solid #EADFD7; border-radius: 12px;
                padding: 14px 16px; font-size: 15px; font-weight: 700; }
QLineEdit, QPlainTextEdit, QComboBox, QListWidget { background: #FFFFFF; border: 1px solid #DDD3CF;
    border-radius: 10px; padding: 8px; selection-background-color: #EADCF9; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QListWidget:focus { border: 2px solid #9334E6; }
QListWidget::item { padding: 10px 9px; border-bottom: 1px solid #F2ECE8; }
QListWidget::item:selected { background: #F2E9FB; color: #342F3B; border-radius: 7px; }
QTableWidget { background: #FFFFFF; alternate-background-color: #FAF7F4; color: #342F3B;
    gridline-color: #EEE5DF; selection-background-color: #EADCF9; selection-color: #342F3B; }
QHeaderView::section { background: #EEE8E3; color: #342F3B; padding: 7px; border: 0; font-weight: 700; }
QTabBar::tab { background: #EEE8E3; color: #766F7D; padding: 12px 24px; font-weight: 700; }
QTabBar::tab:selected { background: #FFFCFA; color: #1A73E8; border-bottom: 3px solid #1A73E8; }
QCheckBox { spacing: 8px; padding: 5px 2px; }
QPushButton { background: #EEE8E3; color: #342F3B; border: 0; border-radius: 10px;
              padding: 10px 16px; font-weight: 700; }
QPushButton:hover { background: #E5DBD4; }
QPushButton:disabled { background: #DDD7D4; color: #928B8A; }
QPushButton#submit { color: white; padding: 14px 18px; font-size: 14px; }
QPushButton#submit[mode="auto"] { background: #1A73E8; }
QPushButton#submit[mode="edit"], QPushButton#draftAction { background: #9334E6; color: white; }
QPushButton#submit:disabled, QPushButton#draftAction:disabled { background: #C9BFC9; color: #FFFFFF; }
QPushButton#rawToggle { background: transparent; color: #766F7D; text-align: left; padding: 6px; }
QSplitter::handle { background: #F8F4F1; width: 10px; }
"""
