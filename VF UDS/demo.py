import os
import re


def renumber_yaml_ids(
    file_path, start_num=82, prefix="Diag_readDID_", output_path=None
):
    """读取 YAML 文件，将形如 `id: "Diag_readDID_xxx"` 的 ID 从 start_num 开始重新排序号。"""
    if not os.path.exists(file_path):
        print(f"❌ 错误：找不到文件 '{file_path}'")
        return

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    current_num = start_num

    # 正则表达式匹配 id: "Diag_readDID_001" 或 id: 'Diag_readDID_001'
    pattern = rf'(id:\s*["\']{re.escape(prefix)})\d+(["\'])'

    def replacer(match):
        nonlocal current_num
        # 生成 3 位数字补零，如 082, 083, 100
        new_id = f"{match.group(1)}{current_num:03d}{match.group(2)}"
        current_num += 1
        return new_id

    # 执行替换
    new_content, count = re.subn(pattern, replacer, content)

    if count == 0:
        print(f"⚠️ 未找到匹配 `{prefix}` 的用例 ID，请检查前缀是否正确。")
        return

    # 写入文件
    target_file = output_path if output_path else file_path
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(
        f"✅ 处理完成！已成功重命名 {count} 个用例 ID。"
    )
    print(
        f"   序号范围：{prefix}{start_num:03d} -> {prefix}{(current_num - 1):03d}"
    )
    print(f"   保存路径：{target_file}")


if __name__ == "__main__":
    # ================= 配置区 =================
    # 替换为你具体的 YAML 文件路径
    YAML_FILE_PATH = "Diagnostic_Service_22.yaml"

    # 开始编号（82 开始）
    START_NUMBER = 10

    # ID 前缀（根据实际情况调整）
    ID_PREFIX = "Diag_ioCtrl_"
    # =========================================

    renumber_yaml_ids(YAML_FILE_PATH, start_num=START_NUMBER, prefix=ID_PREFIX)