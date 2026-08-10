"""
****************************************************************************
**  【批量全量版】单轴波形解析脚本                                        **
**  1. 使用相对路径，自动定位当前脚本所在的 XY 根目录                      **
**  2. 一次性扫描并解包 XY 下所有子目录 (data15, data30...) 的 TXT 文件     **
**  3. 将 Crash X 和 Crash Y 整行 1000 字节全量解包为 500 个点的 JSON 数组   **
****************************************************************************
"""

import os
import json
import re

def parse_single_channel_hex(hex_str):
    """
    将整行十六进制数据按“两两一组（小端序 int16）”全量解包
    1000 字节 -> 500 个采样点
    """
    raw_bytes = [int(b, 16) for b in hex_str.split()]

    acc_values = []
    # 每 2 个字节解包为 1 个有符号整数
    for i in range(0, len(raw_bytes) - 1, 2):
        # 小端序：raw_bytes[i] 为低字节，raw_bytes[i+1] 为高字节
        raw = bytes([raw_bytes[i+1], raw_bytes[i]])
        val = int.from_bytes(raw, byteorder='big', signed=True)
        acc_values.append(val)

    return acc_values

def sanitize_filename(name):
    """替换 Windows 文件名非法字符"""
    return re.sub(r'[\/:*?"<>|]', '-', name)

def batch_process_single_channel(target_dir):
    """处理单个文件夹下的所有 TXT 文件"""
    files = [f for f in os.listdir(target_dir) if f.lower().endswith('.txt')]
    if not files:
        print(f"  └─ 在目录 [{target_dir}] 中未找到任何 .txt 文件，跳过。")
        return 0

    folder_name = os.path.basename(os.path.normpath(target_dir))
    print(f"  ├─ 找到 {len(files)} 个 txt 文件，开始全量解包解析...")

    total_json_generated = 0

    for file_idx, file_name in enumerate(files, 1):
        txt_file_path = os.path.join(target_dir, file_name)
        txt_filename_base = os.path.splitext(file_name)[0]

        with open(txt_file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        file_success_count = 0
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line or ",Data:" not in line:
                continue

            try:
                meta_part, data_part = line.split(",Data:", 1)
                meta_info = meta_part.strip()
                hex_str = data_part.strip()

                # 全量解包整行数据 (500 个点)
                acc_values = parse_single_channel_hex(hex_str)

                # 构造统一格式的 JSON (区分 X 轴与 Y 轴命名，或统一命名)
                if "Crash X" in meta_info:
                    parsed_result = {"000_x_acc_variable": acc_values}
                elif "Crash Y" in meta_info:
                    parsed_result = {"001_y_acc_variable": acc_values}
                else:
                    parsed_result = {"acc_variable": acc_values}

                # 保存 JSON 文件
                clean_meta = sanitize_filename(meta_info)
                json_filename = f"{txt_filename_base}_{clean_meta}.json"
                json_save_path = os.path.join(target_dir, json_filename)

                with open(json_save_path, 'w', encoding='utf-8') as jf:
                    json.dump(parsed_result, jf, indent=4, ensure_ascii=False)

                file_success_count += 1
                total_json_generated += 1

            except Exception as e:
                print(f"  │  └─ [Line {line_num}] 解析出错 ({meta_info}): {str(e)}")

    print(f"  └─ 文件夹 [{folder_name}] 解析完成，成功生成 {total_json_generated} 个 JSON 文件。")
    return total_json_generated

def extract_data_num(path):
    """提取自然数字，用于升序排列 data15, data30, data45..."""
    match = re.search(r'data(\d+)', path, re.IGNORECASE)
    return int(match.group(1)) if match else 999

def process_all_txt_folders(xy_root_dir):
    """一次性递归搜寻 XY 目录下所有包含 txt 的子文件夹并全量解析"""
    if not os.path.exists(xy_root_dir):
        print(f"错误: 目标根目录不存在 -> {xy_root_dir}")
        return

    # 搜寻所有包含 .txt 文件的子文件夹
    target_subdirs = []
    for root, dirs, files in os.walk(xy_root_dir):
        if any(f.lower().endswith('.txt') for f in files):
            target_subdirs.append(root)

    if not target_subdirs:
        print(f"在目录 {xy_root_dir} 及其子目录下未找到任何 .txt 文件！")
        return

    # 按 data15, data30, data45... 自然数字顺序排序
    target_subdirs.sort(key=extract_data_num)

    print("="*60)
    print(f"【批量解析 TXT】找到 {len(target_subdirs)} 个包含 TXT 的数据文件夹，开始全量解包...")
    print("="*60)

    total_all_json = 0
    for sdir in target_subdirs:
        folder_name = os.path.basename(os.path.normpath(sdir))
        print(f"\n正在处理目录: [{folder_name}] ...")
        count = batch_process_single_channel(sdir)
        total_all_json += count

    print("\n" + "="*60)
    print(f"🎉 所有子目录的 TXT 文件已全部解析完成！共生成 {total_all_json} 个 JSON 文件。")
    print("="*60)

if __name__ == "__main__":
    # 【相对路径】：自动获取当前脚本 parse_txt_data.py 所在的 XY 根目录
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

    # 一次性处理 XY 根目录下所有的 dataXX 子文件夹中的 TXT
    process_all_txt_folders(SCRIPT_DIR)