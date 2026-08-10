"""****************************************************************************************
Script Name: outputReport.py
Version: 1.3.0
Author: Duan Zhaobing
Date: 2025-10-22
EMail: duanzb@waythink.cn
Description:
    This script parses log files and generates an HTML report summarizing test results.
    It supports multiple log files, extracts test case information, and formats the
    results into a structured HTML document.

Features:
    - Parses log files with different encodings (UTF-8, GBK, Latin1).
    - Extracts test case names and results.
    - Filters out irrelevant hexadecimal records.
    - Generates a summary of test results and detailed logs in HTML format.
    - Provides a user-friendly file selection dialog using Tkinter.

Usage:
    Run this script directly to select log files and generate an HTML report.
    The script will prompt the user to select log files and specify the output HTML file.

Requirements:
    - Python 3.x
    - Tkinter (for file dialogs)

License:
    This script is provided "as is" without warranty of any kind. You are free to use,
    modify, and distribute it for personal or commercial purposes.
****************************************************************************************"""

import re  # 导入正则表达式模块,用于解析日志文件
import tkinter as tk  # 导入tkinter模块,用于创建GUI界面
import os  # 导入os模块,用于获取文件名和路径操作
from tkinter import filedialog  # 导入文件对话框模块,用于选择文件

# 全局变量:存储所有文件的测试结果统计信息
global_test_summary = {
    "total_tests": 0,      # 测试用例总数
    "passed_tests": 0,     # 通过的测试用例数
    "failed_tests": 0,     # 失败的测试用例数
    "test_results": []     # 用于存储所有文件的测试结果列表
}

def parse_log_to_html(log_files, output_html, logs_collapsed_by_default=True):
    """解析日志文件并生成HTML报告

    Args:
        log_files: 日志文件列表
        output_html: 输出的HTML文件路径
        logs_collapsed_by_default: 日志内容是否默认折叠显示,默认为True(折叠)
    """
    global global_test_summary  # 声明使用全局变量
    # log_files = list(reversed(log_files))  # 可选:反转日志文件的处理顺序
    encodings = ['utf-8', 'gbk', 'latin1']  # 支持的文件编码列表
    test_info = None  # 存储测试信息
    test_info_file = None  # 存储testIni.json文件路径

    # 1. 查找testIni.json文件并优先读取测试信息
    for file in log_files:
        if os.path.basename(file).lower() == "testini.json":
            test_info_file = file
            break

    # 如果找到testIni.json文件,则读取并解析JSON数据
    if test_info_file:
        try:
            with open(test_info_file, 'r', encoding='utf-8') as f:
                test_info = f.read()
                import json
                test_info = json.loads(test_info)
        except Exception as e:
            test_info = None  # 解析失败时置为None

    # HTML报告的基础结构,包含CSS样式和JavaScript交互函数
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Test Report</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
            th, td {
                font-size: 12px;
                border: 1px solid #ddd;
                padding: 8px;
                text-align: left;
            }
            th { background-color: #f4f4f4; }
            .passed { color: green; }  /* 通过状态:绿色 */
            .failed { color: red; }    /* 失败状态:红色 */
            h4 { /* 盒子外标题的样式 */
                margin-top: 20px;
                margin-bottom: 5px;
                font-size: 14px;
                font-weight: 600;
            }
            .log-entry {
                margin-bottom: 15px;
                box-shadow: 0px 2px 4px rgba(0,0,0,0.1);
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: #f9f9f9;
            }
            .log-header {
                cursor: pointer;  /* 鼠标悬停时显示手型光标 */
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 10px;
                font-size: 13px;
                color: #333;
            }
            .log-content {
                display: none; /* 默认隐藏(折叠状态) */
                padding: 10px;
                border-top: 1px solid #eee;
            }
            .log-content.expanded {
                display: block; /* 展开时显示 */
            }
            .log-content pre {
                background-color: #f4f4f4;
                padding: 10px;
                border-radius: 4px;
                border: 1px solid #ddd;
                overflow-x: auto;
                white-space: pre-wrap;
                word-wrap: break-word;
                margin: 0;
            }
            .toggle-icon {
                font-size: 1em;
                color: #555;
                transition: transform 0.2s ease-in-out;  /* 图标旋转动画 */
            }
            .log-header.collapsed .toggle-icon {
                transform: rotate(-90deg);  /* 折叠时图标旋转-90度 */
            }
            .error { color: red; font-weight: bold; }  /* 错误信息:粗体红色 */
            .ok { color: green; font-weight: bold; }   /* 成功信息:粗体绿色 */
            td {
                word-wrap: break-word;
                word-break: break-word;
                white-space: normal;
            }
        </style>
        <script>
            // 切换日志条目的展开/折叠状态
            function toggleLogEntry(headerElement) {
                const content = headerElement.nextElementSibling;
                if (content) {
                    headerElement.classList.toggle('collapsed');
                    content.classList.toggle('expanded');
                }
            }
        </script>
    </head>
    <body>
    """

    # The content of the chapter is added with the ID
    test_info_html = ""
    if test_info:
        test_info_html = f"""
        <h2 id="section1">1 Test Information</h2>
        """
        # 1.1 Execution Information
        if "Execution Information" in test_info and isinstance(test_info["Execution Information"], dict):
            test_info_html += "<h3>1.1 Execution Information</h3>\n<table><tbody>\n"
            for k, v in test_info["Execution Information"].items():
                test_info_html += f"<tr><td>{k}</td><td>{v}</td></tr>\n"
            test_info_html += "</tbody></table>\n"
        # 1.2 References
        if "References" in test_info and isinstance(test_info["References"], list):
            test_info_html += "<h3>1.2 References</h3>\n<ul>\n"
            for ref in test_info["References"]:
                test_info_html += f"<li>{ref}</li>\n"
            test_info_html += "</ul>\n"
        # 1.3 Signature Block (added)
        if "Signature Block" in test_info and isinstance(test_info["Signature Block"], dict):
            test_info_html += "<h3>1.3 Signature Block</h3>\n<table><tbody>\n"
            for k, v in test_info["Signature Block"].items():
                test_info_html += f"<tr><td>{k}</td><td>{v}</td></tr>\n"
            test_info_html += "</tbody></table>\n"


    # 章节编号分配
    file_chapter_base = 2 if test_info else 1  # 全局汇总的章节编号(如果有测试信息则从2开始,否则从1开始)
    log_file_chapter_base = file_chapter_base + 1  # 日志文件的章节编号从全局汇总章节之后开始

    per_file_html = ""
    for file_index, log_file in enumerate(log_files, start=log_file_chapter_base):
        print(f"Processing file: {log_file}")        
        if os.path.basename(log_file).lower() == "testini.json":
            continue

        log_data = []
        for encoding in encodings:
            try:
                with open(log_file, 'r', encoding=encoding) as file:
                    log_data = file.readlines()
                break
            except UnicodeDecodeError:
                print(f"Failed to decode {log_file} with {encoding}, trying next encoding...")
        else:
            print(f"Failed to decode the file {log_file} with available encodings.")
            continue

        test_case_pattern = re.compile(r'\[ RUN\s+\] (.+?)(?:\s*-\s*)?$')
        test_result_pattern = re.compile(r'\[\s+(OK|FAILED)\s+\] (.+?)(?:\s*-\s*)?$')
        hex_pattern = re.compile(r'- (0x[0-9A-Fa-f]{2} -- )*0x[0-9A-Fa-f]{2} -')
        
        current_test = None
        test_results = []
        log_entries = []
        
        test_case_counter = 0
        
        # 逐行解析日志数据
        for line in log_data:
            # 跳过十六进制数据行
            if hex_pattern.search(line):
                continue

            # 匹配测试用例开始行
            match_case = test_case_pattern.search(line)
            if match_case:
                current_test = match_case.group(1).strip()  # 提取测试用例名称
                test_case_counter += 1
                section_str = f"{file_index}.2.{test_case_counter}"

                # 初始状态设置为"Pass",后续会根据测试结果更新
                test_results.append((f"{section_str} {current_test}", "Pass", f"<a href='#log-entry-{file_index}-{test_case_counter}'>View Log</a>"))

                # --- 新结构:包含标题、折叠按钮和日志内容 ---
                # 1. 标题(盒子外部)
                log_title = f"<h4>{file_index}.3.{test_case_counter} {current_test}</h4>"

                # 2. 盒子:包含折叠按钮和内容
                header_class = "log-header collapsed" if logs_collapsed_by_default else "log-header"
                content_class = "log-content" if logs_collapsed_by_default else "log-content expanded"

                # 创建可点击的折叠按钮
                log_header = f"""<div class='{header_class}' onclick='toggleLogEntry(this)'>
                                     <span>Show/Hide Details</span>
                                     <span class='toggle-icon'>&#9660;</span>
                                 </div>"""
                log_content_start = f"<div class='{content_class}'><pre>"

                # 组合标题和盒子的开始部分
                log_box_start = f"<div class='log-entry' id='log-entry-{file_index}-{test_case_counter}'>{log_header}{log_content_start}"
                log_entries.append(log_title + log_box_start)
                # --- 新结构结束 ---

                # 高亮显示OK和ERROR/FAILED标签
                line = re.sub(r'(\[\s*OK\s*\])', r'<span class="ok">\1</span>', line)
                line = re.sub(r'(\[\s*ERROR\s*\]|\[\s*FAILED\s*\])', r'<span class="error">\1</span>', line)
                log_entries[-1] += line
                continue

            # 匹配测试结果行
            match_result = test_result_pattern.search(line)
            if match_result and current_test:
                # 根据测试结果设置状态为"Pass"或"Fail"
                status = "Pass" if match_result.group(1) == "OK" else "Fail"
                test_results[-1] = (test_results[-1][0], status, test_results[-1][2])

                # 高亮显示测试结果行并关闭日志条目
                line = re.sub(r'(\[\s*OK\s*\])', r'<span class="ok">\1</span>', line)
                line = re.sub(r'(\[\s*ERROR\s*\]|\[\s*FAILED\s*\])', r'<span class="error">\1</span>', line)
                log_entries[-1] += line + "</pre></div></div>"  # 关闭pre、log-content和log-entry标签
                current_test = None  # 当前测试用例处理完成
            elif current_test:
                # 当前测试用例的普通日志行
                line = re.sub(r'(\[\s*OK\s*\])', r'<span class="ok">\1</span>', line)
                line = re.sub(r'(\[\s*ERROR\s*\]|\[\s*FAILED\s*\])', r'<span class="error">\1</span>', line)
                log_entries[-1] += line

        # 统计测试结果
        total_tests = len(test_results)
        passed_tests = sum(1 for result in test_results if result[1] == "Pass")
        failed_tests = total_tests - passed_tests

        # 更新全局统计信息
        global_test_summary["total_tests"] += total_tests
        global_test_summary["passed_tests"] += passed_tests
        global_test_summary["failed_tests"] += failed_tests
        global_test_summary["test_results"].extend([(file_index, *result) for result in test_results])

        # 生成该文件的汇总部分HTML
        per_file_html += f"""
        <h2 id="section{file_index}">{file_index} {os.path.basename(log_file)}</h2>
        <h3 id="section{file_index}_1">{file_index}.1 Summary</h3>
        """
        if total_tests > 0:
            per_file_html += f"""
            <p>Total tests: {total_tests}</p>
            <p class="passed">Passed tests: {passed_tests} ({(passed_tests / total_tests) * 100:.2f}%)</p>
            <p class="failed">Failed tests: {failed_tests} ({(failed_tests / total_tests) * 100:.2f}%)</p>
            """
        else:
            per_file_html += "<p>No test cases found in this file.</p>"

        # 生成测试结果表格
        per_file_html += f"<h3 id=\"section{file_index}_2\">{file_index}.2 Test Results</h3><table><thead><tr><th>Chapter</th><th>Test Case</th><th>Status</th><th>Details</th></tr></thead><tbody>"
        for i, (test_case, status, details) in enumerate(test_results, start=1):
            status_class = "passed" if status == "Pass" else "failed"
            chapter, test_name = test_case.split(' ', 1)
            per_file_html += f"""
            <tr>
                <td>{chapter}</td>
                <td>{test_name}</td>
                <td class="{status_class}">{status}</td>
                <td>{details}</td>
            </tr>
            """
        per_file_html += "</tbody></table>"

        # 添加测试项目的详细日志
        per_file_html += f"<h3 id=\"section{file_index}_3\">{file_index}.3 Test Items</h3>"
        for entry in log_entries:
            per_file_html += entry

    # 生成全局汇总部分的HTML
    global_summary_html = f"""
    <h2 id="section{file_chapter_base}">{file_chapter_base} Global Summary</h2>
    <h3 id="section{file_chapter_base}_1">{file_chapter_base}.1 Summary</h3>
    """
    if global_test_summary['total_tests'] > 0:
        global_summary_html += f"""
        <p>Total tests: {global_test_summary['total_tests']}</p>
        <p class="passed">Passed tests: {global_test_summary['passed_tests']} ({(global_test_summary['passed_tests'] / global_test_summary['total_tests'] * 100):.2f}%)</p>
        <p class="failed">Failed tests: {global_test_summary['failed_tests']} ({(global_test_summary['failed_tests'] / global_test_summary['total_tests'] * 100):.2f}%)</p>
        """
    else:
        global_summary_html += "<p>No tests were run.</p>"

    # 生成全局测试结果表格
    global_summary_html += f"""
    <h3 id="section{file_chapter_base}_2">{file_chapter_base}.2 Global Test Results</h3>
    <table>
        <thead>
            <tr>
                <th>File Name</th>
                <th>Chapter</th>
                <th>Test Case</th>
                <th>Status</th>
                <th>Details</th>
            </tr>
        </thead>
        <tbody>
    """

    # 填充全局测试结果表格数据
    for file_index, test_case, status, details in global_test_summary["test_results"]:
        file_name = os.path.basename(log_files[file_index - log_file_chapter_base])
        status_class = "passed" if status == "Pass" else "failed"
        chapter, test_name = test_case.split(' ', 1)
        global_summary_html += f"""
        <tr>
            <td>{file_name}</td>
            <td>{chapter}</td>
            <td>{test_name}</td>
            <td class="{status_class}">{status}</td>
            <td>{details}</td>
        </tr>
        """
    global_summary_html += "</tbody></table>"

    # 组合所有HTML内容:测试信息 + 全局汇总 + 各文件详细内容
    html_content += test_info_html
    html_content += global_summary_html
    html_content += per_file_html

    # 结束HTML文档
    html_content += """
    </body>
    </html>
    """

    # 将HTML内容写入输出文件
    with open(output_html, 'w', encoding='utf-8') as file:
        file.write(html_content)

    print(f"HTML report generated: {output_html}")

def reorder_files_dialog(parent, files):
    """显示模态对话框,允许用户重新排序选中的文件

    Args:
        parent: 父窗口
        files: 文件列表

    Returns:
        重新排序后的文件列表,如果取消则返回None
    """
    dlg = tk.Toplevel(parent)
    dlg.title("Reorder selected files")
    dlg.resizable(False, False)
    # 设置为模态对话框
    dlg.transient(parent)
    dlg.grab_set()

    # 创建列表框显示文件
    lb = tk.Listbox(dlg, selectmode=tk.SINGLE, width=100, height=12)
    for f in files:
        lb.insert(tk.END, f)
    lb.grid(row=0, column=0, columnspan=3, padx=8, pady=8)

    def move_up():
        """将选中的文件向上移动"""
        sel = lb.curselection()
        if not sel:
            return
        i = sel[0]
        if i == 0:
            return
        item = lb.get(i)
        lb.delete(i)
        lb.insert(i - 1, item)
        lb.select_set(i - 1)
        lb.activate(i - 1)

    def move_down():
        """将选中的文件向下移动"""
        sel = lb.curselection()
        if not sel:
            return
        i = sel[0]
        if i == lb.size() - 1:
            return
        item = lb.get(i)
        lb.delete(i)
        lb.insert(i + 1, item)
        lb.select_set(i + 1)
        lb.activate(i + 1)

    def on_ok():
        """确认按钮回调:保存排序结果并关闭对话框"""
        dlg.result = list(lb.get(0, tk.END))
        dlg.destroy()

    def on_cancel():
        """取消按钮回调:不保存结果并关闭对话框"""
        dlg.result = None
        dlg.destroy()

    # 创建按钮
    btn_up = tk.Button(dlg, text="Move Up", width=12, command=move_up)
    btn_up.grid(row=1, column=0, padx=6, pady=(0,8))
    btn_down = tk.Button(dlg, text="Move Down", width=12, command=move_down)
    btn_down.grid(row=1, column=1, padx=6, pady=(0,8))
    btn_frame_ok = tk.Button(dlg, text="OK", width=12, command=on_ok)
    btn_frame_ok.grid(row=1, column=2, padx=6, pady=(0,8))
    btn_cancel = tk.Button(dlg, text="Cancel", width=12, command=on_cancel)
    btn_cancel.grid(row=2, column=2, padx=6, pady=(0,8))

    # 将对话框居中显示在父窗口上
    parent.update_idletasks()
    dlg.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - dlg.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - dlg.winfo_height()) // 2
    dlg.geometry(f"+{max(x,0)}+{max(y,0)}")

    dlg.wait_window()
    return getattr(dlg, "result", None)

# 使用tkinter对话框选择文件并生成报告
def select_files_and_generate_report(logs_collapsed_by_default):
    """使用文件对话框选择日志文件并生成HTML报告

    Args:
        logs_collapsed_by_default: 日志内容是否默认折叠
    """
    root = tk.Tk()
    root.withdraw()  # 隐藏主窗口
    # 打开文件选择对话框
    log_files = filedialog.askopenfilenames(title="Select log files", filetypes=[("Text files", "*.*"), ("All files", "*.*")])
    if not log_files:
        return
    # 转换为列表以便重新排序
    selected = list(log_files)

    # 允许用户按期望的顺序重新排序文件
    # 如果用户取消重新排序对话框(result为None),则保持原始顺序
    root.deiconify()  # 需要可见的父窗口用于模态对话框定位
    ordered = reorder_files_dialog(root, selected)
    root.withdraw()
    if ordered is not None:
        selected = ordered

    # 打开保存文件对话框
    output_html_path = filedialog.asksaveasfilename(title="Save HTML report as", defaultextension=".html", filetypes=[("HTML files", "*.html")])
    if output_html_path:
        parse_log_to_html(selected, output_html_path, logs_collapsed_by_default=logs_collapsed_by_default)

# 主函数
def main():
    """程序入口函数"""
    # --- 配置项 ---
    # 设置为True使日志默认折叠,设置为False使日志默认展开
    DEFAULT_LOG_STATE_COLLAPSED = False
    # --- 配置结束 ---

    # 调用文件选择和报告生成函数
    select_files_and_generate_report(logs_collapsed_by_default=DEFAULT_LOG_STATE_COLLAPSED)

# 脚本入口点
if __name__ == "__main__":
    main()