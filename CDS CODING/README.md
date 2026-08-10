# Generate_CDS - CDS Fuzzing 数据生成工具

## 简介

基于原车 CDS 诊断报文数据，通过**字节变异 + CRC8 自动校验**的方式，批量生成 Fuzzing 测试用例。工具从 YAML 配置文件中读取原车报文，对指定字节位进行穷举变异，自动计算并替换 CRC 校验值，最终输出为标准 YAML 格式的测试数据文件。

## 工作原理

```
输入 YAML (原车报文) → 字节变异 (组合+穷举) → CRC8 自动校验 → 输出 YAML (Fuzzing 数据)
```

### 数据帧结构 (49 字节)

| 字节范围 | 说明 |
|---------|------|
| 0 - 11  | 固定头 (不参与变异) |
| **12 - 47** | **参与变异的可变区域** (其中 16-33 位为默认变异区间) |
| 48 | CRC8 校验位 (自动计算并替换) |

### 变异策略

1. 从可变区间 `VAR_START_IDX` ~ `VAR_END_IDX` 中选取 `MUTATE_BYTES` 个字节位
2. 对选中的每个字节位，从 `ALLOWED_VALUES` (`["01", "10", "11"]`) 中穷举赋值
3. 自动剔除与原车数据完全一致的冗余组合
4. 对变异后的数据，取第 12-47 位（共 36 字节）重新计算 CRC8，写入第 48 位

### CRC8 校验算法

- 标准：**SAE J1850**
- 多项式：`0x1D`
- 初始值：`0xFF`
- 异或输出：`0xFF`
- 计算范围：第 12 位到第 47 位（共 36 字节）

## 依赖环境

- Python 3.6+
- PyYAML

```bash
pip install pyyaml
```

## 使用方法

### 1. 准备输入 YAML 文件

在 `Initial_test.yaml/` 目录下放置输入配置文件，格式如下：

```yaml
payloads:
  - name: "车型A_配置1"
    data: "00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F 10 11 12 13 14 15 16 17 18 19 1A 1B 1C 1D 1E 1F 20 21 22 23 24 25 26 27 28 29 2A 2B 2C 2D 2E 2F 30"
  - name: "车型B_配置2"
    data: "00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F 10 11 12 13 14 15 16 17 18 19 1A 1B 1C 1D 1E 1F 20 21 22 23 24 25 26 27 28 29 2A 2B 2C 2D 0E 0F"
```

> 注意：`data` 字段是以空格分隔的十六进制字节字符串，总长度应为 **49 字节**（含 CRC 位）。

### 2. 配置参数

在 `Generate_CDS.py` 中修改以下配置项：

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `YAML_FILE` | `Initial_test.yaml/Initial_cds_coding.yaml` | 输入 YAML 文件的绝对路径 |
| `OUTPUT_DIR` | `Generate_test.yaml/` | 输出文件目录（相对于脚本所在目录） |
| `ALLOWED_VALUES` | `["01", "10", "11"]` | 变异允许的取值（已剔除 `"00"`） |
| `VAR_START_IDX` | `16` | 变异起始索引（Python 索引，即第 17 字节） |
| `VAR_END_IDX` | `33` | 变异结束索引（Python 索引，即第 34 字节） |
| `MUTATE_BYTES` | `2` | 每次同时变异的字节数 |

### 3. 运行脚本

```bash
cd CDS_coding
python Generate_CDS.py
```

### 4. 输出结果

生成的文件保存在 `Generate_test.yaml/` 目录下，每个输入配置对应一个输出文件，命名格式为 `{车型名}_{变异字节数}bytes.yaml`。

输出文件格式：

```yaml
payloads:
  - name: "车型A_配置1_1"
    data: "00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F 01 11 12 13 14 15 16 17 18 19 1A 1B 1C 1D 1E 1F 20 21 22 23 24 25 26 27 28 29 2A 2B 2C 2D 2E 5A"
  - name: "车型A_配置1_2"
    data: "00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F 10 11 12 13 14 15 16 17 18 19 1A 1B 1C 1D 1E 1F 20 21 22 23 24 25 26 27 28 29 2A 2B 2C 2D 2E 3B"
```

## 数据量估算

默认配置下（18 个可变位，每次变异 2 字节，3 种取值）：

```
C(18,2) × 3² = 153 × 9 = 1,377 条/每个输入配置
```

如需增加覆盖范围，可调整 `MUTATE_BYTES` 参数，但数据量会显著增长：

| MUTATE_BYTES | 组合数 | 数据量 (×3^N) |
|:---:|:---:|:---:|
| 1 | 18 | 54 |
| 2 | 153 | 1,377 |
| 3 | 816 | 22,032 |
| 4 | 3,060 | 247,887 |

## 项目结构

```
CDS_coding/
├── Generate_CDS.py          # 主脚本（Fuzzing 数据生成器）
├── CDS_coding.py            # CDS 编码相关逻辑
├── USBCANFD_DEMO.py         # USB CAN-FD 设备演示
├── USBCANFD系列.py           # USB CAN-FD 系列驱动
├── zlgcan.py                # 周立功 CAN 设备接口
├── Initial_test.yaml/       # 输入 YAML 配置文件目录
│   └── Initial_cds_coding.yaml
├── Generate_test.yaml/      # 输出 Fuzzing 数据目录（自动生成）
├── kerneldlls/              # CAN 设备内核驱动库
└── log/                     # 日志目录
```
