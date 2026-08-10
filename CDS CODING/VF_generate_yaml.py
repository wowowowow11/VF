import pandas as pd
import yaml
from yaml.resolver import BaseResolver  # 引入默认解析器
import re
import os

# ==========================
# 配置
# ==========================

EXCEL_FILE = "VF6NP_ACU Coding CDS file_20260709.xlsx"
SHEET_NAME = "VF6NP_Coding CDS"

START_BYTE = 0
END_BYTE = 48  # 共49Byte (0 ~ 48)

# 目标输出文件夹名称（若不存在则会自动创建）
OUTPUT_DIR = "Initial_test.yaml"


# ==========================
# 自定义 PyYAML 双引号字符串类及表示器
# ==========================

class DoubleQuotedStr(str):
    """用于强制在 YAML 中输出双引号的字符串类"""
    pass


def double_quoted_scalar_representer(dumper, data):
    """
    自定义表示器：凡是 DoubleQuotedStr 类型的对象，均使用双引号输出。
    使用 BaseResolver.DEFAULT_SCALAR_TAG 避免输出类型标签 !<tag:yaml.org,002:str>
    """
    return dumper.represent_scalar(BaseResolver.DEFAULT_SCALAR_TAG, str(data), style='"')


# 注册表示器
yaml.add_representer(DoubleQuotedStr, double_quoted_scalar_representer)


# ==========================
# 辅助函数
# ==========================

def calculate_crc8_sae_j1850(data: list) -> int:
    """
    根据 Excel 规范计算 SAE J1850 CRC-8 值。
    多项式 (Poly): 0x1D, 初始值 (Init): 0xFF, 异或输出值 (XorOut): 0xFF
    """
    POLY = 0x1D
    INIT = 0xFF
    XOR_OUT = 0xFF

    crc = INIT
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc ^ XOR_OUT


def write_bits(byte_array, byte_pos, bit_pos, bit_length, value):
    """
    将指定的 bit_length 长度 of value，写入到 byte_array 的 byte_pos 起始（字节索引）
    以及 bit_pos 偏移（0 到 7，LSB 优先）的对应二进制位中（支持多字段组合）。
    """
    for i in range(bit_length):
        bit_val = (value >> i) & 1
        # 计算全局 bit 位偏移
        total_bit_pos = byte_pos * 8 + bit_pos + i
        target_byte = total_bit_pos // 8
        target_bit = total_bit_pos % 8

        if target_byte >= len(byte_array):
            continue

        # 设置或清除目标 Byte 中的指定 bit 位
        if bit_val:
            byte_array[target_byte] |= (1 << target_bit)
        else:
            byte_array[target_byte] &= ~(1 << target_bit)


def parse_value(value, bit_length, data_type):
    """
    统一解析单元格数据，转换成对应的整数或 ASCII 字符串
    """
    if pd.isna(value):
        return 0

    s = str(value).strip()

    # 显式过滤常见的非数值占位文本
    if s.lower() in ["identical", "reserved", "nan", "", "unsupported", "crc checksum crc32", "crc32"]:
        return 0

    # 如果是 ASCII 数据类型，直接返回清理后的字符串，由外部逻辑转换为 ASCII 字节数组
    if "ASCII" in str(data_type).upper():
        return s

    # 提取 0xXX 十六进制数值
    m = re.search(r'0x([0-9A-Fa-f]+)', s)
    if m:
        return int(m.group(1), 16)

    # 如果是纯十进制数字
    if s.isdigit():
        return int(s)

    # 默认回退到 0
    return 0


# ==========================
# 主业务流程
# ==========================

# 1. 自动检查并创建目标文件夹
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)
    print(f"已创建目标文件夹: {OUTPUT_DIR}")

# 2. 解析生成的文件名，使其与 Excel 文件名一致
base_name, _ = os.path.splitext(os.path.basename(EXCEL_FILE))
yaml_filename = f"{base_name}.yaml"
yaml_filepath = os.path.join(OUTPUT_DIR, yaml_filename)

# 3. 先以无 header 方式读取前 3 行，用于处理第二行的合并单元格逻辑（作为兜底）
df_raw = pd.read_excel(
    EXCEL_FILE,
    sheet_name=SHEET_NAME,
    header=None,
    nrows=3
)
drive_hand_row = df_raw.iloc[1].ffill()

# 4. 正常载入完整的 df（以第 3 行为表头，即 header=2）
df = pd.read_excel(
    EXCEL_FILE,
    sheet_name=SHEET_NAME,
    header=2
)
print("表头字段：", df.columns.tolist())

# 定位 "Steering Wheel Position" 所在的行（用于精确判定 LHD/RHD）
steering_row = df[df['Parameter'].astype(str).str.contains('Steering Wheel Position', na=False, case=False)]

# 找到Byte列
byte_col = None
for c in df.columns:
    if "Byte" in str(c):
        byte_col = c
        break

if byte_col is None:
    raise Exception("找不到Byte列")

# 找所有Variant列
variant_cols = []
for c in df.columns:
    name = str(c)
    if ("INDO" in name
            or "INDIA" in name
            or "PHL" in name
            or "VN" in name
            or "EU" == name
            or "Default value" in name):
        variant_cols.append(c)

print("发现 Variant 列：")
for c in variant_cols:
    print(" -", c)

payloads = []

for variant in variant_cols:
    # 1. 清理变体名中的换行符和多余空格（转换为标准空格）
    variant_name_clean = re.sub(r'\s+', ' ', str(variant)).strip()

    # 2. 识别该变体所在列对应的 LHD / RHD 后缀
    suffix = ""
    found_via_steering = False

    # 优先方法：从 "Steering Wheel Position" 这一行获取（最精准，不受合并单元格干扰）
    if not steering_row.empty:
        steering_val = str(steering_row.iloc[0][variant]).upper()
        if "LHD" in steering_val:
            suffix = "_LHD"
            found_via_steering = True
        elif "RHD" in steering_val:
            suffix = "_RHD"
            found_via_steering = True

    # 兜底方法：如果无法通过 steering 匹配，则回退到第二行合并单元格逻辑
    if not found_via_steering:
        col_idx = list(df.columns).index(variant)
        drive_info = str(drive_hand_row.iloc[col_idx]).strip().lower() if col_idx < len(drive_hand_row) else ""
        if "LHD" in drive_info:
            suffix = "_LHD"
        elif "RHD" in drive_info:
            suffix = "_RHD"

    # 如果原变体列名中没有此后缀，则加上后缀，否则保持原样
    if suffix and suffix not in variant_name_clean.lower():
        variant_name = f"{variant_name_clean}{suffix}"
    else:
        variant_name = variant_name_clean

    # 3. 初始化 49 字节的数据（Byte 0 到 48）
    bytes_data_int = [0] * 49

    # 4. 手动将 Byte 0, 1, 2 设置为 UDS Header 的 2E F1 08
    bytes_data_int[0] = 0x2E
    bytes_data_int[1] = 0xF1
    bytes_data_int[2] = 0x08

    # 5. 遍历 Excel 中的每一行数据，进行位拼装与组合
    for idx, row in df.iterrows():
        byte_pos_raw = row[byte_col]
        if pd.isna(byte_pos_raw):
            continue

        try:
            byte_pos = int(byte_pos_raw)
        except (ValueError, TypeError):
            continue

        # 排除 0, 1, 2 避免不小心覆盖我们手动设置的 UDS Header
        if byte_pos < 3:
            continue

        # 跳过 CRC (Byte 48)，后面会统一计算
        if byte_pos >= 48:
            continue

        bit_pos = int(row['BitPos'])
        bit_length = int(row['BitLength'])
        raw_val = row[variant]
        row_data_type = str(row.get('DataType', ''))

        # 解析该 variant 对应的值
        val = parse_value(raw_val, bit_length, row_data_type)

        if "ASCII" in row_data_type.upper() and isinstance(val, str):
            # 处理 ASCII 字符串并逐字节写入数组（如 Short VIN）
            ascii_bytes = list(val.encode('ascii', errors='ignore'))
            target_byte_len = bit_length // 8
            # 填充或截断至指定字节长度
            if len(ascii_bytes) < target_byte_len:
                ascii_bytes += [0] * (target_byte_len - len(ascii_bytes))
            else:
                ascii_bytes = ascii_bytes[:target_byte_len]

            for offset, b in enumerate(ascii_bytes):
                if byte_pos + offset < len(bytes_data_int):
                    bytes_data_int[byte_pos + offset] = b
        else:
            # 确保 val 为整数形式，执行位拼装
            if not isinstance(val, int):
                val = 0
            # 写入对应位
            write_bits(bytes_data_int, byte_pos, bit_pos, bit_length, val)

    # --------------------------------------------------
    # CRC 计算与注入 (根据 Excel 下方要求：12 到 47 位)
    # --------------------------------------------------
    payload_for_crc = bytes_data_int[12:48]
    new_crc = calculate_crc8_sae_j1850(payload_for_crc)

    # 替换第 48 位 (Byte 48)
    bytes_data_int[48] = new_crc

    # 将整型列表统一转换为两位大写的 Hex 字符串列表
    bytes_data_hex = [f"{b:02X}" for b in bytes_data_int]

    # 将 name 和 data 的值使用 DoubleQuotedStr 包裹，使其在 YAML 中带有双引号
    payload = {
        "name": DoubleQuotedStr(variant_name),
        "data": DoubleQuotedStr(" ".join(bytes_data_hex))
    }
    payloads.append(payload)

yaml_data = {
    "payloads": payloads
}

# 写入目标文件夹中的目标文件
with open(yaml_filepath, "w", encoding="utf-8") as f:
    yaml.dump(
        yaml_data,
        f,
        allow_unicode=True,
        sort_keys=False,
        width=float('inf')  # 阻止长字符串自动换行，保持在单行输出
    )

print(f"生成完成，已保存至：{yaml_filepath}")