import itertools
import time
import os
import yaml
import re

# ==========================================
# 1. 基础配置
# ==========================================
# 自动获取 Generate_CDS.py 所在的真实文件夹绝对路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 你的 YAML 源文件绝对路径
YAML_FILE = "/CDS_coding/Initial_test.yaml/VF6_425_Initial_cds_coding.yaml"

OUTPUT_DIR = os.path.join(SCRIPT_DIR, "Generate_test.yaml")  # 生成的文件也保存在该目录下

VAR_START_IDX = 16  # 第17位的索引
VAR_END_IDX = 33    # 第34位的索引

# 每次挑几个字节进行变异 (根据要求改为 1)
MUTATE_BYTES = 1

# 【新增核心规则】定义不同原始值允许的变化范围 (0不变，1可变)
MUTATION_RULES = {
    "01": ["00", "01"],                 # 0不变，1变成0或1
    "10": ["00", "10"],                 # 0不变，1变成0或1
    "11": ["00", "01", "10", "11"]      # 两个1都可以变
}

# ==========================================
# 2. 校验算法层 (CRC8)
# ==========================================
def calculate_crc8(hex_list):
    """
    通用汽车 CRC8 计算函数
    默认采用 SAE J1850 标准 (多项式 0x1D)
    """
    data_bytes = [int(x, 16) for x in hex_list]

    poly = 0x1D
    crc = 0xFF
    xor_out = 0xFF

    for b in data_bytes:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ poly
            else:
                crc <<= 1
            crc &= 0xFF

    final_crc = crc ^ xor_out
    return f"{final_crc:02X}"


# ==========================================
# 3. 核心生成器
# ==========================================
def generate_local_fuzzing_data(original_data_list):
    """
    基于“1可变，0不可变”规则生成变异数据
    """
    # 动态生成索引列表：剔除原数据为 "00" 的位置
    variable_indices = []
    for idx in range(VAR_START_IDX, VAR_END_IDX + 1):
        if original_data_list[idx] != "00":
            variable_indices.append(idx)
    # 增加一个集合，用于记录已经生成过的序列，防止重复
    seen_payloads = set()
    # 挑选目标索引（不包含原数据为 "00" 的位置）
    for target_indices in itertools.combinations(variable_indices, MUTATE_BYTES):
        # 根据选中的原车数据，动态获取它允许的变异列表
        # 例如：选中的原始值是 "10"，那么池子就是 ["00", "10"]
        pools = [MUTATION_RULES[original_data_list[idx]] for idx in target_indices]
        # 穷举当前特定候选池的所有组合
        for value_combo in itertools.product(*pools):
            temp_data = original_data_list.copy()
            # 替换变异的字节
            for idx, val in zip(target_indices, value_combo):
                temp_data[idx] = val
            # 剔除和原车数据完全一样的数据
            if temp_data == original_data_list:
                continue
            # 将列表转为不可变的 tuple 存入 set 中检查，去重
            data_tuple = tuple(temp_data)
            if data_tuple in seen_payloads:
                continue
            seen_payloads.add(data_tuple)
            # ---------------------------------------------------------
            # 取出需要计算 CRC 的数据段 (Python 索引 12 到 47，共36个字节)
            # ---------------------------------------------------------
            payload_for_crc = temp_data[12:48]
            # 计算新的 CRC 值
            new_crc = calculate_crc8(payload_for_crc)
            # 替换第 48 位
            temp_data[48] = new_crc
            yield temp_data


# ==========================================
# 4. 辅助函数：安全的文件名
# ==========================================
def sanitize_filename(name):
    """将名字中的特殊字符(如空格、括号)替换为下划线，用于创建安全的文件名"""
    clean_name = re.sub(r'[\\/*?:"<>| ()]+', "_", name)
    return clean_name.strip("_")


# ==========================================
# 5. 执行主流程
# ==========================================
def main():
    if not os.path.exists(YAML_FILE):
        print(f"❌ 错误: 找不到 YAML 文件 '{YAML_FILE}'，请检查绝对路径是否正确！")
        return

    # 创建输出文件夹
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("======================================================")
    print("🚀 正在读取 YAML 配置并批量生成 Fuzzing 数据 (保存为 YAML)...")
    print(f"变异策略: 每次挑选 {MUTATE_BYTES} 字节")
    print(f"变异规则: 0保持不变，1可变 (降维打击模式)")
    print("附加操作: 自动计算第12-47位 CRC8 并替换第48位")
    print("======================================================\n")

    start_time = time.time()

    # 解析输入的 YAML 文件
    with open(YAML_FILE, 'r', encoding='utf-8') as f:
        yaml_data = yaml.safe_load(f)

    payloads = yaml_data.get("payloads", [])
    if not payloads:
        print("❌ 错误: YAML 文件中没有找到 payloads 列表！")
        return

    total_files = 0
    total_lines = 0

    # 遍历处理每一个配置
    for item in payloads:
        car_name = item.get("name", "Unknown")
        raw_data_str = item.get("data", "")

        if not raw_data_str:
            continue

        data_list = raw_data_str.split(" ")

        # 输出文件后缀改为 test.yaml
        safe_name = sanitize_filename(car_name)
        output_file = os.path.join(OUTPUT_DIR, f"{safe_name}_{MUTATE_BYTES}bytes.yaml")

        print(f"🔄 正在处理: [{car_name}] ...")

        generator = generate_local_fuzzing_data(data_list)

        count = 0
        # 按照标准 YAML 格式手工拼接并写入文件
        with open(output_file, 'w', encoding='utf-8') as out_f:
            # 写入 YAML 的根节点
            out_f.write("payloads:\n")

            for payload in generator:
                count += 1
                payload_str = " ".join(payload)
                # 写入每一条变异数据，格式与你要求的完全一致，加上递增编号区分
                out_f.write(f'  - name: "{car_name}_{count}"\n')
                out_f.write(f'    data: "{payload_str}"\n')

        total_files += 1
        total_lines += count

        print(f"   └─ 保存至: {output_file} (生成 {count} 条 payload)")

    end_time = time.time()

    print("\n✅ 所有任务处理完毕！")
    print("======================================================")
    print(f"📊 统计结果: 共处理 {total_files} 个配置，累计生成 {total_lines:,} 条变异数据。")
    print(f"⏱️ 耗时: {end_time - start_time:.3f} 秒")
    print(f"📁 结果均已保存在 '{os.path.abspath(OUTPUT_DIR)}' 文件夹下的 YAML 文件中。")


if __name__ == "__main__":
    main()