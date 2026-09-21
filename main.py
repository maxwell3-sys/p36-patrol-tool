import os
import re
import sys
import tarfile
import logging
from datetime import datetime
if getattr(sys, "frozen", False):
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(
        sys._MEIPASS, "PyQt5", "Qt5", "plugins", "platforms"
    )
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser,
    QPushButton, QFileDialog, QSizePolicy, QProgressBar,
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QSize
KEY_FILES = {
            'log_system_info.txt',
            'ontInfoDetail.txt',
            'ontTransceiver.txt',
            'oltCounter.txt',
            'onuCounter.txt'
        }
from collections import Counter, defaultdict
from html import escape

"""
处理流程：
diag.tar.gz
    │
    ▼
browse_file()
    │
    ▼
extract_and_read()
    │
    ├─ 只读取 KEY_FILES 中的文件
    ▼
self.file_content
格式：{文件名: [文本行, 文本行, ...]}
    │
    ▼
run_checks()
    │
    ▼
process_file()
    │
    ├─ 根据文件名调用不同检查函数
    ▼
self.results_xxx
格式：list[dict]
    │
    ├─ display_results()：界面显示异常/无异常
    └─ save_to_html()：生成详细 HTML 报告

"""


class MyWindow(QWidget):
    # UI和初始化
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(20, 20, 20, 20)

        top_layout = QVBoxLayout()
        top_layout.setSpacing(20)

        self.text_browser = QTextBrowser()
        self.text_browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.text_browser.setStyleSheet("""
            QTextBrowser {
                border: 1px solid #4a4a4a;
                background-color: #1e1e1e;
                color: #ffee58;
                border-radius: 5px;
                padding: 15px;
                font-size: 14px;
                font-family: Arial;
            }
        """)

        self.tool_button = QPushButton("选择文件")
        self.tool_button.setIcon(QIcon.fromTheme("document-open"))
        self.tool_button.setIconSize(QSize(16, 16))
        self.tool_button.setFixedSize(120, 30)
        self.tool_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.push_button_1 = QPushButton("启动检查")
        self.push_button_2 = QPushButton("导出结果")
        self.push_button_1.setFixedSize(120, 30)
        self.push_button_2.setFixedSize(120, 30)
        self.push_button_1.setEnabled(False)
        self.push_button_2.setEnabled(False)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addWidget(self.tool_button, alignment=Qt.AlignLeft)
        button_layout.addStretch()
        button_layout.addWidget(self.push_button_1)
        button_layout.addWidget(self.push_button_2)

        top_layout.addWidget(self.text_browser)
        top_layout.addLayout(button_layout)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setVisible(False)
        top_layout.addWidget(self.progress_bar)

        main_layout.addLayout(top_layout)

        self.setLayout(main_layout)
        self.setWindowTitle("GPON巡检工具")
        self.resize(600, 400)  # 将窗口大小调整为 600x400

        self.set_styles()

        self.file_paths = []
        self.file_content = {}
        self.results_crc = []
        self.results_version = []
        self.results_time_sync = []
        self.results_cpu_memory = []
        self.results_vlan = []
        self.results_temperature = []
        self.results_transceiver = []
        self.results_outband_ip_forbidden = []
        self.results_inband_ip_forbidden = []
        self.results_ont_status = []
        self.results_vlan_translate = []
        self._vlan_translate_issue_keys = set()

        self.text_browser.setText("请选择GPON的诊断文件压缩包 diag.tar.gz 文件")

        self.tool_button.clicked.connect(self.browse_file) #选择文件
        self.push_button_1.clicked.connect(self.run_checks) #启动检查
        self.push_button_2.clicked.connect(self.save_to_html) #导出结果

        self.set_button_state('initial')    # 初始化按钮状态机

    def set_styles(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #2b2b2b;
                color: #cccccc;
            }
            QPushButton {
                background-color: #3c7cb5;
                color: #ffffff;
                font-size: 14px;
                padding: 5px 15px;
                border: none;
                border-radius: 5px;
                transition: background-color 0.3s, transform 0.1s;
            }
            QPushButton:disabled {
                background-color: #444444;
                color: #888888;
            }
            QPushButton:hover:enabled {
                background-color: #336699;
            }
            QPushButton:pressed:enabled {
                background-color: #2a527a;
                transform: scale(0.98);
            }
        """)

    def set_button_state(self, state):
        #按钮状态机
        states = {
            'initial': (True, False, False),
            'file_selected': (True, True, False),
            'checks_run': (True, False, True),
            'result_exported': (True, False, False)
        }

        tools_state, check_state, export_state = states.get(state, (True, False, False))

        self.tool_button.setEnabled(tools_state)
        self.push_button_1.setEnabled(check_state)
        self.push_button_2.setEnabled(export_state)

# —————————————————————————————————————————按钮1：选择文件—————————————————————————————————————————————————————
    def browse_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "选择 diag.tar.gz",
            "",
            "diag.tar.gz (*.tar.gz)"
        )

        if not filepath:
            return

        if os.path.basename(filepath) != "diag.tar.gz":
            self.text_browser.setText("请选择文件名为 diag.tar.gz 的文件")
            return

        try:
            logging.debug(f"选中的文件: {filepath}")

            self.file_content = self.extract_and_read(filepath)

            if not self.file_content:
                self.text_browser.setText("未找到需要的关键文件")
                return

            loaded_files = list(self.file_content.keys())

            self.text_browser.setText(
                "文件已成功加载:\n" + "\n".join(loaded_files)
            )

            self.set_button_state('file_selected')

        except Exception as e:
            logging.error(f"处理文件失败: {e}")
            self.text_browser.setText(f"处理文件失败: {e}")

    def extract_and_read(self, filepath):

        encodings = ['utf-8', 'utf-16', 'latin-1', 'cp936', 'gbk']

        file_content = {}

        with tarfile.open(filepath, 'r:gz') as tar:

            for member in tar:

                if not member.isfile():
                    continue

                filename = os.path.basename(member.name)

                if filename not in KEY_FILES:
                    continue

                extracted = tar.extractfile(member)

                if not extracted:
                    continue

                raw_data = extracted.read()

                content = None

                for enc in encodings:
                    try:
                        content = raw_data.decode(enc).splitlines(keepends=True)
                        logging.debug(f"{filename} 使用编码 {enc} 读取成功")
                        break
                    except Exception:
                        continue

                if content is None:
                    logging.warning(f"{filename} 所有编码均尝试失败")
                    continue

                file_content[filename] = content

        logging.info(f"成功读取 {len(file_content)} 个关键文件")

        return file_content

    # ————————————————————————————————————————按钮2：启动检查————————————————————————————————————————
    def run_checks(self):
        try:
            if not self.file_content:
                # 检查是否加载了文件内容
                logging.warning("没有加载任何文件内容。")
                self.text_browser.setText("没有加载任何文件内容。")
                return

            # 在每次运行新的检查前清空结果列表，以确保结果不会从之前的一次运行中残留。
            self.results_crc.clear()
            self.results_version.clear()
            self.results_time_sync.clear()
            self.results_cpu_memory.clear()
            self.results_vlan.clear()
            self.results_temperature.clear()
            self.results_transceiver.clear()
            self.results_outband_ip_forbidden.clear()
            self.results_inband_ip_forbidden.clear()
            self.results_ont_status.clear()
            self.results_vlan_translate.clear()
            self._vlan_translate_issue_keys.clear()

            self.text_browser.setText("正在解析文件，请稍候...")
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            QApplication.processEvents()

            # 计算总行数和已处理行数，用于进度条更新
            total_lines = sum(len(content) for content in self.file_content.values())
            processed_lines = 0

            # 处理每个文件
            for filepath, lines in self.file_content.items():
                processed_lines =self.process_file(filepath, lines, total_lines, processed_lines)

            # 按钮2完成后，隐藏进度条
            self.progress_bar.setVisible(False)

            # 显示结果
            self.display_results()

            # 设置按钮状态为"检查已完成"
            self.set_button_state('checks_run')

        except Exception as e:
            logging.error(f"检查过程中出错: {e}")
            self.text_browser.setText(f"处理错误: {e}")

    def process_file(self, filepath, lines, total_lines, processed_lines):
        """处理单个文件"""
        logging.debug(f"分析文件: {filepath}，行数: {len(lines)}")

        # 根据文件名进行特定处理
        filename = os.path.basename(filepath)

        # 通用处理逻辑
        if filename == 'log_system_info.txt':
            self.check_time_sync_in_system(lines)
            self.check_cpu_memory_in_system(lines)
            self.check_vlan_info_in_system(lines)
            self.check_temperature_in_system(lines)
            self.check_version_in_system(lines)
            self.check_outband_ip_forbidden_in_system(lines)
            self.check_inband_ip_forbidden_in_system(lines)
            self.check_vlan_translate_consistency_in_system(lines)
            #查用ont-vlan的
            #查vlan转换配的错了的
            self.check_ont_status_in_system(lines)
        elif filename == 'ontTransceiver.txt':
            self.check_ont_transceiver_in_file(lines)

        elif filename == 'oltCounter.txt':
            self.check_crc_errors_in_olt_counter(lines)

        # 更新进度
        processed_lines = self.update_progress(processed_lines, total_lines)
        return processed_lines

    def update_progress(self, processed_lines, total_lines):
        # 更新进度条
        processed_lines += 1
        if processed_lines % 1000 == 0:
            progress_percentage = (processed_lines + 1) / total_lines * 100
            self.progress_bar.setValue(int(progress_percentage))
            self.text_browser.setText(f"正在解析文件... {progress_percentage:.2f}% 完成")
            logging.debug(f"解析进度: {progress_percentage:.2f}%")
            QApplication.processEvents()
        return processed_lines

    def _find_second_occurrence_and_process(self, lines, start_marker, end_marker, processor_func):
        """给log_system_info.txt用的：找到命令行第二次出现的标记并处理"""
        # 找到所有起始标记的位置
        positions = []
        for i, line in enumerate(lines):
            if start_marker in line:
                positions.append(i)

        if len(positions) < 2:
            return  # 不足两次出现，无法区分

        # 取第二次出现的位置作为开始
        second_pos = positions[1]

        # 找到结束标记
        end_idx = len(lines)
        for i in range(second_pos + 1, len(lines)):
            if end_marker in lines[i]:
                end_idx = i
                break

        # 提取并处理内容
        processor_func(lines[second_pos:end_idx])

    def display_results(self):
        # 显示检查结果
        crc_status = "异常" if self.results_crc else "无异常"
        version_status = "异常" if self.results_version else "无异常"
        time_sync_status = "异常" if self.results_time_sync else "无异常"
        cpu_memory_status = "异常" if self.results_cpu_memory else "无异常"
        vlan_status = "异常" if self.results_vlan else "无异常"
        temperature_status = "异常" if self.results_temperature else "无异常"
        transceiver_status = "异常" if self.results_transceiver else "无异常"
        outband_ip_status = "异常" if self.results_outband_ip_forbidden else "无异常"
        intband_ip_status = "异常" if self.results_inband_ip_forbidden else "无异常"
        ont_status = "异常" if self.results_ont_status else "无异常"
        vlan_translate_status = "异常" if self.results_vlan_translate else "无异常"


        result_summary = f"""
               检查结果<br>
               CRC错包检测： {crc_status}<br>
               版本问题检测： {version_status}<br>
               时间同步检测： {time_sync_status}<br>
               CPU和内存使用率检测： {cpu_memory_status}<br>
               VLAN使用检测： {vlan_status}<br>
               温度检测： {temperature_status}<br>
               光功率检测： {transceiver_status}<br>
               带外地址ip禁止： {outband_ip_status}<br>
               带内地址ip禁止： {intband_ip_status}<br>
               ONU状态检测： {ont_status}<br>
               VLAN转换与模板一致性检测： {vlan_translate_status}<br>
               """

        self.text_browser.setHtml(result_summary)

    def highlight_line_in_text(self, text, line_to_highlight):
        """
        在给定文本中高亮特定行
        :param text: 原始文本
        :param line_to_highlight: 需要高亮的行
        :return: 高亮后的文本
        """
        highlighted_text = text.replace(line_to_highlight,
                                        f"<span style='background-color:yellow;'>{line_to_highlight}</span>")
        return highlighted_text

    def check_crc_errors_in_olt_counter(self, lines):
        """在OLT计数器文件中检查CRC错误 - 专用模式"""
        # 初始化正则表达式，用于匹配特定模式
        self.slot_pattern = re.compile(r'Slot NO\s+: (\d+)')
        self.port_pattern = re.compile(r'Port NO\s+: (\d+)')
        self.crc_error_pattern = re.compile(r'rxCrcErrors\s+: (\d+)')

        current_slot = None
        current_port = None
        slot_start_idx = 0
        port_start_idx = 0

        for idx, line in enumerate(lines):
            # 提取单板号
            if 'Slot NO' in line and ':' in line:
                slot_match = self.slot_pattern.match(line)
                if slot_match:
                    current_slot = slot_match.group(1)
                    slot_start_idx = idx
                    port_start_idx = idx  # 重置端口开始位置

            # 提取端口号
            elif 'Port NO' in line and ':' in line:
                port_match = self.port_pattern.match(line)
                if port_match:
                    current_port = port_match.group(1)
                    port_start_idx = idx

            # 处理CRC错误
            elif 'rxCrcErrors' in line and ':' in line:
                crc_error_match = self.crc_error_pattern.match(line)
                if crc_error_match:
                    crc_errors = int(crc_error_match.group(1))
                    # 只有当有有效的单板和端口信息且CRC错误不为0时才记录
                    if current_slot and current_port and crc_errors != 0:
                        # 获取当前端口的完整上下文信息
                        end_idx = idx
                        # 找到下一个Slot NO或文件结尾
                        for i in range(idx + 1, len(lines)):
                            if 'Slot NO' in lines[i] and ':' in lines[i]:
                                end_idx = i - 1
                                break
                        else:
                            end_idx = len(lines) - 1

                        # 获取从端口开始到结束的上下文
                        surrounding_lines = lines[port_start_idx:end_idx + 1]
                        diagnostic_text = self.highlight_line_in_text(''.join(surrounding_lines), line.strip())

                        self.results_crc.append({
                            '单板板号': current_slot,
                            'PON口号': current_port,
                            'PON口接收到的CRC错包数量': crc_errors,
                            '诊断原文': diagnostic_text
                        })


    def check_version_in_system(self, lines):
        """在系统信息文件中检查版本信息"""
        self._find_second_occurrence_and_process(
            lines,
            '#*** show slot ***',
            '#*** show device manuinfo ***',
            self.check_slot_version
        )

    def check_slot_version(self, lines):
        version_pattern = re.compile(r'(\d+-\w[A-Z]?)\s+([A-Z0-9()]+)\s+MAT\s+U\s+(\S+:\S+)\s+(\S+)\s*(\S*)')
        uptime_pattern = re.compile(r'(\d+)\s+days')

        version_data = []
        unmatched_lines = []  # 保存不匹配的行
        header = None
        show_slot_line = "#*** show slot ***"

        logging.debug("开始进行版本问题检测")

        # 首次遍历，记录所有板卡信息
        for line in lines:
            line = line.strip()  # 移除两端的空白

            if show_slot_line in line or 'Sh-S Module' in line or not line:
                continue

            logging.debug(f"处理行: {line}")

            match = version_pattern.search(line)
            if match:
                fields = match.groups()
                slot_id, module, hw_ver, sw_ver, serial = fields
                version_data.append((slot_id, module, hw_ver, sw_ver, serial, line))
            else:
                unmatched_lines.append(line)

        complete_diag_text = (header + '\n' if header else '') + '\n'.join(unmatched_lines + [d[-1] for d in version_data])
        master_version = None

        for data in version_data:
            slot_id, _, _, sw_ver, _, original_line = data

            if slot_id == '1-A':
                master_version = sw_ver
                break

        if not master_version:
            logging.error("未找到主控板1-A的版本信息")
            return

        unique_boards = {}
        for data in version_data:
            slot_id, _, _, sw_ver, _, original_line = data

            # -------- UP Time检测 --------
            uptime_match = uptime_pattern.search(original_line)
            if uptime_match:
                uptime_days = int(uptime_match.group(1))
                if uptime_days < 7:
                    diagnostic_text = self.highlight_line_in_text(complete_diag_text, original_line)
                    board_type = '主用主控板' if slot_id == '1-A' else ('备用主控板' if slot_id == '1-B' else '板卡')

                    unique_boards[f"{slot_id}_uptime"] = {
                        '状态': f'{slot_id}号{board_type}最近7天内发生过重启 (UP Time {uptime_days} days)',
                        '诊断原文': diagnostic_text,
                        '关键字': '重启',
                        '优先级': '告警'
                    }

            if not re.match(r'1-[AB1-9]$', slot_id):
                continue

            # 确保版本是数字
            sw_ver_num_match = re.search(r'(\d+)', sw_ver)
            sw_ver_num = int(sw_ver_num_match.group(1)) if sw_ver_num_match else None

            if sw_ver_num is None or not (1001 <= sw_ver_num <= 1900):
                # 版本异常
                diagnostic_text = self.highlight_line_in_text(complete_diag_text, original_line)
                status_key = '主用主控板' if slot_id == '1-A' else ('备用主控板' if slot_id == '1-B' else '线卡板')
                unique_boards[slot_id] = {
                    '状态': f'{slot_id}号{status_key}版本异常: {sw_ver}',
                    '诊断原文': diagnostic_text,
                    '关键字': status_key,
                    '优先级': '异常'
                }
            elif sw_ver_num < 1006:
                # 版本过老
                diagnostic_text = self.highlight_line_in_text(complete_diag_text, original_line)
                status_key = '主用主控板' if slot_id == '1-A' else ('备用主控板' if slot_id == '1-B' else '线卡板')
                unique_boards[slot_id] = {
                    '状态': f'{slot_id}号{status_key}版本过老: {sw_ver}，请升级到至少1006版本',
                    '诊断原文': diagnostic_text,
                    '关键字': status_key,
                    '优先级': '异常'
                }
            elif slot_id != '1-A' and master_version != sw_ver:
                # 版本一致性检查
                diagnostic_text = self.highlight_line_in_text(complete_diag_text, original_line)
                status_key = '备用主控板' if slot_id == '1-B' else '线卡板'
                unique_boards[slot_id] = {
                    '状态': f'{slot_id}号{status_key}与1-A主用主控版本不一致：{sw_ver}，请升级到与主用主控一致的版本',
                    '诊断原文': diagnostic_text,
                    '关键字': '一致性',
                    '优先级': '不一致'
                }

        for board in unique_boards.values():
            self.results_version.append(board)

        logging.info("版本信息检查完成。")

    def check_time_sync_in_system(self, lines):
        """在系统信息文件中检查时间同步"""
        self._find_second_occurrence_and_process(
            lines,
            '#*** show time ***',
            '#*** show system ***',
            self.check_time_sync
        )

    def check_time_sync(self, lines):
        time_pattern = re.compile(r'Local time\s+:\s+(\d{4}/\d{2}/\d{2}), (\d{2}:\d{2}:\d{2})')

        for line in lines:
            match = time_pattern.search(line.strip())
            if match:
                local_date, local_time = match.groups()
                local_datetime_str = f"{local_date} {local_time}"

                # 解析时间
                try:
                    local_datetime = datetime.strptime(local_datetime_str, '%Y/%m/%d %H:%M:%S')

                    # 以2025年为分界线
                    if local_datetime.year <= 2025:
                        # 2025年之前的时间被认为是错误的（未设置或设置错误）
                        diagnostic_text = self.highlight_line_in_text(''.join(lines), line.strip())
                        self.results_time_sync.append({
                            '状态': 'olt设备本地时间过早，可能未正确设置',
                            '诊断原文': diagnostic_text,
                            '本地时间': local_datetime_str,
                            '建议': '请检查并设置正确的本地时间'
                        })
                        logging.debug(f"检测到可能错误的时间: 本地时间={local_datetime_str}")
                    else:
                        # 2025年及之后的时间被认为是正确的
                        logging.debug(f"时间正常: 本地时间={local_datetime_str}")

                except ValueError as e:
                    logging.error(f"时间解析错误: {e}")
                break

    def check_cpu_memory_in_system(self, lines):
        """在系统信息文件中检查CPU内存"""
        self._find_second_occurrence_and_process(
            lines,
            '#*** show cpu-memory ***',
            '#*** ip show ip ***',
            self.check_cpu_memory
        )

    def check_cpu_memory(self, lines):
        cpu_usage_pattern = re.compile(r'(Active|Standby) CSM CPU usage\s+:\s+(\d+)%')
        memory_usage_pattern = re.compile(r'(Active|Standby) CSM Memory usage\s+:\s+(\d+)%')

        name_map = {
            'Active': '主用主控',
            'Standby': '备用主控'
        }

        for line in lines:
            cpu_match = cpu_usage_pattern.search(line)
            memory_match = memory_usage_pattern.search(line)

            state = None
            cpu_usage = '正常'
            memory_usage = '正常'

            if cpu_match:
                state = name_map.get(cpu_match.group(1), '未知')
                cpu_usage_value = int(cpu_match.group(2))

                if cpu_usage_value >= 90:
                    diagnostic_text = self.highlight_line_in_text(''.join(lines), line.strip())
                    self.results_cpu_memory.append({
                        '状态': f'{state} CPU 利用率超限',
                        '诊断原文': diagnostic_text,
                        'CPU 使用': f"{cpu_usage_value}%",
                        '存储内存使用': memory_usage
                    })
                    logging.debug(f"检测到{state} CPU利用率超限: {cpu_usage_value}%")
                else:
                    cpu_usage = '正常'  # 超限无情况将"正常"显示

            if memory_match:
                state = name_map.get(memory_match.group(1), '未知')
                memory_usage_value = int(memory_match.group(2))

                if memory_usage_value > 80:
                    diagnostic_text = self.highlight_line_in_text(''.join(lines), line.strip())
                    self.results_cpu_memory.append({
                        '状态': f'{state} 存储内存利用率超限',
                        '诊断原文': diagnostic_text,
                        'CPU 使用': cpu_usage,
                        '存储内存使用': f"{memory_usage_value}%"
                    })
                    logging.debug(f"检测到{state} 存储内存利用率超限: {memory_usage_value}%")
                else:
                    memory_usage = '正常'  # 超限无情况将"正常"显示

    def check_vlan_info_in_system(self, lines):
        """在系统信息文件中检查VLAN信息"""
        self._find_second_occurrence_and_process(
            lines,
            '#*** brief-show vlan ***',
            '#*** brief-show vlan-translate ***',
            self.check_vlan_usage
        )
    def check_vlan_usage(self, lines):
        vlan_pattern = re.compile(r'^\s*(\d+)\s+\S+')

        # 跳过VLAN 1，检查其他在2到24之间的VLAN
        for line in lines:
            match = vlan_pattern.match(line)
            if match:
                vid = int(match.group(1))
                if 2 <= vid <= 24:
                    diagnostic_text = self.highlight_line_in_text(''.join(lines), line.strip())
                    self.results_vlan.append({
                        'VLAN ID': vid,
                        '状态': f'OLT 使用了保留 VLAN，VLAN {vid} 在1到24之间，请检查',
                        '诊断原文': diagnostic_text
                    })
                    logging.debug(f"VLAN使用告警: VLAN ID = {vid}")

    def _get_nth_command_block(
            self,
            lines,
            start_marker,
            end_marker,
            occurrence=2
    ):
        """
        提取第 occurrence 次 start_marker 到后续第一个 end_marker 之间的内容。

        返回：
        - 成功：行列表
        - 起始或结束标记不存在：None
        """
        start_positions = [
            index for index, line in enumerate(lines)
            if start_marker in line
        ]

        if len(start_positions) < occurrence:
            return None

        start_index = start_positions[occurrence - 1]

        for index in range(start_index + 1, len(lines)):
            if end_marker in lines[index]:
                return lines[start_index + 1:index]

        return None

    def _make_vlan_translate_diagnostic(self, rows):
        """
        生成该检查项使用的HTML诊断信息。

        rows格式：
        [
            ("VLAN Translate", 原始行, True),
            ("ONU Service", 原始行, False),
        ]
        """
        result = []

        for label, text, highlighted in rows:
            safe_label = escape(str(label))
            safe_text = escape(str(text).strip())

            if highlighted:
                safe_text = (
                    "<span class='highlight-line'>"
                    f"{safe_text}"
                    "</span>"
                )

            result.append(f"<b>{safe_label}：</b>{safe_text}")

        return "<br>".join(result)

    def _add_vlan_translate_issue(
            self,
            status,
            diagnostic="",
            suggestion="",
            slot=None,
            port=None,
            ont=None,
            vport=None,
            profile=None,
            issue_key=None
    ):
        """统一添加VLAN Translate检查结果"""

        if issue_key is not None:
            if issue_key in self._vlan_translate_issue_keys:
                return

            self._vlan_translate_issue_keys.add(issue_key)

        result = {
            '检查项': 'VLAN转换与Flow/TCONT一致性检测',
            '状态': status,
            '诊断原文': diagnostic
        }

        if suggestion:
            result['建议'] = suggestion

        if slot is not None:
            result['槽位'] = slot

        if port is not None:
            result['PON口'] = port

        if ont is not None:
            result['ONT'] = ont

        if vport is not None:
            result['VPORT'] = vport

        if profile is not None:
            result['模板'] = profile

        self.results_vlan_translate.append(result)

    def _parse_vlan_translate_table(self, lines):
        """
        解析 brief-show vlan-translate 表格。

        只使用以下字段：
        slot port ont vport svid cvid new-svid new-cvid
        """
        row_pattern = re.compile(
            r'^\s*'
            r'(\d+)\s+'       # slot
            r'(\d+)\s+'       # port
            r'(\d+)\s+'       # ont
            r'(\d+)\s+'       # vport
            r'(\d+)\s+'       # svid
            r'(\d+)\s+'       # cvid
            r'(\d+)\s+'       # new-svid
            r'(\d+)\s+'       # new-cvid
            r'\S+'             # cos
        )

        entries = []

        for index, raw_line in enumerate(lines):
            match = row_pattern.match(raw_line)

            if not match:
                # 表头、分隔线不记录；疑似数字数据行解析失败时写debug日志
                if re.match(r'^\s*\d+\s+', raw_line):
                    logging.debug(
                        'VLAN Translate疑似数据行解析失败，'
                        '区段第%s行：%r',
                        index + 1,
                        raw_line.rstrip('\r\n')
                    )
                continue

            (
                slot,
                port,
                ont,
                vport,
                svid,
                cvid,
                new_svid,
                new_cvid
            ) = map(int, match.groups())

            entries.append({
                'slot': slot,
                'port': port,
                'ont': ont,
                'vport': vport,
                'svid': svid,
                'cvid': cvid,
                'new_svid': new_svid,
                'new_cvid': new_cvid,
                'raw': raw_line.rstrip('\r\n'),
                'line_index': index
            })

        return entries

    def _parse_single_slot_configuration(
            self,
            slot,
            board_type,
            block_lines
    ):
        """解析一个slot配置块中的Flow、TCONT和ONU Service配置"""

        interface_pattern = re.compile(
            r'^\s*interface\s+gpon-olt\s+(\d+)/(\d+)\s*$',
            re.I
        )

        flow_prefix_pattern = re.compile(
            r'^\s*gpon\s+profile\s+flow\b',
            re.I
        )

        flow_pattern = re.compile(
            r'^\s*gpon\s+profile\s+flow\s+id\s+'
            r'(\d+)\s+'                         # 大Flow组x
            r'(\d+)\b'                          # 组内编号y
            r'.*?\bupmap-type\s+vlanId\s+'
            r'(\d+)\s+'                         # 第一个vlanId
            r'(\d+)\b'                          # 第二个vlanId
            r'.*?\bvport\s+'
            r'(\d+)\b',                         # vport
            re.I
        )

        tcont_prefix_pattern = re.compile(
            r'^\s*gpon\s+profile\s+tcont-bind\b',
            re.I
        )

        tcont_pattern = re.compile(
            r'^\s*gpon\s+profile\s+tcont-bind\s+id\s+'
            r'(\d+)\s+v-port\s+(\d+)\b',
            re.I
        )

        ont_pattern = re.compile(
            r'^\s*ont\s+(\d+)\s*$',
            re.I
        )
        ont_binding_pattern = re.compile(
            r'^\s*sn\s+(?P<sn>\S+)\s+type\s+'
            r'(?P<ont_type>\S+)(?:\s+.*)?$',
            re.I
        )
        virtual_port_pattern = re.compile(
            r'^\s*virtual-port\s+(\d+)\b',
            re.I
        )
        service_prefix_pattern = re.compile(
            r'^\s*service\s+flow-profile\b',
            re.I
        )

        service_pattern = re.compile(
            r'^\s*service\s+flow-profile\s+(\d+)'
            r'\s+tcont-bind-profile\s+(\d+)'
            r'\s+svc-type\s+(\S+)',
            re.I
        )

        # 第一个interface之前为Flow和TCONT模板区域
        first_interface_index = len(block_lines)

        for index, line in enumerate(block_lines):
            if interface_pattern.match(line):
                first_interface_index = index
                break

        profile_lines = block_lines[:first_interface_index]

        flow_profiles = defaultdict(list)
        tcont_profiles = defaultdict(list)

        # 解析Flow和TCONT模板
        for raw_line in profile_lines:
            if flow_prefix_pattern.match(raw_line):
                match = flow_pattern.match(raw_line)

                if not match:
                    self._add_vlan_translate_issue(
                        status=(
                            f'Slot {slot} 存在无法解析的Flow模板配置'
                        ),
                        diagnostic=self._make_vlan_translate_diagnostic([
                            ('Flow配置', raw_line, True)
                        ]),
                        suggestion=(
                            '请确认Flow配置包含upmap-type vlanId、'
                            '两个VLAN ID以及vport字段。'
                        ),
                        slot=slot
                    )
                    continue

                profile_id, flow_number, vlan_1, vlan_2, vport = (
                    map(int, match.groups())
                )

                flow_profiles[profile_id].append({
                    'profile_id': profile_id,
                    'flow_number': flow_number,
                    'vlan_1': vlan_1,
                    'vlan_2': vlan_2,
                    'vport': vport,
                    'raw': raw_line.rstrip('\r\n')
                })

            elif tcont_prefix_pattern.match(raw_line):
                match = tcont_pattern.match(raw_line)

                if not match:
                    self._add_vlan_translate_issue(
                        status=(
                            f'Slot {slot} 存在无法解析的TCONT模板配置'
                        ),
                        diagnostic=self._make_vlan_translate_diagnostic([
                            ('TCONT配置', raw_line, True)
                        ]),
                        suggestion=(
                            '请确认配置格式为：'
                            'gpon profile tcont-bind id X v-port Y'
                        ),
                        slot=slot
                    )
                    continue

                profile_id, vport = map(int, match.groups())

                tcont_profiles[profile_id].append({
                    'profile_id': profile_id,
                    'vport': vport,
                    'raw': raw_line.rstrip('\r\n')
                })

        # 解析interface、ONU绑定信息和Service关系
        services = defaultdict(list)

        # key: (port, ont)
        # value: ONU的SN绑定信息
        ont_bindings = defaultdict(list)

        # 记录是否出现过“ont N”配置入口，用于区分：
        # 1. 完全没有ONT配置
        # 2. 有ONT配置，但没有SN绑定
        ont_declarations = defaultdict(list)

        # key: (port, ont)
        # value: ONU中配置的virtual-port
        virtual_ports = defaultdict(list)

        current_port = None
        current_ont = None
        current_ont_header = None

        for raw_line in block_lines[first_interface_index:]:
            stripped = raw_line.strip()

            interface_match = interface_pattern.match(raw_line)

            if interface_match:
                interface_slot = int(interface_match.group(1))
                interface_port = int(interface_match.group(2))

                if interface_slot == slot:
                    current_port = interface_port

                    if not 1 <= interface_port <= 16:
                        self._add_vlan_translate_issue(
                            status=(
                                f'Slot {slot}配置了超出1～16范围的'
                                f'PON口：{interface_port}'
                            ),
                            diagnostic=self._make_vlan_translate_diagnostic([
                                ('Interface配置', raw_line, True)
                            ]),
                            suggestion='PON口范围必须为1～16。',
                            slot=slot,
                            port=interface_port,
                            issue_key=(
                                'invalid_interface_port',
                                slot,
                                interface_port
                            )
                        )
                else:
                    current_port = None

                current_ont = None
                current_ont_header = None
                continue

            if current_port is None:
                continue

            ont_match = ont_pattern.match(raw_line)

            if ont_match:
                current_ont = int(ont_match.group(1))
                current_ont_header = raw_line.rstrip('\r\n')

                ont_declarations[(current_port, current_ont)].append({
                    'raw': current_ont_header
                })

                if not 1 <= current_ont <= 256:
                    self._add_vlan_translate_issue(
                        status=(
                            f'Slot {slot} PON {current_port}配置了'
                            f'超出1～256范围的ONT：{current_ont}'
                        ),
                        diagnostic=self._make_vlan_translate_diagnostic([
                            ('ONT配置', raw_line, True)
                        ]),
                        suggestion='ONT编号范围必须为1～256。',
                        slot=slot,
                        port=current_port,
                        ont=current_ont,
                        issue_key=(
                            'invalid_ont_number',
                            slot,
                            current_port,
                            current_ont
                        )
                    )

                continue

            if stripped.lower() == 'exit':
                if current_ont is not None:
                    # 退出当前ONT配置块，但仍在当前PON口内
                    current_ont = None
                    current_ont_header = None
                else:
                    # 退出当前interface
                    current_port = None

                continue

            if current_ont is None:
                continue

            # 解析ONU的SN绑定信息
            binding_match = ont_binding_pattern.match(raw_line)

            if binding_match:
                ont_bindings[(current_port, current_ont)].append({
                    'sn': binding_match.group('sn'),
                    'ont_type': binding_match.group('ont_type'),
                    'ont_raw': (
                            current_ont_header
                            or f'ont {current_ont}'
                    ),
                    'raw': raw_line.rstrip('\r\n')
                })
                continue
            # 解析ONU的virtual-port
            virtual_port_match = virtual_port_pattern.match(raw_line)

            if virtual_port_match:
                virtual_port = int(
                    virtual_port_match.group(1)
                )

                virtual_ports[(current_port, current_ont)].append({
                    'vport': virtual_port,
                    'raw': raw_line.rstrip('\r\n')
                })

                if not 1 <= virtual_port <= 16:
                    self._add_vlan_translate_issue(
                        status=(
                            f'Slot {slot} PON {current_port} '
                            f'ONT {current_ont}配置了超出1～16范围的'
                            f'virtual-port：{virtual_port}'
                        ),
                        diagnostic=self._make_vlan_translate_diagnostic([
                            ('ONU virtual-port配置', raw_line, True)
                        ]),
                        suggestion='virtual-port范围必须为1～16。',
                        slot=slot,
                        port=current_port,
                        ont=current_ont,
                        vport=virtual_port,
                        issue_key=(
                            'invalid_onu_virtual_port',
                            slot,
                            current_port,
                            current_ont,
                            virtual_port
                        )
                    )

                continue
            # 解析ONU的Service绑定
            if service_prefix_pattern.match(raw_line):
                service_match = service_pattern.match(raw_line)

                if not service_match:
                    self._add_vlan_translate_issue(
                        status=(
                            f'Slot {slot} PON {current_port} '
                            f'ONT {current_ont} 的Service配置无法解析'
                        ),
                        diagnostic=self._make_vlan_translate_diagnostic([
                            ('Service配置', raw_line, True)
                        ]),
                        suggestion=(
                            '请确认Service配置中同时包含'
                            'flow-profile、tcont-bind-profile和svc-type。'
                        ),
                        slot=slot,
                        port=current_port,
                        ont=current_ont
                    )
                    continue

                flow_profile, tcont_profile, svc_type = (
                    service_match.groups()
                )

                services[(current_port, current_ont)].append({
                    'flow_profile': int(flow_profile),
                    'tcont_profile': int(tcont_profile),
                    'svc_type': svc_type,
                    'raw': raw_line.rstrip('\r\n')
                })

        return {
            'slot': slot,
            'board_type': board_type,
            'flow_profiles': dict(flow_profiles),
            'tcont_profiles': dict(tcont_profiles),
            'ont_bindings': dict(ont_bindings),
            'ont_declarations': dict(ont_declarations),
            'virtual_ports': dict(virtual_ports),
            'services': dict(services)
        }

    def _parse_vlan_slot_configurations(self, lines):
        """
        解析以下格式的slot配置：

        #slot 1 GPFB
        ...
        #slot 1 GPFB end
        """
        slot_start_pattern = re.compile(
            r'^\s*#slot\s+(\d+)\s+(GPFB|XGFSA|XGFCA)\s*$',
            re.I
        )

        slot_end_pattern = re.compile(
            r'^\s*#slot\s+(\d+)\s+(GPFB|XGFSA|XGFCA)\s+end\s*$',
            re.I
        )

        slot_configs = {}
        index = 0

        while index < len(lines):
            start_match = slot_start_pattern.match(lines[index])

            if not start_match:
                index += 1
                continue

            slot = int(start_match.group(1))
            board_type = start_match.group(2).upper()
            start_index = index
            end_index = None

            for search_index in range(start_index + 1, len(lines)):
                end_match = slot_end_pattern.match(lines[search_index])

                if not end_match:
                    continue

                end_slot = int(end_match.group(1))
                end_board = end_match.group(2).upper()

                if end_slot == slot and end_board == board_type:
                    end_index = search_index
                    break

            if end_index is None:
                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} {board_type} 配置未找到结束标记'
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic([
                        ('Slot开始行', lines[start_index], True)
                    ]),
                    suggestion=(
                        f'请确认存在：#slot {slot} {board_type} end'
                    ),
                    slot=slot
                )
                index += 1
                continue

            if not 1 <= slot <= 17:
                self._add_vlan_translate_issue(
                    status=f'检测到超出1～17范围的Slot号：{slot}',
                    diagnostic=self._make_vlan_translate_diagnostic([
                        ('Slot配置', lines[start_index], True)
                    ]),
                    slot=slot
                )
                index = end_index + 1
                continue

            if slot in slot_configs:
                self._add_vlan_translate_issue(
                    status=f'Slot {slot} 出现了重复配置块',
                    diagnostic=self._make_vlan_translate_diagnostic([
                        ('重复Slot配置', lines[start_index], True)
                    ]),
                    slot=slot
                )
                index = end_index + 1
                continue

            block_lines = lines[start_index + 1:end_index]

            slot_configs[slot] = (
                self._parse_single_slot_configuration(
                    slot,
                    board_type,
                    block_lines
                )
            )

            index = end_index + 1

        return slot_configs

    def _validate_flow_profiles(self, slot_config):
        """检查Flow模板自身的编号、VLAN和VPORT规则"""
        slot = slot_config['slot']
        flow_profiles = slot_config['flow_profiles']

        for profile_id, entries in flow_profiles.items():
            flow_numbers = [
                entry['flow_number'] for entry in entries
            ]

            expected_numbers = list(
                range(1, len(flow_numbers) + 1)
            )

            sequence_problems = []

            if flow_numbers != expected_numbers:
                sequence_problems.append(
                    f'实际y顺序为{flow_numbers}，'
                    f'正确顺序应为{expected_numbers}'
                )

            out_of_range = [
                number for number in flow_numbers
                if not 1 <= number <= 16
            ]

            if out_of_range:
                sequence_problems.append(
                    f'y超出1～16范围：{out_of_range}'
                )

            duplicate_y = sorted([
                number
                for number, count in Counter(flow_numbers).items()
                if count > 1
            ])

            if duplicate_y:
                sequence_problems.append(
                    f'存在重复的y编号：{duplicate_y}'
                )

            if sequence_problems:
                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} Flow Profile {profile_id} '
                        f'的y编号不符合规则：'
                        + '；'.join(sequence_problems)
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic([
                        ('Flow配置', entry['raw'], True)
                        for entry in entries
                    ]),
                    suggestion=(
                        '同一个Flow Profile内，y必须从1开始，'
                        '按1、2、3……顺序连续配置，最大为16。'
                    ),
                    slot=slot,
                    profile=f'flow-profile {profile_id}'
                )

            # 每条Flow检查两个VLAN是否相同，以及y和vport是否相同
            for entry in entries:
                flow_number = entry['flow_number']
                vlan_1 = entry['vlan_1']
                vlan_2 = entry['vlan_2']
                vport = entry['vport']
                range_problems = []

                if not 1 <= vport <= 16:
                    range_problems.append(
                        f'vport={vport}超出1～16范围'
                    )

                if not 1 <= vlan_1 <= 4095:
                    range_problems.append(
                        f'第一个vlanId={vlan_1}超出1～4095范围'
                    )

                if not 1 <= vlan_2 <= 4095:
                    range_problems.append(
                        f'第二个vlanId={vlan_2}超出1～4095范围'
                    )

                if range_problems:
                    self._add_vlan_translate_issue(
                        status=(
                            f'Slot {slot} Flow Profile {profile_id} '
                            f'的y={flow_number}存在范围错误：'
                            + '；'.join(range_problems)
                        ),
                        diagnostic=self._make_vlan_translate_diagnostic([
                            ('Flow配置', entry['raw'], True)
                        ]),
                        suggestion=(
                            'Flow的vport范围必须为1～16，'
                            'vlanId范围必须为1～4095。'
                        ),
                        slot=slot,
                        vport=vport,
                        profile=f'flow-profile {profile_id}',
                        issue_key=(
                            'invalid_flow_range',
                            slot,
                            profile_id,
                            flow_number
                        )
                    )

                if vlan_1 != vlan_2:
                    self._add_vlan_translate_issue(
                        status=(
                            f'Slot {slot} Flow Profile {profile_id} '
                            f'的y={flow_number}配置了不同的VLAN：'
                            f'{vlan_1}、{vlan_2}'
                        ),
                        diagnostic=self._make_vlan_translate_diagnostic([
                            ('Flow配置', entry['raw'], True)
                        ]),
                        suggestion=(
                            'upmap-type vlanId后面的两个VLAN ID必须相同。'
                        ),
                        slot=slot,
                        vport=vport,
                        profile=f'flow-profile {profile_id}'
                    )

                if flow_number != vport:
                    self._add_vlan_translate_issue(
                        status=(
                            f'Slot {slot} Flow Profile {profile_id} '
                            f'的y={flow_number}与vport={vport}不一致'
                        ),
                        diagnostic=self._make_vlan_translate_diagnostic([
                            ('Flow配置', entry['raw'], True)
                        ]),
                        suggestion=(
                            '请将Flow的组内编号y与vport配置为相同编号。'
                        ),
                        slot=slot,
                        vport=vport,
                        profile=f'flow-profile {profile_id}'
                    )

            # 同一个Flow Profile内不允许两个y使用相同VLAN
            vlan_entries = defaultdict(list)

            for entry in entries:
                vlan_entries[entry['vlan_1']].append(entry)

            for vlan_id, same_vlan_entries in vlan_entries.items():
                if len(same_vlan_entries) <= 1:
                    continue

                flow_ids = [
                    entry['flow_number']
                    for entry in same_vlan_entries
                ]

                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} Flow Profile {profile_id} '
                        f'中的多个y使用了相同VLAN {vlan_id}，'
                        f'y编号为{flow_ids}'
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic([
                        ('重复VLAN的Flow配置', entry['raw'], True)
                        for entry in same_vlan_entries
                    ]),
                    suggestion=(
                        '同一个Flow Profile中，每个y应对应不同的VLAN。'
                    ),
                    slot=slot,
                    profile=f'flow-profile {profile_id}'
                )

    def _validate_tcont_profiles(self, slot_config):
        """检查TCONT的v-port范围及重复编号"""

        slot = slot_config['slot']
        tcont_profiles = slot_config['tcont_profiles']

        for profile_id, entries in tcont_profiles.items():
            vports = [
                entry['vport']
                for entry in entries
            ]

            invalid_vports = sorted({
                vport
                for vport in vports
                if not 1 <= vport <= 16
            })

            duplicate_vports = sorted([
                vport
                for vport, count in Counter(vports).items()
                if count > 1
            ])

            problems = []

            if invalid_vports:
                problems.append(
                    f'超出1～16范围的v-port：{invalid_vports}'
                )

            if duplicate_vports:
                problems.append(
                    f'重复的v-port：{duplicate_vports}'
                )

            if not problems:
                continue

            self._add_vlan_translate_issue(
                status=(
                    f'Slot {slot} TCONT Profile {profile_id}配置异常：'
                    + '；'.join(problems)
                ),
                diagnostic=self._make_vlan_translate_diagnostic([
                    ('TCONT配置', entry['raw'], True)
                    for entry in entries
                ]),
                suggestion=(
                    'TCONT的v-port范围必须为1～16，'
                    '同一个模板内不能重复配置相同v-port。'
                ),
                slot=slot,
                profile=f'tcont-bind-profile {profile_id}',
                issue_key=(
                    'invalid_tcont_profile',
                    slot,
                    profile_id
                )
            )

    def _validate_service_profile_pairs(self, slot_config):
        """
        根据ONU的service绑定关系，检查：

        Flow Profile中的y编号
            ==
        TCONT Profile中的v-port编号

        使用Counter比较，因此编号和数量都必须一一对应。
        """
        slot = slot_config['slot']
        flow_profiles = slot_config['flow_profiles']
        tcont_profiles = slot_config['tcont_profiles']
        services = slot_config['services']

        profile_pairs = defaultdict(list)

        for (port, ont), service_list in services.items():
            if len(service_list) > 1:
                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} PON {port} ONT {ont} '
                        f'检测到多条Service绑定'
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic([
                        ('Service配置', service['raw'], True)
                        for service in service_list
                    ]),
                    suggestion='一个ONU应只保留一条正确的Service绑定。',
                    slot=slot,
                    port=port,
                    ont=ont,
                    issue_key=(
                        'multiple_service',
                        slot,
                        port,
                        ont
                    )
                )

            for service in service_list:
                pair = (
                    service['flow_profile'],
                    service['tcont_profile']
                )

                profile_pairs[pair].append({
                    'port': port,
                    'ont': ont,
                    'raw': service['raw']
                })

        # 同一组绑定关系只检查一次
        for (
                flow_profile_id,
                tcont_profile_id
        ), service_references in profile_pairs.items():

            flow_entries = flow_profiles.get(flow_profile_id)
            tcont_entries = tcont_profiles.get(tcont_profile_id)
            service_reference = service_references[0]

            if not flow_entries:
                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} 的Service引用了不存在的'
                        f'Flow Profile {flow_profile_id}'
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic([
                        ('Service配置', service_reference['raw'], True)
                    ]),
                    suggestion=(
                        '请创建对应Flow Profile，'
                        '或修改Service绑定的flow-profile编号。'
                    ),
                    slot=slot,
                    port=service_reference['port'],
                    ont=service_reference['ont'],
                    profile=f'flow-profile {flow_profile_id}',
                    issue_key = (
                        'missing_flow_profile',
                        slot,
                        flow_profile_id
                    )
                )

            if not tcont_entries:
                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} 的Service引用了不存在的'
                        f'TCONT Profile {tcont_profile_id}'
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic([
                        ('Service配置', service_reference['raw'], True)
                    ]),
                    suggestion=(
                        '请创建对应TCONT Profile，'
                        '或修改Service绑定的tcont-bind-profile编号。'
                    ),
                    slot=slot,
                    port=service_reference['port'],
                    ont=service_reference['ont'],
                    profile=f'tcont-bind-profile {tcont_profile_id}',
                    issue_key=(
                        'missing_tcont_profile',
                        slot,
                        tcont_profile_id
                    )
                )

            if not flow_entries or not tcont_entries:
                continue

            flow_y_counter = Counter(
                entry['flow_number']
                for entry in flow_entries
            )

            tcont_vport_counter = Counter(
                entry['vport']
                for entry in tcont_entries
            )

            if flow_y_counter == tcont_vport_counter:
                continue

            missing_tcont_vports = sorted(
                (flow_y_counter - tcont_vport_counter).elements()
            )

            extra_tcont_vports = sorted(
                (tcont_vport_counter - flow_y_counter).elements()
            )

            difference_text = []

            if missing_tcont_vports:
                difference_text.append(
                    f'TCONT缺少v-port {missing_tcont_vports}'
                )

            if extra_tcont_vports:
                difference_text.append(
                    f'TCONT多出v-port {extra_tcont_vports}'
                )

            self._add_vlan_translate_issue(
                status=(
                    f'Slot {slot} Flow Profile {flow_profile_id} '
                    f'与TCONT Profile {tcont_profile_id}未一一对应；'
                    f'Flow y={list(flow_y_counter.elements())}，'
                    f'TCONT v-port={list(tcont_vport_counter.elements())}；'
                    + '；'.join(difference_text)
                ),
                diagnostic=self._make_vlan_translate_diagnostic(
                    [
                        ('Service配置', service_reference['raw'], True)
                    ]
                    + [
                        ('Flow配置', entry['raw'], True)
                        for entry in flow_entries
                    ]
                    + [
                        ('TCONT配置', entry['raw'], True)
                        for entry in tcont_entries
                    ]
                ),
                suggestion=(
                    '绑定的Flow Profile和TCONT Profile必须具有'
                    '完全相同的虚接口编号和数量。'
                ),
                slot=slot,
                profile=(
                    f'flow-profile {flow_profile_id} / '
                    f'tcont-bind-profile {tcont_profile_id}'
                ),
                issue_key=(
                    'flow_tcont_mismatch',
                    slot,
                    flow_profile_id,
                    tcont_profile_id
                )
            )
    def _validate_onu_virtual_port_links(self, slot_config):
        """
        检查：
        1. 已经SN绑定的ONU必须配置Service；
        2. Flow中的VPORT必须与ONU的virtual-port完全一致。

        Flow与TCONT由_validate_service_profile_pairs检查；
        Flow与Translate由方向A、方向B检查。
        """

        slot = slot_config['slot']
        services = slot_config['services']
        flow_profiles = slot_config['flow_profiles']
        ont_bindings = slot_config.get('ont_bindings', {})
        virtual_ports = slot_config.get('virtual_ports', {})

        # 从SN绑定的ONU开始遍历，所以即使没有Translate也能发现
        for (port, ont), binding_list in ont_bindings.items():
            if not 1 <= port <= 16 or not 1 <= ont <= 256:
                continue

            onu_key = (port, ont)
            service_list = services.get(onu_key, [])

            # 已绑定，但没有Service
            if not service_list:
                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} PON {port} ONT {ont} '
                        f'已经完成SN绑定，但没有配置Service绑定'
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic([
                        ('ONU绑定信息', item['raw'], True)
                        for item in binding_list
                    ]),
                    suggestion=(
                        '请配置service flow-profile和'
                        'tcont-bind-profile。'
                    ),
                    slot=slot,
                    port=port,
                    ont=ont,
                    issue_key=(
                        'missing_service',
                        slot,
                        port,
                        ont
                    )
                )
                continue

            # 多Service已经由其他方法告警，此处不选择其中一条
            if len(service_list) != 1:
                continue

            service = service_list[0]
            flow_profile_id = service['flow_profile']
            flow_entries = flow_profiles.get(flow_profile_id)

            # Flow不存在已经由其他方法告警
            if not flow_entries:
                continue

            # Flow自身y/vport异常已经由_validate_flow_profiles告警，
            # 此时跳过四方关联，避免产生派生告警
            flow_structure_valid = all(
                entry['flow_number'] == entry['vport']
                and 1 <= entry['vport'] <= 16
                for entry in flow_entries
            )

            if not flow_structure_valid:
                continue

            flow_vports = {
                entry['vport']
                for entry in flow_entries
            }

            virtual_port_entries = virtual_ports.get(onu_key, [])

            onu_virtual_ports = {
                entry['vport']
                for entry in virtual_port_entries
                if 1 <= entry['vport'] <= 16
            }

            if flow_vports == onu_virtual_ports:
                continue

            missing_virtual_ports = sorted(
                flow_vports - onu_virtual_ports
            )

            extra_virtual_ports = sorted(
                onu_virtual_ports - flow_vports
            )

            differences = []

            if missing_virtual_ports:
                differences.append(
                    f'ONU缺少virtual-port {missing_virtual_ports}'
                )

            if extra_virtual_ports:
                differences.append(
                    f'ONU多出virtual-port {extra_virtual_ports}'
                )

            self._add_vlan_translate_issue(
                status=(
                    f'Slot {slot} PON {port} ONT {ont} '
                    f'的ONU virtual-port与Flow Profile '
                    f'{flow_profile_id}不一致；'
                    f'Flow VPORT={sorted(flow_vports)}，'
                    f'ONU virtual-port={sorted(onu_virtual_ports)}；'
                    + '；'.join(differences)
                ),
                diagnostic=self._make_vlan_translate_diagnostic(
                    [
                        ('ONU绑定信息', item['raw'], False)
                        for item in binding_list
                    ]
                    + [
                        ('ONU Service', service['raw'], True)
                    ]
                    + [
                        ('Flow配置', item['raw'], True)
                        for item in flow_entries
                    ]
                    + [
                        (
                            'ONU virtual-port配置',
                            item['raw'],
                            True
                        )
                        for item in virtual_port_entries
                    ]
                ),
                suggestion=(
                    '请确保Flow、TCONT、ONU virtual-port和'
                    'VLAN Translate中的虚接口编号完全一致。'
                ),
                slot=slot,
                port=port,
                ont=ont,
                profile=f'flow-profile {flow_profile_id}',
                issue_key=(
                    'flow_onu_virtual_port_mismatch',
                    slot,
                    port,
                    ont,
                    flow_profile_id
                )
            )
    def _validate_vlan_translate_rows(self, translate_entries):
        """检查VLAN Translate表格自身的范围、VLAN和重复键规则"""

        translate_groups = defaultdict(list)

        for entry in translate_entries:
            key = (
                entry['slot'],
                entry['port'],
                entry['ont'],
                entry['vport']
            )
            translate_groups[key].append(entry)

        for key, duplicate_entries in translate_groups.items():
            if len(duplicate_entries) <= 1:
                continue

            slot, port, ont, vport = key

            self._add_vlan_translate_issue(
                status=(
                    f'Slot {slot} PON {port} ONT {ont} '
                    f'VPORT {vport}存在'
                    f'{len(duplicate_entries)}条重复的'
                    f'VLAN Translate记录'
                ),
                diagnostic=self._make_vlan_translate_diagnostic([
                    ('重复VLAN Translate', item['raw'], True)
                    for item in duplicate_entries
                ]),
                suggestion=(
                    '同一个Slot/PON/ONT/VPORT只能保留一条'
                    'VLAN Translate记录。'
                ),
                slot=slot,
                port=port,
                ont=ont,
                vport=vport,
                issue_key=(
                    'duplicate_translate',
                    slot,
                    port,
                    ont,
                    vport
                )
            )
        for entry in translate_entries:
            problems = []

            if not 1 <= entry['slot'] <= 17:
                problems.append(
                    f"Slot {entry['slot']}超出1～17范围"
                )

            if not 1 <= entry['port'] <= 16:
                problems.append(
                    f"PON {entry['port']}超出1～16范围"
                )

            if not 1 <= entry['ont'] <= 256:
                problems.append(
                    f"ONT {entry['ont']}超出1～256范围"
                )

            if not 1 <= entry['vport'] <= 16:
                problems.append(
                    f"VPORT {entry['vport']}超出1～16范围"
                )

            if not 1 <= entry['svid'] <= 4095:
                problems.append(
                    f"svid {entry['svid']}超出1～4095范围"
                )

            if not 1 <= entry['new_svid'] <= 4095:
                problems.append(
                    f"new-svid {entry['new_svid']}超出1～4095范围"
                )

            if entry['svid'] != entry['new_svid']:
                problems.append(
                    f"svid={entry['svid']}与"
                    f"new-svid={entry['new_svid']}不一致"
                )

            if not problems:
                continue

            self._add_vlan_translate_issue(
                status=(
                    f"Slot {entry['slot']} PON {entry['port']} "
                    f"ONT {entry['ont']} VPORT {entry['vport']}："
                    + '；'.join(problems)
                ),
                diagnostic=self._make_vlan_translate_diagnostic([
                    ('VLAN Translate', entry['raw'], True)
                ]),
                suggestion=(
                    '请确认Slot、PON、ONT、VPORT和VLAN范围正确，'
                    '并确保svid与new-svid相同。'
                ),
                slot=entry['slot'],
                port=entry['port'],
                ont=entry['ont'],
                vport=entry['vport'],
                issue_key=(
                    'invalid_translate_row',
                    entry['line_index']
                )
            )

    def _validate_vlan_translate_links(
            self,
            translate_entries,
            slot_configs
    ):
        """
        方向A检查：

        VLAN Translate
            -> ONU Service
            -> Flow Profile
            -> 对应VPORT的VLAN

        检查每条VLAN Translate是否能找到对应的ONU、Service和Flow，
        并检查Translate中的VLAN是否与Flow中的VLAN一致。
        """
        for translate in translate_entries:
            slot = translate['slot']
            port = translate['port']
            ont = translate['ont']
            vport = translate['vport']

            # 范围错误已经由表格自身检查报告，
            # 不再继续产生关联类派生告警
            if not (
                    1 <= slot <= 17
                    and 1 <= port <= 16
                    and 1 <= ont <= 256
                    and 1 <= vport <= 16
            ):
                continue

            slot_config = slot_configs.get(slot)

            if not slot_config:
                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} PON {port} ONT {ont} '
                        f'VPORT {vport}存在VLAN Translate，'
                        f'但未找到对应Slot配置'
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic([
                        ('VLAN Translate', translate['raw'], True)
                    ]),
                    suggestion='请检查Slot配置块是否存在或板卡类型是否正确。',
                    slot=slot,
                    port=port,
                    ont=ont,
                    vport=vport
                )
                continue
            onu_key = (port, ont)

            # 第一步：先确认该ONU是否有SN绑定信息
            binding_list = slot_config.get(
                'ont_bindings',
                {}
            ).get(onu_key, [])

            ont_declarations = slot_config.get(
                'ont_declarations',
                {}
            ).get(onu_key, [])

            if not binding_list:
                diagnostic_rows = [
                    ('VLAN Translate', translate['raw'], True)
                ]

                # 如果出现了ont N，但里面没有SN，可以一起显示
                diagnostic_rows.extend([
                    ('发现的ONT配置入口', item['raw'], False)
                    for item in ont_declarations
                ])

                if ont_declarations:
                    status = (
                        f'Slot {slot} PON {port} ONT {ont} '
                        f'VPORT {vport}存在VLAN Translate，'
                        f'也找到了ONT配置入口，但未找到SN绑定信息'
                    )
                else:
                    status = (
                        f'Slot {slot} PON {port} ONT {ont} '
                        f'VPORT {vport}存在VLAN Translate，'
                        f'但未找到该ONU的绑定信息'
                    )

                self._add_vlan_translate_issue(
                    status=status,
                    diagnostic=self._make_vlan_translate_diagnostic(
                        diagnostic_rows
                    ),
                    suggestion=(
                        f'请确认在interface gpon-olt '
                        f'{slot}/{port}下存在ONU的绑定信息：'
                    ),
                    slot=slot,
                    port=port,
                    ont=ont,
                    vport=vport,
                    issue_key=(
                        'missing_ont_binding',
                        slot,
                        port,
                        ont
                    )
                )
                continue

            # 第二步：ONU已绑定，再检查Service绑定
            service_list = slot_config.get(
                'services',
                {}
            ).get(onu_key, [])

            if not service_list:
                binding = binding_list[0]

                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} PON {port} ONT {ont} '
                        f'已绑定ONU（SN: {binding["sn"]}），'
                        f'VPORT {vport}存在VLAN Translate，'
                        f'但该ONU未配置Service绑定'
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic([
                        (
                            'VLAN Translate',
                            translate['raw'],
                            True
                        ),
                        (
                            'ONU配置',
                            binding['ont_raw'],
                            False
                        ),
                        (
                            'ONU绑定信息',
                            binding['raw'],
                            True
                        )
                    ]),
                    suggestion=(
                        '请确认该ONU配置了：'
                        'service flow-profile X '
                        'tcont-bind-profile Y svc-type ...'
                    ),
                    slot=slot,
                    port=port,
                    ont=ont,
                    vport=vport,
                    issue_key=(
                        'missing_service',
                        slot,
                        port,
                        ont
                    )
                )
                continue

            # 已确认正常情况下只有一条Service
            service = service_list[0]
            flow_profile_id = service['flow_profile']

            flow_entries = slot_config['flow_profiles'].get(
                flow_profile_id
            )

            if not flow_entries:
                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} PON {port} ONT {ont} '
                        f'引用的Flow Profile {flow_profile_id}不存在'
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic([
                        ('VLAN Translate', translate['raw'], True),
                        ('ONU Service', service['raw'], True)
                    ]),
                    suggestion=(
                        '请创建对应的Flow Profile，'
                        '或修正ONU Service绑定。'
                    ),
                    slot=slot,
                    port=port,
                    ont=ont,
                    vport=vport,
                    profile=f'flow-profile {flow_profile_id}',
                    issue_key=(
                        'missing_flow_profile',
                        slot,
                        flow_profile_id
                    )
                )
                continue

            # 要求Flow中的y和vport都等于Translate的vport
            matching_flows = [
                entry for entry in flow_entries
                if entry['flow_number'] == vport
                and entry['vport'] == vport
            ]

            if not matching_flows:
                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} PON {port} ONT {ont} '
                        f'的Flow Profile {flow_profile_id}中'
                        f'未找到y={vport}且vport={vport}的Flow配置'
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic(
                        [
                            ('VLAN Translate', translate['raw'], True),
                            ('ONU Service', service['raw'], True)
                        ]
                        + [
                            ('Flow配置', entry['raw'], False)
                            for entry in flow_entries
                        ]
                    ),
                    suggestion=(
                        f'请在Flow Profile {flow_profile_id}中'
                        f'正确配置y={vport}、vport={vport}。'
                    ),
                    slot=slot,
                    port=port,
                    ont=ont,
                    vport=vport,
                    profile=f'flow-profile {flow_profile_id}',
                    issue_key=(
                        'translate_missing_flow_vport',
                        slot,
                        port,
                        ont,
                        flow_profile_id,
                        vport
                    )
                )
                continue

            if len(matching_flows) > 1:
                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} Flow Profile {flow_profile_id} '
                        f'中存在多条y/vport={vport}的Flow配置'
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic(
                        [
                            ('VLAN Translate', translate['raw'], True),
                            ('ONU Service', service['raw'], True)
                        ]
                        + [
                            ('重复Flow配置', entry['raw'], True)
                            for entry in matching_flows
                        ]
                    ),
                    suggestion='请删除重复的Flow虚接口配置。',
                    slot=slot,
                    port=port,
                    ont=ont,
                    vport=vport,
                    profile=f'flow-profile {flow_profile_id}',
                    issue_key=(
                        'duplicate_matching_flow',
                        slot,
                        flow_profile_id,
                        vport
                    )
                )
                continue

            flow = matching_flows[0]

            # 内部VLAN已经分别检查。
            # 只有两边内部都合法时，再进行Translate和Flow的交叉比较。
            translate_vlan_valid = (
                1 <= translate['svid'] <= 4095
                and 1 <= translate['new_svid'] <= 4095
                and translate['svid'] == translate['new_svid']
            )

            flow_vlan_valid = (
                1 <= flow['vlan_1'] <= 4095
                and 1 <= flow['vlan_2'] <= 4095
                and flow['vlan_1'] == flow['vlan_2']
            )

            if (
                    translate_vlan_valid
                    and flow_vlan_valid
                    and translate['svid'] != flow['vlan_1']
            ):
                self._add_vlan_translate_issue(
                    status=(
                        f'Slot {slot} PON {port} ONT {ont} '
                        f'VPORT {vport}的VLAN Translate VLAN为'
                        f"{translate['svid']}，"
                        f'但Flow Profile {flow_profile_id}中为'
                        f"{flow['vlan_1']}"
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic([
                        ('VLAN Translate', translate['raw'], True),
                        ('ONU Service', service['raw'], True),
                        ('Flow配置', flow['raw'], True)
                    ]),
                    suggestion=(
                        '请确保VLAN Translate的svid/new-svid'
                        '与绑定Flow Profile中对应vport的vlanId一致。'
                    ),
                    slot=slot,
                    port=port,
                    ont=ont,
                    vport=vport,
                    profile=f'flow-profile {flow_profile_id}',
                    issue_key=(
                        'translate_flow_vlan_mismatch',
                        slot,
                        port,
                        ont,
                        vport
                    )
                )

    def _validate_flow_to_vlan_translate_links(
            self,
            translate_entries,
            slot_configs
    ):
        """
        方向B检查：

        ONU Service
            -> Flow Profile
            -> Flow中的每一个VPORT
            -> VLAN Translate是否存在对应记录

        对应关系：
            (slot, port, ont, vport)

        注意：
        1. VLAN值是否正确由方向A检查；
        2. 本方法主要检查Flow中存在、但Translate中缺失的VPORT；
        3. Flow自身存在y/vport不一致或两个vlanId不一致时，
           已由_validate_flow_profiles()报告，此处跳过，避免重复告警。
        """

        # 建立VLAN Translate索引
        # key: (slot, port, ont, vport)
        translate_index = defaultdict(list)

        for translate in translate_entries:
            if not (
                    1 <= translate['slot'] <= 17
                    and 1 <= translate['port'] <= 16
                    and 1 <= translate['ont'] <= 256
                    and 1 <= translate['vport'] <= 16
            ):
                continue

            key = (
                translate['slot'],
                translate['port'],
                translate['ont'],
                translate['vport']
            )
            translate_index[key].append(translate)

        # 避免重复Flow配置导致重复报告同一个缺失项
        reported_missing = set()

        for slot in sorted(slot_configs):
            slot_config = slot_configs[slot]

            flow_profiles = slot_config['flow_profiles']
            services = slot_config['services']

            # 遍历该Slot下所有已绑定Service的ONU
            for (port, ont), service_list in services.items():

                # 方向B也必须先确认这是一个已经绑定的ONU
                binding_list = slot_config.get(
                    'ont_bindings',
                    {}
                ).get((port, ont), [])

                if not binding_list:
                    # 没有SN绑定的ONU，不继续执行Flow -> Translate检查，
                    # 避免误报“缺少VLAN Translate”
                    continue

                for service in service_list:
                    flow_profile_id = service['flow_profile']

                    flow_entries = flow_profiles.get(flow_profile_id)

                    # Flow Profile不存在的问题，
                    # 已经由_validate_service_profile_pairs()报告
                    if not flow_entries:
                        continue

                    # 检查绑定Flow Profile中的每个Flow VPORT
                    for flow in flow_entries:
                        flow_number = flow['flow_number']
                        flow_vport = flow['vport']
                        vlan_1 = flow['vlan_1']
                        vlan_2 = flow['vlan_2']

                        # Flow自身结构异常已经有独立告警，
                        # 此处跳过，防止产生派生的重复告警
                        if flow_number != flow_vport:
                            continue

                        if vlan_1 != vlan_2:
                            continue

                        if not 1 <= vlan_1 <= 4095:
                            continue

                        if not 1 <= flow_number <= 16:
                            continue

                        translate_key = (
                            slot,
                            port,
                            ont,
                            flow_vport
                        )

                        # 已存在对应Translate。
                        # 具体VLAN是否一致，由方向A继续检查。
                        if translate_index.get(translate_key):
                            continue

                        missing_key = (
                            slot,
                            port,
                            ont,
                            flow_profile_id,
                            flow_vport
                        )

                        if missing_key in reported_missing:
                            continue

                        reported_missing.add(missing_key)

                        self._add_vlan_translate_issue(
                            status=(
                                f'Slot {slot} PON {port} ONT {ont} '
                                f'绑定的Flow Profile {flow_profile_id}中'
                                f'存在VPORT {flow_vport}，VLAN为{vlan_1}，'
                                f'但VLAN Translate表中缺少对应记录'
                            ),
                            diagnostic=self._make_vlan_translate_diagnostic([
                                (
                                    'ONU Service',
                                    service['raw'],
                                    True
                                ),
                                (
                                    'Flow配置',
                                    flow['raw'],
                                    True
                                )
                            ]),
                            suggestion=(
                                f'请为Slot {slot} PON {port} ONT {ont} '
                                f'的VPORT {flow_vport}增加VLAN Translate配置，'
                                f'并确保svid和new-svid均为{vlan_1}。'
                            ),
                            slot=slot,
                            port=port,
                            ont=ont,
                            vport=flow_vport,
                            profile=f'flow-profile {flow_profile_id}'
                        )

    def check_vlan_translate_consistency_in_system(self, lines):
        """VLAN Translate、Flow、TCONT、ONU Service综合检查入口"""

        translate_block = self._get_nth_command_block(
            lines,
            '#*** brief-show vlan-translate ***',
            '#*** brief-show interface ***',
            occurrence=2
        )

        configuration_block = self._get_nth_command_block(
            lines,
            '#*** brief-show configuration running ***',
            '#*** brief-show ont ***',
            occurrence=2
        )

        translate_entries = []
        slot_configs = {}

        if translate_block is None:
            self._add_vlan_translate_issue(
                status=(
                    '未找到第二个brief-show vlan-translate区段，'
                    '或未找到其结束标记brief-show interface'
                ),
                diagnostic=self._make_vlan_translate_diagnostic([
                    (
                        '需要的区段',
                        '#*** brief-show vlan-translate *** '
                        '至 #*** brief-show interface ***',
                        True
                    )
                ]),
                suggestion='请确认log_system_info.txt内容完整。'
            )
        else:
            translate_entries = self._parse_vlan_translate_table(
                translate_block
            )

            self._validate_vlan_translate_rows(
                translate_entries
            )

        if configuration_block is None:
            self._add_vlan_translate_issue(
                status=(
                    '未找到第二个brief-show configuration running区段，'
                    '或未找到其结束标记brief-show ont'
                ),
                diagnostic=self._make_vlan_translate_diagnostic([
                    (
                        '需要的区段',
                        '#*** brief-show configuration running *** '
                        '至 #*** brief-show ont ***',
                        True
                    )
                ]),
                suggestion='请确认log_system_info.txt内容完整。'
            )
        else:
            slot_configs = self._parse_vlan_slot_configurations(
                configuration_block
            )

            if not slot_configs:
                self._add_vlan_translate_issue(
                    status=(
                        'configuration running区段内'
                        '未解析到有效的Slot配置块'
                    ),
                    diagnostic=self._make_vlan_translate_diagnostic([
                        (
                            '期望格式',
                            '#slot 1 GPFB ... #slot 1 GPFB end',
                            True
                        )
                    ]),
                    suggestion=(
                        '请确认板卡类型为GPFB、XGFSA或XGFCA，'
                        '并检查Slot开始和结束标记。'
                    )
                )
            for slot_config in slot_configs.values():
                self._validate_flow_profiles(slot_config)
                self._validate_tcont_profiles(slot_config)
                self._validate_service_profile_pairs(slot_config)
                self._validate_onu_virtual_port_links(slot_config)

        # 只有两个区段都存在时才执行关联检查
        if (
                translate_block is not None
                and configuration_block is not None
        ):
            # 方向A：
            # VLAN Translate -> ONU Service -> Flow Profile
            self._validate_vlan_translate_links(
                translate_entries,
                slot_configs
            )

            # 方向B：
            # ONU Service -> Flow Profile -> VLAN Translate
            self._validate_flow_to_vlan_translate_links(
                translate_entries,
                slot_configs
            )

    def check_temperature_in_system(self, lines):
        """在系统信息文件中检查温度信息"""
        self._find_second_occurrence_and_process(
            lines,
            '#*** brief-show temperature ***',
            '#*** show cpu-memory ***',
            self.check_temperature_values
        )

    def check_temperature_values(self, lines):
        # 正则表达式匹配温度
        csm_pattern = re.compile(r'(CSM Temperature|Peer CSM Temperature)\s*:\s*(\d+)\s*Celsius')
        slot_pattern = re.compile(r'Slot\[\s*(\d+)]\s*Temperature\s*:\s*(\d+)\s*Celsius')

        for line in lines:
            csm_match = csm_pattern.search(line)
            slot_match = slot_pattern.search(line)

            if csm_match:
                temp_value = int(csm_match.group(2))
                if temp_value > 110:
                    diagnostic_text = self.highlight_line_in_text(''.join(lines), line.strip())
                    self.results_temperature.append({
                        '部件': csm_match.group(1),
                        '温度': temp_value,
                        '状态': "超过110°C",
                        '诊断原文': diagnostic_text
                    })

            elif slot_match:
                slot_number = slot_match.group(1)
                temp_value = int(slot_match.group(2))
                if temp_value > 90:
                    diagnostic_text = self.highlight_line_in_text(''.join(lines), line.strip())
                    self.results_temperature.append({
                        '部件': f"Slot[{slot_number}]",
                        '温度': temp_value,
                        '状态': "超过90°C",
                        '诊断原文': diagnostic_text
                    })



    def check_ont_transceiver_in_file(self, lines):
        """专门处理光功率文件"""
        data_pattern = re.compile(r'(\d+/\d+/\d+)\s+([\d.]+)\s+([-.\d]+)\s+([-.\d]+)\s+([\d.]+)\s+([\d.]+)')
        logging.debug(f"开始检查transceiver数据，共{len(lines)}行")

        for i, line in enumerate(lines):
            data_match = data_pattern.match(line.strip())
            if data_match:
                ont, voltage, rx_power, tx_power, bias_current, temperature = data_match.groups()
                rx_power = float(rx_power)
                logging.debug(
                    f"解析数据 - ONT: {ont}, 电压: {voltage}, Rx功率: {rx_power}, Tx功率: {tx_power}, 偏置电流: {bias_current}, 温度: {temperature}")

                # 修改后的光功率判断逻辑
                if rx_power == 0:
                    logging.debug(f"光功率为0 - ONT: {ont}, Rx功率: {rx_power}")
                    status = "ONU不在线"
                elif rx_power > -10:  # 光功率过高
                    logging.debug(f"光功率过高 - ONT: {ont}, Rx功率: {rx_power}")
                    status = "光功率过高"
                elif -10 >= rx_power > -15:  # 光功率较高
                    logging.debug(f"光功率较高 - ONT: {ont}, Rx功率: {rx_power}")
                    status = "光功率较高"
                elif -15 >= rx_power > -23:  # 光功率正常
                    logging.debug(f"光功率正常 - ONT: {ont}, Rx功率: {rx_power}")
                    continue  # 正常范围，不添加到结果中
                elif rx_power <= -23:  # 光功率过低
                    logging.debug(f"光功率过低 - ONT: {ont}, Rx功率: {rx_power}")
                    status = "光功率过低"
                else:
                    logging.debug(f"光功率异常 - ONT: {ont}, Rx功率: {rx_power}")
                    status = "光功率异常"

                # 只获取异常行及其上下文（前后各2行）
                start_idx = max(0, i - 2)
                end_idx = min(len(lines), i + 3)
                context_lines = lines[start_idx:end_idx]

                # 只高亮当前行，但显示上下文
                diagnostic_text = self.highlight_line_in_text(''.join(context_lines), line.strip())
                self.results_transceiver.append({
                    'ONT': ont,
                    'Rx power': rx_power,
                    '状态': status,
                    '诊断原文': diagnostic_text
                })

        if not self.results_transceiver:
            logging.debug("没有发现光功率异常")

    def check_outband_ip_forbidden_in_system(self, lines):
        """【带外地址ip禁止】在系统信息文件中检查带外管理IP是否落入禁止网段"""
        self._find_second_occurrence_and_process(
            lines,
            '#*** ip show ip ***',
            '#*** ip show arp ***',
            self.check_outband_ip_forbidden
        )

    def check_outband_ip_forbidden(self, lines):
        ip_pattern = re.compile(r'Management IP address\s*:\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})')

        mgmt_ip = None
        hit_line = None

        for line in lines:
            m = ip_pattern.search(line)
            if m:
                mgmt_ip = m.group(1)
                hit_line = line.strip()
                break

        # 没找到管理IP就不报错（也可以改成提示）
        if not mgmt_ip:
            return

        # 简单合法性校验
        parts = mgmt_ip.split('.')
        if len(parts) != 4:
            return
        try:
            o1, o2, o3, o4 = [int(x) for x in parts]
        except ValueError:
            return
        if not all(0 <= x <= 255 for x in (o1, o2, o3, o4)):
            return

        forbidden = (
                (o1 == 192 and o2 == 168 and o3 == 100)  # 192.168.100.0/24
                or
                (o1 == 172 and o2 == 31)  # 172.31.0.0/16
        )

        if forbidden:
            diagnostic_text = self.highlight_line_in_text(''.join(lines), hit_line)
            self.results_outband_ip_forbidden.append({
                '检查项': '带外地址ip禁止',
                '状态': f'带外管理IP({mgmt_ip})落入禁止网段(192.168.100.0/24 或 172.31.0.0/16)',
                '管理IP': mgmt_ip,
                '诊断原文': diagnostic_text,
                '建议': '请修改带外管理地址，避免使用 192.168.100.x 与 172.31.x.x 网段'
            })

    def check_inband_ip_forbidden_in_system(self, lines):
        """【带内地址ip禁止】在系统信息文件中检查带内管理IP是否落入禁止网段"""
        self._find_second_occurrence_and_process(
            lines,
            '#*** ip show route ***',
            '#*** ipv6 show ipv6 ***',
            self.check_inband_ip_forbidden
        )

    def check_inband_ip_forbidden(self, lines):
        # 匹配默认路由行：0.0.0.0 0.0.0.0 <gateway>
        route_pattern = re.compile(
            r'^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+([0-9]{1,3}(?:\.[0-9]{1,3}){3})\s*$'
        )

        gw_ip = None
        hit_line = None

        for line in lines:
            m = route_pattern.match(line)
            if m:
                gw_ip = m.group(1)
                hit_line = line.rstrip('\n')
                break

        # 没有默认路由就不报错（你也可改为提示）
        if not gw_ip:
            return

        # 合法性校验
        parts = gw_ip.split('.')
        if len(parts) != 4:
            return
        try:
            o1, o2, o3, o4 = [int(x) for x in parts]
        except ValueError:
            return
        if not all(0 <= x <= 255 for x in (o1, o2, o3, o4)):
            return

        forbidden = (
                (o1 == 192 and o2 == 168 and o3 == 100)  # 192.168.100.0/24
                or
                (o1 == 172 and o2 == 31)  # 172.31.0.0/16
        )

        if forbidden:
            diagnostic_text = self.highlight_line_in_text(''.join(lines), hit_line.strip())
            self.results_inband_ip_forbidden.append({
                '检查项': '带内地址ip禁止',
                '状态': f'带内管理IP({gw_ip})落入禁止网段(192.168.100.0/24 或 172.31.0.0/16)',
                '网关IP': gw_ip,
                '诊断原文': diagnostic_text,
                '建议': '请修改带内管理IP地址，避免使用 192.168.100.x 与 172.31.x.x 网段'
            })


    def check_ont_status_in_system(self, lines):
        """【ONU状态检测】在log_system_info.txt中解析 brief-show ont 的 Auth/Unauth ONU状态并告警"""
        self._find_second_occurrence_and_process(
            lines,
            '#*** brief-show ont ***',
            '#*** show dual-homing state',
            self.check_ont_status_block
        )

    def check_ont_status_block(self, lines):
        """
        规则：
        - Authenticated：
          - ready：Reason 若为 'no service' -> 告警；Reason 为 none 正常
          - offline：按 Reason 分类告警与建议（dying gasp/inactive/los/ranging failed/admin control auto...)
          - online：按 Reason 分类告警与建议（temp omcc problem/mib audit/其他）
          - standby：固定 none（不告警）
        - Unauthenticated：该段所有ONU都告警
        """
        ont_line_pattern = re.compile(
            r'^\s*(\d+/\d+/(?:\d+|-))\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+?)\s*$'
        )

        def norm(s: str) -> str:
            return re.sub(r'\s+', ' ', (s or '').strip()).lower()

        section = None  # 'auth' / 'unauth'
        in_table = False

        for i, raw in enumerate(lines):
            line = raw.rstrip('\n')

            if line.strip().lower() == 'authenticated':
                section = 'auth'
                in_table = False
                continue
            if line.strip().lower() == 'unauthenticated':
                section = 'unauth'
                in_table = False
                continue

            if line.strip().startswith('ONT') and 'Status' in line:
                in_table = True
                continue

            if not section or not in_table:
                continue

            if (not line.strip()
                    or set(line.strip()) == {'-'}
                    or line.strip().lower().startswith('total')
                    or line.strip().lower().startswith('active')):
                continue

            m = ont_line_pattern.match(line)
            if not m:
                continue

            ont, onu_type, sn, loid, status, find, auth_mode, rest = m.groups()
            status_n = norm(status)
            rest_n = norm(rest)

            multi_reason_candidates = [
                'admin control',
                'temp omcc problem',
                'ranging failed',
                'dying gasp',
                'mib audit',
                'no service',
                'inactive',
                'los',
                'un-auth',
            ]
            reason = None
            for r in multi_reason_candidates:
                if rest_n.startswith(r):
                    reason = r
                    break
            if reason is None:
                reason = rest_n.split(' ')[0] if rest_n else ''

            start_idx = max(0, i - 2)
            end_idx = min(len(lines), i + 3)
            context = ''.join(lines[start_idx:end_idx])
            diag = self.highlight_line_in_text(context, line.strip())

            # Unauthenticated：全部告警
            if section == 'unauth':
                self.results_ont_status.append({
                    '检查项': 'ONU状态检测(Unauthenticated)',
                    'ONT': ont,
                    'SN': sn,
                    'Status': status,
                    'Reason': reason,
                    '状态': '发现未在OLT绑定认证的ONU（Unauthenticated）',
                    '建议': '需要在OLT的PON口下绑定该ONU进行注册',
                    '诊断原文': diag
                })
                continue

            # Authenticated：按状态判断
            if status_n == 'ready':
                if reason == 'no service':
                    self.results_ont_status.append({
                        '检查项': 'ONU状态检测(Authenticated)',
                        'ONT': ont,
                        'SN': sn,
                        'Status': status,
                        'Reason': reason,
                        '状态': 'No Service（未下发业务配置）',
                        '建议': '需要在OLT上给该ONU下发service/业务模板配置',
                        '诊断原文': diag
                    })
                continue

            if status_n == 'offline':
                reason_n = norm(reason)

                state_msg = 'ONU离线(OFFLINE)'
                suggest = '检查光路/供电/ONU侧状态；必要时现场排障。'

                if reason_n == 'admin control ':
                    state_msg = 'ONU离线(OFFLINE)：admin control auto（OLT命令下线）'
                    suggest = '这是OLT侧下发的下线控制：在olt的该onu端口上输入active，或者enable'
                elif reason_n == 'dying gasp':
                    state_msg = 'ONU离线(OFFLINE)：dying gasp（疑似ONU掉电）'
                    suggest = '检查的电源，或者在onu的web界面查看上电时间'
                elif reason_n == 'los':
                    state_msg = 'ONU离线(OFFLINE)：LOS（光信号丢失）'
                    suggest = '重点检查光纤链路/接头/法兰口清洁/弯折/断纤，查看光功率是否异常，ONU是否上电'
                elif reason_n == 'ranging failed':
                    state_msg = 'ONU离线(OFFLINE)：ranging failed（测距/注册失败）'
                    suggest = '重点检查光纤链路/接头/法兰口清洁/弯折/断纤，查看光功率是否异常，ONU是否上电'
                elif reason_n == 'inactive':
                    state_msg = 'ONU离线(OFFLINE)：inactive（光信号丢失）'
                    suggest = '重点检查光纤链路/接头/法兰口清洁/弯折/断纤，查看光功率是否异常，ONU是否上电'

                self.results_ont_status.append({
                    '检查项': 'ONU状态检测(Authenticated)',
                    'ONT': ont,
                    'SN': sn,
                    'Status': status,
                    'Reason': reason,
                    '状态': state_msg,
                    '建议': suggest,
                    '诊断原文': diag
                })
                continue

            if status_n == 'online':
                reason_n = norm(reason)

                state_msg = 'ONU处于ONLINE过渡态（未进入READY）'
                suggest = '建议观察是否可自行转为READY；如持续不恢复再进一步排查。'

                if reason_n == 'temp omcc problem':
                    state_msg = 'ONU处于ONLINE：temp omcc problem（OLT<->ONU OMCI管理通道异常）'
                    suggest = '通常为管理通道异常（omci报文交互异常）：可先尝试重启ONU'
                elif reason_n == 'mib audit':
                    state_msg = 'ONU处于ONLINE：mib audit（正在注册/同步配置）'
                    suggest = '一般属正常注册过程：建议等待几分钟观察是否转为READY；如长时间不转READY再排查ONU，或no掉onu重新注册'

                self.results_ont_status.append({
                    '检查项': 'ONU状态检测(Authenticated)',
                    'ONT': ont,
                    'SN': sn,
                    'Status': status,
                    'Reason': reason,
                    '状态': state_msg,
                    '建议': suggest,
                    '诊断原文': diag
                })
                continue

            if status_n == 'standby':
                continue

            self.results_ont_status.append({
                '检查项': 'ONU状态检测(Authenticated)',
                'ONT': ont,
                'SN': sn,
                'Status': status,
                'Reason': reason,
                '状态': f'ONU状态未知({status})，建议人工确认',
                '诊断原文': diag
            })

    # ————————————————————————————————————————按钮3：导出结果————————————————————————————————————————————————————
    def save_to_html(self):
        try:
            # 检查是否存在任何要导出的数据
            if not (self.results_crc or
                    self.results_version or
                    self.results_time_sync or
                    self.results_cpu_memory or
                    self.results_vlan or
                    self.results_temperature or
                    self.results_transceiver or
                    self.results_outband_ip_forbidden or
                    self.results_inband_ip_forbidden or
                    self.results_ont_status or
                    self.results_vlan_translate):
                self.text_browser.setText("没有要导出的数据。")
                return

            save_path, _ = QFileDialog.getSaveFileName(self, "保存文件", "", "HTML files (*.html);;All Files (*)")
            logging.debug(f"保存文件路径: {save_path}")
            if not save_path:
                self.text_browser.setText("没有选择保存路径。")
                return

            crc_error_solution = """
                                            CRC错误包说明: 
                                            <br>CRC错误表示接收的数据包在传输过程中出现了校验和错误。可能的原因包括：<br>
                                            1. 光纤链路质量差。<br>
                                            2. 光纤接口未插紧。<br>
                                            解决方案：<br>
                                            1. 检查物理连接确保连接紧密。<br>
                                            2. 使用酒精擦拭onu的法兰口和光纤的光口，确保链路上没有打结或过大的弯折，到 onu 的光功率值在-15到-23 dbm之间。<br>
                                            """

            version_check_solution = """
                                            版本问题说明:
                                            <br>确保主控板和线卡板的版本最新版本一致，如果不一致可能导致系统运行问题。<br>
                                            解决方案：<br>
                                            1. 升级或降级版本至与1-A主控版本一致。<br>
                                            """

            time_sync_solution = """
                                            时间同步检测说明:
                                            <br>时间同步问题可能会影响系统日志和事件的准确性。确保设备的本地时间与实际时间一致。<br>
                                            解决方案：<br>
                                            1. 通过命令P3600# time <yyyy/mm/dd/HH/MM/SS> 设置P3617/P3608/P3602 CLI系统时间，时区和夏令时。<br>
                                            """

            cpu_memory_solution = """
                                            CPU和内存使用率检测说明:
                                            <br>确保CPU和内存使用率在合理范围内。如果超过阈值，可能影响系统性能。<br>
                                            解决方案：<br>
                                            1. 检查和优化配置以降低网络中的arp报文。<br>
                                            2. 删除olt存储空间的文件以减少存储内存的使用率。<br>
                                            """

            vlan_usage_solution = """
                                            VLAN 使用检测说明:
                                            <br>确保所有VLAN ID符合网络规范，位于1到24之间的VLAN是pon网络的保留vlan，不建议使用。<br>
                                            解决方案: <br>
                                            1. 不使用vlan 1-24作为业务vlan或者管理vlan。 <br>
                                            """


            # 温度检测说明
            temperature_solution = """
                                            温度检测说明:
                                            <br>确保设备温度在合理范围内。过高的温度可能导致设备性能下降或损坏。<br>
                                            解决方案：<br>
                                            1. 确保设备环境通风良好。<br>
                                            2. 检查设备风扇或冷却系统是否正常工作。<br>
                                            """
            # 光功率检测说明
            transceiver_solution = """
                                            光功率检测说明: 
                                            <br>检测光接收功率是否在-15~-23 dBm的合理范围内，若超出范围可能导致ONU掉线或业务丢包。<br>
                                            光功率判断标准：<br>
                                            1. 光功率 > -10 dBm：光功率过高<br>
                                            2. -10 dBm ≥ 光功率 > -15 dBm：光功率较高<br>
                                            3. -15 dBm ≥ 光功率 > -23 dBm：光功率正常<br>
                                            4. 光功率 ≤ -23 dBm：光功率过低<br>
                                            解决方案:<br>
                                            1. 确保ONT设备和光纤的连接紧密。<br>
                                            2. 检查光纤线路是否有弯折或损坏。<br>
                                            3. 在OLT或ONU的光口处增加光衰。<br>
                                            """

            outband_ip_forbidden_solution = """
                                            带外地址ip禁止说明:
                                            <br>带外管理地址不允许使用以下网段：<br>
                                            1. 192.168.100.0/24<br>
                                            2. 172.31.0.0/16<br>
                                            若使用上述网段，可能与默认规划/现网地址冲突，造成管理不可达或地址冲突问题。<br>
                                            解决方案：<br>
                                            1. 修改 Management IP address 到允许的网段，并确认网关/路由可达。<br>
                                            """

            inband_ip_forbidden_solution = """
                                            带内地址ip禁止说明:
                                            <br>带内管理IP不允许使用以下网段：<br>
                                            1. 192.168.100.0/24<br>
                                            2. 172.31.0.0/16<br>
                                            若使用上述网段，可能与默认规划/现网地址冲突，造成业务/管理不可达或地址冲突问题。<br>
                                            解决方案：<br>
                                            1. 修改带内管理IP到允许的网段，并确认回程路由正确。<br>
                                            """
            ont_status_solution = """
                                            ONU状态检测说明:
                                            <br>解析 brief-show ont 的 Authenticated / Unauthenticated ONU状态。<br>
                                            判定规则：<br>
                                            1) Authenticated: ready + none 为正常；ready + no service 告警。<br>
                                            2) Authenticated: offline 全部告警。<br>
                                            3) Authenticated: online 全部告警。<br>
                                            4) Authenticated: standby 通常为备OLT状态，不告警。<br>
                                            5) Unauthenticated: 未绑定认证ONU，全部告警。<br>
                                            """
            vlan_translate_solution = """
                                            VLAN转换与Flow/TCONT一致性检测说明：<br>
                                            1. Slot范围为1～17，PON范围为1～16，ONT范围为1～256，VPORT范围为1～16。<br>
                                            2. svid和new-svid范围为1～4095，并且二者必须相同。<br>
                                            3. 同一Flow Profile中的y必须从1开始连续递增，范围为1～16。<br>
                                            4. Flow中的两个vlanId必须相同，且y必须与vport相同。<br>
                                            5. 同一Flow Profile中不允许多个y使用相同VLAN。<br>
                                            6. Flow与TCONT的虚接口编号和数量必须一一对应。<br>
                                            7. Flow、TCONT、ONU virtual-port和VLAN Translate中的虚接口必须一致。<br>
                                            8. 同一个Slot/PON/ONT/VPORT只能存在一条VLAN Translate。<br>
                                            9. 已完成SN绑定的ONU必须配置Service绑定。<br>
                                            10. VLAN Translate中的VLAN必须与对应Flow的vlanId一致。<br>
                                        """

            # HTML 内容
            html_content = f"""
                                        <!DOCTYPE html>
                                        <html lang="zh">
                                        <head>
                                            <meta charset="UTF-8">
                                            <meta name="viewport" content="width=device-width, initial-scale=1.0">
                                            <title>检查结果</title>
                                            <link rel="stylesheet" href="https://cdn.datatables.net/1.10.24/css/jquery.dataTables.min.css">
                                            <style>
                                                body {{
                                                    font-family: Arial, sans-serif;
                                                    background-color: #f5f5f5;
                                                    color: #333;
                                                    margin: 0;
                                                    padding: 20px;
                                                }}
                                                .table-container {{
                                                    width: 90%;
                                                    margin: 0 auto;
                                                    background-color: white;
                                                    padding: 20px;
                                                    border-radius: 10px;
                                                    box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
                                                }}
                                                table {{
                                                    width: 100%;
                                                    border-collapse: collapse;
                                                    margin-top: 20px;
                                                }}
                                                th, td {{
                                                    border: 1px solid #dddddd;
                                                    text-align: left;
                                                    padding: 8px;
                                                    word-break: break-word;
                                                }}
                                                th {{
                                                    background-color: #f2f2f2;
                                                }}
                                                td.check-result {{
                                                    width: 10%;
                                                }}
                                                td.details {{
                                                    width: 70%;
                                                    position: relative;
                                                }}
                                                .diagnosis-details {{
                                                    display: none;
                                                    text-align: left;
                                                    margin-top: 10px;
                                                    font-size: 14px;
                                                    background-color: #f9f9f9;
                                                    padding: 10px;
                                                    border-radius: 5px;
                                                    border: 1px solid #ddd;
                                                    white-space: pre-wrap;
                                                    overflow-x: auto;
                                                    max-height: 800px;
                                                    overflow-y: auto;
                                                }}
                                                .highlight-line {{
                                                    background-color: #ffdddd;
                                                }}
                                                .info-container {{
                                                    position: relative;
                                                    display: flex;
                                                    flex-direction: column;
                                                }}
                                                .tooltip {{
                                                    visibility: hidden;
                                                    width: 250px;
                                                    background-color: #f5f5c5;
                                                    color: #333;
                                                    text-align: left;
                                                    border-radius: 10px;
                                                    border: 1px solid #ccc;
                                                    padding: 15px;
                                                    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.15);
                                                    position: absolute;
                                                    z-index: 1;
                                                    opacity: 0;
                                                    transition: opacity 0.3s ease-in-out;
                                                }}
                                                .tooltip::after {{
                                                    content: '';
                                                    position: absolute;
                                                    top: 50%;
                                                    left: -10px;
                                                    transform: translateY(-50%);
                                                    width: 0;
                                                    height: 0;
                                                    border-width: 10px;
                                                    border-style: solid;
                                                    border-color: transparent #f5f5c5 transparent transparent;
                                                    z-index: 1;
                                                }}
                                                .self-hover:hover .tooltip {{
                                                    visibility: visible;
                                                    opacity: 1;
                                                }}
                                                .self-hover {{
                                                    padding: 10px;
                                                    border: 1px solid #ddd;
                                                    margin: 5px 0;
                                                    border-radius: 5px;
                                                    transition: background-color 0.3s;
                                                    cursor: pointer;
                                                }}
                                                .self-hover:hover {{
                                                    background-color: #d3d3d3;
                                                }}
                                                .details-row {{
                                                    display: flex;
                                                    flex-direction: column;
                                                }}
                                                .not-pass-cell {{
                                                    background-color: #ffdddd;
                                                }}
                                                .pass-cell {{
                                                    background-color: #ddffdd;
                                                }}
                                                button {{
                                                    margin-right: 5px;
                                                    width: 20px;
                                                    height: 20px;
                                                    font-size: 12px;
                                                    line-height: 0;
                                                    display: flex;
                                                    justify-content: center;
                                                    align-items: center;
                                                }}
                                                .icon {{
                                                    margin-right: 5px;
                                                    font-size: 20px;
                                                    line-height: 20px;
                                                }}
                                            </style>
                                            <script src="https://code.jquery.com/jquery-3.5.1.min.js"></script>
                                            <script src="https://cdn.datatables.net/1.10.24/js/jquery.dataTables.min.js"></script>
                                            <script>
                                                $(document).ready(function() {{
                                                    $('#results-table').DataTable({{
                                                        "paging": false,
                                                        "ordering": false,
                                                        "info": false,
                                                        "searching": true,
                                                        "language": {{
                                                            "search": "搜索:"
                                                        }}
                                                    }});
                                                }});

                                                function toggleDetails(id, iconId) {{
                                                    var details = document.getElementById(id);
                                                    var icon = document.getElementById(iconId);
                                                    if (details.style.display === "none" || details.style.display === "") {{
                                                        details.style.display = "block";
                                                        icon.innerHTML = "&#x2796;";
                                                    }} else {{
                                                        details.style.display = "none";
                                                        icon.innerHTML = "&#x2795;";
                                                    }}
                                                    event.stopPropagation();
                                                }}

                                                function showTooltip(event, solutionId) {{
                                                    const tooltip = document.getElementById(solutionId);
                                                    tooltip.style.visibility = 'visible';
                                                    tooltip.style.opacity = '1';
                                                    tooltip.style.top = `${{event.currentTarget.offsetTop + event.currentTarget.offsetHeight / 2 - tooltip.offsetHeight / 2}}px`;
                                                    tooltip.style.left = `${{event.currentTarget.offsetLeft + event.currentTarget.offsetWidth + 10}}px`;
                                                    const tooltipRect = tooltip.getBoundingClientRect();
                                                    if (tooltipRect.right > window.innerWidth) {{
                                                        const offsetRight = tooltipRect.right - window.innerWidth;
                                                        tooltip.style.left = `${{tooltip.offsetLeft - offsetRight - 10}}px`;
                                                    }}
                                                }}

                                                function hideTooltip(solutionId) {{
                                                    const tooltip = document.getElementById(solutionId);
                                                    tooltip.style.visibility = 'hidden';
                                                    tooltip.style.opacity = '0';
                                                }}
                                            </script>
                                        </head>
                                        <body>
                                            <div class="table-container">
                                                <h1>检查结果</h1>
                                                <table id="results-table">
                                                    <thead>
                                                        <tr>
                                                            <th>检查号</th>
                                                            <th>检查项目</th>
                                                            <th class="check-result">检查结果</th>
                                                            <th class="details">详细信息</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                        """
            check_number_map = 1

            # CRC错误检测
            detail_id = f"crc_{check_number_map}"
            icon_id = f"icon_{check_number_map}"
            tooltip_id = f"crc_tooltip"

            detail_button = f'<div class="self-hover top-level" onmouseover="showTooltip(event, \'{tooltip_id}\')" onmouseout="hideTooltip(\'{tooltip_id}\')" onclick="toggleDetails(\'{detail_id}\', \'{icon_id}\')"><span id="{icon_id}" class="icon">&#x2795;</span>{len(self.results_crc)} 个 CRC 错包的 PON 口<span id="{tooltip_id}" class="tooltip">{crc_error_solution}</span></div>'
            hidden_details = f"<div id='{detail_id}' class='diagnosis-details'>"

            if self.results_crc:
                for index, result in enumerate(self.results_crc, start=1):
                    slot_no = result.get('单板板号', '未知')
                    port_no = result.get('PON口号', '未知')
                    crc_errors = result.get('PON口接收到的CRC错包数量', '未知')
                    diagnostic_info = result.get('诊断原文', '未知')

                    diagnostic_lines = diagnostic_info.split('\n')
                    highlighted_diagnostic_info = "诊断原文:<br>"
                    for line in diagnostic_lines:
                        if line.strip() != "":
                            if "rxCrcErrors" in line:
                                highlighted_diagnostic_info += f"<span class='highlight-line'>{line.strip()}</span><br>"
                            else:
                                highlighted_diagnostic_info += f"{line.strip()}<br>"

                    sub_detail_id = f"{detail_id}_slot_{index}"
                    sub_icon_id = f"{sub_detail_id}_icon"
                    diagnostic_id = f"{detail_id}_diag_{index}"

                    hidden_details += f"""
                                                    <div id="{sub_detail_id}" class="details-row">
                                                        <div class="self-hover" onclick="toggleDetails('{diagnostic_id}', '{sub_icon_id}')"><span id="{sub_icon_id}" class="icon">&#x2795;</span>槽位号: {slot_no}, PON口号: {port_no}, 错包数量: {crc_errors}</div>
                                                        <div id="{diagnostic_id}" class="diagnosis-details">{highlighted_diagnostic_info}</div>
                                                    </div>
                                                """
                crc_result = "不通过"
                crc_class = "not-pass-cell"
            else:
                hidden_details += "未检测到CRC错包"
                crc_result = "通过"
                crc_class = "pass-cell"
            hidden_details += "</div>"

            html_content += f"""
                                        <tr class="result-row">
                                            <td>{check_number_map}</td>
                                            <td>CRC错包检测</td>
                                            <td class="{crc_class}">{crc_result}</td>
                                            <td class="details">
                                                <div class="info-container">
                                                    {detail_button}
                                                    {hidden_details}
                                                </div>
                                            </td>
                                        </tr>
                                    """
            check_number_map += 1

            # 版本问题
            detail_id = f"version_{check_number_map}"
            icon_id = f"icon_{check_number_map}_version"
            tooltip_id = f"version_tooltip"

            detail_button = f'<div class="self-hover top-level" onmouseover="showTooltip(event, \'{tooltip_id}\')" onmouseout="hideTooltip(\'{tooltip_id}\')" onclick="toggleDetails(\'{detail_id}\', \'{icon_id}\')"><span id="{icon_id}" class="icon">&#x2795;</span>{len(self.results_version)} 处 版本问题<span id="{tooltip_id}" class="tooltip">{version_check_solution}</span></div>'
            hidden_details = f"<div id='{detail_id}' class='diagnosis-details'>"

            if self.results_version:
                for index, result in enumerate(self.results_version, start=1):
                    state = result.get('状态', '未知')
                    diagnostic_info = result.get('诊断原文', '未知')

                    diagnostic_lines = diagnostic_info.split('\n')
                    highlighted_diagnostic_info = "诊断原文:<br>"
                    for line in diagnostic_lines:
                        if line.strip() != "":
                            highlighted_diagnostic_info += f"{line.strip()}<br>"

                    sub_detail_id = f"{detail_id}_state_{index}"
                    sub_icon_id = f"{sub_detail_id}_icon"
                    diagnostic_id = f"{detail_id}_diag_{index}"

                    hidden_details += f"""
                                                    <div id="{sub_detail_id}" class="details-row">
                                                        <div class="self-hover" onclick="toggleDetails('{diagnostic_id}', '{sub_icon_id}')"><span id="{sub_icon_id}" class="icon">&#x2795;</span>状态: {state}</div>
                                                        <div id="{diagnostic_id}" class="diagnosis-details">{highlighted_diagnostic_info}</div>
                                                    </div>
                                                """
                version_result = "不通过"
                version_class = "not-pass-cell"
            else:
                hidden_details += "未检测到版本不一致或版本异常"
                version_result = "通过"
                version_class = "pass-cell"
            hidden_details += "</div>"

            html_content += f"""
                                        <tr class="result-row">
                                            <td>{check_number_map}</td>
                                            <td>版本问题检测</td>
                                            <td class="{version_class}">{version_result}</td>
                                            <td class="details">
                                                <div class="info-container">
                                                    {detail_button}
                                                    {hidden_details}
                                                </div>
                                            </td>
                                        </tr>
                                    """
            check_number_map += 1

            # 时间同步检测
            detail_id = f"time_sync_{check_number_map}"
            icon_id = f"icon_{check_number_map}_time_sync"
            tooltip_id = f"time_sync_tooltip"

            detail_button = f'<div class="self-hover top-level" onmouseover="showTooltip(event, \'{tooltip_id}\')" onmouseout="hideTooltip(\'{tooltip_id}\')" onclick="toggleDetails(\'{detail_id}\', \'{icon_id}\')"><span id="{icon_id}" class="icon">&#x2795;</span>{len(self.results_time_sync)} 处 时间同步问题<span id="{tooltip_id}" class="tooltip">{time_sync_solution}</span></div>'
            hidden_details = f"<div id='{detail_id}' class='diagnosis-details'>"

            if self.results_time_sync:
                for index, result in enumerate(self.results_time_sync, start=1):
                    state = result.get('状态', '未知')
                    local_time = result.get('本地时间', '未知')
                    diagnostic_info = result.get('诊断原文', '未知')

                    diagnostic_lines = diagnostic_info.split('\n')
                    highlighted_diagnostic_info = "诊断原文:<br>"
                    for line in diagnostic_lines:
                        if line.strip() != "":
                            if local_time in line:
                                highlighted_diagnostic_info += f"<span class='highlight-line'>{line.strip()}</span><br>"
                            else:
                                highlighted_diagnostic_info += f"{line.strip()}<br>"

                    sub_detail_id = f"{detail_id}_time_sync_{index}"
                    sub_icon_id = f"{sub_detail_id}_icon"
                    diagnostic_id = f"{detail_id}_diag_{index}"

                    hidden_details += f"""
                                                    <div id="{sub_detail_id}" class="details-row">
                                                        <div class="self-hover" onclick="toggleDetails('{diagnostic_id}', '{sub_icon_id}')"><span id="{sub_icon_id}" class="icon">&#x2795;</span>状态: {state}, 本地时间: {local_time}</div>
                                                        <div id="{diagnostic_id}" class="diagnosis-details">{highlighted_diagnostic_info}</div>
                                                    </div>
                                                """
                time_sync_result = "不通过"
                time_sync_class = "not-pass-cell"
            else:
                hidden_details += "未检测到时间同步问题"
                time_sync_result = "通过"
                time_sync_class = "pass-cell"
            hidden_details += "</div>"

            html_content += f"""
                                        <tr class="result-row">
                                            <td>{check_number_map}</td>
                                            <td>时间同步检测</td>
                                            <td class="{time_sync_class}">{time_sync_result}</td>
                                            <td class="details">
                                                <div class="info-container">
                                                    {detail_button}
                                                    {hidden_details}
                                                </div>
                                            </td>
                                        </tr>
                                    """
            check_number_map += 1

            # CPU和内存使用检测
            detail_id = f"cpu_memory_{check_number_map}"
            icon_id = f"icon_{check_number_map}_cpu_memory"
            tooltip_id = f"cpu_memory_tooltip"

            detail_button = f'<div class="self-hover top-level" onmouseover="showTooltip(event, \'{tooltip_id}\')" onmouseout="hideTooltip(\'{tooltip_id}\')" onclick="toggleDetails(\'{detail_id}\', \'{icon_id}\')"><span id="{icon_id}" class="icon">&#x2795;</span>{len(self.results_cpu_memory)} 处 CPU 或存储内存使用率超限<span id="{tooltip_id}" class="tooltip">{cpu_memory_solution}</span></div>'
            hidden_details = f"<div id='{detail_id}' class='diagnosis-details'>"

            if self.results_cpu_memory:
                for index, result in enumerate(self.results_cpu_memory, start=1):
                    state = result.get('状态', '未知')
                    cpu_usage = result.get('CPU 使用', '未知')
                    memory_usage = result.get('存储内存使用', '未知')
                    diagnostic_info = result.get('诊断原文', '未知')

                    sub_detail_id = f"{detail_id}_usage_{index}"
                    sub_icon_id = f"{sub_detail_id}_icon"
                    diagnostic_id = f"{detail_id}_diag_{index}"

                    # 判断显示 CPU、内存或两者
                    usage_details = []
                    if cpu_usage != '正常':  # 或者检查具体阈值
                        usage_details.append(f"CPU使用: {cpu_usage}")
                    if memory_usage != '正常':  # 或者检查具体阈值
                        usage_details.append(f"存储内存使用: {memory_usage}")

                    usage_text = ', '.join(usage_details)

                    hidden_details += f"""
                                                    <div id="{sub_detail_id}" class="details-row">
                                                        <div class="self-hover" onclick="toggleDetails('{diagnostic_id}', '{sub_icon_id}')"><span id="{sub_icon_id}" class="icon">&#x2795;</span>状态: {state}, {usage_text}</div>
                                                        <div id="{diagnostic_id}" class="diagnosis-details">{diagnostic_info}</div>
                                                    </div>
                                              """

                cpu_memory_result = "不通过"
                cpu_memory_class = "not-pass-cell"
            else:
                hidden_details += "未检测到CPU或Memory使用率问题"
                cpu_memory_result = "通过"
                cpu_memory_class = "pass-cell"
            hidden_details += "</div>"

            html_content += f"""
                    <tr class="result-row">
                        <td>{check_number_map}</td>
                        <td>CPU和内存使用率检测</td>
                        <td class="{cpu_memory_class}">{cpu_memory_result}</td>
                        <td class="details">
                            <div class="info-container">
                                {detail_button}
                                {hidden_details}
                            </div>
                        </td>
                    </tr>
                    """
            check_number_map += 1

            # vlan检测
            detail_id = f"vlan_{check_number_map}"
            icon_id = f"icon_{check_number_map}_vlan"
            tooltip_id = f"vlan_tooltip"

            detail_button = f'<div class="self-hover top-level" onmouseover="showTooltip(event, \'{tooltip_id}\')" onmouseout="hideTooltip(\'{tooltip_id}\')" onclick="toggleDetails(\'{detail_id}\', \'{icon_id}\')"><span id="{icon_id}" class="icon">&#x2795;</span>{len(self.results_vlan)} 处 VLAN 使用了保留vlan<span id="{tooltip_id}" class="tooltip">{vlan_usage_solution}</span></div>'
            hidden_details = f"<div id='{detail_id}' class='diagnosis-details'>"

            if self.results_vlan:
                for index, result in enumerate(self.results_vlan, start=1):
                    vlan_id = result.get('VLAN ID', '未知')
                    state = result.get('状态', '未知')
                    diagnostic_info = result.get('诊断原文', '未知')

                    diagnostic_lines = diagnostic_info.split('\n')
                    highlighted_diagnostic_info = "诊断原文:<br>"
                    for line in diagnostic_lines:
                        if line.strip() != "":
                            highlighted_diagnostic_info += f"{line.strip()}<br>"

                    sub_detail_id = f"{detail_id}_vlan_{index}"
                    sub_icon_id = f"{sub_detail_id}_icon"
                    diagnostic_id = f"{detail_id}_diag_{index}"

                    hidden_details += f"""
                                                    <div id="{sub_detail_id}" class="details-row">
                                                        <div class="self-hover" onclick="toggleDetails('{diagnostic_id}', '{sub_icon_id}')"><span id="{sub_icon_id}" class="icon">&#x2795;</span>状态: {state}, VLAN ID: {vlan_id}</div>
                                                        <div id="{diagnostic_id}" class="diagnosis-details">{highlighted_diagnostic_info}</div>
                                                    </div>
                                                """
                vlan_result = "不通过"
                vlan_class = "not-pass-cell"
            else:
                hidden_details += "未检测到问题的VLAN使用"
                vlan_result = "通过"
                vlan_class = "pass-cell"
            hidden_details += "</div>"

            html_content += f"""
                        <tr class="result-row">
                            <td>{check_number_map}</td>
                            <td>VLAN使用检测</td>
                            <td class="{vlan_class}">{vlan_result}</td>
                            <td class="details">
                                <div class="info-container">
                                    {detail_button}
                                    {hidden_details}
                                </div>
                            </td>
                        </tr>
                    """
            check_number_map += 1


            # 温度检测部分
            detail_id = f"temperature_{check_number_map}"
            icon_id = f"icon_{check_number_map}_temperature"
            tooltip_id = f"temperature_tooltip"

            detail_button = f'<div class="self-hover top-level" onmouseover="showTooltip(event, \'{tooltip_id}\')" onmouseout="hideTooltip(\'{tooltip_id}\')" onclick="toggleDetails(\'{detail_id}\', \'{icon_id}\')"><span id="{icon_id}" class="icon">&#x2795;</span>{len(self.results_temperature)} 处温度异常<span id="{tooltip_id}" class="tooltip">{temperature_solution}</span></div>'
            hidden_details = f"<div id='{detail_id}' class='diagnosis-details'>"

            if self.results_temperature:
                for index, result in enumerate(self.results_temperature, start=1):
                    part = result.get('部件', '未知')
                    temp = result.get('温度', '未知')
                    state = result.get('状态', '未知')
                    diagnostic_info = result.get('诊断原文', '未知')

                    diagnostic_lines = diagnostic_info.split('\n')
                    highlighted_diagnostic_info = "诊断原文:<br>"
                    for line in diagnostic_lines:
                        if line.strip() != "":
                            highlighted_diagnostic_info += f"{line.strip()}<br>"

                    sub_detail_id = f"{detail_id}_temperature_{index}"
                    sub_icon_id = f"{sub_detail_id}_icon"
                    diagnostic_id = f"{detail_id}_diag_{index}"

                    hidden_details += f"""
                                    <div id="{sub_detail_id}" class="details-row">
                                        <div class="self-hover" onclick="toggleDetails('{diagnostic_id}', '{sub_icon_id}')"><span id="{sub_icon_id}" class="icon">&#x2795;</span>部件: {part}, 温度: {temp}°C, 状态: {state}</div>
                                        <div id="{diagnostic_id}" class="diagnosis-details">{highlighted_diagnostic_info}</div>
                                    </div>
                                    """
                temperature_result = "不通过"
                temperature_class = "not-pass-cell"
            else:
                hidden_details += "未检测到温度问题"
                temperature_result = "通过"
                temperature_class = "pass-cell"
            hidden_details += "</div>"

            html_content += f"""
                                <tr class="result-row">
                                    <td>{check_number_map}</td>
                                    <td>温度检测</td>
                                    <td class="{temperature_class}">{temperature_result}</td>
                                    <td class="details">
                                        <div class="info-container">
                                            {detail_button}
                                            {hidden_details}
                                        </div>
                                    </td>
                                </tr>
                            """
            check_number_map += 1

            # ONT发射机光功率检查
            detail_id = f"transceiver_{check_number_map}"
            icon_id = f"icon_{check_number_map}_transceiver"
            tooltip_id = f"transceiver_tooltip"

            detail_button = f'<div class="self-hover top-level" onmouseover="showTooltip(event, \'{tooltip_id}\')" onmouseout="hideTooltip(\'{tooltip_id}\')" onclick="toggleDetails(\'{detail_id}\', \'{icon_id}\')"><span id="{icon_id}" class="icon">&#x2795;</span>{len(self.results_transceiver)} 处光功率问题<span id="{tooltip_id}" class="tooltip">{transceiver_solution}</span></div>'
            hidden_details = f"<div id='{detail_id}' class='diagnosis-details'>"

            if self.results_transceiver:
                for index, result in enumerate(self.results_transceiver, start=1):
                    ont = result.get('ONT', '未知')
                    rx_power = result.get('Rx power', '未知')
                    state = result.get('状态', '未知')
                    diagnostic_info = result.get('诊断原文', '未知')

                    diagnostic_lines = diagnostic_info.split('\n')
                    highlighted_diagnostic_info = "诊断原文:<br>"
                    for line in diagnostic_lines:
                        if line.strip() != "":
                            highlighted_diagnostic_info += f"{line.strip()}<br>"

                    sub_detail_id = f"{detail_id}_transceiver_{index}"
                    sub_icon_id = f"{sub_detail_id}_icon"
                    diagnostic_id = f"{detail_id}_diag_{index}"

                    hidden_details += f"""
                                <div id="{sub_detail_id}" class="details-row">
                                    <div class="self-hover" onclick="toggleDetails('{diagnostic_id}', '{sub_icon_id}')"><span id="{sub_icon_id}" class="icon">&#x2795;</span>ONT: {ont}, Rx power: {rx_power}, 状态: {state}</div>
                                    <div id="{diagnostic_id}" class="diagnosis-details">{highlighted_diagnostic_info}</div>
                                </div>
                            """
                transceiver_result = "不通过"
                transceiver_class = "not-pass-cell"
            else:
                hidden_details += "未检测到光功率问题"
                transceiver_result = "通过"
                transceiver_class = "pass-cell"
            hidden_details += "</div>"

            html_content += f"""
                        <tr class="result-row">
                            <td>{check_number_map}</td>
                            <td>ONT发射机光功率检测</td>
                            <td class="{transceiver_class}">{transceiver_result}</td>
                            <td class="details">
                                <div class="info-container">
                                    {detail_button}
                                    {hidden_details}
                                </div>
                            </td>
                        </tr>
                    """
            check_number_map += 1

            # 带外地址ip禁止
            detail_id = f"outband_ip_{check_number_map}"
            icon_id = f"icon_{check_number_map}_outband_ip"
            tooltip_id = f"outband_ip_tooltip"

            detail_button = (
                f'<div class="self-hover top-level" '
                f'onmouseover="showTooltip(event, \'{tooltip_id}\')" '
                f'onmouseout="hideTooltip(\'{tooltip_id}\')" '
                f'onclick="toggleDetails(\'{detail_id}\', \'{icon_id}\')">'
                f'<span id="{icon_id}" class="icon">&#x2795;</span>'
                f'{len(self.results_outband_ip_forbidden)} 处 带外管理IP命中禁止网段'
                f'<span id="{tooltip_id}" class="tooltip">{outband_ip_forbidden_solution}</span>'
                f'</div>'
            )

            hidden_details = f"<div id='{detail_id}' class='diagnosis-details'>"

            if self.results_outband_ip_forbidden:
                for index, result in enumerate(self.results_outband_ip_forbidden, start=1):
                    state = result.get('状态', '未知')
                    mgmt_ip = result.get('管理IP', '未知')
                    diagnostic_info = result.get('诊断原文', '未知')

                    diagnostic_lines = diagnostic_info.split('\n')
                    highlighted_diagnostic_info = "诊断原文:<br>"
                    for line in diagnostic_lines:
                        if line.strip():
                            # 尽量高亮 Management IP address 那行
                            if ("Management IP address" in line) or (mgmt_ip != '未知' and mgmt_ip in line):
                                highlighted_diagnostic_info += f"<span class='highlight-line'>{line.strip()}</span><br>"
                            else:
                                highlighted_diagnostic_info += f"{line.strip()}<br>"

                    sub_detail_id = f"{detail_id}_item_{index}"
                    sub_icon_id = f"{sub_detail_id}_icon"
                    diagnostic_id = f"{detail_id}_diag_{index}"

                    hidden_details += f"""
                        <div id="{sub_detail_id}" class="details-row">
                            <div class="self-hover" onclick="toggleDetails('{diagnostic_id}', '{sub_icon_id}')">
                                <span id="{sub_icon_id}" class="icon">&#x2795;</span>
                                管理IP: {mgmt_ip}, 状态: {state}
                            </div>
                            <div id="{diagnostic_id}" class="diagnosis-details">{highlighted_diagnostic_info}</div>
                        </div>
                    """
                outband_ip_result = "不通过"
                outband_ip_class = "not-pass-cell"
            else:
                hidden_details += "未检测到带外管理IP落入禁止网段"
                outband_ip_result = "通过"
                outband_ip_class = "pass-cell"

            hidden_details += "</div>"

            html_content += f"""
                <tr class="result-row">
                    <td>{check_number_map}</td>
                    <td>带外地址ip禁止</td>
                    <td class="{outband_ip_class}">{outband_ip_result}</td>
                    <td class="details">
                        <div class="info-container">
                            {detail_button}
                            {hidden_details}
                        </div>
                    </td>
                </tr>
            """
            check_number_map += 1

            # 带内地址ip禁止
            detail_id = f"inband_ip_{check_number_map}"
            icon_id = f"icon_{check_number_map}_inband_ip"
            tooltip_id = f"inband_ip_tooltip"

            detail_button = (
                f'<div class="self-hover top-level" '
                f'onmouseover="showTooltip(event, \'{tooltip_id}\')" '
                f'onmouseout="hideTooltip(\'{tooltip_id}\')" '
                f'onclick="toggleDetails(\'{detail_id}\', \'{icon_id}\')">'
                f'<span id="{icon_id}" class="icon">&#x2795;</span>'
                f'{len(self.results_inband_ip_forbidden)} 处 带内管理IP命中禁止网段'
                f'<span id="{tooltip_id}" class="tooltip">{inband_ip_forbidden_solution}</span>'
                f'</div>'
            )

            hidden_details = f"<div id='{detail_id}' class='diagnosis-details'>"

            if self.results_inband_ip_forbidden:
                for index, result in enumerate(self.results_inband_ip_forbidden, start=1):
                    state = result.get('状态', '未知')
                    gw_ip = result.get('网关IP', '未知')
                    diagnostic_info = result.get('诊断原文', '未知')

                    diagnostic_lines = diagnostic_info.split('\n')
                    highlighted_diagnostic_info = "诊断原文:<br>"
                    for line in diagnostic_lines:
                        if line.strip():
                            if (gw_ip != '未知' and gw_ip in line) or ("0.0.0.0" in line and gw_ip != '未知'):
                                highlighted_diagnostic_info += f"<span class='highlight-line'>{line.strip()}</span><br>"
                            else:
                                highlighted_diagnostic_info += f"{line.strip()}<br>"

                    sub_detail_id = f"{detail_id}_item_{index}"
                    sub_icon_id = f"{sub_detail_id}_icon"
                    diagnostic_id = f"{detail_id}_diag_{index}"

                    hidden_details += f"""
                        <div id="{sub_detail_id}" class="details-row">
                            <div class="self-hover" onclick="toggleDetails('{diagnostic_id}', '{sub_icon_id}')">
                                <span id="{sub_icon_id}" class="icon">&#x2795;</span>
                                带内管理IP: {gw_ip}, 状态: {state}
                            </div>
                            <div id="{diagnostic_id}" class="diagnosis-details">{highlighted_diagnostic_info}</div>
                        </div>
                    """
                inband_ip_result = "不通过"
                inband_ip_class = "not-pass-cell"
            else:
                hidden_details += "未检测到带内管理IP落入禁止网段"
                inband_ip_result = "通过"
                inband_ip_class = "pass-cell"

            hidden_details += "</div>"

            html_content += f"""
                <tr class="result-row">
                    <td>{check_number_map}</td>
                    <td>带内地址ip禁止</td>
                    <td class="{inband_ip_class}">{inband_ip_result}</td>
                    <td class="details">
                        <div class="info-container">
                            {detail_button}
                            {hidden_details}
                        </div>
                    </td>
                </tr>
            """
            check_number_map += 1

            # ONU状态检测
            detail_id = f"ont_status_{check_number_map}"
            icon_id = f"icon_{check_number_map}_ont_status"
            tooltip_id = f"ont_status_tooltip"

            detail_button = (
                f'<div class="self-hover top-level" '
                f'onmouseover="showTooltip(event, \'{tooltip_id}\')" '
                f'onmouseout="hideTooltip(\'{tooltip_id}\')" '
                f'onclick="toggleDetails(\'{detail_id}\', \'{icon_id}\', event)">'
                f'<span id="{icon_id}" class="icon">&#x2795;</span>'
                f'{len(getattr(self, "results_ont_status", []))} 处 ONU 状态异常/需处理'
                f'<span id="{tooltip_id}" class="tooltip">{ont_status_solution}</span>'
                f'</div>'
            )

            hidden_details = f"<div id='{detail_id}' class='diagnosis-details'>"
            results_ont_status = getattr(self, "results_ont_status", [])

            if results_ont_status:
                for index, result in enumerate(results_ont_status, start=1):
                    ont = result.get('ONT', '未知')
                    sn = result.get('SN', '未知')
                    status = result.get('Status', '未知')
                    reason = result.get('Reason', '未知')
                    state = result.get('状态', result.get('state', '未知'))
                    suggestion = result.get('建议', '')
                    diagnostic_info = result.get('诊断原文', '未知')

                    # 高亮：包含 status/reason 的行尽量标红
                    diagnostic_lines = str(diagnostic_info).split('\n')
                    highlighted_diagnostic_info = "诊断原文:<br>"
                    for line in diagnostic_lines:
                        if line.strip():
                            if (status != '未知' and status in line) or (reason != '未知' and reason in line) or (
                                    ont != '未知' and ont in line):
                                highlighted_diagnostic_info += f"<span class='highlight-line'>{line.strip()}</span><br>"
                            else:
                                highlighted_diagnostic_info += f"{line.strip()}<br>"

                    sub_detail_id = f"{detail_id}_item_{index}"
                    sub_icon_id = f"{sub_detail_id}_icon"
                    diagnostic_id = f"{detail_id}_diag_{index}"

                    suggestion_html = f"<br><b>建议:</b> {suggestion}" if suggestion else ""

                    hidden_details += f"""
                        <div id="{sub_detail_id}" class="details-row">
                            <div class="self-hover" onclick="toggleDetails('{diagnostic_id}', '{sub_icon_id}', event)">
                                <span id="{sub_icon_id}" class="icon">&#x2795;</span>
                                ONT: {ont}, SN: {sn}, Status: {status}, Reason: {reason}<br>
                                <b>状态:</b> {state}{suggestion_html}
                            </div>
                            <div id="{diagnostic_id}" class="diagnosis-details">{highlighted_diagnostic_info}</div>
                        </div>
                    """
                ont_result = "不通过"
                ont_class = "not-pass-cell"
            else:
                hidden_details += "未检测到 ONU 状态异常/未认证ONU"
                ont_result = "通过"
                ont_class = "pass-cell"

            hidden_details += "</div>"

            html_content += f"""
                <tr class="result-row">
                    <td>{check_number_map}</td>
                    <td>ONU状态检测</td>
                    <td class="{ont_class}">{ont_result}</td>
                    <td class="details">
                        <div class="info-container">
                            {detail_button}
                            {hidden_details}
                        </div>
                    </td>
                </tr>
            """
            check_number_map += 1

            # VLAN转换与Flow/TCONT一致性检测
            detail_id = f"vlan_translate_{check_number_map}"
            icon_id = f"icon_{check_number_map}_vlan_translate"
            tooltip_id = "vlan_translate_tooltip"

            detail_button = (
                f'<div class="self-hover top-level" '
                f'onmouseover="showTooltip(event, \'{tooltip_id}\')" '
                f'onmouseout="hideTooltip(\'{tooltip_id}\')" '
                f'onclick="toggleDetails(\'{detail_id}\', \'{icon_id}\')">'
                f'<span id="{icon_id}" class="icon">&#x2795;</span>'
                f'{len(self.results_vlan_translate)} 处 '
                f'VLAN转换或模板配置问题'
                f'<span id="{tooltip_id}" class="tooltip">'
                f'{vlan_translate_solution}'
                f'</span>'
                f'</div>'
            )

            hidden_details = (
                f"<div id='{detail_id}' class='diagnosis-details'>"
            )

            if self.results_vlan_translate:
                for index, result in enumerate(
                        self.results_vlan_translate,
                        start=1
                ):
                    state = escape(str(
                        result.get('状态', '未知')
                    ))

                    slot = result.get('槽位')
                    port = result.get('PON口')
                    ont = result.get('ONT')
                    vport = result.get('VPORT')
                    profile = result.get('模板')
                    suggestion = result.get('建议', '')
                    diagnostic_info = result.get(
                        '诊断原文',
                        '无诊断原文'
                    )

                    location_parts = []

                    if slot is not None:
                        location_parts.append(f"Slot: {slot}")

                    if port is not None:
                        location_parts.append(f"PON: {port}")

                    if ont is not None:
                        location_parts.append(f"ONT: {ont}")

                    if vport is not None:
                        location_parts.append(f"VPORT: {vport}")

                    if profile is not None:
                        location_parts.append(
                            f"模板: {escape(str(profile))}"
                        )

                    location_text = "，".join(location_parts)

                    if not location_text:
                        location_text = "全局配置"

                    suggestion_html = ""

                    if suggestion:
                        suggestion_html = (
                            f"<br><b>建议：</b>"
                            f"{escape(str(suggestion))}"
                        )

                    sub_detail_id = (
                        f"{detail_id}_item_{index}"
                    )
                    sub_icon_id = (
                        f"{sub_detail_id}_icon"
                    )
                    diagnostic_id = (
                        f"{detail_id}_diag_{index}"
                    )

                    hidden_details += f"""
                        <div id="{sub_detail_id}" class="details-row">
                            <div class="self-hover"
                                 onclick="toggleDetails(
                                     '{diagnostic_id}',
                                     '{sub_icon_id}'
                                 )">
                                <span id="{sub_icon_id}"
                                      class="icon">&#x2795;</span>
                                {location_text}<br>
                                <b>状态：</b>{state}
                                {suggestion_html}
                            </div>

                            <div id="{diagnostic_id}"
                                 class="diagnosis-details">
                                {diagnostic_info}
                            </div>
                        </div>
                    """

                vlan_translate_result = "不通过"
                vlan_translate_class = "not-pass-cell"

            else:
                hidden_details += (
                    "未检测到VLAN转换、Flow、TCONT或Service关联问题"
                )
                vlan_translate_result = "通过"
                vlan_translate_class = "pass-cell"

            hidden_details += "</div>"

            html_content += f"""
                <tr class="result-row">
                    <td>{check_number_map}</td>
                    <td>VLAN转换与Flow/TCONT一致性检测</td>
                    <td class="{vlan_translate_class}">
                        {vlan_translate_result}
                    </td>
                    <td class="details">
                        <div class="info-container">
                            {detail_button}
                            {hidden_details}
                        </div>
                    </td>
                </tr>
            """

            check_number_map += 1

            html_content += """
                                                    </tbody>
                                                </table>
                                            </div>
                                        </body>
                                        </html>
                                        """

            # 保存HTML文件
            with open(save_path, 'w', encoding='utf-8') as file:
                file.write(html_content)

            self.text_browser.setText(f"结果已成功导出到 {save_path}")
            self.set_button_state('result_exported')

        except Exception as e:
            logging.error(f"导出HTML时出错: {e}")
            self.text_browser.setText(f"导出错误: {e}")

if __name__ == "__main__":
        # 设置日志配置
        log_path = "debug.log"
        logging.basicConfig(level=logging.DEBUG,
                            format='%(asctime)s - %(levelname)s - %(message)s',
                            handlers=[
                                logging.FileHandler(log_path, mode='w', encoding='utf-8'),
                                logging.StreamHandler(sys.stdout)
                            ])

        # 初始化应用程序
        app = QApplication(sys.argv)
        window = MyWindow()

        # 显示窗口
        window.show()

        # 启动应用程序事件循环
        sys.exit(app.exec_())